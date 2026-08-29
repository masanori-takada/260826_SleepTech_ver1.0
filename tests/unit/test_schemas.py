"""永続化エンティティの往復変換。"""

from sleeptech.memory.schemas import DailyVital, Feedback, Prediction


def test_daily_vital_round_trip():
    v = DailyVital(date="2026-08-26", total_sleep_min=330, wake_h=31.0, rem_sleep_min=70)
    assert DailyVital.from_dict(v.to_dict()) == v


def test_daily_vital_ignores_unknown_keys():
    """将来フィールドが増減しても復元が壊れないこと。"""
    got = DailyVital.from_dict({"date": "2026-08-26", "legacy_field": 1})
    assert got.date == "2026-08-26"


def test_to_sleep_input_returns_none_without_sleep():
    assert DailyVital(date="2026-08-26").to_sleep_input() is None


def test_to_sleep_input_derives_bedtime_when_missing():
    """就寝時刻が欠損していれば睡眠時間から逆算する。"""
    got = DailyVital(date="2026-08-26", total_sleep_min=360, wake_h=31.0).to_sleep_input()
    assert got.bedtime_h == 25.0
    assert got.duration_h == 6.0


def test_to_sleep_input_prefers_actual_sleep_over_recorded_bedtime():
    """C3: 中途覚醒で断片化した晩は、記録上の入床時刻（床上時間/TIB）ではなく
    total_sleep_min（実睡眠）から逆算した就寝時刻を予測に使う。
    23:00 就寝・07:00 起床・実睡眠6時間（中途覚醒で2時間覚醒）の例。
    """
    v = DailyVital(
        date="2026-08-26",
        total_sleep_min=360,  # 実睡眠6h
        bedtime_h=23.0,       # 記録上の入床時刻（床上時間なら8h）
        wake_h=31.0,          # 翌7:00
    )
    got = v.to_sleep_input()
    assert got.duration_h == 6.0  # 8.0（床上時間）ではなく実睡眠の6.0であること
    assert got.bedtime_h == 25.0  # wake_h - total_sleep_min/60.0


def test_to_sleep_input_returns_none_when_total_sleep_min_missing_even_with_bedtime():
    """has_sleep は total_sleep_min の存在を要求するため、bedtime_h だけでは
    to_sleep_input は None を返す（total_sleep_min 欠損時の従来ロジックへの
    フォールバック分岐は、has_sleep のゲートにより現状は到達しない防御的分岐）。
    """
    v = DailyVital(date="2026-08-26", total_sleep_min=None, bedtime_h=23.0, wake_h=31.0)
    assert v.has_sleep is False
    assert v.to_sleep_input() is None


def test_prediction_round_trip():
    p = Prediction(date="2026-08-26", slot="NIGHT", bedtime_h=23.5, mean_drop_pct=8)
    assert Prediction.from_dict(p.to_dict()) == p


def test_feedback_round_trip():
    f = Feedback(date="2026-08-26", score=4, answered_at="2026-08-26T23:10:00+09:00")
    assert Feedback.from_dict(f.to_dict()) == f


def test_legacy_feedback_derives_dates():
    feedback = Feedback.from_dict({"date": "2026-08-27", "score": 1})
    assert feedback.effective_evaluated_date == "2026-08-27"
    assert feedback.effective_prediction_date == "2026-08-26"


def test_feedback_preserves_source_event_id_for_idempotency():
    feedback = Feedback(date="2026-08-27", score=1, source_event_id="event-1")

    assert Feedback.from_dict(feedback.to_dict()).source_event_id == "event-1"


def test_prediction_reads_old_record_without_new_fields():
    pred = Prediction.from_dict({"date": "2026-08-26", "slot": "MORNING", "mean_drop_pct": 8})
    assert pred.raw_mean_drop_pct is None
    assert pred.calibrated_mean_drop_pct is None


def test_daily_vital_reads_legacy_heart_rate_as_daily_average():
    vital = DailyVital.from_dict({
        "date": "2026-08-26",
        "night_avg_hr": 61.5,
    })

    assert vital.daily_avg_hr == 61.5
    assert vital.sleep_avg_hr is None
    assert vital.heart_rate_source == "legacy_daily_rollup"
