# Implementation Plan: Night Ramen Breakdown 自律改善ループシステム

**Branch**: `001-night-ramen-breakdown` | **Date**: 2026-06-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-night-ramen-breakdown/spec.md`

## Summary

ユーザーの夜食ラーメン習慣を断つため、(1)毎朝 Google Health からバイタルを収集して長期記憶に蓄積し、
(2)ラーメンが身体に与えた悪影響を分析・数値化し、(3)目標達成率に応じて強度調整した説得メッセージを
ガードレール検証の上で 23:00 に LINE へ1回送信し、(4)結果を学習して説得プロンプトを自律更新するループを
構築する。インフラは AWS CDK(Python) で IaC 化し、Lambda + EventBridge をスケジューラ、agentcore を
記憶・ガードレール基盤、strands をワークフロー制御、Amazon Bedrock を LLM 基盤とする。

## Technical Context

**Language/Version**: Python 3.13（Lambda ランタイムは 3.13、ローカルも同一）

**Primary Dependencies**:
- `aws-cdk-lib` 2.259 / `constructs` 10.6（IaC）
- `strands-agents` 1.43 / `strands-agents-tools`（自律ループ・エージェントオーケストレーション）
- `bedrock-agentcore` 0.1（長期記憶 Memory・ガードレール）+ `bedrock-agentcore-starter-toolkit`（agentcore CLI）
- `boto3`（Bedrock Runtime, Secrets Manager, EventBridge 等）
- `line-bot-sdk` 3.23（LINE Messaging API）
- `google-auth` / `google-auth-oauthlib` / `google-api-python-client`（Google Health OAuth 2.0）

**Storage**:
- agentcore Memory（長期記憶：デイリーバイタルレコード・説得履歴・現行プロンプト戦略・分析結果）
- AWS Secrets Manager（Google OAuth トークン、LINE チャネルトークン）
- 補助メタ（目標設定・冪等キー・通知済みフラグ）は DynamoDB（単一テーブル）

**Testing**: pytest（ユニット）+ moto（AWS スタブ）。ガードレール検証ロジックは必須テスト（原則 II）。

**Target Platform**: AWS（Lambda, EventBridge, Secrets Manager, DynamoDB, Bedrock, agentcore Runtime）。リージョン: バイタル/Bedrock 整合のため単一リージョン（既定 us-east-1、Bedrock モデル可用性で確定）。

**Project Type**: クラウドネイティブ・サーバーレス（バックエンドのみ。フロントは LINE が代替）。

**Performance Goals**: バッチ志向。朝の収集は数分以内、夜の通知は 23:00±5分以内に配信（SC-002）。同時実行は単一ユーザー前提で低い。

**Constraints**:
- 機微健康データ・認証情報を平文でログ/コードに残さない（原則 III, FR-011）。
- LLM は Bedrock 経由のみ。初期は Claude Haiku に統一、本番は分析=Opus 4.8 / 生成=Sonnet 4.6（構成で切替）。
- ユーザーへ届く全メッセージはガードレール通過必須、不通過時は最大2回再生成→固定安全文面（FR-008/009）。
- 通知は1晩1回（FR-014）。

**Scale/Scope**: 単一ユーザー（本人）。マルチテナントはスコープ外。1日あたり収集1回・通知1回・自律更新1回。

## Constitution Check

*GATE: Phase 0 前に通過必須。Phase 1 設計後に再確認。*

| 原則 | ゲート | 本計画での充足 |
|------|--------|----------------|
| I. 仕様駆動開発 | 全実装が spec/plan/tasks に由来 | 本 plan は spec.md の FR/SC を直接トレース。research.md に対応表を記載 |
| II. 安全・ガードレール最優先 (NON-NEGOTIABLE) | 送信前検証＋検証ロジックに自動テスト | `guardrail` モジュールを送信経路の単一ゲートに設置。pytest 必須。fail-closed 設計 |
| III. プライバシー・最小権限 | 最小 IAM / シークレット管理 / PII 非ログ | トークンは Secrets Manager。Lambda ごとに最小 IAM。構造化ログは PII マスキング |
| IV. Infrastructure as Code | 全リソース CDK / cdk diff 可能 | `infra/` に CDK(Python) スタック。手動コンソール変更禁止 |
| V. 観測可能性・コスト規律 | 構造化ログ / モデル切替 / 各ステップ追跡 | CloudWatch 構造化ログ + 相関ID。モデルは設定値で切替（既定 Haiku）|

**判定**: ✅ 違反なし（Complexity Tracking 記載不要）。

## Project Structure

### Documentation (this feature)

```text
specs/001-night-ramen-breakdown/
├── plan.md              # 本ファイル
├── research.md          # Phase 0: 技術調査と決定（FR対応表含む）
├── data-model.md        # Phase 1: エンティティ定義
├── quickstart.md        # Phase 1: ローカル/デプロイ手順
├── contracts/           # Phase 1: 内部コントラクト（記憶スキーマ・エージェントIF）
│   ├── memory-schemas.md
│   └── agent-interfaces.md
└── tasks.md             # Phase 2: /speckit-tasks が生成（本コマンドでは作らない）
```

### Source Code (repository root)

```text
src/night_ramen/
├── collectors/                # 収集層（US2 / FR-001,002,003）
│   ├── google_health.py       #   Google Health API クライアント（OAuth・指標取得）
│   └── self_report.py         #   夜食ラーメン自己申告の取り込み
├── memory/                    # 長期記憶（agentcore Memory ラッパ）
│   ├── client.py              #   Memory への read/write
│   └── schemas.py             #   デイリーバイタル/説得履歴/戦略/分析結果のスキーマ
├── agents/                    # エージェント（US1,3,4 / FR-004,006,010）
│   ├── analyzer.py            #   分析エージェント（悪影響の数値化）
│   ├── persuader.py           #   説得最適化エージェント（メッセージ生成）
│   └── prompts.py             #   プロンプト・モデル設定（Haiku/Opus/Sonnet 切替）
├── goals/                     # 目標管理（US3 / FR-005,006,017）
│   └── evaluator.py           #   達成率評価・強度トーン決定
├── guardrail/                 # ガードレール（原則II / FR-008,009,015）
│   ├── validator.py           #   3観点チェック＋fail-closed＋再生成制御
│   └── fallback.py            #   固定安全文面
├── notifier/                  # 通知（US1 / FR-007,014）
│   └── line.py                #   LINE Messaging API 送信・1晩1回冪等制御
├── workflow/                  # strands ループ制御（FR-013）
│   ├── morning.py             #   収集→記憶（朝ルーティン）
│   └── night.py               #   分析→目標評価→生成→検証→送信→学習（夜ルーティン）
├── config.py                  # 環境・モデル・しきい値設定
└── observability.py           # 構造化ログ・相関ID・PIIマスキング

src/handlers/                  # Lambda エントリポイント
├── morning_handler.py         #   EventBridge: 朝の収集
└── night_handler.py           #   EventBridge: 夜の通知ループ

infra/                         # AWS CDK (Python)  ── 原則IV
├── app.py
└── stacks/
    └── night_ramen_stack.py   #   Lambda×2, EventBridge×2, DynamoDB, Secrets, IAM最小権限

tests/
├── unit/
│   ├── test_guardrail.py      #   ★必須（原則II / 品質ゲート）
│   ├── test_goals.py
│   ├── test_collectors.py
│   └── test_memory_schemas.py
└── integration/
    ├── test_morning_flow.py
    └── test_night_flow.py     #   ダミーデータでUS1のIndependent Test
```

**Structure Decision**: 単一サーバーレスバックエンド構成を採用。`src/night_ramen/` にドメインロジックを
層別配置し、`src/handlers/` で Lambda 入口を薄く保つ。インフラは `infra/` の CDK に集約し、ビジネス
ロジックから分離（テスト容易性とIaC原則の両立）。フロントエンドは LINE が担うため別ディレクトリは不要。

## Complexity Tracking

> Constitution Check に違反がないため記載なし。
