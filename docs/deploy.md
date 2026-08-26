# SleepTech ver1.0 デプロイ手順書

この1枚で本番デプロイと実機確認を完走できることを目標にしている。
すべてのコマンド・キー名・ペイロード形式は実際のソースコードから読み取った値であり、
推測は含まない（参照元ファイルを各節に明記する）。

対象スタック: `SleepTechStack`（`infra/app.py`）
Lambda論理名: `NightFn` / `MorningFn` / `WebhookFn`（`infra/stacks/sleeptech_stack.py`）

---

## 1. 前提

事前に用意するもの:

- **LINE チャネル**（Messaging API）
  1. [LINE Developers](https://developers.line.biz/) で Provider / Messaging API チャネルを作成する
  2. チャネルアクセストークン（長期）を発行する
  3. チャネルシークレットを控える
  4. 通知先となる自分の LINE ユーザーID（`userId`）を控える（LINE公式アカウントとの1:1トークで Webhook イベントを一度発生させると `source.userId` として確認できる。または LINE Developers の「あなたのユーザーID」参照）
  5. 応答設定で「応答メッセージ」をオフ、「Webhookの利用」は後述の手順4でオンにする
- **OpenAI API キー**（文章生成用）
- **AWS 認証情報**（デプロイ先アカウントへの `aws configure` 済みプロファイル、または SSO）
- **Google OAuth のクライアント情報とリフレッシュトークン**（`client_id` / `client_secret` / `refresh_token`）。既存の `night-ramen/google-oauth` シークレットと同じ Google アカウント・スコープ（`health.sleep.readonly` を含む）を使う場合は、そこから値をコピーできる（手順2参照）

このリポジトリから **`aws` コマンドを実行しないこと**（このタスクでは認証情報が無い）。以下のコマンドはすべて利用者自身の端末・権限で実行する。

---

## 2. Secrets 登録

Secrets の名前と JSON キーは `src/sleeptech/config.py`（既定値）と `src/handlers/deps.py`（読み取り箇所）から読み取ったもの。

| Secret 名（既定） | 環境変数で上書き | 読み取り箇所 | JSON キー |
|---|---|---|---|
| `sleeptech/google-oauth` | `STC_SECRET_GOOGLE` | `deps._health_source` → `google_auth_helper.get_access_token` | `client_id`, `client_secret`, `refresh_token` |
| `sleeptech/line` | `STC_SECRET_LINE` | `deps._push`（push用）/ `webhook_handler.handler`（署名検証・送信者認可用） | `channel_access_token`, `user_id`, `channel_secret` |
| `sleeptech/openai` | `STC_SECRET_OPENAI` | `deps._llm` | `api_key` |

CDK スタック（`infra/stacks/sleeptech_stack.py` の `SECRET_NAMES`）はこの3つの既定名をそのまま Secrets Manager から `from_secret_name_v2` で参照するだけで、Secret 自体は作成しない。**デプロイ前に手動で作成しておく必要がある。**

```bash
# OpenAI
aws secretsmanager create-secret \
  --name sleeptech/openai \
  --secret-string '{"api_key":"sk-xxxxxxxxxxxxxxxxxxxx"}'

# LINE（channel_access_token / user_id / channel_secret の3キー必須）
aws secretsmanager create-secret \
  --name sleeptech/line \
  --secret-string '{"channel_access_token":"xxxxx","user_id":"Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx","channel_secret":"xxxxx"}'

# Google OAuth
aws secretsmanager create-secret \
  --name sleeptech/google-oauth \
  --secret-string '{"client_id":"xxxxx","client_secret":"xxxxx","refresh_token":"xxxxx"}'
```

既存の `night-ramen/google-oauth` から値をコピーする場合:

```bash
# 既存シークレットの値を取得（画面に表示されるので取り扱い注意）
aws secretsmanager get-secret-value \
  --secret-id night-ramen/google-oauth \
  --query SecretString --output text

# 取得した JSON をそのまま sleeptech/google-oauth の --secret-string に渡して create-secret する
```

---

## 3. デプロイ

```bash
cd infra
pip install -r requirements.txt   # 初回のみ（aws-cdk-lib / constructs）

# 初回のみ: このAWSアカウント/リージョンでCDKを使ったことがない場合
cdk bootstrap

# 差分確認
cdk diff
```

**`cdk diff` の出力に削除（`-` で始まる行、特に既存テーブルや既存Lambdaの削除）が無いことを必ず確認してから `cdk deploy` に進むこと。** このリポジトリでは過去に意図しない削除が事故になったことがある。特に `SleepTechTable`（DynamoDB、`RemovalPolicy.RETAIN` 設定済みだが誤ったスタック名変更などでの再作成に注意）が対象。

差分に問題が無ければデプロイする:

```bash
cdk deploy
```

デプロイ完了後、出力（Outputs）に `WebhookFunctionUrl` が表示される。これと、CloudFormation コンソールまたは `aws cloudformation describe-stacks --stack-name SleepTechStack` から `NightFn` / `MorningFn` / `WebhookFn` の実際の物理Lambda関数名（例: `SleepTechStack-NightFnXXXXXXXX-XXXXXXXXXXXXX`）を控えておく。以降の手順3や実機確認で使う。

---

## 4. Webhook URL の設定

1. `cdk deploy` の出力 `WebhookFunctionUrl` の値（`https://xxxx.lambda-url.<region>.on.aws/` 形式）を控える
2. LINE Developers コンソール → 対象チャネル → Messaging API 設定 → Webhook URL に貼り付けて保存
3. 「Webhookの利用」を **ON** にする
4. 「検証」ボタンで疎通確認（200が返ればOK。ただし検証リクエストは署名検証を通らない可能性があるため、失敗しても慌てず手順6の実機確認に進んでよい）

---

## 5. モデルIDの確認と差し替え

`LLM_MODEL` の既定値は `src/sleeptech/config.py` および `infra/stacks/sleeptech_stack.py` の環境変数で `gpt-5.6-luna` としているが、これは **暫定値**（コメントにも「実機で変更が必要な場合は環境変数だけを差し替える」「暫定値。API実機値はTask 17で確認する」と明記されている）。

実機で OpenAI API から 400/404 が返る場合（`used_fallback: true` かつ CloudWatch Logs に `llm_failed` が出る、手順6参照）は、モデルIDを差し替える。

**即時差し替え（暫定・次回デプロイで元に戻る）:**

```bash
aws lambda update-function-configuration \
  --function-name <NightFn の物理名> \
  --environment "Variables={STC_ENV=prod,STC_DDB_TABLE=SleepTechTable,STC_SECRET_GOOGLE=sleeptech/google-oauth,STC_SECRET_LINE=sleeptech/line,STC_SECRET_OPENAI=sleeptech/openai,LLM_MODEL=<正しいモデルID>,PYTHONPATH=/var/task/src}"

# MorningFn / WebhookFn も同様に実行する（3関数とも同じ environment を持つため）
```

`update-function-configuration` は environment を丸ごと上書きするため、他のキー（`STC_ENV` 等）も含めて指定すること。上記の値は `infra/stacks/sleeptech_stack.py` の `environment` 辞書と同一。

**恒久化する場合:**

`infra/stacks/sleeptech_stack.py` の以下の行を修正して `cdk deploy` し直す:

```python
"LLM_MODEL": "gpt-5.6-luna",  # ← 正しいモデルIDに変更
```

---

## 6. 実機確認

### 6-1. 夜フローの手動実行

夜ハンドラ（`src/handlers/night_handler.py`）は次のいずれかの契約を受け付ける（`event_keys` が完全一致しないと400を返す）:

- `{}` （壁時計時刻から起点日を算出。手動確認向け）
- `{"date": "2026-08-26", "now_h": 22.5}` （`date` と `now_h` は**両方必須**。片方だけは400）
- `{"scheduled_time": "2026-08-26T13:30:00Z"}` （EventBridge Scheduler が実際に渡す形。UTCのISO文字列）

手動実行するなら `date` + `now_h` のペアが分かりやすい:

```bash
aws lambda invoke \
  --function-name <NightFn の物理名> \
  --payload '{"date":"2026-08-26","now_h":22.5}' \
  --cli-binary-format raw-in-base64-out \
  out_night.json
cat out_night.json
```

または実際のスケジュール起動を模したいなら `{}` のみを渡して壁時計時刻に任せる。

### 6-2. 朝フローの手動実行

朝ハンドラ（`src/handlers/morning_handler.py`）の契約:

- `{}` （壁時計時刻から起点日=前日を算出）
- `{"date": "2026-08-26"}` （`date` のみで良い。夜と違い `now_h` は不要）
- `{"scheduled_time": "2026-08-26T23:00:00Z"}` （EventBridge Scheduler形式）

```bash
aws lambda invoke \
  --function-name <MorningFn の物理名> \
  --payload '{"date":"2026-08-26"}' \
  --cli-binary-format raw-in-base64-out \
  out_morning.json
cat out_morning.json
```

朝フローで使う `date` は夜メッセージと同じ日付を指定すること（`night_handler` の起点日と揃える。次項参照）。

### 6-3. 確認すべき項目

初回の実機確認では、特に次の3点を必ず確認すること。

1. **`X-Line-Retry-Key` が UUID v4 として LINE に受理されるか**
   LINE Messaging API の `X-Line-Retry-Key` は UUID version 4 の文字列であることが仕様で要求される（`src/sleeptech/workflow/_common.py` 参照。過去に uuid5 形状のまま送っていた欠陥があり、LINE 側で拒否される不具合があった）。夜・朝いずれも初回1通の push が実際に LINE に届くことを必ず目視確認する。届かない・エラーになる場合は CloudWatch Logs で該当 Lambda のエラーを確認する。

2. **`used_fallback` が `false` か**
   夜・朝ハンドラのレスポンスJSON（`out_night.json` / `out_morning.json`）内に `used_fallback` フィールドがある。`true` の場合はガードレール不合格 or LLM呼び出し失敗によるフォールバック文面が送信されている。特にモデルIDの誤りが原因のことが多いので、CloudWatch Logs で `llm_failed`（`error` フィールドにOpenAI APIのエラー内容が入る）が出ていないか確認する。出ていれば手順5でモデルIDを差し替える。

3. **夜と朝で同じ `<date>` が使われているか**
   CloudWatch Logs で夜フローの `night_start`（`date` フィールド）と朝フローの `morning_start`（`date` フィールド）のログを比較し、同じ日付になっていることを確認する（`src/sleeptech/workflow/night.py` / `morning.py` 参照）。ずれている場合、朝の「同じ日の予測との整合」ロジック（推奨就寝時刻からの実績比較等）が機能しない。

   DynamoDB を直接確認する場合、冪等フラグは `pk = "IDEM#NIGHT"` または `"IDEM#MORNING"`、`sk = <date>` のキー形状（`src/sleeptech/memory/dynamo_backend.py` / `client.py` 参照）:

   ```bash
   aws dynamodb get-item \
     --table-name SleepTechTable \
     --key '{"pk":{"S":"IDEM#NIGHT"},"sk":{"S":"2026-08-26"}}'

   aws dynamodb get-item \
     --table-name SleepTechTable \
     --key '{"pk":{"S":"IDEM#MORNING"},"sk":{"S":"2026-08-26"}}'
   ```

   フィードバック（クイックリプライの回答）を確認する場合は `pk = "FEEDBACK"`, `sk = <date>`:

   ```bash
   aws dynamodb get-item \
     --table-name SleepTechTable \
     --key '{"pk":{"S":"FEEDBACK"},"sk":{"S":"2026-08-26"}}'
   ```

### 6-4. Webhook の実機確認

LINEアプリから通知先チャネルに「おはよう」と送ると、Webhook Lambda（`WebhookFn`）が起床トリガーとして検出し、朝フローを前倒し実行する（`src/handlers/webhook_handler.py` の `_WAKEUP_RE`。「おはよう」「おはようございます」「起きた」「おきた」「データ更新」「同期」「計測」のいずれかに完全一致）。また、夜メッセージのクイックリプライで数値評価を返信すると、直近の未回答予測（当日・前日の2日分のみ検索）にフィードバックとして保存される。

いずれも許可した本人（Secret の `user_id`）からのメッセージのみ処理される点に注意。

---

## 7. 既存 Night Ramen の停止

**この手順を実行する前に、必ず利用者本人の確認を取ること。** 旧プロジェクト（Night Ramen Breakdown）の通知を止めるための作業であり、実行するかどうかは利用者の判断による。

**スタック自体は削除しない**（DynamoDB のデータを残すため）。EventBridge Scheduler だけを無効化する:

```bash
# 旧プロジェクトの Scheduler 名を確認する
aws scheduler list-schedules

# 対象の Scheduler を無効化する（例: NightSchedule / MorningSchedule 相当の名前）
aws scheduler update-schedule \
  --name <旧プロジェクトのScheduler名> \
  --group-name default \
  --state DISABLED \
  --schedule-expression <既存のcron式をそのまま指定> \
  --flexible-time-window Mode=OFF \
  --target <既存のtarget設定をそのまま指定>
```

`update-schedule` は差分更新ではなく全項目の再指定が必要なため、事前に `aws scheduler get-schedule --name <名前>` で現在の設定を取得し、`State` だけ `DISABLED` に変えて渡すこと。

---

## 8. ロールバック

SleepTech の通知に問題があり、いったん止めたい場合は、SleepTech 側の EventBridge Scheduler を無効化する（スタックは削除しない）。

```bash
# 対象の Scheduler 名を確認（NightSchedule / MorningSchedule の物理名）
aws scheduler list-schedules --name-prefix SleepTechStack

# 現在の設定を取得してから State だけ DISABLED にして再指定する
aws scheduler get-schedule --name <NightSchedule の物理名>
aws scheduler update-schedule \
  --name <NightSchedule の物理名> \
  --state DISABLED \
  --schedule-expression "cron(30 22 * * ? *)" \
  --schedule-expression-timezone "Asia/Tokyo" \
  --flexible-time-window Mode=OFF \
  --target <get-schedule で取得した Target をそのまま渡す>
```

`MorningSchedule`（`cron(0 8 * * ? *)`）も同様に行う。再開する場合は `--state ENABLED` で同じコマンドを実行する。

Webhook（おはよう前倒し）を止めたい場合は Lambda Function URL 自体を無効化するか、LINE Developers コンソールで「Webhookの利用」をOFFにする。
