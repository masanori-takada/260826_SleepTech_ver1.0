# Task 4 実装レポート

## RED

本番コードを変更する前に、reply endpoint、前日のMORNING予測への紐付け、同日NIGHT予測の拒否、成功返信、未紐付け返信、reply失敗後の保存維持をテストへ追加した。

最初の対象テストは次の理由で失敗した。

- `LinePushClient` に `reply` がなく `AttributeError`。
- `webhook_handler` に `resolve_feedback_target` がなく `AttributeError`。
- Webhook返信テストでは、取得済みsecretからreplyクライアントを構築する `_push_from_secret` がなく失敗。

いずれも未実装機能に対応した期待どおりのREDである。

## GREEN

- `LinePushClient.reply()`をLINE reply endpointと`replyToken`・1件のmessageのbodyで実装。
- `FeedbackTarget`と`resolve_feedback_target()`を追加し、当日・前日の配信済みNIGHTメッセージ、前日起点のMORNING予測、未保存評価だけを候補化。
- Webhookイベントごとに本文と同じイベントのreplyTokenを保持。
- `Feedback`へ`evaluated_date`と`prediction_date`を保存。
- 保存後に確認返信を行い、返信失敗時も`feedback_reply_failed`を記録してHTTP 200を返す。
- LINE secretは既存の取得結果をreplyクライアント構築へ再利用。

対象テストはGREENとなった。

## 検証結果

- `python -m pytest tests/unit/test_line.py tests/integration/test_webhook.py tests/unit/test_handlers.py -q`：118 passed
- `python -m pytest -q`：513 passed
- `python -m compileall -q src infra`：成功
- `git diff --check`：成功

## 変更ファイル

- `src/sleeptech/notifier/line.py`
- `src/handlers/webhook_handler.py`
- `src/handlers/deps.py`
- `tests/unit/test_line.py`
- `tests/integration/test_webhook.py`
- `tests/unit/test_handlers.py`

## 残課題

- 実機LINE、DynamoDB、CloudWatchでの本番相当の一連の確認はTask 5の範囲。
- LINE Official Account Managerの汎用自動応答無効化は運用設定作業として未実施。

## Fix Round 1/5: 再送冪等性

### RED

同一batchの同一`webhookEventId`、同じ署名済みイベントのhandler再送、欠損IDの安全方針、Feedbackキーの原子的保存をテストに追加した。修正前は次の失敗を確認した。

- `Feedback`に`source_event_id`がなく、イベントIDを保存できない。
- `MemoryClient.save_feedback_once()`と処理済みイベント判定がなく、原子的claimができない。
- 同一batchの2件目が当日候補から前日候補へフォールスルーし、前日Feedbackを誤保存する。
- 同じイベントのhandler再送で、8/26の候補へ誤保存・重複返信する。
- `webhookEventId`なしでも従来処理され、再送保護ができない。

### GREEN

- `Feedback.source_event_id`を追加し、旧Feedbackの読み取りを維持。
- InMemoryBackendではロック内、DynamoDBでは`TransactWriteItems`と`attribute_not_exists`条件で、Feedbackとイベントclaimを原子的に保存。
- 正規イベントIDの処理済み判定を候補解決より前に行い、duplicateはHTTP 200・無返信・別日フォールスルーなし。
- reply失敗後もイベントclaimを残し、同じイベントの再送は再返信しない方針とした。
- `webhookEventId`欠損時は既存fixture互換のためreplyTokenを限定的なlegacy IDとして利用し、replyTokenもない場合または明示的に不正なIDの場合は保存・返信せずHTTP 200とする。

### Fix Round 1 検証

- 対象テスト：`159 passed`
- 全pytest：`519 passed`
- `python -m compileall -q src infra`：成功
- `git diff --check`：成功

### Fix Round 1 変更ファイル

- `src/sleeptech/memory/schemas.py`
- `src/sleeptech/memory/client.py`
- `src/sleeptech/memory/dynamo_backend.py`
- `src/handlers/webhook_handler.py`
- `tests/unit/test_schemas.py`
- `tests/unit/test_memory_client.py`
- `tests/integration/test_webhook.py`
- `tests/unit/test_handlers.py`

### Fix Round 1 残課題

- DynamoDB実環境でのトランザクション競合・再送検証は未実施。
- LINE実機での`webhookEventId`欠損/再送挙動確認は未実施。

## Fix Round 2/5: Transaction/IAMと未紐付けclaim

### RED

次の失敗テストを先に追加し、修正前に確認した。

- Webhook role policyに`dynamodb:TransactWriteItems`が存在しない。
- 未紐付けイベントclaim APIがなく、同一batchで未紐付け返信を重複送信する。
- `TransactionCanceledException`を一時障害でもFalseへ変換してしまい、再送可能な例外を隠す。

### GREEN

