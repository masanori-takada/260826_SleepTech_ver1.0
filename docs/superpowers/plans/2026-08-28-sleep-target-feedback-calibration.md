# Sleep Target and Feedback Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 慢性的な短時間睡眠を推奨基準にしない就寝時刻、予測幅と信頼度、5段階評価による安全な個人補正、明確なLINE保存返信を実装する。

**Architecture:** 習慣値と7時間以上の目標値を`Baseline`で分離し、純粋な生理モデルの出力へ独立した`calibration`モジュールの上限付き補正を適用する。Webhookは夜質問日と前日朝予測を結び、保存結果をMessaging API replyで返す。元値・補正値・範囲・信頼度をDynamoDBへ併記し、旧レコードを後方互換で読む。

**Tech Stack:** Python 3.12 / pytest / boto3 / AWS CDK Python / DynamoDB / LINE Messaging API / OpenAI Chat Completions API

**Spec:** `docs/superpowers/specs/2026-08-28-sleep-target-feedback-calibration-design.md`

## Global Constraints

- Lambda実行時依存はboto3とPython標準ライブラリのみ。`requests`、`openai`、`numpy`を追加しない。
- `src/sleeptech/model/`は純粋関数のみ。環境変数、現在時刻、boto3へ依存させない。
- LLMに数値、時刻、範囲、信頼度を生成させない。確定値の文章化だけに使う。
- 目標実睡眠時間の既定下限は成人向け`7.0`時間、設定可能範囲は`7.0`〜`9.0`時間。
- 個人補正は直近30件、重み`0.9^i`、縮小`n/(n+7)`、上限`-15`〜`+15`ポイント。
- フィードバックは夜質問日を`evaluated_date`、その前日を朝予測の`prediction_date`として保存する。
- 既存DynamoDB項目を削除しない。旧スキーマを読み取れること。
- コメントとdocstringは日本語で書く。
- 実装担当は`gpt-5.6-luna`・reasoning effort medium、レビュー担当は`gpt-5.6-sol`・reasoning effort medium。
- 各タスクはRED→GREEN→対象テスト→全テスト→レビューの順で完了させる。
- ユーザー所有の未追跡ファイル`docs/SleepTech_開発ストーリー.pptx`へ触れない。

---

## File Structure

| ファイル | 責務 |
|---|---|
| `src/sleeptech/model/calibration.py` | 評価→代理低下率、縮小補正、信頼度、表示範囲の純粋関数 |
| `src/sleeptech/model/baseline.py` | 習慣睡眠と目標睡眠の算出 |
| `src/sleeptech/model/two_process.py` | 目標睡眠を使う負債・基準カーブ・就寝探索 |
| `src/sleeptech/memory/schemas.py` | Baseline、Feedback、Predictionの後方互換スキーマ |
| `src/sleeptech/memory/client.py` | 評価履歴と対応朝予測の取得 |
| `src/sleeptech/workflow/_calibration.py` | DynamoDBレコードを純粋な補正入力へ変換 |
| `src/sleeptech/workflow/night.py` | 補正済み就寝推奨、範囲、信頼度の送信と保存 |
| `src/sleeptech/workflow/morning.py` | 補正済み時間帯カーブ、範囲、信頼度の送信と保存 |
| `src/sleeptech/notifier/line.py` | LINE pushとreplyのHTTPクライアント |
| `src/handlers/webhook_handler.py` | 評価対象日の解決、保存、確認返信 |
| `src/sleeptech/guardrail/*`, `src/sleeptech/llm/prompts.py` | 新しい範囲表示の確定値検証と文章化 |
| `src/sleeptech/config.py`, `infra/stacks/sleeptech_stack.py` | 最低目標睡眠時間の設定 |
| `docs/deploy.md` | LINE自動応答無効化、移行、実機確認手順 |

---

### Task 1: 習慣睡眠と目標睡眠を分離する

**Files:**
- Modify: `src/sleeptech/config.py`
- Modify: `src/sleeptech/memory/schemas.py`
- Modify: `src/sleeptech/model/baseline.py`
- Modify: `src/sleeptech/model/two_process.py`
- Modify: `src/sleeptech/workflow/night.py`
- Modify: `src/sleeptech/workflow/morning.py`
- Modify: `infra/stacks/sleeptech_stack.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_baseline.py`
- Test: `tests/unit/test_recommend_bedtime.py`
- Test: `tests/unit/test_two_process_curve.py`
- Test: `tests/integration/test_night_flow.py`

**Interfaces:**
- Consumes: `Baseline`, `Settings`, `recommend_bedtime()`の既存契約
- Produces:
  - `Settings.min_target_sleep_h: float`
  - `Baseline.target_sleep_h: float | None`
  - `Baseline.effective_target_sleep_h: float`
  - `baseline.compute(recent, override_wake_h=None, min_target_sleep_h=7.0) -> Baseline`
  - `recommend_bedtime(..., calibration_offset_pct=0.0) -> BedtimeAdvice`

- [ ] **Step 1: 設定とBaselineの失敗テストを書く**

