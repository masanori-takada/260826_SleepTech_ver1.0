"""取得済みデータを行動可能なコンディション領域へ変換する純粋関数。"""

from __future__ import annotations

from dataclasses import dataclass, field
import statistics

from sleeptech.memory.schemas import DailyVital, HourlyDrop
from sleeptech.model.recovery import RecoveryTimes
from sleeptech.timefmt import fmt_hm_plain


_DOMAIN_LABELS = {
    "focus": "集中力",
    "sleepiness": "眠気",
    "sleep_recovery": "睡眠回復",
    "autonomic": "自律神経",
    "mood": "気分",
    "physical_load": "身体負荷",
    "body_composition": "体組成",
}


# リカバリーを出す評価レベル。［良好］［通常］には出さない。
_TARGET_LEVELS = {"注意", "要休養"}

# 評価レベルの見え方。埋まっている星の数が多いほど良い状態を表す。
# 空きは☆で埋めて幅を揃える（要休養の行だけ字下げがずれると読みにくい）。
_LEVEL_STARS = {"良好": 3, "通常": 2, "注意": 1, "要休養": 0}
_STAR_FILLED = "⭐"
_STAR_EMPTY = "☆"
_STAR_SLOTS = 3


def level_stars(level: str) -> str:
    """評価レベルを星3つ〜星0個で表す。"""
    filled = _LEVEL_STARS.get(level, 0)
    return _STAR_FILLED * filled + _STAR_EMPTY * (_STAR_SLOTS - filled)

# 材料に載せる時刻の意味。フィールド名 → 表示ラベル。
_TIME_MEANINGS = {
    "wake_h": "起床 ",
    "commute_light_h": "通勤で外光を浴びる ",
    "focus_block_h": "集中して作業できる ",
    "caffeine_times_h": "コーヒーを飲むなら ",
    "lunch_nap_h": "昼休みに目を閉じる ",
    "desk_break_h": "席でできる小休憩 ",
    "dip_h": "集中力の谷 ",
    "caffeine_cutoff_h": "カフェインを避け始める ",
    "exercise_h": "退勤後に体を動かす ",
    "dinner_h": "家族との夕食 ",
    "light_avoid_after_h": "強い光を避け始める ",
    "bath_h": "入浴を終える目安 ",
    "winddown_h": "活動を落とす ",
    "bedtime_h": "今夜の就寝目標 ",
}

