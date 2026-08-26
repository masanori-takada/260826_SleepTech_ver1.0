"""Two-Process Model による翌日パフォーマンス予測（仕様 第5節）。

このモジュールは純粋関数のみで構成する。boto3・環境変数・現在時刻の取得を
含めてはならない。時刻はすべて引数で受け取る。
"""

from __future__ import annotations

import math

from sleeptech.model import constants as C


def pressure_awake(s0: float, hours: float) -> float:
    """覚醒を hours 時間続けた後の睡眠圧。s0 は開始時点の睡眠圧。"""
    return round(1.0 - (1.0 - s0) * math.exp(-hours / C.TAU_RISE), 6)


def pressure_asleep(s0: float, hours: float) -> float:
    """睡眠を hours 時間続けた後の睡眠圧。s0 は入眠時点の睡眠圧。"""
    return round(s0 * math.exp(-hours / C.TAU_DECAY), 6)


def circadian(t_h: float, wake_h: float) -> float:
    """時刻 t_h における概日リズム成分。

    第1項は起床2時間前に最小値をとる24時間周期。
    第2項は起床8時間後に谷を作る12時間周期（post-lunch dip）。
    """
    t_min = wake_h + C.PHASE_MIN_OFFSET_H
    t_dip = wake_h + C.PHASE_DIP_OFFSET_H
    main = -C.CIRCADIAN_AMPLITUDE * math.cos(2 * math.pi * (t_h - t_min) / 24.0)
    dip = -C.CIRCADIAN_AMPLITUDE * C.HARMONIC_RATIO * math.cos(
        2 * math.pi * (t_h - t_dip) / 12.0
    )
    return round(main + dip, 6)