```python
def test_min_target_sleep_defaults_to_seven_hours(monkeypatch):
    monkeypatch.delenv("STC_MIN_TARGET_SLEEP_H", raising=False)
    assert load_settings().min_target_sleep_h == 7.0


@pytest.mark.parametrize("raw", ["6.9", "9.1", "abc"])
def test_min_target_sleep_rejects_out_of_range(monkeypatch, raw):
    monkeypatch.setenv("STC_MIN_TARGET_SLEEP_H", raw)
    with pytest.raises(ValueError):
        load_settings()


def test_short_habit_is_not_used_as_target():
    recent = [_sleep(hours=5.4, wake_h=32.68) for _ in range(13)]
    got = baseline.compute(recent, min_target_sleep_h=7.0)
    assert got.sleep_h == 5.4
    assert got.target_sleep_h == 7.0
    assert got.effective_target_sleep_h == 7.0
```

- [ ] **Step 2: REDを確認する**

Run: `python -m pytest tests/unit/test_config.py tests/unit/test_baseline.py -v`

Expected: `min_target_sleep_h`、`target_sleep_h`、`effective_target_sleep_h`が未定義でFAIL。

- [ ] **Step 3: 設定とBaselineを最小実装する**

```python
def _env_float_in_range(name: str, default: float, low: float, high: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value
```

`Settings`へ`min_target_sleep_h`を追加し、`load_settings()`では次を使う。

```python
min_target_sleep_h=_env_float_in_range("STC_MIN_TARGET_SLEEP_H", 7.0, 7.0, 9.0)
```

`Baseline`末尾へ後方互換フィールドとプロパティを追加する。

```python
target_sleep_h: float | None = None

@property
def effective_target_sleep_h(self) -> float:
    return self.target_sleep_h if self.target_sleep_h is not None else self.sleep_h
```

`baseline.compute()`では履歴十分時に次を設定する。

```python
target_sleep_h = min(9.0, max(float(sleep_h), min_target_sleep_h))
```

履歴不足時は`DEFAULT_SLEEP_H`を`target_sleep_h`にも使う。

- [ ] **Step 4: 目標睡眠を使うモデルの失敗テストを書く**

```python
def test_recommendation_never_sacrifices_seven_hour_target():
    base = Baseline(
        wake_h=32.68, sleep_h=5.4, rem_min=32, deep_min=80,
        days_used=13, is_default=False, target_sleep_h=7.0,
    )
    got = recommend_bedtime(22.5, base, debt_h=0.561)
    assert got.bedtime_h <= 25.68 + 0.084
    assert got.bedtime_h < 27.25


def test_sleep_debt_uses_target_not_short_habit():
    base = Baseline(wake_h=31, sleep_h=5.5, target_sleep_h=7.0)
    assert sleep_debt_h([_sleep(5.5)], base) == 1.5


def test_ideal_curve_uses_target_sleep():
    base = Baseline(wake_h=31, sleep_h=5.5, target_sleep_h=7.0)
    curve = predict_curve(_sleep(5.5), base, debt_h=0)
    assert any(point.focus_pct > 0 for point in curve)
```

- [ ] **Step 5: REDを確認する**

Run: `python -m pytest tests/unit/test_recommend_bedtime.py tests/unit/test_two_process_curve.py -v`

Expected: 現行コードが5.4〜5.5時間を理想として扱うためFAIL。

- [ ] **Step 6: 目標睡眠をモデルへ適用する**

次の3箇所を`baseline.effective_target_sleep_h`へ変更する。

```python
deficit = max(0.0, baseline.effective_target_sleep_h - s.duration_h)
ideal_bedtime_h = sleep.wake_h - baseline.effective_target_sleep_h
comfortable_h = baseline.wake_h - baseline.effective_target_sleep_h
```

`recommend_bedtime()`の候補上限は次の小さい方とする。

```python
limit_h = min(
    baseline.wake_h - C.MIN_SLEEP_BEFORE_WAKE_H,
    baseline.wake_h - baseline.effective_target_sleep_h,
)
```

夜・朝の`baseline.compute()`へ`deps.settings.min_target_sleep_h`を渡す。CDK環境へ`"STC_MIN_TARGET_SLEEP_H": "7.0"`を追加する。

- [ ] **Step 7: Task 1の対象テストを通す**

Run: `python -m pytest tests/unit/test_config.py tests/unit/test_baseline.py tests/unit/test_recommend_bedtime.py tests/unit/test_two_process_curve.py tests/integration/test_night_flow.py tests/integration/test_morning_flow.py -v`

Expected: PASS。

- [ ] **Step 8: 全テストと静的検査を行う**

Run: `python -m pytest -q`

Run: `python -m compileall -q src infra`

Run: `git diff --check`

Expected: すべてexit 0。

- [ ] **Step 9: コミットする**

