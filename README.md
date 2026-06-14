# Night Ramen Breakdown

深夜のラーメン断ち自律改善ループシステム。Google Health のバイタルデータを分析し、ユーザーに
最も効果的な説得メッセージを 23:00 に LINE へ送り、結果を学習して説得アプローチを自律改善する。

Spec Kit による仕様駆動開発（SDD）で構築。仕様・計画・タスクは `specs/001-night-ramen-breakdown/` 参照。

## アーキテクチャ

| 層 | 技術 |
|----|------|
| インフラ | AWS CDK (Python) — Lambda×2, EventBridge Scheduler×2, DynamoDB, Secrets Manager |
| エージェント | strands（ループ制御）, agentcore（長期記憶・ガードレール）|
| LLM | Amazon Bedrock（初期=Haiku / 本番=分析Opus 4.8・生成Sonnet 4.6）|
| 通知 | LINE Messaging API |
| データ源 | Google Health API（OAuth 2.0 Restricted）|

## ディレクトリ

```
src/night_ramen/   ドメインロジック（collectors/memory/agents/goals/guardrail/notifier/workflow）
src/handlers/      Lambda エントリ（morning/night）
infra/             AWS CDK スタック
specs/             SDD 成果物（spec/plan/research/data-model/contracts/tasks/analysis）
tests/             pytest（unit/integration）
```

## セットアップ

```bash
# 依存
uv pip install -r requirements.txt   # or pip install -r requirements.txt

# テスト（ガードレール検証は必須・最優先）
python -m pytest tests/

# 夜ループをダミーデータで確認（Bedrock/LINE はフェイク注入のテスト参照）
python -m pytest tests/integration/test_night_flow.py -v
```

## デプロイ（前提: AWS 認証・Bedrock モデル有効化・Secrets 登録）

```bash
# シークレット登録
aws secretsmanager create-secret --name night-ramen/google-oauth --secret-string '{"client_id":"...","client_secret":"...","refresh_token":"..."}'
aws secretsmanager create-secret --name night-ramen/line --secret-string '{"channel_access_token":"...","user_id":"..."}'

# 目標設定（/goal 相当）
python -m night_ramen.goals.cli --success-rate 85% --weight-loss 2kg --save

# インフラ
cd infra
cdk bootstrap        # 初回のみ
cdk diff             # 変更レビュー（手動コンソール変更は禁止 / 原則IV）
cdk deploy
```

## 設計原則（Constitution）

1. 仕様駆動開発  2. **安全・ガードレール最優先（NON-NEGOTIABLE）**  3. プライバシー・最小権限
4. Infrastructure as Code  5. 観測可能性・コスト規律

詳細は `.specify/memory/constitution.md`。
