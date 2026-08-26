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


def test_prediction_round_trip():
    p = Prediction(date="2026-08-26", slot="NIGHT", bedtime_h=23.5, mean_drop_pct=8)
    assert Prediction.from_dict(p.to_dict()) == p


def test_feedback_round_trip():
    f = Feedback(date="2026-08-26", score=4, answered_at="2026-08-26T23:10:00+09:00")
    assert Feedback.from_dict(f.to_dict()) == f