# 領域 → (使ってよい時刻のフィールド, 決定論フォールバックの候補)。
# 候補は (必要な時刻, 文面) の並びで、必要な時刻が全部使える最初のものを採る。
# 文面は平日の勤務中でも実行できる粒度に限る（席でできる動作、コーヒー1杯、
# 昼休みの仮眠、退勤後の散歩など）。材料・構造検査・フォールバックは
# すべてこの表を参照する。
_RECOVERY_MENU: dict[str, tuple[tuple[str, ...], tuple[tuple[tuple[str, ...], str], ...]]] = {
    "focus": (
        ("focus_block_h", "desk_break_h", "dip_h"),
        (
            (("focus_block_h", "dip_h"),
             "{focus_block_h}から重要な作業に取りかかり、{dip_h}前後は手を動かす作業に回す"),
            (("focus_block_h",), "{focus_block_h}から重要な作業を1件だけ片づける"),
            (("desk_break_h",), "{desk_break_h}に席で肩甲骨を10回まわす"),
            (("dip_h",), "{dip_h}前後は判断の重い作業を入れない"),
        ),
    ),
    "sleepiness": (
        ("lunch_nap_h", "caffeine_times_h", "desk_break_h", "caffeine_cutoff_h"),
        (
            (("lunch_nap_h",), "{lunch_nap_h}に15分だけ目を閉じる"),
            (("caffeine_times_h",), "{caffeine_times_h}にコーヒーを1杯だけ飲む"),
            (("desk_break_h",), "{desk_break_h}に立ってスクワットを10回する"),
            (("caffeine_cutoff_h",), "{caffeine_cutoff_h}以降はカフェインを取らない"),
        ),
    ),
    "sleep_recovery": (
        ("bath_h", "winddown_h", "bedtime_h", "light_avoid_after_h"),
        (
            (("bath_h", "bedtime_h"),
             "{bath_h}に湯船から出て、{bedtime_h}までに布団に入る"),
            (("bedtime_h",), "{bedtime_h}までに布団に入る"),
            (("winddown_h",), "{winddown_h}から画面を閉じて体を休める"),
            (("bath_h",), "{bath_h}に15分湯船につかる"),
            (("light_avoid_after_h",), "{light_avoid_after_h}以降は照明を落とす"),
        ),
    ),
    "autonomic": (
        ("desk_break_h", "winddown_h", "dinner_h"),
        (
            (("desk_break_h",),
             "{desk_break_h}に席で4秒吸って8秒吐く呼吸を5回する"),
            (("winddown_h",), "{winddown_h}から画面を閉じ、深呼吸を10回する"),
            (("dinner_h",), "{dinner_h}の夕食は腹八分で終える"),
        ),
    ),
    "mood": (
        ("commute_light_h", "desk_break_h", "exercise_h", "winddown_h"),
        (
            (("commute_light_h",), "{commute_light_h}の通勤で日なたを10分歩く"),
            (("exercise_h",), "{exercise_h}に20分歩いて気分を切り替える"),
            (("desk_break_h",), "{desk_break_h}に席を立って深呼吸を5回する"),
            (("winddown_h",), "{winddown_h}から画面を閉じて頭を休める"),
        ),
    ),
    "physical_load": (
        ("desk_break_h", "exercise_h", "bath_h"),
        (
            (("exercise_h", "bath_h"),
             "{exercise_h}の運動は軽い散歩までにして、{bath_h}に湯船で脚をほぐす"),
            (("exercise_h",), "{exercise_h}の運動は軽い散歩までにする"),
            (("bath_h",), "{bath_h}に湯船で脚を10分ほぐす"),
            (("desk_break_h",), "{desk_break_h}に席でふくらはぎを30秒伸ばす"),
        ),
    ),
    "body_composition": (
        ("dinner_h", "exercise_h"),
        (
            (("dinner_h",), "{dinner_h}の夕食は主食を控えめにする"),
            (("exercise_h",), "{exercise_h}に20分歩く"),
        ),
    ),
}


@dataclass(frozen=True)
class DomainAssessment:
    """1つのコンディション領域の評価結果。"""

    domain: str
    level: str
    headline: str
    evidence: tuple[str, ...] = ()
    confidence: str = "medium"
    valid_days: int = 0
    freshness: str = "current"
    recommended_actions: tuple[str, ...] = ()
    dip_index: int | None = None


@dataclass(frozen=True)
class DailyConditionReport:
    """朝LINEへ表示する領域と、内部だけに保持する領域。"""

    target_date: str
    displayed_assessments: tuple[DomainAssessment, ...]
    hidden_assessments: tuple[DomainAssessment, ...] = ()
    recovery_actions: tuple[str, ...] = ()
    data_quality: dict[str, str] = field(default_factory=dict)
    model_version: str = "condition-v1"


def _present(history: list[DailyVital], field_name: str) -> list[float]:
    values = [getattr(item, field_name, None) for item in history[:28]]
    return [float(value) for value in values if value is not None]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _robust_deviation(value: float, values: list[float]) -> float:
    """個人中央値からのずれをMAD相当の尺度で返す。"""
    center = statistics.median(values)
    mad = statistics.median(abs(item - center) for item in values)
    scale = 1.4826 * mad
    if scale < 1e-6:
        scale = max(abs(center) * 0.1, 1.0)
    return (value - center) / scale


def _level_for_drop(high: int) -> str:
    if high <= 5:
        return "良好"
    if high <= 10:
        return "通常"
    if high <= 20:
        return "注意"
    return "要休養"


def _focus_assessment(
    curve: list[HourlyDrop], ranges: list[tuple[int, int]]
) -> DomainAssessment:
    start, end = 2, min(10, len(curve) - 1)
    indices = list(range(start, end + 1)) or list(range(len(curve)))
    worst = max(indices, key=lambda index: ranges[index][1])
    low, high = ranges[worst]
    hour = curve[worst].hour
    return DomainAssessment(
        domain="focus",
        level=_level_for_drop(high),
        headline=f"{hour}時台が谷、平常時比{low}〜{high}%低下見込み",
        evidence=("Two-Process Modelの睡眠圧・概日リズム",),
        confidence="medium",
        recommended_actions=("重要作業は谷の時間帯を避ける",),
        dip_index=worst,
    )


