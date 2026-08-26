# 最終レビュー修正 最終波 報告（出荷ブロッカー2件 + Minor M1〜M6）

対象: 最優先1（scheduled_time フォールバック閾値の退行）、最優先2（MorningFn の
日跨ぎリトライ保護なし）、および Minor M1〜M6。

## 最優先1: scheduled_time のフォールバック閾値が長時間リトライを誤検出する

### 選んだ方式: (b) 「naiveな数字がcronの予定時刻と一致するか」判定

前回の修正は「壁時計との乖離が7時間を超えたらタイムゾーン誤りとみなす」という
対称な閾値判定だった。EventBridge Scheduler は RetryPolicy 未指定だと既定で
最大24時間・185回リトライするため、7時間超の乖離は「タイムゾーン誤り」ではなく
「正規の長時間リトライ」でも普通に起こり得る。壁時計との乖離量に依存する限り、
どんな閾値を選んでも「本物の長時間リトライ」と「Z誤ラベル（9時間ずれ）」を
区別できない領域が残る。

そこで (b) を採用し、判定基準を「乖離量」から「scheduled_time自体の形」に変更した。
`src/handlers/deps.py` に `resolve_scheduled_time(value, cron_hour, cron_minute)` を
新設し、次の条件を **両方** 満たす場合だけ「ローカル時刻に誤ってZを付けて返ってきた」
疑いありと判定する。

1. タイムゾーン表記がUTC（`Z` または `+00:00`）である
2. その数字（タイムゾーン変換前の生の時刻）をそのままJST時刻とみなすと、
   cronの予定時刻（夜22:30・朝8:00）と厳密に一致する

`scheduled_time` は常にcronの予定時刻そのものであり（実行がどれだけ遅延しても
`scheduled_time` の値自体は変わらない）、この判定は壁時計にも乖離量にも依存しない。
したがって長時間リトライがどれだけ発生しても誤検出しない。(a)（8〜10時間の帯域判定）も
検討したが、正規の長時間リトライがたまたま9時間前後の遅延になった場合に誤検出しうる
点で (b) より弱いため見送った。

判定条件2にオフセット条件（条件1）も課しているのは、`+02:00` のような正当な非UTC
オフセットが偶然「数字だけ読むと22:30/8:00に一致する」場合の誤検出を防ぐため。

- `src/handlers/deps.py`: `resolve_scheduled_time` を新設（`_scheduled_datetime` の
  乖離量ベース判定を置き換え）。
- `src/handlers/night_handler.py`: `_SCHEDULED_TIME_DRIFT_TOLERANCE_SEC` と
  `_scheduled_datetime` を削除し、`hd.resolve_scheduled_time` を使うよう変更。
- `src/handlers/morning_handler.py`: 同ロジックを流用（最優先2で新設）。

### 3ケースの実測値（`tests/unit/test_handlers.py`）

| ケース | scheduled_time | 壁時計 | 結果 date / now_h |
|---|---|---|---|
| 1. 正しいUTC、壁時計もほぼ同時刻 | `2026-08-26T13:30:00Z` | 2026-08-26 22:34 JST | `2026-08-26` / `22.5`（`test_night_handler_uses_scheduled_time_when_utc_and_wall_clock_roughly_agree`） |
| 2. ローカル時刻にZ誤付与、壁時計もほぼ同時刻 | `2026-08-26T22:30:00Z` | 2026-08-26 22:31 JST | `2026-08-26` / `22.5167`（壁時計へフォールバック、`test_night_handler_falls_back_to_wall_clock_when_scheduled_time_looks_locally_labeled`） |
| 3. **正しいUTC、壁時計が7時間半進んでいる（長時間リトライ）** | `2026-08-26T13:30:00Z` | 2026-08-27 06:00 JST | `2026-08-26` / `22.5`（scheduled_timeを信頼、**フォールバックしない**。`test_night_handler_trusts_scheduled_time_despite_long_retry_delay`） |

いずれも自動テストで確認済み。ケース3は前回の修正では `date='2026-08-27',
now_h=6.0` に誤って壁時計へフォールバックしていた（本波で追加した回帰テストで
その退行が再発しないことを固定した）。

## 最優先2: MorningFn に日跨ぎリトライの保護が無い

`src/handlers/morning_handler.py` を全面書き換えし、夜と同じ `scheduled_time`
契約（`{"scheduled_time": "<aws.scheduler.scheduled-time>"}`、`{"date": "..."}`
での手動上書き、空イベントでの壁時計フォールバックの3系統）を受け付けるようにした。

- `scheduled_time` があれば `hd.resolve_scheduled_time(value, 8, 0)` で解決し、
  ローカル誤ラベル疑いでなければ **常にその瞬時から起点日を導く**（壁時計は見ない）。
  これにより「8:00起動が失敗し続けて翌00:30に成功」しても、起点日は
  `scheduled_time` 由来のまま08-26に固定され、日跨ぎで1日ずれることがなくなった。
