# Phase 0: Research & Decisions — Night Ramen Breakdown

**Date**: 2026-06-14 | **Branch**: `001-night-ramen-breakdown`

技術選定の決定・根拠・却下した代替案を記録する。すべて spec.md の FR/SC と Constitution に紐づく。

## D1. LLM 基盤とモデル選択

- **決定**: Amazon Bedrock を唯一の LLM 経路とする。モデルは設定値で切替：
  - 初期開発: `anthropic.claude-haiku`（最安、全用途統一）
  - 本番: 分析=`claude-opus-4-8`、メッセージ生成=`claude-sonnet-4-6`
- **根拠**: 原則 V（コスト規律）と spec の指定。Bedrock 経由なら IAM とログ統制が一元化（原則 III）。
- **却下案**: 外部 LLM API 直接呼び出し → IAM/監査が分散し原則 III に反する。

## D2. 長期記憶 (Memory)

- **決定**: agentcore Memory を一次ストアとし、デイリーバイタル・説得履歴・現行プロンプト戦略・分析結果を保持。
  目標設定・冪等キー・通知済みフラグなど構造化メタは DynamoDB 単一テーブルに置く。
- **根拠**: spec が「agentcore の長期記憶へ直接蓄積」を要求（FR-002）。一方、冪等制御や目標の単純な
  キー値は決定的に読みたいため DynamoDB が適する。
- **却下案**: 全データ DynamoDB のみ → agentcore のセマンティック想起を活かせず FR-010 学習が弱くなる。

## D3. 自律ループ制御

- **決定**: strands でワークフローを2本定義：朝（収集→記憶）、夜（分析→目標評価→生成→検証→送信→学習）。
- **根拠**: spec 指定（§4,§5）。strands はエージェント連携とステップ追跡（FR-013）に適合。
- **却下案**: Step Functions のみ → LLM エージェント連携の記述が冗長。strands を主、必要なら EventBridge/Lambda で外殻。

## D4. スケジューリング

- **決定**: EventBridge Scheduler を2本。朝の収集（例: 08:00 JST）、夜の通知（23:00 JST、cron は UTC 14:00）。
- **根拠**: FR-001/FR-007。サーバーレスでクーロン的発火に最適。
- **注意**: タイムゾーン。JST 23:00 = UTC 14:00。EventBridge Scheduler は `timezone` 指定可のため JST 直指定。

## D5. ガードレール（NON-NEGOTIABLE）

- **決定**: 送信経路の単一ゲート `guardrail/validator.py` を設置。検証は2層：
  1. **決定的チェック**: 禁止語・人格否定パターン・長さ/書式の機械検査。
  2. **agentcore ガードレール / Bedrock Guardrails**: 差別・有害・逸脱を LLM/ポリシで検査。
  - fail-closed：判定不能や例外時は「不合格」とみなす。最大2回再生成（FR-009）、超過で固定安全文面。
- **根拠**: 原則 II・FR-008/009。安全側に倒す設計。
- **テスト**: `tests/unit/test_guardrail.py` で (a)差別表現 (b)目標逸脱 (c)正論の暴力 の各陽性/陰性ケース必須。
- **却下案**: 生成プロンプトに「優しくして」と書くだけ → 検証なしは原則 II 違反。

## D6. 通知 UI

- **決定**: LINE Messaging API（push message）。1晩1回の冪等は DynamoDB の `notified#<date>` キーで担保（FR-014）。
- **根拠**: spec 指定。push 型で 23:00 に能動送信可能。
- **却下案**: メール/SNS → 即時性・開封率で LINE に劣る。

## D7. Google Health データ取得

- **決定**: OAuth 2.0 Authorization Code（Restricted スコープ）。リフレッシュトークンを Secrets Manager に格納し、
  Lambda が都度アクセストークンを更新。テストユーザー同意は開発コンソールで明示管理。
- **根拠**: FR-001/FR-011/原則 III。トークンはコード/ログに出さない。
- **欠損・異常値**: 取得できた指標のみ保存、欠損は欠損として記録（FR-003 シナリオ3）。異常値は除外/平滑化（FR-015）。
- **却下案**: 端末ローカル同期 → サーバーレス自動化に不適。

## D8. シークレット管理 / IAM

- **決定**: AWS Secrets Manager に Google リフレッシュトークン・LINE チャネルアクセストークンを格納。
  Lambda 実行ロールは関数ごとに最小権限（朝関数は Health/Memory/Secrets read、夜関数は Bedrock/Memory/LINE/DynamoDB）。
- **根拠**: 原則 III。
- **却下案**: 環境変数に平文トークン → 漏洩リスク、原則 III 違反。

## D9. IaC

- **決定**: AWS CDK (Python) 単一スタック `NightRamenStack`。Lambda×2・EventBridge×2・DynamoDB・Secrets 参照・IAM。
- **根拠**: 原則 IV。`cdk diff` でレビュー可能。
- **却下案**: 手動コンソール / SAM → 原則 IV か、CDK 統一方針に劣る。

## D10. 観測可能性

- **決定**: 構造化 JSON ログ（CloudWatch）。相関ID（実行ごとUUID）で朝/夜ループの各ステップを追跡（FR-013）。
  PII/トークンはマスキングして出力（原則 III）。
- **却下案**: print デバッグ → 追跡・マスキング不可。

## FR → 実装トレーサビリティ

| FR | 実装モジュール |
|----|----------------|
| FR-001,003 | `collectors/google_health.py` |
| FR-002 | `collectors/*` + `memory/client.py` |
| FR-004 | `agents/analyzer.py` |
| FR-005,006,017 | `goals/evaluator.py` |
| FR-007,014 | `notifier/line.py` + `workflow/night.py` |
| FR-008,009,015 | `guardrail/validator.py`, `guardrail/fallback.py` |
| FR-010 | `agents/persuader.py` + `memory/client.py`（戦略更新） |
| FR-011 | `observability.py`（マスキング）+ Secrets Manager |
| FR-012 | `collectors/google_health.py`（同意失効時の停止） |
| FR-013 | `workflow/*` + `observability.py`（相関ID） |
| FR-016 | DynamoDB TTL / Memory 保持ポリシ（1年） |

## 未解決事項

- Google Health API の正式な製品名/エンドポイント仕様は実装着手時に最新ドキュメントで確認（AWS とは独立）。
- Bedrock のモデルアクセス有効化（Opus 4.8 / Sonnet 4.6 / Haiku）はアカウント側の手動承認が必要。