def _sleepiness_assessment(ranges: list[tuple[int, int]]) -> DomainAssessment:
    high = max((item[1] for item in ranges[2:11]), default=0)
    if high <= 5:
        headline = "強い眠気の上昇は見込みにくい"
    elif high <= 10:
        headline = "軽い眠気が出る可能性"
    elif high <= 20:
        headline = "眠気が強まりやすい"
    else:
        headline = "強い眠気に備えて休憩を優先"
    return DomainAssessment(
        domain="sleepiness",
        level=_level_for_drop(high),
        headline=headline,
        evidence=("睡眠圧・概日リズム",),
        confidence="medium",
        recommended_actions=("必要なら13時台に15〜20分休む",),
    )


def _recovery_assessment(vital: DailyVital, target_sleep_h: float) -> DomainAssessment:
    sleep_h = float(vital.total_sleep_min or 0) / 60.0
    deficit = max(0.0, target_sleep_h - sleep_h)
    level = "通常"
    if deficit >= 2.0 or (vital.sleep_efficiency_pct is not None and vital.sleep_efficiency_pct < 80):
        level = "要休養"
    elif deficit >= 1.0 or (vital.awake_duration_min or 0) >= 30:
        level = "注意"
    elif deficit <= 0.25 and (
        vital.sleep_efficiency_pct is None or vital.sleep_efficiency_pct >= 90
    ):
        level = "良好"

    evidence = [f"実睡眠{int(vital.total_sleep_min or 0) // 60}時間{int(vital.total_sleep_min or 0) % 60:02d}分"]
    if vital.awake_duration_min is not None:
        evidence.append(f"中途覚醒{vital.awake_duration_min}分")
    if vital.sleep_efficiency_pct is not None:
        evidence.append(f"睡眠効率{vital.sleep_efficiency_pct:.0f}%")
    return DomainAssessment(
        domain="sleep_recovery",
        level=level,
        headline="・".join(evidence),
        evidence=tuple(evidence),
        confidence="high",
        recommended_actions=("今日は回復を優先して負荷を調整する",) if level in {"注意", "要休養"} else (),
    )


def _autonomic_assessment(vital: DailyVital, history: list[DailyVital]) -> DomainAssessment | None:
    hrv_values = _present(history, "hrv")
    if vital.hrv is None or len(hrv_values) < 7:
        return None
    hrv_z = _robust_deviation(float(vital.hrv), hrv_values)
    heart_values = _present(history, "prior_day_avg_hr")
    heart_z = None
    if vital.prior_day_avg_hr is not None and len(heart_values) >= 7:
        heart_z = _robust_deviation(float(vital.prior_day_avg_hr), heart_values)

    temperature_values = _present(history, "skin_temp_delta")
    temperature_z = None
    if vital.skin_temp_delta is not None and len(temperature_values) >= 7:
        temperature_z = _robust_deviation(float(vital.skin_temp_delta), temperature_values)
    respiration_values = _present(history, "respiration_rate")
    respiration_z = None
    if vital.respiration_rate is not None and len(respiration_values) >= 7:
        respiration_z = _robust_deviation(float(vital.respiration_rate), respiration_values)

    severe = (
        hrv_z <= -3
        or (heart_z is not None and heart_z >= 3)
        or (temperature_z is not None and abs(temperature_z) >= 3)
        or (respiration_z is not None and abs(respiration_z) >= 3)
    )
    warning = (
        hrv_z <= -1.5
        or (heart_z is not None and heart_z >= 2)
        or (temperature_z is not None and abs(temperature_z) >= 2)
        or (respiration_z is not None and abs(respiration_z) >= 2)
    )
    level = "要休養" if severe else "注意" if warning else "通常"
    metrics = [f"HRV{vital.hrv:.0f}ms"]
    if vital.prior_day_avg_hr is not None:
        metrics.append(f"前日平均心拍{vital.prior_day_avg_hr:.0f}bpm")
    if vital.skin_temp_delta is not None and len(temperature_values) >= 7:
        metrics.append(f"皮膚温差{vital.skin_temp_delta:+.1f}℃")
    if vital.respiration_rate is not None and len(respiration_values) >= 7:
        metrics.append(f"呼吸{vital.respiration_rate:.1f}回/分")
    return DomainAssessment(
        domain="autonomic",
        level=level,
        headline="、".join(metrics),
        evidence=("本人の28日以内の中央値・MADとの比較",),
        confidence="high" if len(hrv_values) >= 14 else "medium",
        valid_days=min(len(hrv_values), 28),
        recommended_actions=("高強度の負荷を控えめにする",) if warning else (),
    )


