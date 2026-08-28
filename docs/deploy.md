# SleepTech ver1.0 デプロイ手順書

この1枚で本番デプロイと実機確認を完走できることを目標にしている。
すべてのコマンド・キー名・ペイロード形式は実際のソースコードから読み取った値であり、
推測は含まない（参照元ファイルを各節に明記する）。

対象スタック: `SleepTechStack`（`infra/app.py`）
Lambda論理名: `NightFn` / `MorningFn` / `WebhookFn` / `BackfillFn`（`infra/stacks/sleeptech_stack.py`）

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

Secret は AWS Console の Secrets Manager から、各 Secret 名を指定して **Store a new
secret** を開き、**Other type of secrets** を選んで JSON キーと値を画面へ手入力する。
値は CLI 引数、PowerShell 変数、シェル履歴、ログ、LLM、チャットへ入れない。
保存後は値を表示せず、Secret 名とキーの存在だけを権限管理された画面で確認する。
この作業では Secret の値を返す CLI/API を実行しない。

既存の `night-ramen/google-oauth` を引き継ぐ場合も、秘密値を取得・表示・ログしない。
認証済みの運用担当者が、Secrets Manager の権限管理された画面または既存の安全な
シークレット移行手順でコピーする。シェル履歴、CloudWatch Logs、チャット、チケットへ
秘密値を貼り付けない。このタスクでは Secret の読み取り・作成・更新を実行しない。

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

デプロイ完了後、出力（Outputs）に `WebhookFunctionUrl` が表示される。Lambda の物理名は
次の CloudFormation コマンドで論理IDから取得する。Secret の値は返さない。

```powershell
foreach ($logicalId in @("NightFn", "MorningFn", "WebhookFn", "BackfillFn")) {
  aws cloudformation describe-stack-resource `
    --stack-name SleepTechStack `
    --logical-resource-id $logicalId `
    --query "StackResourceDetail.[LogicalResourceId,PhysicalResourceId]" `
    --output table
  if ($LASTEXITCODE -ne 0) { throw "物理名の取得に失敗したため停止: $logicalId" }
}
```

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

モデルIDを変更する場合は、Lambda の直接設定変更や environment 全置換を行わず、
`infra/stacks/sleeptech_stack.py` の CDK 環境辞書にある `LLM_MODEL` を変更する。
モデルID専用の追加環境変数は使用しない。

```powershell
# infra/stacks/sleeptech_stack.py の値をレビュー済みのモデルIDへ変更してから実行する
cd infra
cdk synth --strict
cdk diff SleepTechStack --fail false
cdk deploy SleepTechStack --require-approval never
cd ..
```

差分で `STC_MIN_TARGET_SLEEP_H`、Secret 参照、`PYTHONPATH` など既存キーが消えていない
こと、削除・置換がないことを確認してからデプロイする。恒久変更は CDK ソースで管理する。

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

---

## 9. 評価補正の移行（既存2件を削除せず補完）

この節は、デプロイ後に承認済みの運用担当者が実行する手順である。この作業では
秘密値を取得・表示・ログしない。対象キー以外の項目や DynamoDB のキーは変更せず、
削除、全件置換、テーブル再作成を行わない。

### 9-1. 更新前の照合

次の PowerShell は、対象テーブルとリージョンだけを変数に入れてそのまま実行できる。
JSON は一時ファイルに書き、PowerShell の quoting と CLI 引数への JSON 直書きを避ける。
出力は件数・キー・検証結果だけにし、Secret の値は扱わない。