```powershell
git add -- src/sleeptech/config.py src/sleeptech/memory/schemas.py src/sleeptech/model/baseline.py src/sleeptech/model/two_process.py src/sleeptech/workflow/night.py src/sleeptech/workflow/morning.py infra/stacks/sleeptech_stack.py tests/unit/test_config.py tests/unit/test_baseline.py tests/unit/test_recommend_bedtime.py tests/unit/test_two_process_curve.py tests/integration/test_night_flow.py tests/integration/test_morning_flow.py
git commit -m "fix: 短時間睡眠を就寝目標として扱わない"
```

---

### Task 2: フィードバック補正と後方互換スキーマを実装する

**Files:**
- Create: `src/sleeptech/model/calibration.py`
- Create: `src/sleeptech/workflow/_calibration.py`
- Modify: `src/sleeptech/memory/schemas.py`
- Modify: `src/sleeptech/memory/client.py`
- Test: `tests/unit/test_calibration.py`
- Test: `tests/unit/test_memory_client.py`
- Test: `tests/unit/test_schemas.py`
- Test: `tests/unit/test_workflow_calibration.py`

**Interfaces:**
- Consumes: `Feedback`, `Prediction`, `MemoryClient.get_prediction()`
- Produces:
  - `Calibration(offset_pct: float, feedback_count: int, confidence: str, margin_pct: int)`
  - `score_to_drop_pct(score: int) -> float`
  - `fit_offset(pairs: list[tuple[int, float]]) -> float`
  - `confidence_for(sleep_days: int, feedback_count: int) -> tuple[str, int]`
  - `apply_offset(value: float, offset_pct: float) -> int`
  - `display_range(value: int, margin_pct: int) -> tuple[int, int]`
  - `load_calibration(memory, sleep_days: int) -> Calibration`
  - `MemoryClient.get_feedback_desc(limit: int) -> list[Feedback]`

- [ ] **Step 1: 純粋な補正関数の失敗テストを書く**

```python
@pytest.mark.parametrize("score,want", [(5, 0), (4, 7.5), (3, 15), (2, 22.5), (1, 30)])
def test_score_mapping(score, want):
    assert score_to_drop_pct(score) == want


def test_one_feedback_is_shrunk_but_nonzero():
    assert fit_offset([(1, 5.0)]) == pytest.approx(3.125, abs=0.001)


def test_offset_is_capped_both_directions():
    assert fit_offset([(1, 0.0)] * 30) == 15.0
    assert fit_offset([(5, 100.0)] * 30) == -15.0


@pytest.mark.parametrize(
    "sleep_days,count,want",
    [(6, 30, ("low", 5)), (14, 2, ("low", 5)),
     (14, 7, ("medium", 3)), (14, 30, ("high", 2))],
)
def test_confidence(sleep_days, count, want):
    assert confidence_for(sleep_days, count) == want


def test_display_range_clamps_to_zero_and_hundred():
    assert display_range(3, 5) == (0, 8)
    assert display_range(98, 5) == (93, 100)
```

- [ ] **Step 2: REDを確認する**

Run: `python -m pytest tests/unit/test_calibration.py -v`

Expected: `sleeptech.model.calibration`が存在せずFAIL。

- [ ] **Step 3: `calibration.py`を実装する**

```python
SCORE_DROP_PCT = {1: 30.0, 2: 22.5, 3: 15.0, 4: 7.5, 5: 0.0}
MAX_FEEDBACK = 30
RECENCY = 0.9
SHRINK_K = 7.0
MAX_OFFSET = 15.0


@dataclass(frozen=True)
class Calibration:
    offset_pct: float
    feedback_count: int
    confidence: str
    margin_pct: int


def fit_offset(pairs: list[tuple[int, float]]) -> float:
    selected = pairs[:MAX_FEEDBACK]
    if not selected:
        return 0.0
    weighted = [(score_to_drop_pct(score) - predicted) * (RECENCY ** i)
                for i, (score, predicted) in enumerate(selected)]
    weights = [RECENCY ** i for i in range(len(selected))]
    shrunk = (sum(weighted) / sum(weights)) * len(selected) / (len(selected) + SHRINK_K)
    return round(max(-MAX_OFFSET, min(MAX_OFFSET, shrunk)), 3)
```

`apply_offset()`は0〜100へクランプしてroundし、`display_range()`も0〜100へクランプする。

- [ ] **Step 4: スキーマと履歴取得の失敗テストを書く**

```python
def test_legacy_feedback_derives_dates():
    feedback = Feedback.from_dict({"date": "2026-08-27", "score": 1})
    assert feedback.effective_evaluated_date == "2026-08-27"
    assert feedback.effective_prediction_date == "2026-08-26"


def test_prediction_reads_old_record_without_new_fields():
    pred = Prediction.from_dict({"date": "2026-08-26", "slot": "MORNING", "mean_drop_pct": 8})
    assert pred.raw_mean_drop_pct is None
    assert pred.calibrated_mean_drop_pct is None


def test_feedback_desc_is_newest_first_and_limited():
    memory = MemoryClient(InMemoryBackend())
    for day in ("2026-08-25", "2026-08-26", "2026-08-27"):
        memory.save_feedback(Feedback(date=day, score=3))
    assert [f.date for f in memory.get_feedback_desc(2)] == ["2026-08-27", "2026-08-26"]
```

