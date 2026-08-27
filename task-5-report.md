# Task 5 実施報告

## 対象

- 作業ツリー: `sleep-target-feedback-calibration`
- 対象: `docs/deploy.md`、`tests/unit/test_infra_synth.py`
- コミットメッセージ: `docs: 評価補正の移行とLINE応答設定を追加`
- コミット SHA: このファイルを含む最終コミットで `git rev-parse HEAD` を確認する

## 変更内容

- LINE Official Account Manager の応答メッセージ OFF、Chat 自動応答無効、Webhook ON の手順を追加。
- 評価後に SleepTech の保存確認が1回だけ返り、汎用自動応答が0件である確認項目を追加。
- 2026-08-26 → `prediction_date=2026-08-25`、2026-08-27 → `prediction_date=2026-08-26` の既存 Feedback 2件を、削除なし・件数維持・旧 JSON 完全一致・対応朝予測存在確認・条件付き更新で補完する手順を追加。
- `IDEM#FEEDBACK` を評価履歴件数へ含めない監査条件を追加。
- ロールバック時に Lambda/CDK だけを前版へ戻し、旧フィールドを無視して読める DynamoDB レコードを削除・巻き戻ししない方針を追加。
- デプロイ前後の `UPDATE_COMPLETE`、Scheduler 2本の `ENABLED`、Night/Morning の表示、DynamoDB 監査値、CloudWatch、LINE reply の確認項目を追加。
- 秘密値を取得・表示・ログしない注意を明記し、既存 Secret の値を表示する手順を削除。
- バックフィルを除く全ランタイム Lambda の `STC_MIN_TARGET_SLEEP_H=7.0` を検証する infra test を追加。

## TDD 記録

1. Task 5 の infra 契約テストを先に追加。
2. Task 1 で共通 Lambda 環境が既に実装済みだったため、期待した RED ではなく既存実装による GREEN を確認。
3. 対象 infra test: `31 passed in 53.45s`。

## 検証結果

- 必須語確認: `rg -n "応答メッセージ|STC_MIN_TARGET_SLEEP_H|prediction_date|ロールバック" docs/deploy.md` — ヒット確認。
- 全 pytest: `530 passed in 70.26s`。
- 通常 compileall: `python -m compileall -q src infra` — 成功。
- strict synth: `cd infra; cdk synth --strict` — 成功。
- diff check: `git diff --check` — 成功。
- CDK diff: `cd infra; cdk diff SleepTechStack --fail false` — 成功。削除・置換なし。差分は Lambda コード／環境変数更新と、Webhook の `dynamodb:TransactWriteItems` 権限追加。

## 未実施・残課題

- AWS デプロイ、DynamoDB の既存 Feedback 更新、LINE 管理画面操作、実メッセージ送信は実施していない。
- 本番の `UPDATE_COMPLETE`、Scheduler 状態、DynamoDB 件数・監査値、CloudWatch、LINE 応答は、レビュー後に運用担当者が実施する。
- Sol 最終レビューは別工程で実施する。
