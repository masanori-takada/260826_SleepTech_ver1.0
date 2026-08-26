"""LLM 障害時のテンプレート文（仕様 第7節）。"""

from sleeptech.guardrail import fallback
from sleeptech.guardrail import validator as v
from sleeptech.memory.schemas import Baseline, BedtimeAdvice, DailyVital, HourlyDrop, RecoveryPlan


BASE = Baseline(wake_h=31.0, sleep_h=7.5, rem_min=90.0, deep_min=60.0, days_used=14, is_default=False)
CURVE = [HourlyDrop(hour=h, focus_pct=p, mood_pct=p + 2) for h, p in ((10, 6), (14, 19), (17, 11))]
ADVICE = BedtimeAdvice(bedtime_h=23.5, mean_drop_pct=8, curve=CURVE, status="ok")
PLAN = RecoveryPlan(caffeine_times_h=[32.0, 37.5], caffeine_cutoff_h=37.5, light_morning_h=31.5, light_avoid_after_h=44.5)
VITAL = DailyVital(date="2026-08-26", total_sleep_min=342, deep_sleep_min=48, rem_sleep_min=70, awakenings=3, wake_h=31.0)


def test_night_text_contains_the_recommended_time():
    assert "23:30" in fallback.night_text(ADVICE, BASE, debt_h=2.3)


def test_night_text_passes_its_own_guardrail():
    """テンプレート文自身がガードレールを通ること。"""
    text = fallback.night_text(ADVICE, BASE, debt_h=2.3)
    facts = v.Facts(times=[23.5], percents=[8])
    assert v.validate(text, facts).passed is True


def test_night_text_mentions_it_is_an_estimate():
    assert "目安" in fallback.night_text(ADVICE, BASE, debt_h=0.0)


def test_night_text_warns_when_baseline_is_default():
    """履歴不足のときは精度が低い旨を含める。"""
    default_base = Baseline(wake_h=31.0, sleep_h=7.5, days_used=1, is_default=True)
    assert "データが少ない" in fallback.night_text(ADVICE, default_base, debt_h=0.0)


def test_night_text_changes_for_unreachable_status():
    """閾値に届かない場合は文面が切り替わる。"""
    unreachable = BedtimeAdvice(bedtime_h=23.0, mean_drop_pct=28, curve=CURVE, status="unreachable")
    assert "何時に寝ても" in fallback.night_text(unreachable, BASE, debt_h=10.0)


def test_night_text_changes_for_too_late_status():
    too_late = BedtimeAdvice(bedtime_h=29.0, mean_drop_pct=25, curve=CURVE, status="too_late")
    assert "今すぐ" in fallback.night_text(too_late, BASE, debt_h=5.0)


def test_morning_text_lists_the_curve_and_recovery():
    got = fallback.morning_text(CURVE, PLAN, VITAL)
    assert "14" in got and "19" in got
    assert "8:00" in got
    assert "☕" in got and "☀" in got


def test_morning_text_handles_no_caffeine():
    """カフェインを勧めない場合でも文が壊れない。"""
    plan = RecoveryPlan(caffeine_times_h=[], caffeine_cutoff_h=30.0, light_morning_h=31.5, light_avoid_after_h=44.5)
    assert "カフェイン" in fallback.morning_text(CURVE, plan, VITAL)