```powershell
$ErrorActionPreference = "Stop"
$TableName = "SleepTechTable"
$Region = "ap-northeast-1"
$MigrationDir = Join-Path $env:TEMP "sleeptech-feedback-migration"
New-Item -ItemType Directory -Force -Path $MigrationDir | Out-Null

function Write-Utf8Json {
  param([string]$Path, [object]$Value, [int]$Depth = 8)
  $json = $Value | ConvertTo-Json -Compress -Depth $Depth
  [System.IO.File]::WriteAllText(
    $Path,
    $json,
    [System.Text.UTF8Encoding]::new($false)
  )
}

$queryValuesFile = Join-Path $MigrationDir "feedback-query-values.json"
$queryNamesFile = Join-Path $MigrationDir "feedback-query-names.json"
Write-Utf8Json $queryValuesFile @{ ":pk" = @{ S = "FEEDBACK" } }
Write-Utf8Json $queryNamesFile @{ "#json" = "json" }

$query = aws dynamodb query `
  --region $Region --table-name $TableName --consistent-read `
  --key-condition-expression "pk = :pk" `
  --expression-attribute-values "file://$queryValuesFile" `
  --projection-expression "sk,#json" `
  --expression-attribute-names "file://$queryNamesFile" `
  --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "FEEDBACK Query failed" }

$items = @($query.Items)
$beforeCount = $items.Count
Write-Output "更新前 FEEDBACK 件数: $beforeCount"

$expectedDates = @("2026-08-26", "2026-08-27")
$actualDates = @($items | ForEach-Object { $_.sk.S })
if ($actualDates.Count -ne 2 -or
    (($actualDates | Sort-Object) -join ",") -ne (($expectedDates | Sort-Object) -join ",")) {
  throw "対象が既知の2件と一致しないため停止"
}

$expectedOldJson = @{
  "2026-08-26" = '{"date": "2026-08-26", "score": 3, "answered_at": "2026-08-26T23:32:24+09:00"}'
  "2026-08-27" = '{"date": "2026-08-27", "score": 1, "answered_at": "2026-08-27T23:47:52+09:00"}'
}
foreach ($item in $items) {
  $date = $item.sk.S
  if ($item.json.S -cne $expectedOldJson[$date]) {
    throw "旧JSONが承認済みの完全一致値ではないため停止: $date"
  }
}
Write-Output "旧JSON完全一致: 2026-08-26, 2026-08-27"
```

この Query の件数は冪等 marker の `IDEM#FEEDBACK` を含まない。対象2件以外が存在する
環境では安全のため停止し、対象を追加して更新しない。

次に、対応する Morning 予測を強整合 BatchGet で確認し、さらに各キーを強整合 GetItem
でも確認する。

   | `date` | `score` | `answered_at` | 対応する `prediction_date` |
   |---|---:|---|---|
   | `2026-08-26` | 3 | `2026-08-26T23:32:24+09:00` | `2026-08-25` |
   | `2026-08-27` | 1 | `2026-08-27T23:47:52+09:00` | `2026-08-26` |

```powershell
$batchFile = Join-Path $MigrationDir "morning-batch-get.json"
Write-Utf8Json $batchFile @{
  RequestItems = @{
    $TableName = @{
      Keys = @(
        @{ pk = @{ S = "PREDICT#MORNING" }; sk = @{ S = "2026-08-25" } }
        @{ pk = @{ S = "PREDICT#MORNING" }; sk = @{ S = "2026-08-26" } }
      )
      ConsistentRead = $true
      ProjectionExpression = "pk,sk"
    }
  }
}

$batch = aws dynamodb batch-get-item `
  --region $Region --request-items "file://$batchFile" --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Morning BatchGet failed" }
$responseProperty = @(
  $batch.Responses.PSObject.Properties |
    Where-Object { $_.Name -eq $TableName }
)
if ($responseProperty.Count -ne 1) { throw "BatchGetの応答に対象テーブルがないため停止" }
$batchItems = @($responseProperty[0].Value)
if ($batchItems.Count -ne 2) { throw "対応 Morning 予測が2件そろわないため停止" }
Write-Output "対応 Morning BatchGet 件数: $($batchItems.Count)"

