"""LLM 障害時のテンプレート文（仕様 第7節）。"""

from sleeptech.guardrail import fallback
from sleeptech.guardrail import validator as v
from sleeptech.memory.schemas import (
    Baseline,
    BedtimeAdvice,
    DailyVital,
    HourlyDrop,
    RecoveryPlan,
    SleepInput,
)
from sleeptech.model import two_process as tp


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
    assert "23:30" in text


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


def test_night_text_too_late_reports_available_sleep_instead_of_securing_target():
    advice = BedtimeAdvice(
        bedtime_h=26.0, mean_drop_pct=25, curve=CURVE, status="too_late",
        available_sleep_h=6 + 40 / 60,
    )
    text = fallback.night_text(advice, Baseline(wake_h=32 + 40 / 60, sleep_h=7.0, target_sleep_h=7.0), debt_h=5.0)
    assert "目標時刻は過ぎています" in text
    assert "6時間40分" in text
    assert "7.0時間を確保" not in text


def test_night_text_too_late_with_zero_drop_avoids_minus_zero_percent():
    """M1: too_late でも mean_drop_pct=0 なら「-0%」を出さない。"""
    too_late_zero = BedtimeAdvice(bedtime_h=31.0, mean_drop_pct=0, curve=CURVE, status="too_late")
    text = fallback.night_text(too_late_zero, BASE, debt_h=3.5)
    assert "-0%" not in text
    assert "今すぐ" in text


def test_night_text_unreachable_with_zero_drop_avoids_minus_zero_percent():
    """M1: unreachable でも mean_drop_pct=0 なら「-0%」を出さない。"""
    unreachable_zero = BedtimeAdvice(bedtime_h=27.5, mean_drop_pct=0, curve=CURVE, status="unreachable")
    text = fallback.night_text(unreachable_zero, BASE, debt_h=3.5)
    assert "-0%" not in text
    assert "何時に寝ても" in text


def test_night_text_uses_day_prefix_for_next_day_bedtime():
    advice = BedtimeAdvice(bedtime_h=24.5, mean_drop_pct=8, curve=CURVE, status="ok")
    assert "翌 0:30" in fallback.night_text(advice, BASE, debt_h=0.0)


def test_morning_text_lists_the_curve_and_recovery():
    got = fallback.morning_text(CURVE, PLAN, VITAL)
    assert "14" in got and "19" in got
    assert "8:00" in got
    assert "☕" in got and "☀" in got


def test_morning_text_handles_no_caffeine():
    """カフェインを勧めない場合でも文が壊れない。"""
    plan = RecoveryPlan(caffeine_times_h=[], caffeine_cutoff_h=30.0, light_morning_h=31.5, light_avoid_after_h=44.5)
    assert "カフェイン" in fallback.morning_text(CURVE, plan, VITAL)


# --- 「← 谷」は主要時間帯（起床+2h〜+10h）に限定する回帰テスト ---

_BASELINE_75H = Baseline(wake_h=31.0, sleep_h=7.5, rem_min=90.0, deep_min=60.0, days_used=14, is_default=False)


def _curve_for(sleep_h: float):
    """wake_h=31.0（7:00）・指定した睡眠時間の予測カーブを返す。"""
    sleep = SleepInput(bedtime_h=31.0 - sleep_h, wake_h=31.0, rem_min=90.0, deep_min=60.0)
    return tp.predict_curve(sleep, _BASELINE_75H, debt_h=0.0)


def _marked_hour(text: str) -> int | None:
    """「← 谷」が付いた行の時台を返す。付いていなければ None。"""
    for line in text.splitlines():
        if "← 谷" in line:
            return int(line.strip().split("時台")[0])
    return None


def test_morning_text_marks_the_dip_inside_the_main_window_not_at_wake_time():
    """5時間睡眠・起床7:00 のとき、「← 谷」は主要時間帯（起床+2h〜+10h＝9〜17時）に
    付き、起床直後（7時・8時）には付かない。

    Two-Process Model では残存睡眠圧により起床直後が全区間の最大になるため、
    全区間から谷を探すと毎回起床時刻に印が付いてしまう不具合があった。
    """
    curve = _curve_for(5.0)
    text = fallback.morning_text(curve, PLAN, VITAL)
    hour = _marked_hour(text)
    assert hour is not None
    assert hour not in (7, 8)
    assert 9 <= hour <= 17


def test_morning_text_does_not_mark_a_dip_when_all_percents_are_zero():
    """8時間睡眠（負債なし）で全時間帯 0% のときは「← 谷」を付けない。"""
    curve = _curve_for(8.0)
    assert all(d.focus_pct == 0 for d in curve)
    text = fallback.morning_text(curve, PLAN, VITAL)
    assert "← 谷" not in text


def test_morning_text_does_not_raise_for_various_sleep_durations():
    """5h/7h/8h いずれでも例外が出ないこと。"""
    for sleep_h in (5.0, 7.0, 8.0):
        curve = _curve_for(sleep_h)
        fallback.morning_text(curve, PLAN, VITAL)


def test_night_text_explains_sleep_target_range_and_confidence():
    text = fallback.night_text(
        ADVICE, BASE, debt_h=1.0, range_low=3, range_high=13,
        confidence="low", feedback_count=2,
    )
    assert "7.5時間" in text
    assert "3〜13%" in text
    assert "信頼度：低" in text
    assert "評価データ2件" in text
    assert "平常時100%" not in text
    assert "目標を満たせます" not in text


def test_morning_text_uses_ranges_not_single_percent():
    ranges = [(3, 13)] * len(CURVE)
    text = fallback.morning_text(CURVE, ranges, PLAN, VITAL, "low", 2)
    assert "7時台" in text
    assert "3〜13%" in text
    assert "実測の仕事能力ではありません" in text
    assert "中途覚醒・HRV・心拍数は現行の%計算には使っていません" in text
    assert "-8%" not in text
