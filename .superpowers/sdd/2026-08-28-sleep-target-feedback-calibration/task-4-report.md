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
