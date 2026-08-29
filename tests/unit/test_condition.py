"""コンディション領域の決定論的評価。"""

from sleeptech.guardrail.validator import validate_recovery_structure
from sleeptech.memory.schemas import DailyVital, HourlyDrop, RecoveryPlan
from sleeptech.model import recovery as rc
from sleeptech.timefmt import fmt_hm_plain as _hm
from sleeptech.model.condition import (
    assess_conditions,
    build_recovery_material,
    actionable_targets,
    recovery_domain_times,
    recovery_targets,
    recovery_time_materials,
    render_condition_section,
    render_morning_report,
    render_recovery_section,
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

    text = render_morning_report(report, vital, _times(report, vital, plan))

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


# --- リカバリーは［注意］［要休養］の領域だけに1:1で出す ---


def _times(report, vital, plan):
    dip = next(
        (a.dip_index for a in report.displayed_assessments if a.domain == "focus"),
        8,
    )
    return rc.times(vital.wake_h, vital.wake_h + 16.5, vital.wake_h + dip, plan)


def _plan():
    return RecoveryPlan(
        caffeine_times_h=[32.0, 37.5],
        caffeine_cutoff_h=39.5,
        light_morning_h=31.5,
        light_avoid_after_h=44.5,
    )


def _case(total_sleep_min: int, efficiency: float, awake_min: int):
    vital = DailyVital(
        date="2026-08-26",
        total_sleep_min=total_sleep_min,
        deep_sleep_min=99,
        rem_sleep_min=74,
        awakenings=3,
        awake_duration_min=awake_min,
        wake_h=31.0,
        sleep_efficiency_pct=efficiency,
    )
    curve, ranges = _curve()
    report = assess_conditions(vital, _history(), curve, ranges, target_sleep_h=7.0)
    plan = _plan()
    return report, vital, plan, _times(report, vital, plan)


def test_condition_section_holds_no_recovery_and_no_ability_note():
    """確定部は睡眠実績と領域評価だけにする（リカバリーはLLMが作る）。"""
    report, vital, _, _ = _case(316, 78.0, 24)

    text = render_condition_section(report, vital)

    assert "今日のコンディション" in text
    assert "今日のリカバリー" not in text
    assert "実測の仕事能力ではありません" not in text


def test_recovery_targets_keep_only_attention_and_rest_levels():
    """［注意］［要休養］だけを対象にし、［良好］［通常］は落とす。"""
    report, _, _, _ = _case(316, 78.0, 24)

    targets = recovery_targets(report)

    assert targets, "この入力では対象領域が1つ以上あるはず"
    assert {item.level for item in targets} <= {"注意", "要休養"}
    assert all(item.level not in {"良好", "通常"} for item in targets)


def test_no_recovery_section_when_no_domain_needs_attention():
    """対象が0件なら節ごと出さない。"""
    vital = DailyVital(
        date="2026-08-26", total_sleep_min=430, wake_h=31.0,
        sleep_efficiency_pct=96.0, awake_duration_min=5,
    )
    curve = [HourlyDrop(hour=9 + i, focus_pct=1, mood_pct=1) for i in range(16)]
    ranges = [(0, 2) for _ in curve]
    report = assess_conditions(vital, _history(), curve, ranges, target_sleep_h=7.0)
    times = _times(report, vital, _plan())

    assert recovery_targets(report) == ()
    assert render_recovery_section(recovery_targets(report), times) == ""
    assert "今日のリカバリー" not in render_morning_report(report, vital, times)


def test_recovery_material_lists_only_target_domains_with_times():
    """材料は対象領域だけを、使ってよい時刻付きで並べる。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)

    material = build_recovery_material(targets, times)

    assert "使ってよい時刻" in material
    for item in targets:
        assert item.level in material
    assert "体組成" not in material


def test_recovery_time_materials_match_the_offered_menu():
    """許可集合は対象領域へ提示した時刻の全体と一致する。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)

    offered = {
        value
        for _, values, _headline in recovery_domain_times(targets, times)
        for value in values
    }

    assert set(recovery_time_materials(targets, times)) == offered


def test_deterministic_recovery_matches_targets_one_to_one():
    """決定論フォールバックも対象領域と1:1で、各行に時刻を含む。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)

    section = render_recovery_section(targets, times)

    assert section.splitlines()[0] == "今日のリカバリー"
    assert len(section.splitlines()) == len(targets) + 1
    assert validate_recovery_structure(
        section, recovery_domain_times(targets, times)
    ).passed


def test_morning_report_fallback_keeps_deterministic_recovery():
    """LLMが使えないときのフォールバックは決定論的リカバリーを保つ。"""
    report, vital, _, times = _case(316, 78.0, 24)

    text = render_morning_report(report, vital, times)

    assert "今日のコンディション" in text
    assert "今日のリカバリー" in text
    assert "実測の仕事能力ではありません" not in text


def test_recovery_line_may_not_restate_measurements():
    """測定値の名前を持ち出した行は行動になっていないので拒否する。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)
    expected = recovery_domain_times(targets, times)
    label, allowed, _ = expected[0]
    quoted = "\n".join([
        "今日のリカバリー",
        f"・{label}：中途覚醒が多いので{_hm(allowed[0])}に休む",
        *(
            f"・{other}：{_hm(values[0])}に休む"
            for other, values, _ in expected[1:]
        ),
    ])

    assert validate_recovery_structure(quoted, expected).reason == "recovery_metric"