- CDK側（`infra/stacks/sleeptech_stack.py`）で `MorningSchedule` にも
  `{"scheduled_time": "<aws.scheduler.scheduled-time>"}` を渡すよう変更。
- 両 `CfnSchedule.TargetProperty` に `RetryPolicy`
  （`maximum_event_age_in_seconds=3600`, `maximum_retry_attempts=5`）を追加し、
  日跨ぎリトライ自体が起きにくいようにした（EventBridge Scheduler の既定・最大
  24時間・185回リトライを明示的に絞った）。

### 検証（`tests/unit/test_handlers.py`）

- `test_morning_handler_derives_date_from_scheduled_time`: `scheduled_time` から
  正しく前日日付を導く。
- `test_morning_handler_survives_overnight_retry_using_scheduled_time`: 8:00起動が
  日跨ぎ（翌00:30）で成功しても `scheduled_time` 由来の起点日（08-26）が
  維持されることを確認（優先2が解決する本体のバグの回帰テスト）。
- `test_morning_handler_falls_back_to_wall_clock_when_scheduled_time_looks_locally_labeled`:
  ローカル誤ラベルの検出・フォールバックを確認。
- `test_morning_handler_uses_wall_clock_for_manual_fallback` /
  `test_morning_handler_accepts_explicit_date_override` /
  `test_morning_handler_rejects_invalid_or_mixed_input` /
  `test_morning_handler_passes_lambda_deadline_to_dependencies`:
  既存の夜ハンドラと同等の契約（入力検証・deadline伝播）を確認。
- `tests/unit/test_infra_synth.py`:
  `test_morning_schedule_also_has_scheduled_time_input`（旧
  `test_morning_schedule_has_no_input` を置き換え）、
  `test_both_schedules_have_a_retry_policy` を追加。

## Minor 対応

### M1: `too_late` / `unreachable` で「-0%」が残る

`src/sleeptech/guardrail/fallback.py` の `night_text` で、`mean_drop_pct == 0`
の判定を `status` の分岐より先に評価するよう並べ替えた。`too_late` / `unreachable`
それぞれについて「ベスト比 -0% 程度」を使わない専用文言（「明日はベストに近い一日
になりそうです」）を追加。`Facts` 側（`src/sleeptech/workflow/night.py`）は
既に `advice.mean_drop_pct != 0` の場合だけ `percents` に値を入れる実装済みの
status非依存ガードだったため追加修正は不要と確認した。
テスト: `tests/unit/test_fallback.py` に
`test_night_text_too_late_with_zero_drop_avoids_minus_zero_percent` /
`test_night_text_unreachable_with_zero_drop_avoids_minus_zero_percent` を追加。

### M2: `Message.delivered` / `regen_count`

- `delivered`: 4箇所とも `_push` が例外を捕捉せず送出する設計（コメントに明記済み）
  のため、`_save_message` に到達している時点で送信は成功済み。`delivered=True` は
  実態と一致するため値は変更せず、その理由を各箇所にコメントとして明記した
  （`night.py` 1箇所、`morning.py` 3箇所）。
- `regen_count`: `night.py` / `morning.py` の `_compose` を修正し、フォールバック時に
  常に `deps.max_regen` を返していたのをやめ、実際に試行した回数
  （`attempts_made`、deadline超過やLLM例外でのbreakも含めて正確にカウント）から
  `max(attempts_made - 1, 0)` を返すよう変更。LLMが初回で例外を投げた場合は
  `regen_count=0`（実際に再試行していないため）になる。

### M3: `status` のトップレベルミラーが `PREDICT#*` に漏れる

`src/sleeptech/memory/dynamo_backend.py` の `_item()` で、`status` のトップレベル
ミラーを `pk.startswith("IDEM#")` の場合に限定した（`lease_until` は元々 IDEM
エンティティにしか値が入らないため変更不要）。既存の moto テスト
（`tests/unit/test_dynamo_backend_moto.py`）で claim/complete の
ConditionExpression 評価が引き続き成功することを確認済み（全件通過）。

### M4: boto3 の既定タイムアウトが60秒Lambdaに対して未拘束

- `src/handlers/deps.py`: `_secret()` が使う Secrets Manager クライアントに
  `botocore.config.Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 2})`
  を適用（`_boto_config()` として共通化）。
- `src/sleeptech/memory/dynamo_backend.py`: DynamoDB resource にも同じ Config を適用。
- `src/sleeptech/collectors/google_auth_helper.py`: トークン取得の
  `urllib.request.urlopen(..., timeout=15)` を `timeout=5` に短縮。
