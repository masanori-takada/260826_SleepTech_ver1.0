# Implementation Plan: LINE 対話エージェント（長期記憶活用）

**Branch**: `002-line-agentcore-memory` | **Date**: 2026-06-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-line-agentcore-memory/spec.md`

## Summary

既存の Webhook Lambda を拡張し、LINE の自由文メッセージに対して「健康コーチ」として対話応答する。
応答は (1) agentcore Memory の長期記憶（事実/嗜好/要約）＋短期会話履歴、(2) DynamoDB のバイタル傾向・目標、
を文脈として Bedrock(Haiku) で生成し、**送信前にガードレール検証**を通してから LINE 返信する。
会話から夜食ラーメンの有無を抽出し、固定パースと併用して当日の自己申告を DynamoDB に記録する。
agentcore Memory はユーザー単位（actorId=LINE userId）に分離し、日をまたいだユーザー理解を蓄積する。

## Technical Context

**Language/Version**: Python 3.13（Lambda・ローカル）

**Primary Dependencies**:
- **boto3 >= 1.43**（`bedrock-agentcore` data plane / `bedrock-agentcore-control` / `bedrock-runtime`）。
  ※ Lambda 同梱の boto3 は古く agentcore 非対応のため、boto3 を **Lambda Layer**（純Python・クロスプラットフォーム安全）で上書きする。
- 既存 `guardrail` / `memory(DynamoDB)` / `notifier(LINE, urllib)` モジュールを再利用。
- agentcore Memory リソース作成（1回限り）は `bedrock-agentcore-control.create_memory`（boto3）。

**採用しない**: `strands` / `bedrock-agentcore` Python SDK の Lambda 同梱（pydantic-core 等のネイティブ依存＋starlette/uvicorn で
クロスプラットフォーム・ビルドが重いため）。agentcore Memory は **boto3 直接呼び出し**で活用する。

**Storage**:
- **agentcore Memory**（新規）: 会話ターン（STM）＋長期記憶レコード（LTM: semantic/userPreference/summary）。ユーザー単位。
- **DynamoDB**（継続）: デイリーバイタル時系列・目標・自己申告(REPORT)・通知冪等(NOTIFY)。厳密な数値範囲取得・原子的冪等のため不可欠。

**Testing**: pytest。会話抽出・ガードレール適用・Memory読み書きの薄いラッパをユニット化（boto3 はフェイク注入）。

**Target Platform**: AWS Lambda（Webhook 同期応答）＋ agentcore Memory ＋ Bedrock ＋ DynamoDB。

**Project Type**: 既存サーバーレスバックエンドへの機能追加。

**Performance Goals**: 通常応答は10秒以内（SC-107）。Haiku＋Memory取得で数秒。Webhook Lambda の timeout/memory を引き上げる。

**Constraints**:
- 全返信はガードレール通過必須（原則II / FR-106）。fail-closed。
- 記憶はユーザー単位分離（FR-107）。actorId=LINE userId。
- Webhook 署名検証（FR-108、既存実装を流用）。
- 機微データ・トークンを平文ログ/コードに出さない（FR-109 / 原則III）。

**Scale/Scope**: 単一ユーザー想定。会話は受動応答（ユーザー発話時のみ）。

## Constitution Check

| 原則 | 充足 |
|------|------|
| I. 仕様駆動 | 本 plan は spec 002 の FR-101〜112 をトレース。research にFR対応表 |
| II. 安全・ガードレール (NON-NEGOTIABLE) | 会話返信も `guardrail.generate_safe_message` 経由で送信前検証・fail-closed |
| III. プライバシー最小権限 | Memory/Bedrock/DDB/Secrets を最小 IAM。userId 以外の PII を記憶へ不要に書かない。ログはマスキング継続 |
| IV. IaC | Memory リソース作成スクリプト＋Lambda Layer・権限を CDK 管理（MEMORY_ID は環境変数）|
| V. 観測・コスト | 会話=Haiku（低コスト）。相関IDログ継続。Memory 呼び出しも追跡 |

**判定**: ✅ 違反なし。

## Project Structure

### Documentation

```text
specs/002-line-agentcore-memory/
├── plan.md / research.md / data-model.md / quickstart.md / contracts/ / tasks.md / analysis.md
```

### Source Code（既存 src/night_ramen への追加）

```text
src/night_ramen/
├── conversation/                  # 新規: 対話機能
│   ├── memory_store.py            #   agentcore Memory ラッパ（boto3直叩き: write_turn / retrieve_context）
│   ├── agent.py                   #   健康コーチ会話エージェント（文脈構築→Bedrock→返信案）
│   └── extractor.py               #   会話からの自己申告抽出（固定パース併用、LLM補完）
├── notifier/line.py               # 既存（reply_message / verify_line_signature 再利用）
├── guardrail/                     # 既存（会話返信の検証に再利用）
└── ...（既存）

src/handlers/
└── webhook_handler.py             # 拡張: 署名検証→(固定パース)→会話エージェント→ガードレール→返信＋申告保存

scripts/
└── create_memory.py               # 1回限り: agentcore Memory(3戦略)を作成し MEMORY_ID を出力

infra/
├── layers/boto3/                  # 新規: boto3>=1.43 を入れた Lambda Layer 素材
└── stacks/night_ramen_stack.py    # 拡張: Layer・MEMORY_ID env・bedrock-agentcore IAM を Webhook Lambda に付与

tests/
├── unit/test_conversation_extractor.py
├── unit/test_memory_store.py        # boto3フェイクで write/retrieve を検証
└── integration/test_webhook_conversation.py  # 署名→会話→ガードレール→返信＋申告
```

**Structure Decision**: 既存構成に `conversation/` を追加。Webhook Lambda を「固定パース＋会話エージェント」のハイブリッドに拡張。
agentcore Memory アクセスは `memory_store.py` に集約し、boto3 をフェイク注入してテスト可能にする。

## 主要設計判断（research.md 参照）

1. **agentcore Memory は boto3 直叩き**（SDK非同梱）。Memory リソースは3戦略付きで一度だけ作成。
2. **DynamoDB 併存**（バイタル/冪等/目標/申告）。Memory は会話・長期理解専用。
3. **会話エージェントは Webhook Lambda 内で同期実行**。Haiku で数秒応答。timeout/memory 引き上げ。
4. **返信は必ずガードレール通過**（原則II）。既存 `generate_safe_message` を会話に適用。
5. **自己申告は併用**: 固定キーワードパース → 判別不能なら会話抽出（LLM）で補完 → DynamoDB REPORT。
6. **依存上書き**: boto3>=1.43 を Lambda Layer で提供（純Python・Docker不要）。

## Complexity Tracking

> Constitution 違反なし。複雑性の正当化は不要。
> 唯一の追加複雑性（boto3 Layer）は「Lambda 同梱 boto3 が agentcore 非対応」という外部制約への最小対応であり、
> strands/SDK 同梱（ネイティブ依存・重量）より単純なため採用。