- [ ] **Step 5: REDを確認する**

Run: `python -m pytest tests/unit/test_schemas.py tests/unit/test_memory_client.py -v`

Expected: 新規フィールドと`get_feedback_desc()`がなくFAIL。

- [ ] **Step 6: 後方互換スキーマと取得を実装する**

`Feedback`末尾へ追加する。

```python
evaluated_date: str | None = None
prediction_date: str | None = None

@property
def effective_evaluated_date(self) -> str:
    return self.evaluated_date or self.date

@property
def effective_prediction_date(self) -> str:
    if self.prediction_date:
        return self.prediction_date
    return (date_cls.fromisoformat(self.date) - timedelta(days=1)).isoformat()
```

`Prediction`末尾へ次を追加する。

```python
raw_mean_drop_pct: float | None = None
calibrated_mean_drop_pct: float | None = None
range_low_pct: int | None = None
range_high_pct: int | None = None
confidence: str | None = None
feedback_count: int = 0
calibration_offset_pct: float = 0.0
```

`MemoryClient.get_feedback_desc()`は`query_desc(PK_FEEDBACK, limit)`を利用する。

- [ ] **Step 7: レコード結合の失敗テストを書く**

```python
def test_load_calibration_uses_only_matching_morning_predictions():
    memory = MemoryClient(InMemoryBackend())
    memory.save_prediction(Prediction(date="2026-08-26", slot="MORNING", mean_drop_pct=5))
    memory.save_feedback(Feedback(
        date="2026-08-27", evaluated_date="2026-08-27",
        prediction_date="2026-08-26", score=1,
    ))
    got = load_calibration(memory, sleep_days=14)
    assert got.feedback_count == 1
    assert got.offset_pct == pytest.approx(3.125, abs=0.001)
    assert got.confidence == "low"


def test_load_calibration_ignores_missing_prediction():
    memory = MemoryClient(InMemoryBackend())
    memory.save_feedback(Feedback(date="2026-08-27", score=1))
    assert load_calibration(memory, sleep_days=14).feedback_count == 0
```

- [ ] **Step 8: REDを確認する**

Run: `python -m pytest tests/unit/test_workflow_calibration.py -v`

Expected: `_calibration`が存在せずFAIL。

- [ ] **Step 9: `_calibration.py`を実装する**

```python
def load_calibration(memory, sleep_days: int) -> Calibration:
    pairs = []
    for feedback in memory.get_feedback_desc(MAX_FEEDBACK):
        prediction = memory.get_prediction(feedback.effective_prediction_date, "MORNING")
        if prediction is None:
            continue
        predicted = (prediction.calibrated_mean_drop_pct
                     if prediction.calibrated_mean_drop_pct is not None
                     else prediction.mean_drop_pct)
        if not 1 <= feedback.score <= 5 or not 0 <= predicted <= 100:
            continue
        pairs.append((feedback.score, float(predicted)))
    confidence, margin = confidence_for(sleep_days, len(pairs))
    return Calibration(fit_offset(pairs), len(pairs), confidence, margin)
```

- [ ] **Step 10: Task 2の対象テストと全テストを通す**

Run: `python -m pytest tests/unit/test_calibration.py tests/unit/test_schemas.py tests/unit/test_memory_client.py tests/unit/test_workflow_calibration.py -v`

Run: `python -m pytest -q`

Run: `python -m compileall -q src infra`

Run: `git diff --check`

Expected: すべてexit 0。

- [ ] **Step 11: コミットする**

```powershell
git add -- src/sleeptech/model/calibration.py src/sleeptech/workflow/_calibration.py src/sleeptech/memory/schemas.py src/sleeptech/memory/client.py tests/unit/test_calibration.py tests/unit/test_schemas.py tests/unit/test_memory_client.py tests/unit/test_workflow_calibration.py
git commit -m "feat: 5段階評価による縮小補正を追加"
```

---

### Task 3: 夜・朝を予測幅と信頼度表示へ変更する

**Files:**
- Modify: `src/sleeptech/memory/schemas.py`
- Modify: `src/sleeptech/model/two_process.py`
- Modify: `src/sleeptech/guardrail/validator.py`
- Modify: `src/sleeptech/guardrail/fallback.py`
- Modify: `src/sleeptech/llm/prompts.py`
- Modify: `src/sleeptech/workflow/night.py`
- Modify: `src/sleeptech/workflow/morning.py`
- Test: `tests/unit/test_guardrail.py`
- Test: `tests/unit/test_fallback.py`
- Test: `tests/unit/test_llm_prompts.py`
- Test: `tests/integration/test_night_flow.py`
- Test: `tests/integration/test_morning_flow.py`

**Interfaces:**
- Consumes: Task 1の`target_sleep_h`、Task 2の`Calibration`と`load_calibration()`
- Produces:
  - `BedtimeAdvice.raw_mean_drop_pct: float | None`
  - 夜・朝の範囲表示
  - 補正前後、範囲、信頼度、評価件数を含む`Prediction`

