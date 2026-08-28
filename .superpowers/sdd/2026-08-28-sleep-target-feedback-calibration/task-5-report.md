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

## Fix Round 1/5 引継ぎ対応

### 追加修正

- 物理Lambda名の確認を、4論理ID（`NightFn` / `MorningFn` / `WebhookFn` /
  `BackfillFn`）ごとの `cloudformation describe-stack-resource` に統一した。
- 移行用JSONの生成をBOMなしUTF-8の `Write-Utf8Json` に統一し、Windows PowerShellの
  `Set-Content -Encoding UTF8` によるAWS CLI JSON読込不整合を避けた。
- BatchGet応答の対象テーブル取得を、Windows PowerShellで動作する
  `PSObject.Properties` 列挙へ変更した。対象テーブル欠落は停止する。
- 旧計画書に残っていたSecret値取得・作成CLIとLambda環境変数全置換例を、AWS Console/
  `asm-exec` またはCDK経由の手順へ置換した。秘密値取得CLIは禁止し、実行していない。
- 上記の移行手順・禁止操作・PowerShell JSON形式を検査するinfra契約テストを追加した。

### RED/GREEN記録

1. `test_deploy_migration_uses_safe_cli_and_powershell_json_patterns` を追加し、現行文書の
   `describe-stack-resource` 不在でREDを確認した。
2. 文書とテストを修正し、同テストが `1 passed` となるGREENを確認した。

### Fix Round 1 fresh検証

- 全pytest: `532 passed in 72.66s`
- compileall: `python -m compileall -q src infra` — 成功
- strict synth: `cd infra; cdk synth --strict` — 成功
- read-only CDK diff: `cd infra; cdk diff SleepTechStack --fail false` — 成功。削除・
  置換なし。差分はLambdaコード、全ランタイムへの `STC_MIN_TARGET_SLEEP_H`追加、
  Webhookの `dynamodb:TransactWriteItems` 権限追加のみ。
- PowerShell検証: `docs/deploy.md` の6ブロックをParserで構文解析 — 成功
- `git diff --check` — 成功

### 変更件数と残課題

- 変更ファイル: 6（`docs/deploy.md`、旧計画書、朝ワークフロー、朝統合テスト、
  infraテスト、移動済み報告書）
- テスト追加: 1件。既存の朝ログ監査テストを含む全532件が成功。
- AWSデプロイ、DynamoDBの既存Feedback更新、LINE管理画面操作、実メッセージ送信、
  本番Scheduler/CloudWatch確認、Sol最終レビューは未実施。これらは運用担当者／別工程で
  実施する。

## Fix Round 2/5 対応

### 修正内容

- 物理Lambda名の解決を `list-stack-resources` のLambdaリソース一覧から行う方式へ変更した。
  `ResourceType` をJMESPathでLambdaに限定し、PowerShellで論理IDの
  `NightFn*` / `MorningFn*` / `WebhookFn*` / `BackfillFn*` prefixを照合する。
  各prefixが0件、複数件、または `PhysicalResourceId` が空の場合は停止する。
- 解決した `$NightFn`、`$MorningFn` を後続のPowerShell `aws lambda invoke` へ渡すようにし、
  固定hashや物理名プレースホルダーを廃止した。
- LINE導線を「LINE Developersコンソール → Messaging API設定 → Auto-reply messages
  （応答メッセージ）のEdit（編集） → LINE Official Account ManagerのResponse settings
  （応答設定）」と明示し、Official Account Manager側にEdit導線がある記述を削除した。
- 上記条件と導線をinfra契約テストへ追加した。

### RED/GREEN・構文検証

- 追加契約アサーションは、旧 `describe-stack-resource` 手順と旧LINE導線の状態でREDを確認。
- 文書修正後、対象テストは `1 passed` でGREEN。
- AWS CLIスケルトンでJMESPath式を検証し、終了コード0を確認。
- `docs/deploy.md` のPowerShell 8ブロックをPowerShell ASTで解析し、成功。

### Fix Round 2 fresh検証

- 全pytest: `532 passed in 68.50s`
- compileall: `python -m compileall -q src infra` — 成功
- strict synth: `cd infra; cdk synth --strict` — 成功
- read-only CDK diff: `cd infra; cdk diff SleepTechStack --fail false` — 成功。削除・
  置換なし。今回の変更によるCDKリソース差分なし。
- `git diff --check` — 成功

### 件数・残課題

- 変更ファイル: 3（`docs/deploy.md`、`tests/unit/test_infra_synth.py`、本報告書）
- 追加契約テストアサーション: 8項目
- AWSデプロイ、DynamoDB本番更新、LINE管理画面操作、実機確認、Sol最終レビューは未実施。

## Fix Round 4/5 対応

### 修正内容

- LINE契約テストのLINE設定／実機確認範囲を、`## 10`から`## 11`までに切り出した。
- SleepTechの確認返信1回と汎用自動応答0件が同じ節内で結び付くことを、範囲付きregexで検証する
  ようにした。装飾や句読点の差には過剰依存しない。

### RED/GREEN・検証

- 文書の「SleepTech返信 1回だけ」を一時的に「2回だけ」へmutationし、契約テストがREDに
  なることを確認した。
- 文書を復元し、対象契約テストはGREEN（`1 passed`）。
- docs検査: LINE設定／実機確認節内のregex結び付きと旧導線不在を確認。
- 全pytest: `532 passed in 104.13s`
- compileall: `python -m compileall -q src infra` — 成功
- strict synth: `cd infra; cdk synth --strict` — 成功
- `git diff --check` — 成功

### 件数・残課題

- 変更ファイル: 2（`tests/unit/test_infra_synth.py`、本報告書）。これは実diff
  `c916b80..af4564e` に対応し、`docs/deploy.md`はmutation後に完全復元されて最終diffなし。
- 追加検証: LINE節regex 1件、mutation RED 1件
- AWSデプロイ、DynamoDB本番更新、LINE管理画面操作、実機確認、Sol最終レビューは未実施。

## Fix Round 3/5 対応

### 修正内容

- LINE Official Account ManagerのResponse settingsで**ChatがONの場合**に、Chatの応答方法を
  確認する手順を追加した。
- 自動文・自動返信の設定なら、手動応答または自動返信を無効化する設定へ変更する具体手順を
  追加した。
- 画面文言がバージョンやアカウント種別で異なる場合も、「ユーザー送信へ汎用返信しない」
  ことを設定名だけでなくテスト送信で判定する安全条件を明記した。
- 設定後の実機確認を必須化し、SleepTech返信1回、汎用自動応答0件を必須結果として記載した。
- 上記のChat条件・導線・実機確認をinfra契約テストで検査するアサーションを追加した。

### RED/GREEN・検証

- Chat条件の契約アサーション追加後、必須文言不足でREDを確認。
- 文書修正後、対象契約テストは `1 passed` でGREEN。
- docs検査: 必須文言、旧導線不在、SleepTech 1回／汎用自動応答0件を確認。
- 全pytest: `532 passed in 99.04s`
- compileall: `python -m compileall -q src infra` — 成功
- strict synth: `cd infra; cdk synth --strict` — 成功
- `git diff --check` — 成功

### 件数・残課題

- 変更ファイル: 3（`docs/deploy.md`、`tests/unit/test_infra_synth.py`、本報告書）
- 追加契約アサーション: 4項目
- AWSデプロイ、DynamoDB本番更新、LINE管理画面操作、実機確認、Sol最終レビューは未実施。