def _mood_assessment(vital: DailyVital, history: list[DailyVital]) -> DomainAssessment | None:
    values = _present(history, "rem_sleep_min")
    if vital.rem_sleep_min is None or len(values) < 7:
        return None
    center = _median(values) or 0.0
    ratio = float(vital.rem_sleep_min) / center if center > 0 else 1.0
    level = "要休養" if ratio < 0.5 else "注意" if ratio < 0.75 else "通常"
    return DomainAssessment(
        domain="mood",
        level=level,
        headline=f"REM {vital.rem_sleep_min}分（個人基準{center:.0f}分）",
        evidence=("REMと直近の個人基準",),
        confidence="medium",
        valid_days=min(len(values), 28),
        recommended_actions=("感情負荷の高い判断は余裕を持つ",) if level != "通常" else (),
    )


def _physical_assessment(vital: DailyVital, history: list[DailyVital]) -> DomainAssessment | None:
    step_values = _present(history, "prior_day_steps")
    calorie_values = _present(history, "prior_day_active_calories")
    if (
        (vital.prior_day_steps is None or len(step_values) < 7)
        and (vital.prior_day_active_calories is None or len(calorie_values) < 7)
    ):
        return None
    deviations: list[float] = []
    if vital.prior_day_steps is not None and len(step_values) >= 7:
        deviations.append(_robust_deviation(float(vital.prior_day_steps), step_values))
    if vital.prior_day_active_calories is not None and len(calorie_values) >= 7:
        deviations.append(
            _robust_deviation(float(vital.prior_day_active_calories), calorie_values)
        )
    high_load = max(deviations, default=0.0) >= 2
    level = "注意" if high_load else "通常"
    metrics: list[str] = []
    if vital.prior_day_steps is not None:
        metrics.append(f"{vital.prior_day_steps:,}歩")
    if vital.prior_day_active_calories is not None:
        metrics.append(f"{vital.prior_day_active_calories:.0f}kcal")
    return DomainAssessment(
        domain="physical_load",
        level=level,
        headline=f"前日{'・'.join(metrics)}、個人基準比{'高負荷' if high_load else '平常範囲'}",
        evidence=("前日の確定活動量と個人基準",),
        confidence="medium",
        valid_days=min(max(len(step_values), len(calorie_values)), 28),
        recommended_actions=("脚の疲労を見て運動強度を下げる",) if high_load else (),
    )


def _body_assessment(vital: DailyVital, history: list[DailyVital]) -> DomainAssessment | None:
    weights = _present(history, "weight_kg")
    fats = _present(history, "body_fat_pct")
    weight_change = 0.0
    fat_change = 0.0
    if vital.weight_kg is not None and len(weights) >= 7:
        center = _median(weights) or vital.weight_kg
        weight_change = (float(vital.weight_kg) - center) / center * 100.0 if center else 0.0
    if vital.body_fat_pct is not None and len(fats) >= 7:
        fat_change = float(vital.body_fat_pct) - (_median(fats) or vital.body_fat_pct)
    if abs(weight_change) < 2.0 and abs(fat_change) < 1.5:
        return None
    return DomainAssessment(
        domain="body_composition",
        level="注意",
        headline=f"体重基準差{weight_change:+.1f}%・体脂肪差{fat_change:+.1f}pt",
        evidence=("直近28日中央値との傾向比較",),
        confidence="medium",
        valid_days=min(max(len(weights), len(fats)), 28),
    )