foreach ($date in @("2026-08-25", "2026-08-26")) {
  $keyFile = Join-Path $MigrationDir "morning-$date-key.json"
  Write-Utf8Json $keyFile @{ pk = @{ S = "PREDICT#MORNING" }; sk = @{ S = $date } } 4
  $one = aws dynamodb get-item `
    --region $Region --table-name $TableName --consistent-read `
    --key "file://$keyFile" --projection-expression "pk,sk" `
    --output json | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or $null -eq $one.Item) {
    throw "対応 Morning 個別 GetItem が見つからないため停止: $date"
  }
  Write-Output "対応 Morning 個別 GetItem 確認: $date"
}
```

片方でも件数、旧 JSON、スコア、回答時刻、対応予測の存在が一致しなければ停止し、
再 Query して差異を調査する。確認できていない値を推測して更新しない。

### 9-2. 条件付き更新

各項目について、確認した旧 JSON を DynamoDB AttributeValue の `{"S":"..."}` として
`:old` に入れ、新 JSON も `{"S":"..."}` として `:new` に入れる。キーを変えず
`UpdateExpression: SET #json = :new`、`ConditionExpression: #json = :old` の
条件付き UpdateItem を1件ずつ行う。条件不成立を含むコマンド失敗は直ちに停止する。

```json
{"date":"2026-08-26","score":3,"answered_at":"2026-08-26T23:32:24+09:00","evaluated_date":"2026-08-26","prediction_date":"2026-08-25"}
{"date":"2026-08-27","score":1,"answered_at":"2026-08-27T23:47:52+09:00","evaluated_date":"2026-08-27","prediction_date":"2026-08-26"}
```

```powershell
$newJson = @{
  "2026-08-26" = '{"date":"2026-08-26","score":3,"answered_at":"2026-08-26T23:32:24+09:00","evaluated_date":"2026-08-26","prediction_date":"2026-08-25"}'
  "2026-08-27" = '{"date":"2026-08-27","score":1,"answered_at":"2026-08-27T23:47:52+09:00","evaluated_date":"2026-08-27","prediction_date":"2026-08-26"}'
}
foreach ($date in $expectedDates) {
  $requestFile = Join-Path $MigrationDir "update-$date.json"
  Write-Utf8Json $requestFile @{
    TableName = $TableName
    Key = @{ pk = @{ S = "FEEDBACK" }; sk = @{ S = $date } }
    UpdateExpression = "SET #json = :new"
    ConditionExpression = "#json = :old"
    ExpressionAttributeNames = @{ "#json" = "json" }
    ExpressionAttributeValues = @{
      ":old" = @{ S = $expectedOldJson[$date] }
      ":new" = @{ S = $newJson[$date] }
    }
  }
  aws dynamodb update-item --region $Region --cli-input-json "file://$requestFile" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "条件付き更新に失敗したため停止: $date" }
  Write-Output "条件付き更新成功: $date"
}

$after = aws dynamodb query `
  --region $Region --table-name $TableName --consistent-read `
  --key-condition-expression "pk = :pk" `
  --expression-attribute-values "file://$queryValuesFile" `
  --projection-expression "sk,#json" `
  --expression-attribute-names "file://$queryNamesFile" `
  --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "更新後 FEEDBACK Query failed" }
$afterItems = @($after.Items)
$afterCount = $afterItems.Count
if ($afterCount -ne $beforeCount) { throw "更新前後の件数が不一致のため停止" }
foreach ($item in $afterItems) {
  if ($item.sk.S -notin $newJson.Keys -or $item.json.S -cne $newJson[$item.sk.S]) {
    throw "更新後 JSON が期待値と不一致のため停止: $($item.sk.S)"
  }
}
Write-Output "更新後 FEEDBACK 件数: $afterCount"
Write-Output "削除件数: 0（削除操作なし）"
```

条件付き更新が1件でも失敗したら、その場で追加更新を止め、再 Query して現在値を
調査する。更新後の強整合 Query で `FEEDBACK` 件数が更新前と同じであること、削除件数が
0であること、2件の `evaluated_date` と `prediction_date` が上表どおりであることを
記録する。対応予測がない場合も Feedback は削除せず、補正対象外として残す。

---

## 10. LINE公式アカウントの応答設定

LINE Developers と LINE Official Account Manager の既存ログイン状態を使い、秘密値を
画面やログへ転記しない。

1. LINE Developers の対象チャネルで **Messaging API設定** を開き、Webhook URL が
   `WebhookFunctionUrl` と一致していることを確認する。
2. LINE Developers の Messaging API設定で **Webhookの利用** を ON のまま維持する。
3. LINE Official Account Manager の **設定 → 応答設定 → 応答メッセージ → 編集** を
   開き、**応答メッセージをオフ**にして保存する。
4. 同画面の Chat の応答方法も自動文を返さない設定にし、汎用の自動応答を無効にする。
5. 画面を再読み込みして、応答メッセージがオフ、Webhookの利用がオンのままであることを
   再確認する。

評価ボタンを1回だけ押して、SleepTech の
「評価Nを記録しました。次回以降の予測補正に反映します。」が1回だけ返り、
汎用自動応答が0件であることを確認する。保存できない評価については、対応する朝予測
がない旨の明確な返信だけが返り、評価が保存されないことを確認する。

---

## 11. デプロイ前の検証

リポジトリのルートで、デプロイや実送信をせずに次を実行する。

```powershell
python -m pytest -q
python -m compileall -q src infra
git diff --check
cd infra
cdk synth --strict
cdk diff SleepTechStack --fail false
cd ..
```

テストでは、バックフィルを除く全ランタイム Lambda に
`STC_MIN_TARGET_SLEEP_H=7.0` があり、Webhook に DynamoDB の
`dynamodb:TransactWriteItems` があり、CDK output が `../cdk.out` のままで、アセットに
ハンドラが含まれ秘密ファイル・開発用ディレクトリ・コンパイル済み Python が含まれない
ことを確認する。

`cdk diff` は既存リソースの削除・置換が0であることを確認する。差分にそれ以外の変更が
あれば停止してレビューする。ここでは AWS デプロイ、DynamoDB 更新、LINE画面操作、
実メッセージ送信を実施しない。

---

## 12. デプロイ後の検証項目（運用担当者が実施）

- CloudFormation の `SleepTechStack` が `UPDATE_COMPLETE` である。
- Scheduler が夜22:30と朝8:00の2本とも `ENABLED` である。
- Night は3:15のような遅い時刻を推奨せず、起床目安、最低7時間、予測幅、信頼度を表示する。
- Night/Morning とも単一の百分率や旧「平常時100%」を表示せず、範囲と信頼度を表示する。
  Morning は各時間帯を16時間の範囲として表示する。
- 表示には「睡眠モデルと主観評価による推定幅で、実測の仕事能力ではない」旨と、
  中途覚醒・HRV・心拍数を現行の百分率計算に使っていない旨がある。
- DynamoDB の Prediction に `raw_mean_drop_pct`、`calibrated_mean_drop_pct`、
  `range_low_pct`、`range_high_pct`、`confidence`、`feedback_count`、
  `calibration_offset_pct` が保存される。`IDEM#FEEDBACK` は評価履歴件数に含めない。
- CloudWatch Logs に `feedback_saved` と `feedback_reply_sent` が出て、補正 offset と
  利用件数が夜・朝のログに出る。返信失敗時は `feedback_reply_failed` となり、保存済み
  評価を失わない。
- 評価時の `FEEDBACK.evaluated_date` は質問日、`prediction_date` はその前日で、
  `PREDICT#NIGHT` には紐付かない。評価確認返信は1回だけで、汎用自動応答は0件である。

---

## 13. ロールバック時のデータ保持

問題があればまず Night/Morning Scheduler を無効化し、必要に応じて Lambda/CDK を前版へ
戻す。旧コードは新しいフィールドを無視して旧レコードを読めるため、DynamoDB の
`FEEDBACK` や `PREDICT` レコードを削除・巻き戻し・再作成しない。既存の評価件数、
`evaluated_date`、`prediction_date`、補正監査値はそのまま保持する。

前版へ戻した後も、Scheduler の状態、Webhook の応答設定、DynamoDB 件数を確認し、
再デプロイ時には再び `cdk synth --strict` と削除・置換のない `cdk diff` を通す。
