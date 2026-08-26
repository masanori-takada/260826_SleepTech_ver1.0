# 最終レビュー修正 第2波 報告（I1・I2・I3・I4・I5・I7 + Minor）

対象: `final-review-findings.md` の Important I1〜I5・I7、および対応可能な Minor。
第1波（C1/C2/C3/I6/I8）は完了済み（コミット `47add96`）で、本波はそれ以降に対応した内容。

## 各指摘への対応内容

### I3: LINE Retry-Key が UUID v4 でない（最優先）

- `src/sleeptech/workflow/_common.py` を新設し、`retry_key(date, slot)` を実装。
  `uuid5(NAMESPACE_URL, ...)` が生成した16バイトをそのまま使い、
  `uuid.UUID(bytes=seed.bytes, version=4)` で version ニブルと variant ビットのみを
  v4 の形状に立て直した。入力バイト列は変えないため決定論性は保たれる。
- `night.py` / `morning.py` の重複していた `_retry_key` / `_push` をこの共通モジュールに
  集約（Minor「night.py と morning.py の _push / _retry_key が完全重複」も解消）。
- テスト: `tests/unit/test_workflow_common.py` に4件追加
  （version==4、variant==RFC4122、決定論性、date/slotで値が変わること）。

### I1: MorningFn のタイムアウト

- `src/sleeptech/collectors/google_health_api.py`
  - `HTTP_TIMEOUT_SEC = 4.0` を新設し、`_default_http_post` / `_default_http_get` の
    `timeout=20` を置き換えた。
  - `GoogleHealthApiSource.__init__` に `deadline` 引数を追加（`ExecutionDeadline` 互換の
    `can_start(expected_sec)` を持つオブジェクト、既定 `None` で従来どおり無制限）。
  - `fetch_raw` は各フィールド取得の直前で `_budget_exhausted()`（残り時間が1リクエスト分を
    割っているか）を確認し、切れていればそのフィールドは取得せず `None`（欠損）にする。
  - `_fetch_list_avg` / `_fetch_sleep` のページループにも同じチェックを追加（
    `LIST_TIME_BUDGET_SEC` はページ「前」しか見ないため、1回のブロッキングI/Oで
    予算を超過しうる問題への対策）。
  - Minor「`_ROLLUP_MAP` の `next_day_steps` / `next_day_calories` が無駄なAPI呼び出し」も
    ここで解消（`DailyVital` に対応フィールドが無く `to_daily_vital` で常に捨てられていたため
    削除。取得フィールドが5→3に減り、直列待ちの最悪時間も短縮）。
- `src/handlers/deps.py`: `_health_source(settings, deadline=None)` に変更し、
  `build_morning_deps` から deadline を渡すようにした。
- Lambda のタイムアウト値そのものは変更していない。

### I2: WebhookFn が deadline 無しで run_morning を呼ぶ

- `src/handlers/webhook_handler.py` の起床トリガー処理で
  `hd.deadline_from_context(context)` を呼び、`night_handler` / `morning_handler` と
  同じパターンで `build_morning_deps(settings, deadline)` に渡すよう修正。
  非同期化はしていない（指示どおり）。

### I4: 平常どおり眠れた晩が「-0%」になる

- `src/sleeptech/workflow/night.py`: `advice.mean_drop_pct == 0` のとき
  `Facts.percents` を空リストにし、0%の明記を必須にしないようにした。
- `src/sleeptech/llm/prompts.py` の `night_user`: 0%のときは
  「明日の主要時間帯の平均低下率: ほぼ低下なし（%の数値は本文に書かないこと）」という
  行に切り替え、LLMに%を書かせないよう明示した。
- `src/sleeptech/guardrail/fallback.py` の `night_text`: `mean_drop_pct == 0` を
  専用分岐にし、「今夜の目安どおりに寝られれば、明日はベストに近い一日になりそうです。」
  という文面（`-0%` を含まない）を返すようにした。
- 朝フロー（`morning.py`）は既に `percents=[worst.focus_pct] if worst.focus_pct > 0 else []`
  という実装済みのガードがあり、同種の問題は発生していなかったことを確認した
  （追加の修正は不要と判断）。

### I7: 朝の allowed_times にカーブの時刻が入っていない

- `src/sleeptech/workflow/morning.py`: `allowed_times = times + [float(d.hour) for d in curve]`
  とし、必須の `times`（カフェイン・光曝露の4〜5個）は変更せず、許可リストにのみ
  カーブの各時刻（起床時刻からの連番、通常 `CURVE_HOURS=16` 個）を追加した。