def assess_conditions(
    vital: DailyVital,
    history: list[DailyVital],
    curve: list[HourlyDrop],
    ranges: list[tuple[int, int]],
    target_sleep_h: float,
) -> DailyConditionReport:
    """計算可能で行動に結びつく領域だけを表示対象として返す。"""
    # 保存直後に取得した履歴は当日を含むため、個人基準から評価対象自身を除外する。
    prior_history = [item for item in history if item.date != vital.date]
    assessments: list[DomainAssessment] = [
        _focus_assessment(curve, ranges),
        _sleepiness_assessment(ranges),
        _recovery_assessment(vital, target_sleep_h),
    ]
    for optional in (
        _autonomic_assessment(vital, prior_history),
        _mood_assessment(vital, prior_history),
        _physical_assessment(vital, prior_history),
        _body_assessment(vital, prior_history),
    ):
        if optional is not None:
            assessments.append(optional)

    actions: list[str] = []
    for assessment in assessments:
        for action in assessment.recommended_actions:
            if action not in actions:
                actions.append(action)
    return DailyConditionReport(
        target_date=vital.date,
        displayed_assessments=tuple(assessments),
        recovery_actions=tuple(actions[:3]),
    )


def render_condition_section(
    report: DailyConditionReport,
    vital: DailyVital,
) -> str:
    """睡眠実績と領域評価だけを整形する（LLMが改変してはならない確定部）。"""
    total_min = int(vital.total_sleep_min or 0)
    hours, minutes = divmod(total_min, 60)
    lines = [f"昨夜：{hours}時間{minutes:02d}分"]

    sleep_parts: list[str] = []
    if vital.deep_sleep_min is not None:
        sleep_parts.append(f"深睡眠{vital.deep_sleep_min}分")
    if vital.rem_sleep_min is not None:
        sleep_parts.append(f"REM{vital.rem_sleep_min}分")
    if vital.awakenings is not None:
        awakening = f"中途覚醒{vital.awakenings}回"
        if vital.awake_duration_min is not None:
            awakening += f"/{vital.awake_duration_min}分"
        sleep_parts.append(awakening)
    if sleep_parts:
        lines.append("・".join(sleep_parts))

    lines.extend(["", "今日のコンディション"])
    for assessment in report.displayed_assessments:
        label = _DOMAIN_LABELS.get(assessment.domain, assessment.domain)
        lines.append(
            f"{level_stars(assessment.level)} {label}［{assessment.level}］"
            f"{assessment.headline}"
        )
    return "\n".join(lines)


def recovery_targets(report: DailyConditionReport) -> tuple[DomainAssessment, ...]:
    """リカバリーを出す対象＝［注意］［要休養］の領域だけを表示順で返す。"""
    return tuple(
        assessment
        for assessment in report.displayed_assessments
        if assessment.level in _TARGET_LEVELS
    )


def _menu_times(times: RecoveryTimes, fields: tuple[str, ...]) -> tuple[float, ...]:
    """メニューのフィールド名を実際の時刻へ展開する。"""
    values: list[float] = []
    for field_name in fields:
        value = getattr(times, field_name)
        if isinstance(value, tuple):
            values.extend(value)
        else:
            values.append(value)
    return tuple(dict.fromkeys(values))


def recovery_domain_times(
    targets: tuple[DomainAssessment, ...],
    times: RecoveryTimes,
    now_h: float | None = None,
) -> tuple[tuple[str, tuple[float, ...], str], ...]:
    """対象領域ごとに (ラベル, 使ってよい時刻, 評価の文言) を並べる。

    材料・構造検査・決定論フォールバックが参照する単一の情報源。
    now_h を渡すと、その時点で既に過ぎた時刻を落とす。朝9:30に届くのに
    「5:50までに屋外光を」と書いても実行できないため、提案の対象にしない。
    使える時刻が残らなかった領域は、そもそも提案を作れないので外す。
    """
    rows: list[tuple[str, tuple[float, ...], str]] = []
    for assessment in targets:
        fields, _ = _RECOVERY_MENU.get(assessment.domain, ((), ()))
        label = _DOMAIN_LABELS.get(assessment.domain, assessment.domain)
        values = _menu_times(times, fields)
        if now_h is not None:
            values = tuple(value for value in values if value >= now_h)
        if not values:
            continue
        rows.append((label, values, assessment.headline))
    return tuple(rows)


def actionable_targets(
    targets: tuple[DomainAssessment, ...],
    times: RecoveryTimes,
    now_h: float | None = None,
) -> tuple[DomainAssessment, ...]:
    """今から実行できる時刻が残っている領域だけを返す。"""
    usable = {label for label, _, _ in recovery_domain_times(targets, times, now_h)}
    return tuple(
        assessment for assessment in targets
        if _DOMAIN_LABELS.get(assessment.domain, assessment.domain) in usable
    )