- [ ] **Step 1: 補正済み就寝探索の失敗テストを書く**

```python
def test_positive_calibration_offset_makes_recommendation_no_later():
    base = Baseline(wake_h=31, sleep_h=7, target_sleep_h=7)
    raw = recommend_bedtime(22.5, base, debt_h=0, calibration_offset_pct=0)
    corrected = recommend_bedtime(22.5, base, debt_h=0, calibration_offset_pct=8)
    assert corrected.bedtime_h <= raw.bedtime_h
    assert corrected.mean_drop_pct >= corrected.raw_mean_drop_pct
```

- [ ] **Step 2: REDを確認して最小実装する**

Run: `python -m pytest tests/unit/test_recommend_bedtime.py::test_positive_calibration_offset_makes_recommendation_no_later -v`

Expected: 新規引数・フィールドがなくFAIL。

`BedtimeAdvice`末尾へ`raw_mean_drop_pct: float | None = None`を追加し、候補評価を次へ変更する。

```python
raw_mean = mean_drop_in_main_window(curve, baseline.wake_h)
corrected_mean = max(0.0, min(100.0, raw_mean + calibration_offset_pct))
return curve, raw_mean, corrected_mean
```

探索判定には`corrected_mean`、保存には`raw_mean`と`ceil(corrected_mean)`を使う。

- [ ] **Step 3: 新しい文面の失敗テストを書く**

```python
def test_night_text_explains_sleep_target_range_and_confidence():
    text = night_text(advice, baseline, debt_h=1.0, range_low=3, range_high=13,
                      confidence="low", feedback_count=2)
    assert "7時間" in text
    assert "3〜13%" in text
    assert "信頼度：低" in text
    assert "評価データ2件" in text
    assert "平常時100%" not in text
    assert "目標を満たせます" not in text


def test_morning_text_uses_ranges_not_single_percent():
    text = morning_text(curve, ranges, plan, vital, "low", 2)
    assert "7時台" in text
    assert "3〜13%" in text
    assert "実測の仕事能力ではありません" in text
    assert "中途覚醒・HRV・心拍数は現行の%計算には使っていません" in text
    assert "-8%" not in text
```

- [ ] **Step 4: REDを確認する**

Run: `python -m pytest tests/unit/test_fallback.py tests/unit/test_llm_prompts.py -v`

Expected: 新規引数と文面が未実装でFAIL。

- [ ] **Step 5: テンプレートとプロンプトを実装する**

夜の確定文面は最低限次を含める。

```text
起床目安 {wake}・{target_sleep_h:.1f}時間の睡眠を確保するなら、遅くとも{bedtime}までに就寝。
明日の主要時間帯の低下見込みは{low}〜{high}%です。
信頼度：{label}（評価データ{count}件）
※睡眠モデルと主観評価による推定幅で、実測の仕事能力ではありません。
※中途覚醒・HRV・心拍数は現行の%計算には使っていません。
```

朝の各行を次の形式にする。

```text
{hour}時台　低下見込み {low}〜{high}%
```

`NIGHT_SYSTEM`と`MORNING_SYSTEM`へ「範囲の両端と信頼度を変更・省略しない」を追加する。

- [ ] **Step 6: ガードレールの失敗テストを書く**

```python
def test_range_endpoints_are_required_and_extra_percent_is_rejected():
    facts = Facts(times=[25.5], percents=[3, 13], allowed_percents=[3, 13])
    assert validate("翌 1:30、低下見込み3〜13%。推定の目安です", facts).passed
    assert not validate("翌 1:30、低下見込み3〜18%。推定の目安です", facts).passed
```

- [ ] **Step 7: REDを確認してガードレールを調整する**

Run: `python -m pytest tests/unit/test_guardrail.py -v`

Expected: 範囲表現の符号・定義注記が現行規則と合わずFAIL。

`validator.py`では「低下見込み」に直結する符号なし百分率を許可し、`Facts.percents`に下限・上限を入れて過不足を検査する。`100%`を平常時定義として使う既存注記依存を外し、新しい推定注記を必須化する。

- [ ] **Step 8: 夜・朝ワークフローの失敗統合テストを書く**

```python
def test_night_saves_raw_calibrated_range_and_confidence():
    result = night.run_night(DATE, NOW_H, deps_with_feedback_score_1())
    pred = memory.get_prediction(DATE, "NIGHT")
    assert result["range_low_pct"] <= result["range_high_pct"]
    assert pred.raw_mean_drop_pct is not None
    assert pred.calibrated_mean_drop_pct == pred.mean_drop_pct
    assert pred.confidence == "low"
    assert pred.feedback_count == 1


def test_morning_applies_offset_only_to_focus_curve():
    result = morning.run_morning(DATE, deps_with_feedback_score_1())
    pred = memory.get_prediction(DATE, "MORNING")
    assert pred.calibration_offset_pct > 0
    assert pred.range_low_pct <= pred.mean_drop_pct <= pred.range_high_pct
```

- [ ] **Step 9: REDを確認する**

Run: `python -m pytest tests/integration/test_night_flow.py tests/integration/test_morning_flow.py -v`

