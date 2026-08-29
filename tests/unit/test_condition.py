"""コンディション領域の決定論的評価。"""

from sleeptech.memory.schemas import DailyVital, HourlyDrop, RecoveryPlan
from sleeptech.model.condition import (
    assess_conditions,
    build_recovery_material,
    recovery_time_materials,
    render_condition_section,
    render_morning_report,
)


def _history(count: int = 14) -> list[DailyVital]:
    return [
        DailyVital(
            date=f"2026-08-{25 - i:02d}",
            total_sleep_min=420,
            rem_sleep_min=90,
            deep_sleep_min=75,
            sleep_efficiency_pct=91.0,
            hrv=45.0,
            skin_temp_delta=0.0,
            respiration_rate=14.0,
            prior_day_avg_hr=62.0,
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
    vital = DailyVital(
        date="2026-08-26", total_sleep_min=420, wake_h=33.0,
        hrv=25.0, prior_day_avg_hr=70.0,
    )
    curve, ranges = _curve()

    short = assess_conditions(vital, _history(6), curve, ranges, target_sleep_h=7.0)
    enough = assess_conditions(vital, _history(14), curve, ranges, target_sleep_h=7.0)

    assert "autonomic" not in _by_domain(short)
    assert _by_domain(enough)["autonomic"].level in {"注意", "要休養"}
    assert "前日平均心拍" in _by_domain(enough)["autonomic"].headline
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
    assert "180kcal" in domains["physical_load"].headline


def test_current_vital_is_excluded_from_personal_baseline():
    """当日の値自身を基準日数や中央値へ混ぜない。"""
    vital = DailyVital(
        date="2026-08-26", total_sleep_min=420, wake_h=33.0,
        hrv=10.0, prior_day_avg_hr=90.0,
    )
    curve, ranges = _curve()
    history = [vital, *_history(6)]

    report = assess_conditions(vital, history, curve, ranges, target_sleep_h=7.0)

    assert "autonomic" not in _by_domain(report)


def test_autonomic_uses_temperature_and_respiration_when_baseline_exists():
    vital = DailyVital(
        date="2026-08-26", total_sleep_min=420, wake_h=33.0,
        hrv=45.0, prior_day_avg_hr=62.0,
        skin_temp_delta=1.5, respiration_rate=18.0,
    )
    curve, ranges = _curve()

    autonomic = _by_domain(
        assess_conditions(vital, _history(), curve, ranges, target_sleep_h=7.0)
    )["autonomic"]

    assert autonomic.level in {"注意", "要休養"}
    assert "皮膚温差+1.5℃" in autonomic.headline
    assert "呼吸18.0回/分" in autonomic.headline


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


# --- リカバリーをLLMに任せるための分割 ---


def _report_and_plan():
    vital = DailyVital(
        date="2026-08-26",
        total_sleep_min=316,
        deep_sleep_min=99,
        rem_sleep_min=74,
        awakenings=3,
        awake_duration_min=24,
        wake_h=31.0,
        sleep_efficiency_pct=78.0,
    )
    curve, ranges = _curve()
    report = assess_conditions(vital, _history(), curve, ranges, target_sleep_h=7.0)
    plan = RecoveryPlan(
        caffeine_times_h=[32.25, 37.75],
        caffeine_cutoff_h=39.5,
        light_morning_h=31.75,
        light_avoid_after_h=46.5,
    )
    return report, vital, plan


def test_condition_section_holds_no_recovery_and_no_ability_note():
    """確定部は睡眠実績と領域評価だけにする（リカバリーはLLMが作る）。"""
    report, vital, plan = _report_and_plan()

    text = render_condition_section(report, vital)

    assert "今日のコンディション" in text
    assert "今日のリカバリー" not in text
    assert "実測の仕事能力ではありません" not in text


def test_recovery_material_gives_levels_and_times_without_percent():
    """材料は領域の評価レベルと使ってよい時刻を渡し、%は含めない。"""
    report, vital, plan = _report_and_plan()

    material = build_recovery_material(report, plan)

    assert "領域別の評価" in material
    assert "集中力：" in material
    assert "8:15" in material  # カフェイン推奨（32.25h）
    assert "%" not in material


def test_recovery_time_materials_lists_every_offered_time():
    """ガードレールの許可集合は材料で提示した時刻と一致する。"""
    _, _, plan = _report_and_plan()

    times = recovery_time_materials(plan)

    assert set(times) == {32.25, 37.75, 39.5, 31.75, 46.5}


def test_morning_report_fallback_keeps_deterministic_recovery():
    """LLMが使えないときのフォールバックは決定論的リカバリーを保つ。"""
    report, vital, plan = _report_and_plan()

    text = render_morning_report(report, vital, plan)

    assert "今日のコンディション" in text
    assert "今日のリカバリー" in text
    assert "実測の仕事能力ではありません" not in text
