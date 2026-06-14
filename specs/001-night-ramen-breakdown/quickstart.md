# Quickstart — Night Ramen Breakdown

**Date**: 2026-06-14 | **Branch**: `001-night-ramen-breakdown`

ローカル開発からデプロイ、ループの手動実行までの手順。

## 前提（インストール済み）

- Python 3.13 / uv / Node.js / AWS CLI 2.35 / AWS CDK 2.1126 / agentcore CLI
- Python ライブラリ: aws-cdk-lib, strands-agents, bedrock-agentcore, line-bot-sdk, google-auth-oauthlib, boto3

## 1. 認証・前提セットアップ

```bash
# AWS 認証（新規ターミナルで PATH 反映後）
aws configure                      # or aws sso login

# Bedrock モデルアクセス有効化（コンソール）: Claude Haiku / Opus 4.8 / Sonnet 4.6
# Secrets Manager に資格情報を登録
aws secretsmanager create-secret --name night-ramen/google-oauth --secret-string '{...}'
aws secretsmanager create-secret --name night-ramen/line --secret-string '{...}'
```

## 2. ローカル開発

```bash
# 依存（プロジェクト venv を使う場合）
uv venv && uv pip install -r requirements.txt

# ユニットテスト（ガードレールは必須・最優先で緑に）
pytest tests/unit/test_guardrail.py -v
pytest tests/unit -v

# 夜ループをダミーデータで手動実行（US1 Independent Test）
python -m src.night_ramen.workflow.night --date 2026-06-14 --dry-run
```

## 3. デプロイ（CDK / 原則IV）

```bash
cd infra
cdk bootstrap          # 初回のみ
cdk diff               # 変更レビュー（手動コンソール変更は禁止）
cdk deploy
```

デプロイされるもの: Lambda×2（朝/夜）, EventBridge Scheduler×2（08:00/23:00 JST）, DynamoDB, IAM 最小権限。

## 4. 動作確認

```bash
# 朝ルーティンを手動起動
aws lambda invoke --function-name NightRamen-Morning out.json

# 夜ルーティン（実送信前に dry-run 推奨）
aws lambda invoke --function-name NightRamen-Night --payload '{"dry_run":true}' out.json

# ログ確認（相関IDで各ステップ追跡）
aws logs tail /aws/lambda/NightRamen-Night --follow
```

## 5. 検証チェックリスト（Done の目安）

- [ ] `pytest tests/unit/test_guardrail.py` が緑（原則II 品質ゲート）
- [ ] ガードレール不通過メッセージが送信されない（SC-003）
- [ ] ダミー実行で LINE に説得メッセージが届く（US1）
- [ ] ログ・成果物にトークン/PII が平文で出ていない（SC-006）
- [ ] `cdk diff` で全インフラ変更が確認できる（原則IV）

## トラブルシュート

- `aws: command not found` → ターミナル再起動で PATH 反映、または `C:\Program Files\Amazon\AWSCLIV2\aws.exe` をフルパス指定。
- Bedrock AccessDenied → モデルアクセス未承認。コンソールで対象モデルを有効化。
- LINE 401 → channel access token 失効。Secrets Manager を更新。