- `botocore` は boto3 の依存のため、Global Constraints（Lambda実行時依存は
  boto3+標準ライブラリのみ）に抵触しない。

### M5: `Settings.night_hour_jst` / `morning_hour_jst` を削除

`src/sleeptech/config.py` から両フィールドと対応する環境変数読み込み
（`STC_NIGHT_HOUR_JST` / `STC_MORNING_HOUR_JST`）を削除。他に参照箇所が無いことを
`grep` で確認済み（CDK側のcron配線は指示通り変更していない）。
仕様設計ドキュメント（`docs/superpowers/specs/...-design.md`）に当該記載は無く、
`docs/superpowers/plans/...md`（実行計画）側のみに記載があるが、これは実行済み計画の
記録であり修正対象外と判断した。`tests/unit/test_config.py` を更新。

### M6: CDK スタックの `env=` 未指定

`infra/app.py` の `SleepTechStack(app, "SleepTechStack")` に
`env=cdk.Environment(account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION"))`
を追加。`tests/unit/test_infra_synth.py` は `SleepTechStack(App(), "TestStack")` を
env無しで直接生成しており、この変更の影響を受けないことを確認済み（全件通過）。

## 手を付けなかったもの

- **New-5**（`allowed_times` へのカーブ時刻追加による捏造検出力の低下）:
  指示通り許容範囲と判断し、変更していない。
- **New-11**（I8 の仕様表を実測値に更新した件）: 指示通り変更していない。

## LLM 成功経路（`used_fallback=False`）の確認結果（5ケース）

いずれも自動テストで `used_fallback is False` を確認済み。

1. **夜・通常の晩**:
   `tests/integration/test_night_flow.py::test_normal_night_llm_success_path_still_passes_with_nonzero_drop`
2. **夜・0%の晩**:
   `tests/integration/test_night_flow.py::test_zero_drop_night_llm_success_path_still_passes`
   （M1でFactsに変更は無いため既存テストのまま通過することを再確認）
3. **夜・`too_late`**（新規追加）:
   `tests/integration/test_night_flow.py::test_too_late_night_llm_success_path_still_passes`
   （`now_h=28.0` で `status="too_late"` になるケースを直接検証。
   `pred.status == "too_late"` と `used_fallback is False` を確認）
4. **夜・日跨ぎ就寝**（新規追加）:
   `tests/integration/test_night_flow.py::test_day_crossing_bedtime_night_llm_success_path_still_passes`
   （`now_h=24.5` で `bedtime_h>=24.0`（「翌」表記が必要な就寝時刻）になるケースを
   `require_day_prefix=True` の照合込みで検証）
5. **朝・通常**:
   `tests/integration/test_morning_flow.py::test_llm_success_path_still_passes_after_percents_change`

## テストの実行コマンドと出力

```
python -m pytest -q
```

```
........................................................................ [ 18%]
........................................................................ [ 37%]
........................................................................ [ 55%]
........................................................................ [ 74%]
........................................................................ [ 93%]
...........................                                              [100%]
387 passed in 28.15s
```

（370件 → 387件。新規追加17件の内訳: `test_handlers.py` 8件（夜1件・朝7件）、
`test_infra_synth.py` 2件（1件置換・1件新規）、`test_fallback.py` 2件、
`test_night_flow.py` 2件、その他既存ファイルの前提修正）

```
cd infra && npx cdk synth SleepTechStack
```

exit code 0（RetryPolicy・MorningSchedule の scheduled_time input を含む
CloudFormation テンプレートが正常に合成されることを確認）。

## 懸念点

- M6 の `env=` 追加により、`CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION` が
  未設定の環境（例えば `cdk bootstrap` 未実施のCI）では合成結果が変わりうる
  （環境非依存スタックから環境依存スタックへ）。本セッションのローカル環境では
  問題なく synth できたが、実デプロイ前に対象AWSアカウントで
  `cdk bootstrap`／`cdk diff` を確認することを推奨する。
- `resolve_scheduled_time` のローカル誤ラベル判定は「タイムゾーン表記がUTCかつ
  数字がcronの予定時刻と厳密一致」という条件のため、cron予定時刻そのもの
  （夜22:30・朝8:00）を変更する場合は `NIGHT_CRON_HOUR_JST` /
  `NIGHT_CRON_MINUTE_JST`（`night_handler.py`）・
  `MORNING_CRON_HOUR_JST` / `MORNING_CRON_MINUTE_JST`（`morning_handler.py`）を
  CDK側のcron式と同期して変更する必要がある（現状は手動同期。定数化はしたが
  自動検証の仕組みは無い）。
- RetryPolicy の `maximum_retry_attempts=5` / `maximum_event_age_in_seconds=3600`
  は指示の目安値（3〜5／3600）をそのまま採用した。実運用でのLambda失敗率や
  許容できる遅延に応じて調整の余地がある。