Expected: 補正・範囲フィールドが保存されずFAIL。

- [ ] **Step 10: ワークフローへ補正と保存を組み込む**

両フローでベースライン確定後に次を行う。

```python
cal = load_calibration(deps.memory, baseline.days_used)
```

夜は`recommend_bedtime(..., calibration_offset_pct=cal.offset_pct)`を呼び、中心値から`display_range()`を作る。朝は生の`focus_pct`各値に`apply_offset()`し、各時刻の範囲を作る。`Prediction`へTask 2の全監査フィールドを保存し、ログへ`offset`、`feedback_count`、`confidence`を出す。

- [ ] **Step 11: Task 3の対象テストと全テストを通す**

Run: `python -m pytest tests/unit/test_recommend_bedtime.py tests/unit/test_guardrail.py tests/unit/test_fallback.py tests/unit/test_llm_prompts.py tests/integration/test_night_flow.py tests/integration/test_morning_flow.py -v`

Run: `python -m pytest -q`

Run: `python -m compileall -q src infra`

Run: `git diff --check`

Expected: すべてexit 0。

- [ ] **Step 12: コミットする**

```powershell
git add -- src/sleeptech/memory/schemas.py src/sleeptech/model/two_process.py src/sleeptech/guardrail/validator.py src/sleeptech/guardrail/fallback.py src/sleeptech/llm/prompts.py src/sleeptech/workflow/night.py src/sleeptech/workflow/morning.py tests/unit/test_recommend_bedtime.py tests/unit/test_guardrail.py tests/unit/test_fallback.py tests/unit/test_llm_prompts.py tests/integration/test_night_flow.py tests/integration/test_morning_flow.py
git commit -m "feat: 予測を範囲と信頼度で表示"
```

---

### Task 4: 評価を正しい朝予測へ紐付けてLINE確認返信を返す

**Files:**
- Modify: `src/sleeptech/notifier/line.py`
- Modify: `src/handlers/webhook_handler.py`
- Modify: `src/handlers/deps.py`
- Test: `tests/unit/test_line.py`
- Test: `tests/integration/test_webhook.py`
- Test: `tests/unit/test_handlers.py`

**Interfaces:**
- Consumes: Task 2の`Feedback.evaluated_date/prediction_date`
- Produces:
  - `LinePushClient.reply(reply_token: str, message: dict) -> None`
  - `FeedbackTarget(evaluated_date: str, prediction_date: str)`
  - `resolve_feedback_target(memory, today: str) -> FeedbackTarget | None`

- [ ] **Step 1: LINE replyクライアントの失敗テストを書く**

```python
def test_reply_uses_reply_endpoint_and_token():
    calls = []
    client = LinePushClient("token", "U1", http_post=lambda u, h, b: calls.append((u, h, b)))
    client.reply("reply-token", {"type": "text", "text": "記録しました"})
    assert calls[0][0] == "https://api.line.me/v2/bot/message/reply"
    assert calls[0][2] == {"replyToken": "reply-token", "messages": [
        {"type": "text", "text": "記録しました"}
    ]}
```

- [ ] **Step 2: REDを確認してreplyを実装する**

Run: `python -m pytest tests/unit/test_line.py::test_reply_uses_reply_endpoint_and_token -v`

Expected: `reply()`がなくFAIL。

```python
REPLY_URL = "https://api.line.me/v2/bot/message/reply"

def reply(self, reply_token: str, message: dict) -> None:
    headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
    self._post(REPLY_URL, headers, {"replyToken": reply_token, "messages": [message]})
```

- [ ] **Step 3: 正しい日付紐付けの失敗テストを書く**

```python
def test_feedback_targets_previous_origin_morning_prediction():
    memory = MemoryClient(InMemoryBackend())
    memory.save_message(Message(date="2026-08-27", slot="NIGHT", delivered=True))
    memory.save_prediction(Prediction(date="2026-08-26", slot="MORNING", mean_drop_pct=8))
    got = resolve_feedback_target(memory, today="2026-08-27")
    assert got.evaluated_date == "2026-08-27"
    assert got.prediction_date == "2026-08-26"


def test_feedback_never_targets_same_day_night_prediction():
    memory = MemoryClient(InMemoryBackend())
    memory.save_prediction(Prediction(date="2026-08-27", slot="NIGHT"))
    assert resolve_feedback_target(memory, today="2026-08-27") is None
```

- [ ] **Step 4: REDを確認して解決関数を実装する**

Run: `python -m pytest tests/integration/test_webhook.py -v`

Expected: 現行`resolve_feedback_date()`が`PREDICT#NIGHT`を返すためFAIL。

```python
@dataclass(frozen=True)
class FeedbackTarget:
    evaluated_date: str
    prediction_date: str


def resolve_feedback_target(memory, today: str) -> FeedbackTarget | None:
    base = date_cls.fromisoformat(today)
    for offset in range(2):
        evaluated = base - timedelta(days=offset)
        prediction = evaluated - timedelta(days=1)
        evaluated_s, prediction_s = evaluated.isoformat(), prediction.isoformat()
        if (memory.get_message(evaluated_s, "NIGHT")
                and memory.get_prediction(prediction_s, "MORNING")
                and memory.get_feedback(evaluated_s) is None):
            return FeedbackTarget(evaluated_s, prediction_s)
    return None
```