def recovery_time_materials(
    targets: tuple[DomainAssessment, ...],
    times: RecoveryTimes,
    now_h: float | None = None,
) -> tuple[float, ...]:
    """ガードレールの allowed_times に渡す、提示済み時刻の全体集合。"""
    values: list[float] = []
    for _, domain_times, _headline in recovery_domain_times(targets, times, now_h):
        values.extend(domain_times)
    return tuple(dict.fromkeys(values))


def build_recovery_material(
    targets: tuple[DomainAssessment, ...],
    times: RecoveryTimes,
    now_h: float | None = None,
) -> str:
    """LLMがリカバリー策を組み立てるための材料。数値はここで確定させる。"""
    rows = recovery_domain_times(targets, times, now_h)
    if not rows:
        return ""
    allowed = {label: values for label, values, _ in rows}
    lines = ["【リカバリー提案の材料】"]
    if now_h is not None:
        lines.append(f"今は{fmt_hm_plain(now_h)}。これより後の時刻だけを使う。")
    for assessment in actionable_targets(targets, times, now_h):
        label = _DOMAIN_LABELS.get(assessment.domain, assessment.domain)
        lines.append(f"■ {label}［{assessment.level}］{assessment.headline}")
        offered = "・".join(
            f"{_TIME_MEANINGS[field]}{fmt_hm_plain(value)}"
            for field, value in _labelled_times(assessment.domain, times)
            if value in allowed[label]
        )
        lines.append(f"  使ってよい時刻: {offered}")
    lines.append(
        "上の時刻以外の時刻・数値を書かないこと。各領域に1行ずつ、"
        "課題を解決する具体的な行動を書くこと。"
    )
    return "\n".join(lines)


def _labelled_times(domain: str, times: RecoveryTimes) -> list[tuple[str, float]]:
    """材料に載せる (意味, 時刻) の並び。"""
    fields, _ = _RECOVERY_MENU.get(domain, ((), ()))
    labelled: list[tuple[str, float]] = []
    for field_name in fields:
        value = getattr(times, field_name)
        if isinstance(value, tuple):
            labelled.extend((field_name, item) for item in value)
        else:
            labelled.append((field_name, value))
    return labelled


def render_recovery_section(
    targets: tuple[DomainAssessment, ...],
    times: RecoveryTimes,
    now_h: float | None = None,
) -> str:
    """LLMが使えないときの決定論的なリカバリー節。出せる領域が無ければ空文字。"""
    rows = recovery_domain_times(targets, times, now_h)
    if not rows:
        return ""
    allowed = {label: values for label, values, _ in rows}
    lines = ["今日のリカバリー"]
    for assessment in actionable_targets(targets, times, now_h):
        label = _DOMAIN_LABELS.get(assessment.domain, assessment.domain)
        line = _fallback_line(assessment.domain, times, allowed[label])
        if line:
            lines.append(f"・{label}：{line}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _field_value(times: RecoveryTimes, field_name: str) -> float | None:
    """フィールド名を単一の時刻へ直す。カフェインは最初の1回を使う。"""
    value = getattr(times, field_name)
    if isinstance(value, tuple):
        return value[0] if value else None
    return value


def _fallback_line(
    domain: str, times: RecoveryTimes, allowed: tuple[float, ...]
) -> str:
    """使える時刻が揃っている最初の候補を採る。

    1つのテンプレートを別の時刻へ流用すると「20:00までに外の光を浴びる」
    のように文意が壊れるため、必要な時刻が残っている候補だけを使う。
    """
    _, candidates = _RECOVERY_MENU.get(domain, ((), ()))
    for required, template in candidates:
        values = {name: _field_value(times, name) for name in required}
        if any(value is None or value not in allowed for value in values.values()):
            continue
        filled = template
        for name, value in values.items():
            filled = filled.replace("{" + name + "}", fmt_hm_plain(value))
        return filled
    return ""


def render_morning_report(
    report: DailyConditionReport,
    vital: DailyVital,
    times: RecoveryTimes,
    now_h: float | None = None,
) -> str:
    """確定部と決定論的リカバリーを繋いだ朝本文（LLM不通時のフォールバック）。"""
    condition = render_condition_section(report, vital)
    recovery = render_recovery_section(recovery_targets(report), times, now_h)
    return "\n\n".join([condition, recovery]) if recovery else condition