### I5: MSG#NIGHT / MSG#MORNING が未実装

- `src/sleeptech/memory/schemas.py` に `Message` データクラス
  （`date, slot, body, guardrail_passed, regen_count, delivered`）を追加。
- `src/sleeptech/memory/client.py` に `save_message` / `get_message` を追加
  （`Prediction` と同じ `put`/`get` パターン、pk=`MSG#{slot}`, sk=`date`）。
- `src/sleeptech/workflow/_common.py` に `save_message(deps, ...)` ヘルパーを追加し、
  `save_prediction` と同じ方針で **失敗しても例外を伝播させずログに残して続行**するようにした。
- 夜・朝フローの全ての送信経路（夜: 通常送信、朝: 通常送信・睡眠データ欠損・同意失効の
  3経路すべて）で送信後に `save_message` を呼ぶようにした。
- `_compose` の戻り値に `regen_count`（ガードレール不通過による再試行回数。最初の1回は
  含まない。フォールバックに落ちた場合は `deps.max_regen`）を追加。

## Retry-Key が v4 形状かつ決定論的であることの確認

```python
from sleeptech.workflow._common import retry_key
import uuid

k1 = retry_key("2026-08-26", "NIGHT")
k2 = retry_key("2026-08-26", "NIGHT")
u = uuid.UUID(k1)
```

実行結果:
```
key: cd7b1dc8-8bba-4d14-8f5f-ae31594d6003
version: 4
variant: specified in RFC 4122
deterministic (same input): True
differs by date: True
differs by slot: True
```

`cd7b1dc8-8bba-4d14-8f5f-ae31594d6003` — 3グループ目が `4` で始まり（version=4）、
4グループ目が `8` で始まる（RFC4122 variant）ことを実値で確認した。
同一入力（同じ `<date>/<slot>`）からは常に同じ文字列が生成される（決定論性）ことも確認済み。
自動テストでも `tests/unit/test_workflow_common.py` で
`.version == 4` / `.variant == uuid.RFC_4122` / 決定論性 / date・slot 差異の4点を検査している。

## LLM 成功経路の確認結果

いずれも `used_fallback=False` になることを自動テストで確認済み。

- **夜・0%の晩**: `tests/integration/test_night_flow.py::test_zero_drop_night_llm_success_path_still_passes`
  （既定の14日履歴＝毎晩7.5h睡眠、`NOW_H=22.5`）。`mean_drop_pct == 0` かつ
  `used_fallback is False` を確認。同ファイルの `test_zero_drop_night_does_not_require_percent_in_facts`
  で `Facts.percents == []` になっていることも確認。
- **夜・通常の晩**: `tests/integration/test_night_flow.py::test_normal_night_llm_success_path_still_passes_with_nonzero_drop`
  （履歴ゼロ→既定ベースラインで `drop > 0` になるケース）。`used_fallback is False` を確認。
  既存の `test_sends_one_message_with_quick_reply` 等の既存回帰テストも全て通過。
- **朝・通常のケース（既定 FakeHealthSource、5.7h睡眠）**:
  既存の `tests/integration/test_morning_flow.py::test_llm_success_path_still_passes_after_percents_change`
  （`used_fallback is False`）が引き続き通過することを確認。加えて I7 対応の検証として
  `test_curve_hour_paraphrase_is_not_rejected_as_fabrication`
  （「13時から15時にかけて」という自然な言い換え）でも `used_fallback is False` を確認した。
- **朝・0%相当**: 朝フローは `worst.focus_pct > 0` のガードが既にあり、`focus_pct == 0`
  になる状況（睡眠不足がゼロ）を意図的な `FakeHealthSource` で作って
  手動確認したところ `Facts.percents == []` になることを確認済み（既存実装のまま、
  自動テストとしては追加していない。理由: 朝の主要時間帯 focus_pct が正確に0になる
  睡眠パラメータの構成がやや不安定で、既存の `test_llm_success_path_still_passes_after_percents_change`
  が実質的に同じガード条件を継続検証しているため）。

## テストの実行コマンドと出力

```
python -m pytest -q
```

```
........................................................................ [ 19%]
........................................................................ [ 38%]
........................................................................ [ 58%]
........................................................................ [ 77%]
........................................................................ [ 97%]
..........                                                               [100%]
370 passed in 28.46s
```

（346件 → 370件。新規追加24件の内訳: `test_workflow_common.py` 4件、
`test_memory_client.py` 2件、`test_night_flow.py` 6件、`test_morning_flow.py` 5件、
`test_collectors.py` 5件、`test_handlers.py` 1件、既存ファイル内の追加分含む）

