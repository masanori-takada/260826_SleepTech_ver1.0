"""目標達成率の評価と説得強度トーンの決定（T027 / FR-005,006,017）。"""

from __future__ import annotations

from dataclasses import dataclass

from night_ramen.memory.schemas import DailyVitalRecord, Goal, Tone


@dataclass
class GoalProgress:
    """達成率の評価結果。"""

    success_rate: float | None        # ラーメン我慢成功率（0-1）。データなしは None
    success_rate_ratio: float | None  # 目標に対する達成度（success_rate / target）
    weight_loss_kg: float | None      # ベースラインからの減量(kg)
    weight_loss_ratio: float | None   # 目標に対する達成度
    days_evaluated: int


def evaluate(goal: Goal, vitals: list[DailyVitalRecord]) -> GoalProgress:
    """成功率・減量の達成率を算出する（FR-005）。

    成功率 = ラーメンを我慢できた日(ramen_last_night is False) / 申告のあった日。
    """
    reported = [v for v in vitals if v.ramen_last_night is not None]
    if reported:
        success_days = sum(1 for v in reported if v.ramen_last_night is False)
        success_rate = success_days / len(reported)
        sr_ratio = (
            success_rate / goal.success_rate_target
            if goal.success_rate_target > 0
            else None
        )
    else:
        success_rate = None
        sr_ratio = None

    # 減量: ベースライン体重 - 直近体重
    weights = [v.weight_kg for v in vitals if v.weight_kg is not None]
    if goal.baseline_weight_kg is not None and weights:
        weight_loss = goal.baseline_weight_kg - weights[-1]
        wl_ratio = (
            weight_loss / goal.weight_loss_target_kg
            if goal.weight_loss_target_kg > 0
            else None
        )
    else:
        weight_loss = None
        wl_ratio = None

    return GoalProgress(
        success_rate=success_rate,
        success_rate_ratio=sr_ratio,
        weight_loss_kg=weight_loss,
        weight_loss_ratio=wl_ratio,
        days_evaluated=len(reported),
    )


def choose_tone(progress: GoalProgress) -> Tone:
    """達成率→強度トーンを決定する（FR-006）。

    データが乏しいときは中立。達成度の代表値（成功率比を優先）で段階分け。
    """
    ratio = progress.success_rate_ratio
    if ratio is None:
        ratio = progress.weight_loss_ratio
    if ratio is None:
        return Tone.NEUTRAL

    if ratio >= 1.0:
        return Tone.PRAISE       # 目標達成以上
    if ratio >= 0.7:
        return Tone.NEUTRAL      # おおむね順調
    if ratio >= 0.4:
        return Tone.WARNING      # やや未達
    return Tone.STRONG_WARNING   # 大幅未達
