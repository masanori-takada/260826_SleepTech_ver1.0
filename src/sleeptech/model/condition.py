"""取得済みデータを行動可能なコンディション領域へ変換する純粋関数。"""

from __future__ import annotations

from dataclasses import dataclass, field
import statistics

from sleeptech.memory.schemas import DailyVital, HourlyDrop
from sleeptech.memory.schemas import RecoveryPlan
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


def render_morning_report(
    report: DailyConditionReport,
    vital: DailyVital,
    recovery_plan: RecoveryPlan,
) -> str:
    """算出済みの領域だけを、朝LINE向けの短い本文に整形する。"""
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
        lines.append(f"・{label}［{assessment.level}］{assessment.headline}")

    action_candidates: list[str] = []
    if recovery_plan.caffeine_times_h:
        times = "・".join(fmt_hm_plain(value) for value in recovery_plan.caffeine_times_h)
        action_candidates.append(
            f"カフェインは{times}、{fmt_hm_plain(recovery_plan.caffeine_cutoff_h)}以降は避ける"
        )
    action_candidates.append(
        f"{fmt_hm_plain(recovery_plan.light_morning_h)}までに屋外光を10分"
    )
    action_candidates.extend(report.recovery_actions)

    unique_actions: list[str] = []
    for action in action_candidates:
        if action not in unique_actions:
            unique_actions.append(action)
    lines.extend(["", "今日のリカバリー"])
    lines.extend(f"・{action}" for action in unique_actions[:3])
    lines.extend(["", "※%は睡眠モデルと主観評価による推定幅です。実測の仕事能力ではありません。"])
    return "\n".join(lines)