- [ ] **Step 5: Webhook保存返信の失敗テストを書く**

LINEイベントfixtureへ`replyToken`を含め、次を検証する。

```python
def test_feedback_is_saved_and_acknowledged_once(monkeypatch):
    response = wh.handler(signed_event("評価:1", reply_token="r1"))
    saved = memory.get_feedback("2026-08-27")
    assert response["statusCode"] == 200
    assert saved.score == 1
    assert saved.evaluated_date == "2026-08-27"
    assert saved.prediction_date == "2026-08-26"
    assert replies == [("r1", "評価1を記録しました。次回以降の予測補正に反映します。")]


def test_unmatched_feedback_gets_clear_reply_without_save(monkeypatch):
    response = wh.handler(signed_event("評価:3", reply_token="r2"))
    assert response["statusCode"] == 200
    assert memory.get_feedback("2026-08-27") is None
    assert "紐付けられませんでした" in replies[0][1]
```

- [ ] **Step 6: REDを確認する**

Run: `python -m pytest tests/integration/test_webhook.py tests/unit/test_handlers.py -v`

Expected: replyTokenを保持せず、返信もしないためFAIL。

- [ ] **Step 7: イベント抽出・保存・返信を実装する**

認可済みイベントは本文だけでなく次を保持する。

```python
@dataclass(frozen=True)
class AuthorizedText:
    text: str
    reply_token: str
```

評価保存時は次を作る。

```python
Feedback(
    date=target.evaluated_date,
    evaluated_date=target.evaluated_date,
    prediction_date=target.prediction_date,
    score=score,
    answered_at=now.isoformat(timespec="seconds"),
)
```

保存後に`LinePushClient.reply()`を呼ぶ。返信失敗は`feedback_reply_failed`へ記録して200を返し、保存済み評価を再処理しない。`deps.py`には秘密値を再取得せず既存LINE secret dataからクライアントを構築する小さなヘルパーを追加する。

- [ ] **Step 8: Task 4の対象テストと全テストを通す**

Run: `python -m pytest tests/unit/test_line.py tests/integration/test_webhook.py tests/unit/test_handlers.py -v`

Run: `python -m pytest -q`

Run: `python -m compileall -q src infra`

Run: `git diff --check`

Expected: すべてexit 0。

- [ ] **Step 9: コミットする**

```powershell
git add -- src/sleeptech/notifier/line.py src/handlers/webhook_handler.py src/handlers/deps.py tests/unit/test_line.py tests/integration/test_webhook.py tests/unit/test_handlers.py
git commit -m "fix: 評価を朝予測へ紐付け保存結果を返信"
```

---

### Task 5: 移行手順・LINE設定・本番検証を完成させる

**Files:**
- Modify: `docs/deploy.md`
- Modify: `tests/unit/test_infra_synth.py`

**Interfaces:**
- Consumes: Tasks 1〜4の全機能
- Produces: 再現可能な移行・LINE設定・ロールバック手順、本番検証結果

- [ ] **Step 1: CDK設定の失敗テストを書く**

```python
def test_all_runtime_functions_receive_min_target_sleep_hours(template):
    functions = template.find_resources("AWS::Lambda::Function")
    runtime_functions = [r for r in functions.values()
                         if r["Properties"]["Handler"] != "src.handlers.backfill_handler.handler"]
    assert all(r["Properties"]["Environment"]["Variables"]["STC_MIN_TARGET_SLEEP_H"] == "7.0"
               for r in runtime_functions)
```

- [ ] **Step 2: REDまたは既存Task 1実装によるGREENを確認する**

Run: `python -m pytest tests/unit/test_infra_synth.py -v`

Expected: Task 1でCDK環境を正しく追加済みならPASS。未追加ならFAILして修正する。

- [ ] **Step 3: `docs/deploy.md`へ正確な運用手順を書く**

次を具体的に記載する。

```text
1. LINE Developers > Messaging API設定 > 応答メッセージ > 編集
2. LINE Official Account Managerで応答メッセージをオフ
3. Chatの応答方法が自動文を返さないことを確認
4. Webhook利用はオンのまま維持
5. 評価ボタンを押し、SleepTechの「評価Nを記録しました」だけが返ることを確認
```

既存評価補完は削除なし・条件付き更新とし、実行前後に`FEEDBACK`件数を照合する。ロールバックはLambdaコードを前版へ戻しても旧フィールドを無視して読めるため、DynamoDBレコードは戻さない。

- [ ] **Step 4: ドキュメント検査と全テストを行う**

Run: `rg -n "応答メッセージ|STC_MIN_TARGET_SLEEP_H|prediction_date|ロールバック" docs/deploy.md`

Run: `python -m pytest -q`

Run: `python -m compileall -q src infra`

Run: `git diff --check`

