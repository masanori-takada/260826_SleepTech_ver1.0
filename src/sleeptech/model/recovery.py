"""カフェインと光曝露の推奨時刻を決定論的に算出する（仕様 第6節）。

純粋関数のみ。現在時刻や外部 I/O には触れない。
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from sleeptech.memory.schemas import RecoveryPlan
from sleeptech.model import constants as C


def caffeine_cutoff(next_bedtime_h: float) -> float:
    """就寝時の残存率が CAFFEINE_BEDTIME_RATIO 以下になる最終摂取時刻。

    残存率 = 0.5 ** (経過時間 / 半減期) を解いた結果。
    """
    hours = -C.CAFFEINE_HALF_LIFE_H * math.log2(C.CAFFEINE_BEDTIME_RATIO)
    return round(next_bedtime_h - hours, 3)


def plan(wake_h: float, next_bedtime_h: float) -> RecoveryPlan:
    """起床時刻と今夜の就寝時刻からリカバリー行動の時刻を求める。"""
    cutoff = caffeine_cutoff(next_bedtime_h)
    first = wake_h + C.CAFFEINE_FIRST_OFFSET_H
    second = wake_h + C.PHASE_DIP_OFFSET_H - C.CAFFEINE_PRE_DIP_H

    times: list[float] = []
    if cutoff >= first:
        times.append(round(first, 3))
        window_h = cutoff - wake_h
        if window_h > C.CAFFEINE_SPLIT_THRESHOLD_H and cutoff >= second:
            times.append(round(second, 3))

    return RecoveryPlan(
        caffeine_times_h=times,
        caffeine_cutoff_h=cutoff,
        light_morning_h=round(wake_h + C.LIGHT_MORNING_WITHIN_MIN / 60.0, 3),
        light_avoid_after_h=round(next_bedtime_h - C.LIGHT_AVOID_BEFORE_BED_H, 3),
    )


def _round_5min(t_h: float) -> float:
    """時刻を5分グリッドへ丸める。

    timefmt._split は half-up、guardrail の _time_key は banker's rounding で
    分を求めるため、t_h * 60 がちょうど .5 になると表示とガードレール照合が
    1分ずれて「材料に載せた時刻なのに捏造扱い」になる。5分グリッドなら
    t_h * 60 が常に整数になり、この経路自体が消える。
    """
    return round(t_h * 12.0) / 12.0


@dataclass(frozen=True)
class RecoveryTimes:
    """リカバリー提案で使ってよい時刻のメニュー。

    領域ごとに「何時に」を書けるようにするための材料であり、
    カフェイン・光の生理モデル出力（RecoveryPlan）とは責務を分ける。
    """

    wake_h: float
    focus_block_h: float
    dip_h: float
    nap_h: float
    caffeine_times_h: tuple[float, ...]
    caffeine_cutoff_h: float
    light_morning_h: float
    exercise_h: float
    dinner_h: float
    bath_h: float
    winddown_h: float
    light_avoid_after_h: float
    bedtime_h: float


def times(
    wake_h: float,
    next_bedtime_h: float,
    dip_h: float,
    plan: RecoveryPlan,
) -> RecoveryTimes:
    """起床・就寝目標・集中力の谷から、提案に使える時刻を揃える。

    dip_h は予測カーブが示した実際の谷であり、定数の午後の谷では代用しない
    （起床時刻が動くと谷も動くため）。
    """
    # 仮眠は「午後の谷」の直前に置く。カーブ上の最悪時刻（dip_h）は睡眠不足の
    # 日に起床直後へ寄るため、それを基準にすると仮眠が谷より後に来て矛盾する。
    nap = min(
        wake_h + C.PHASE_DIP_OFFSET_H - C.NAP_BEFORE_DIP_H,
        plan.caffeine_cutoff_h,
    )
    exercise = max(
        next_bedtime_h - C.EXERCISE_BEFORE_BED_H,
        wake_h + C.EXERCISE_EARLIEST_OFFSET_H,
    )
    return RecoveryTimes(
        wake_h=_round_5min(wake_h),
        focus_block_h=_round_5min(wake_h + C.FOCUS_BLOCK_OFFSET_H),
        dip_h=_round_5min(dip_h),
        nap_h=_round_5min(nap),
        caffeine_times_h=tuple(_round_5min(value) for value in plan.caffeine_times_h),
        caffeine_cutoff_h=_round_5min(plan.caffeine_cutoff_h),
        light_morning_h=_round_5min(plan.light_morning_h),
        exercise_h=_round_5min(exercise),
        dinner_h=_round_5min(next_bedtime_h - C.MEAL_BEFORE_BED_H),
        bath_h=_round_5min(next_bedtime_h - C.BATH_BEFORE_BED_H),
        winddown_h=_round_5min(next_bedtime_h - C.WINDDOWN_BEFORE_BED_H),
        light_avoid_after_h=_round_5min(plan.light_avoid_after_h),
        bedtime_h=_round_5min(next_bedtime_h),
    )