## 対応した Minor

- `night.py` / `morning.py` の `_push` / `_retry_key` 完全重複 → `workflow/_common.py` に集約
  （`inspect.signature` の毎回呼び出しも1箇所に統合）。
- `_ROLLUP_MAP` の `next_day_steps` / `next_day_calories`（無駄なAPI呼び出し）→ 削除
  （`DailyVital` に対応フィールドが無く `to_daily_vital` で常に捨てられていたことを
  `vitals.py` で確認済み。テスト `test_rollup_map_no_longer_fetches_unused_next_day_fields`
  で削除を検査）。
- CDK: Webhook Function URL の `CfnOutput` を追加
  （`infra/stacks/sleeptech_stack.py`、`cdk synth` は既存の `test_infra_synth.py` 20件で
  引き続き通過を確認）。

## 見送った Minor とその理由

- **デッドコード削除**（`MemoryClient.query_desc` / `DynamoBackend.query_desc`、
  `timefmt.to_h`、`Settings.night_hour_jst` / `morning_hour_jst`）:
  いずれも専用の既存テスト（`test_memory_client.py::test_query_desc_continues_when_short_page_has_last_key`、
  `test_timefmt.py`、`test_config.py`）があり、削除にはテストの同時修正が必要で
  リスクの割にメリットが小さいと判断し見送った。`MemoryClient.get_vital` は
  指示どおり残した（`get_vitals_desc` とは別に単発取得の用途がありうるため）。
- **無効なテスト**（`test_webhook.py:142-157` の `_texts({"body": ...})` 呼び出し）:
  今回のスコープ（I1〜I5・I7）に直接関係せず、既存の別ファイル
  （`test_handlers.py::test_texts_returns_only_string_texts_from_valid_events`）で
  `_texts` 自体は別途正しく検査されているため見送った。
- **`MemoryClient.mark_notified` のクラス名文字列分岐**: 現状の実装は既に
  `callable(complete)` によるダックタイピングで判定しており（C1修正時に既に
  この形に修正済み）、指摘内容自体が現状のコードと一致しないため対応不要と判断。
- **`DynamoBackend.complete()` の失敗時フォールバックが date/slot を落とす**、
  **TTLがVITALのみ（PREDICT#* / IDEM#* が無期限に増える）**: いずれも本番運用の
  データ量管理に関わる設計判断であり、Global Constraints（Lambda実行時依存やモデル層の
  制約）とは独立の変更で、テストなしに本番動作を変えるリスクがあるため今回は見送った。
- **朝のテンプレート文が低下率0の時間帯も -0% と表示する点**: 仕様上の指摘リストで
  明示的に「余力があれば」の扱いになっており、`fallback.morning_text` の
  全時間帯列挙表示（`  {hour}時台  -{focus_pct}%`）を変えると、既存の
  `test_fallback_text_marks_the_dip_in_the_main_window_not_at_wake_time` 等の
  谷マーク検証と関連する表示ロジックに触れる範囲が広がるため見送った。
  必須値の0%表記問題（I4 本体）は対応済み。

## 懸念点

- `GoogleHealthApiSource` の `deadline` は `ExecutionDeadline`（`llm/client.py`）と
  ダックタイピングで結合している（`can_start(expected_sec)` を持つオブジェクトを渡す
  想定）。型としての明示的な共有インターフェースは定義していないため、将来
  `ExecutionDeadline` のシグネチャを変える場合はここも合わせて確認が必要。
- I1 の HTTP タイムアウトを 4 秒に設定したが、実機（Fitbit Inspire 3 経由）での
  実測レイテンシは未確認。デプロイ前に一度実際のAPIで応答時間を確認し、
  4秒で頻繁にタイムアウトする場合は `HTTP_TIMEOUT_SEC` の調整を検討されたい。
- I3 は実装・単体テストでは v4 形状と決定論性を確認したが、指摘文にある
  「実機1通で確認必須」は本セッションでは実施していない（LINE APIの呼び出し・
  デプロイは指示により対象外のため）。本番投入前に実機で1通送信し、
  400が返らないことを確認することを強く推奨する。
- I5 で保存した `Message` エンティティに TTL は設定していない（`DynamoBackend._item`
  は `pk == "VITAL"` のときだけ TTL を付与する既存仕様のまま）。障害調査用の
  監査ログという性質上は無期限保持で妥当と考えるが、長期運用でのテーブルサイズには
  注意が必要（上記「見送ったMinor」のTTL論点と同根）。
