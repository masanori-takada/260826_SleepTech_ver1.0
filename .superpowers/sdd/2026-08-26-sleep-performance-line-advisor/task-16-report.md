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
- Webhook Function URLは仕様どおりAuthType=NONE。LINE署名検証はver1 scope外。
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