def test_recovery_reason_codes_survive_log_masking():
    """理由コードは24文字未満にする。

    observability の _TOKENish が24文字以上の英数字・アンダースコア列を
    トークンとみなしてマスクするため、長い理由コードはログで読めなくなる。
    """
    from sleeptech.guardrail import validator as gv
    from sleeptech.observability import mask_value

    report, _, _, times = _case(316, 78.0, 24)
    expected = recovery_domain_times(recovery_targets(report), times)
    broken = "今日のリカバリー\n・存在しない領域：9:00に休む"

    reason = gv.validate_recovery_structure(broken, expected).reason
    assert reason and mask_value("reason", reason) == reason


def test_recovery_line_may_not_pile_up_times():
    """1行に時刻を3つ以上並べると読めなくなるので拒否する。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)
    expected = recovery_domain_times(targets, times)
    label, allowed, _ = expected[0]
    crowded = "\n".join([
        "今日のリカバリー",
        f"・{label}：" + "と".join(_hm(value) for value in allowed[:3]) + "に動く",
        *(
            f"・{other}：{_hm(values[0])}に休む"
            for other, values, _ in expected[1:]
        ),
    ])

    assert validate_recovery_structure(crowded, expected).reason == "recovery_time_crowded"


def test_deterministic_recovery_keeps_at_most_two_times_per_line():
    """決定論フォールバック自身が新しい上限を守っている。"""
    import re

    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)

    section = render_recovery_section(targets, times)

    for line in section.splitlines()[1:]:
        assert len(re.findall(r"\d{1,2}:\d{2}", line)) <= 2


def test_condition_lines_start_with_stars_for_each_level():
    """4段階を星3つ〜星0個で表し、行頭のポチを置き換える。"""
    from sleeptech.model.condition import level_stars

    assert level_stars("良好") == "⭐⭐⭐"
    assert level_stars("通常") == "⭐⭐☆"
    assert level_stars("注意") == "⭐☆☆"
    assert level_stars("要休養") == "☆☆☆"
    assert all(len(level_stars(level)) == 3 for level in
               ("良好", "通常", "注意", "要休養"))


def test_condition_section_shows_stars_instead_of_bullets():
    """コンディションの各行は星で始まり、リカバリーは「・」のままにする。"""
    report, vital, _, times = _case(316, 78.0, 24)

    text = render_morning_report(report, vital, times)
    condition, recovery = text.split("今日のリカバリー")

    body = condition.split("今日のコンディション")[1].strip().splitlines()
    assert body, "コンディション行があるはず"
    assert all(line[0] in "⭐☆" for line in body)
    assert not any(line.startswith("・") for line in body)
    assert all(line.startswith("・") for line in recovery.strip().splitlines())


# --- 配信時点で過ぎている時刻は提案しない ---


def test_past_times_are_dropped_from_the_menu():
    """朝9:30に届くのに「5:50までに屋外光を」とは書けない。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)
    now_h = times.wake_h + 4.0  # 起床から4時間後に配信

    for _label, values, _headline in recovery_domain_times(targets, times, now_h):
        assert all(value >= now_h for value in values)
    assert all(
        value >= now_h for value in recovery_time_materials(targets, times, now_h)
    )


def test_domains_without_a_usable_time_are_dropped():
    """使える時刻が残らない領域は提案を作れないので外す。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)
    late = times.bedtime_h + 1.0  # 就寝目標も過ぎた時点

    assert targets, 'この入力では対象領域があるはず'
    assert actionable_targets(targets, times, late) == ()
    assert recovery_domain_times(targets, times, late) == ()
    assert render_recovery_section(targets, times, late) == ""
    # まだ使える時刻が残る時点なら領域は残る
    early = times.wake_h + 4.0
    assert actionable_targets(targets, times, early) == targets


def test_deterministic_recovery_never_offers_a_past_time():
    """決定論フォールバックも過ぎた時刻を書かない。"""
    import re

    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)
    now_h = times.wake_h + 4.0

    section = render_recovery_section(targets, times, now_h)
    offered = {
        f"{int(h)}:{int(m):02d}"
        for h, m in re.findall(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", section)
    }
    future = {
        _hm(value) for value in recovery_time_materials(targets, times, now_h)
    }

    assert offered
    assert offered <= future


def test_material_tells_the_current_time():
    """材料に現在時刻を書き、それより後だけを使わせる。"""
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)
    now_h = times.wake_h + 4.0

    material = build_recovery_material(targets, times, now_h)

    assert f"今は{_hm(now_h)}" in material


def test_fallback_template_is_not_reused_for_another_time():
    """使えない時刻のテンプレートを別の時刻へ流用しない。

    以前は残っている時刻へ機械的に寄せていたため、朝の光の文面が夕方の
    運動時刻に当てられて「20:00までに外の光を浴びる」が出た。
    """
    report, _, _, times = _case(316, 78.0, 24)
    targets = recovery_targets(report)
    now_h = times.commute_light_h + 0.5  # 通勤で光を浴びる時間帯を過ぎた時点

    section = render_recovery_section(targets, times, now_h)

    assert "日なた" not in section or _hm(times.commute_light_h) in section
    for line in section.splitlines()[1:]:
        assert line.count("：") == 1