- `resource.meta.client`の実オブジェクトを`Stubber`で呼び出し、native Python型のItemが自動marshalされることを検証した。TypeSerializerによる二重marshalは行っていない。
- `DynamoBackend.put_feedback_once()`は取消後にFeedbackキーとイベントmarkerを強整合readし、実在時だけFalse、どちらも無い一時取消は`TransactionCanceledException`を再送する。
- Webhook roleへ対象テーブル限定の`dynamodb:TransactWriteItems`を明示付与。
- 未紐付け評価もイベントmarkerを原子的にclaimしてからreplyし、同一batch・再送はduplicate無返信とした。markerは`IDEM#FEEDBACK`で補正履歴に含めない。

### Fix Round 2 検証

- 対象/CDK関連テスト：`196 passed`（追加後の条件競合テストを含む）
- `cdk synth --strict`：成功
- 全pytest：`525 passed`
- `python -m compileall -q src infra`：`infra/cdk.out`生成asset内のpyc競合で失敗。`cdk.out`を除外した`python -m compileall -q -x 'cdk\\.out' src infra`と`python -m compileall -q src infra/stacks`は成功
- `git diff --check`：成功

### Fix Round 2 変更ファイル

- `infra/stacks/sleeptech_stack.py`
- `src/sleeptech/memory/client.py`
- `src/sleeptech/memory/dynamo_backend.py`
- `src/handlers/webhook_handler.py`
- `tests/unit/test_infra_synth.py`
- `tests/unit/test_memory_client.py`
- `tests/unit/test_dynamo_backend_moto.py`
- `tests/unit/test_handlers.py`

### Fix Round 2 残課題

- 実AWSでのIAM拒否・TransactionCanceledExceptionの実障害再現は未実施。
- LINE実機での未紐付けイベント再送とreply失敗後の無返信確認は未実施。
- `infra/cdk.out`はCDK synthが生成するgitignore対象で、削除は実行ポリシーにより行わず、compileallから除外して検証した。

## Fix Round 3/5: Lambda assetのcompiled artifact除外

### RED

CDKの実stagingを検査するテストを追加した。修正前は`ASSET_EXCLUDES`にcompiled artifact用globがなく、staged assetに複数階層の`__pycache__`が残り、テストが失敗した。`**/__pycache__/**`形式もCDK asset stagingでは機能しないことを実測した。

### GREEN

- `ASSET_EXCLUDES`へ`__pycache__`、`*.pyc`、`*.pyo`、`*.pyd`を追加。
- CDK basename globの階層横断動作をテストで実証。
- fresh `cdk synth --strict`後の`infra/cdk.out`を検査し、compiled files `0`件、`__pycache__` `0`ディレクトリを確認。
- 生成済み`infra/cdk.out`を安全に再生成・クリーン化した後、通常の`python -m compileall -q src infra`が成功。

### Fix Round 3 検証

- RED：compiled artifactテスト `2 failed`
- GREEN対象テスト：`2 passed`
- 全pytest：`527 passed`
- CDK synth関連テスト：`26 passed`
- `cdk synth --strict`：成功
- `python -m compileall -q src infra`：成功（synth生成物をクリーン化後）
- `git diff --check`：成功

### Fix Round 3 変更ファイル

- `infra/stacks/sleeptech_stack.py`
- `tests/unit/test_infra_synth.py`
- 本レポート

### Fix Round 3 残課題

- 実AWSへのデプロイ後asset内容確認は未実施。

## Fix Round 4/5: CDK出力先をinfraツリー外へ固定

### RED

- `cdk.json`に出力設定がなく、出力先を検証するテストが`KeyError: output`で失敗した。
- 修正前の設定では、`infra`で`synth --strict`した後にリポジトリルートから通常の`python -m compileall -q src infra`を実行する実順序を安全に成立させられなかった。

### GREEN

- `infra/cdk.json`の`output`を`../cdk.out`へ固定した。
- 実コマンド`cd infra; cdk synth --strict`で`infra/cdk.out`が生成されず、リポジトリルートの`cdk.out`へmanifest/assetが生成されることを確認した。
- synth直後にリポジトリルートから通常の`python -m compileall -q src infra`を実行し、終了コード0を確認した。
- 新出力先のasset内に`__pycache__`、`*.pyc`、`*.pyo`、`*.pyd`が0件であることを確認した。
- `cdk.json`の相対output設定をテストで検査し、Windowsでは`cdk.cmd`を含む実在コマンドを選択して実コマンド順序を再現した。

### Fix Round 4 検証

- RED対象テスト：`2 failed`
- GREEN対象テスト：`2 passed`
- 全pytest：`529 passed`
- `cdk synth --strict`：成功
- synth直後の`python -m compileall -q src infra`：成功
- 新出力先assetのcompiled artifact：`0`件、`__pycache__`：`0`ディレクトリ
- `git diff --check`：成功

### Fix Round 4 変更ファイル

- `infra/cdk.json`
- `tests/unit/test_infra_synth.py`
- 本レポート

### Fix Round 4 残課題

- 実AWSへの`cdk deploy`/`cdk diff`実行、およびデプロイ後assetの確認は未実施。synthとasset検査では出力先変更後の成果物生成を確認済み。
