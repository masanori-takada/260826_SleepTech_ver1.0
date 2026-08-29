"""コンディション領域の決定論的評価。"""

from sleeptech.memory.schemas import DailyVital, HourlyDrop, RecoveryPlan
from sleeptech.model.condition import assess_conditions, render_morning_report


def _history(count: int = 14) -> list[DailyVital]:
    return [
        DailyVital(
            date=f"2026-08-{25 - i:02d}",
            total_sleep_min=420,
            rem_sleep_min=90,
            deep_sleep_min=75,
            sleep_efficiency_pct=91.0,
            hrv=45.0,
            daily_avg_hr=62.0,
            prior_day_steps=8000,
            prior_day_active_calories=450.0,
            weight_kg=65.0,
            body_fat_pct=18.0,
        )
        for i in range(count)
    ]


def _curve() -> tuple[list[HourlyDrop], list[tuple[int, int]]]:
    curve = [HourlyDrop(hour=9 + i, focus_pct=4 + i // 4, mood_pct=5) for i in range(16)]
    return curve, [(max(0, point.focus_pct - 2), point.focus_pct + 2) for point in curve]


def _by_domain(report):
    return {item.domain: item for item in report.displayed_assessments}


def test_only_calculable_domains_are_displayed():
    vital = DailyVital(date="2026-08-26", total_sleep_min=360, wake_h=33.0)
    curve, ranges = _curve()

    report = assess_conditions(vital, [], curve, ranges, target_sleep_h=7.0)

    assert [item.domain for item in report.displayed_assessments] == [
        "focus", "sleepiness", "sleep_recovery"
    ]
    assert "biological_change" not in _by_domain(report)
    assert all(item.level != "データ不足・学習中" for item in report.displayed_assessments)


def test_fragmentation_and_low_efficiency_raise_recovery_warning():
    vital = DailyVital(
        date="2026-08-26",
        total_sleep_min=360,
        wake_h=33.0,
        awakenings=5,
        awake_duration_min=55,
        sleep_efficiency_pct=78.0,
    )
    curve, ranges = _curve()

    recovery = _by_domain(
        assess_conditions(vital, _history(), curve, ranges, target_sleep_h=7.0)
    )["sleep_recovery"]

    assert recovery.level == "要休養"
    assert any("中途覚醒55分" in evidence for evidence in recovery.evidence)
    assert any("睡眠効率78%" in evidence for evidence in recovery.evidence)


def test_autonomic_is_shown_only_with_personal_baseline():
    vital = DailyVital(date="2026-08-26", total_sleep_min=420, wake_h=33.0, hrv=25.0, daily_avg_hr=70.0)
    curve, ranges = _curve()

    short = assess_conditions(vital, _history(6), curve, ranges, target_sleep_h=7.0)
    enough = assess_conditions(vital, _history(14), curve, ranges, target_sleep_h=7.0)

    assert "autonomic" not in _by_domain(short)
    assert _by_domain(enough)["autonomic"].level in {"注意", "要休養"}
    assert "日中平均心拍" in _by_domain(enough)["autonomic"].headline
    assert "睡眠時心拍" not in _by_domain(enough)["autonomic"].headline


def test_rem_and_activity_use_personal_baselines_without_penalizing_low_steps():
    curve, ranges = _curve()
    vital = DailyVital(
        date="2026-08-26",
        total_sleep_min=420,
        wake_h=33.0,
        rem_sleep_min=45,
        prior_day_steps=3000,
        prior_day_active_calories=180.0,
    )

    report = assess_conditions(vital, _history(), curve, ranges, target_sleep_h=7.0)
    domains = _by_domain(report)

    assert domains["mood"].level == "注意"
    assert domains["physical_load"].level == "通常"


def test_stable_body_composition_is_hidden_from_daily_message():
    vital = DailyVital(
        date="2026-08-26", total_sleep_min=420, wake_h=33.0,
        weight_kg=65.1, body_fat_pct=18.1,
    )
    curve, ranges = _curve()

    report = assess_conditions(vital, _history(), curve, ranges, target_sleep_h=7.0)

    assert "body_composition" not in _by_domain(report)


def test_morning_report_is_concise_and_only_shows_calculated_domains():
    """朝LINEは領域要約と最大3件の行動だけを表示する。"""
    vital = DailyVital(
        date="2026-08-26",
        total_sleep_min=316,
        deep_sleep_min=99,
        rem_sleep_min=74,
        awakenings=3,
        awake_duration_min=24,
        sleep_efficiency_pct=78.0,
        wake_h=31.0,
    )
    curve, ranges = _curve()
    report = assess_conditions(vital, [], curve, ranges, target_sleep_h=7.0)
    plan = RecoveryPlan(
        caffeine_times_h=[32.25, 37.75],
        caffeine_cutoff_h=39.5,
        light_morning_h=31.75,
        light_avoid_after_h=46.5,
    )

    text = render_morning_report(report, vital, plan)

    assert "昨夜：5時間16分" in text
    assert "深睡眠99分・REM74分・中途覚醒3回/24分" in text
    assert "今日のコンディション" in text
    assert "集中力" in text
    assert "眠気" in text
    assert "睡眠回復" in text
    assert "今日のリカバリー" in text
    assert len([line for line in text.splitlines() if line.startswith("・")]) <= 6
    assert "生体変化" not in text
    assert "データ不足" not in text
    assert "現行の%計算には使っていません" not in text
    assert not any(line.startswith("7時台") for line in text.splitlines())
