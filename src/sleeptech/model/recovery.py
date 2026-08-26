"""カフェインと光曝露の推奨時刻を決定論的に算出する（仕様 第6節）。

純粋関数のみ。現在時刻や外部 I/O には触れない。
"""

from __future__ import annotations

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
