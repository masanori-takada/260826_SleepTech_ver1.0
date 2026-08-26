# Task 16 実装レポート

## Status

完了。AWS CDK Pythonの`SleepTechStack`を実装した。deployは実行していない。

## 実装内容

- Lambdaを3個作成（Python 3.12、handlerは`src.handlers.*`、`PYTHONPATH=/var/task/src`）。
- Lambdaコードはリポジトリルートをasset rootとして配布し、不要なテスト・docs等を除外。
- DynamoDB `SleepTechTable`を作成（`pk`/`sk`小文字、TTL=`ttl`、PITR有効、PAY_PER_REQUEST、保持ポリシー）。
- EventBridge Schedulerを2個作成（JST、Night 22:30、Morning 08:00、FlexibleTimeWindow=OFF）。
- Night Target.Inputを`{"scheduled_time":"<aws.scheduler.scheduled-time>"}`に設定。MorningはInputなし。
- Secrets Managerの既存シークレット3件を名前参照し、各LambdaへGetSecretValue権限を付与。値のコード・環境変数直書きはしていない。
- Webhook Function URLは仕様どおりAuthType=NONE。LINE署名検証はTask 16のスコープ外。Task 17の公開前必須ゲート。
- 旧`NightRamenStack` importを`SleepTechStack`へ置換。
- `requirements.txt`からstrands/agentcore等の旧依存を整理し、boto3/CDK/pytest/motoを維持。CDK用`infra/requirements.txt`も追加。

## TDD

- RED: `python -m pytest tests/unit/test_infra_synth.py -v` → `ModuleNotFoundError: No module named 'infra.stacks.sleeptech_stack'`。
- GREEN: CDK構成テスト12件が成功。

## Tests

- `python -m pytest tests/unit/test_infra_synth.py -v` → `12 passed`
- `python -m pytest -q` → `297 passed`

## Synth

- `cd infra && npx cdk synth SleepTechStack` → exit 0（認証不要範囲）。
- 情報警告としてCDK feature flags未設定が表示されたが、synthは成功した。

## Commit

Task 16実装・テスト・本レポートをまとめてコミットする。

## Concerns

- `LLM_MODEL=gpt-5.6-luna`は暫定値で、API実機値はTask 17で確認する。
- Secretの実値登録、CDK deploy、Function URLのLINE設定はTask 17の対象。
- 既存の未追跡`.agents/`と`AGENTS.md`は本タスクの変更対象外として保持した。

## レビュー修正ラウンド1

### 修正内容

- `REPO_ROOT`を`infra/stacks`から2階層上のリポジトリルートへ修正。CDKの実stagingアセット直下に`src/handlers/`と3 handlerが存在するテストを追加。
- Secret Manager権限をLambda単位で最小化。
  - NightFn: LINE、OpenAI
  - MorningFn: Google、LINE、OpenAI
  - WebhookFn: Google、LINE、OpenAI
- 3 Lambdaと2 Schedulerを個別に検査し、DynamoDBのDeletionPolicy/UpdateReplacePolicy=Retainも検査。
- 依存を実測値へ厳密固定。
  - `aws-cdk-lib==2.259.0`
  - `constructs==10.6.0`
  - CDK CLI `2.1126.0 (build a90d578)`
- Webhook Function URLのNONEは維持。LINE署名検証はTask16では実装しない。

### TDD・検証

- RED: Secret権限テストでNightFnのGoogle権限を検出し、アセットrootテストでworktree親を検出。
- RED: 実stagingアセットテスト追加時に未定義`ASSET_EXCLUDES`を検出。
- GREEN: `python -m pytest tests/unit/test_infra_synth.py -v` → `20 passed`。
- 全suite: `python -m pytest -q` → `305 passed`。
- Synth: `cd infra && npx cdk synth SleepTechStack` → exit 0。実アセット直下に`src/`と3 handlerを確認。

### セキュリティゲート

LINE署名検証は公開前必須のTask17セキュリティゲート。未実装のままdeploy不可。

## レビュー修正ラウンド2

### 修正内容

- Lambda assetの開発管理物を追加除外。`.agents/`、`.claude/`、`.specify/`、`.superpowers/`、`AGENTS.md`、`CLAUDE.md`、`infra/`を除外し、`src/`と3 handlerは維持。
- CDK `AssetStaging`を使い、合成アセット直下に`src/handlers/`の3 handlerが存在し、開発管理物が存在しないことをテスト。
- Task17計画を更新。`webhook_handler.py`と署名検証テストを変更対象に追加し、raw body、HMAC-SHA256、constant-time compare、欠落401、不一致403、成功ケースのテストをdeploy前ゲートとして明記。
- `sleeptech/line` Secret登録例へ`channel_secret`を追加。
- Task17の完了条件へ署名欠落・不一致テストを追加し、Task 16のスコープ外・Task 17の公開前必須ゲートとして記録。
- 署名検証コード自体はTask16では実装していない。Task17で実装・5.6-solレビューを行う。

### TDD・検証

- RED: 開発管理物の除外テストが`.agents/`のasset残存で失敗。
- GREEN: `python -m pytest tests/unit/test_infra_synth.py -q` → `20 passed`。
- 全suite: `python -m pytest -q` → `305 passed`。
- Synth: `cd infra && npx cdk synth SleepTechStack` → exit 0。合成アセット直下の`src/`と3 handlerを確認。

### 更新後の公開前ゲート

Task17は、raw bodyに対するLINE署名検証（HMAC-SHA256、constant-time compare）を実装し、署名欠落401・不一致403・正しい署名のテストが成功した後にのみCDK deployを許可する。未実装のままdeploy不可。