Expected: 必須語が見つかり、全コマンドexit 0。

- [ ] **Step 5: コミットする**

```powershell
git add -- docs/deploy.md tests/unit/test_infra_synth.py
git commit -m "docs: 評価補正の移行とLINE応答設定を追加"
```

- [ ] **Step 6: 最終Solレビューを実施する**

レビュー範囲は仕様コミット`1df4458`以降の全差分。次を必須確認項目とする。

```text
- 7時間未満を目標として推奨する経路が残っていない
- 評価日と朝予測起点日の1日差が全境界で正しい
- 補正が二重適用されない
- 少数データ縮小と±15上限が正しい
- 範囲表示をLLMが変更できない
- reply失敗で評価が失われず、Webhook再試行で重複返信しにくい
- 旧DynamoDBレコードが読める
```

Expected: Critical/Important指摘ゼロ。指摘があれば同じLuna実装担当へ戻し、Solで再レビューする。

- [ ] **Step 7: デプロイ前検証を行う**

Run: `python -m pytest -q`

Run: `python -m compileall -q src infra`

Run: `cd infra; cdk synth --strict`

Run: `cd infra; cdk diff SleepTechStack --fail false`

Expected: テスト失敗ゼロ、synth成功、差分はLambdaコードと環境変数更新のみ。既存リソースの削除・置換なし。

- [ ] **Step 8: AWSへデプロイする**

Run: `cd infra; cdk deploy SleepTechStack --require-approval never`

Expected: `SleepTechStack UPDATE_COMPLETE`。

- [ ] **Step 9: 既存フィードバックを安全に補完する**

AWS CLIで`PK=FEEDBACK`を強整合Queryし、次の確認済み2件と一致することを再確認する。

```text
2026-08-26: score=3, answered_at=2026-08-26T23:32:24+09:00, prediction_date=2026-08-25
2026-08-27: score=1, answered_at=2026-08-27T23:47:52+09:00, prediction_date=2026-08-26
```

対応する`PREDICT#MORNING/2026-08-25`と`PREDICT#MORNING/2026-08-26`の存在も強整合BatchGetで再確認する。各FEEDBACK項目はキーを変えず、JSONを次の完全形へ置換する。

```json
{"date":"2026-08-26","score":3,"answered_at":"2026-08-26T23:32:24+09:00","evaluated_date":"2026-08-26","prediction_date":"2026-08-25"}
{"date":"2026-08-27","score":1,"answered_at":"2026-08-27T23:47:52+09:00","evaluated_date":"2026-08-27","prediction_date":"2026-08-26"}
```

更新は`UpdateExpression: SET #json = :new`、`ConditionExpression: #json = :old`を使い、現在のJSONが上記確認時の旧値と完全一致する場合だけ行う。片方でも条件失敗した場合は更新を重ねず、再Queryして差異を調査する。秘密値は読み取らない。

Expected: 更新前件数=更新後件数、削除0、対応予測ありの項目だけ補完。

- [ ] **Step 10: LINE公式アカウントの自動応答を無効化する**

既存ログイン状態のLINE Official Account Managerを使用し、「応答メッセージ」をオフ、Webhookをオンのまま保存する。設定変更後に画面状態を再取得して確認する。

Expected: 汎用の「個別のお問い合わせを受け付けておりません」が返らない。

- [ ] **Step 11: 実機評価フローを確認する**

夜通知または署名済み実機クイックリプライから評価を1回送る。

Expected:

```text
- LINE: 「評価Nを記録しました。次回以降の予測補正に反映します。」が1回だけ届く
- DynamoDB: FEEDBACK.evaluated_dateとprediction_dateが正しい
- CloudWatch: feedback_savedとfeedback_reply_sent
- 汎用自動応答: 0件
```

- [ ] **Step 12: 夜・朝を実機確認する**

NightFnを現在の起点日で1回、MorningFnはテスト用の未送信日または安全な検証経路で1回実行する。既存`SENT`項目を削除しない。

Expected:

```text
- 夜: 3:15を推奨せず、起床目安・7時間・予測幅・信頼度を表示
- 朝: 各時間帯を単一値でなく範囲表示
- DynamoDB: raw/calibrated/range/confidence/feedback_countを保存
- CloudWatch: calibration offsetと利用件数を記録
- Scheduler: 夜22:30、朝8:00、双方ENABLED
```

---

## Final Verification Checklist

- [ ] 仕様書の全受け入れ条件をTask 1〜5へ対応付けて確認した
- [ ] 全pytestがfresh runで成功した
- [ ] `compileall`、`git diff --check`、`cdk synth --strict`が成功した
- [ ] CDK差分に削除・置換がない
- [ ] Sol最終レビューのCritical/Importantがゼロ
- [ ] 既存フィードバック件数が減っていない
- [ ] LINE汎用自動応答が停止し、SleepTech確認返信だけが届く
- [ ] 夜の推奨が最低7時間を確保する
- [ ] 朝・夜とも予測幅、信頼度、非実測注記を表示する
- [ ] Schedulerが2本ともENABLED
