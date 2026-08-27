"""直近履歴から個人の平常時を求める（仕様 第10節）。

純粋関数のみ。現在時刻や DynamoDB には触れない。
"""

from __future__ import annotations

import statistics

from sleeptech.memory.schemas import Baseline, SleepInput
from sleeptech.model import constants as C


def _median_or_none(values: list[float | None]) -> float | None:
    """欠損を除いた中央値。値が1つも無ければ None。"""
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 2) if present else None


def compute(
    recent: list[SleepInput],
    override_wake_h: float | None = None,
    min_target_sleep_h: float = 7.0,
) -> Baseline:
    """新しい順に並んだ睡眠履歴からベースラインを算出する。

    履歴が BASELINE_MIN_DAYS 未満なら起床時刻・睡眠時間に既定値を用い、
    is_default=True を立てる。override_wake_h が与えられれば起床時刻はそれで上書きする。
    """
    used = recent[: C.BASELINE_LOOKBACK_DAYS]
    insufficient = len(used) < C.BASELINE_MIN_DAYS

    if insufficient:
        wake_h = C.DEFAULT_WAKE_H
        sleep_h = C.DEFAULT_SLEEP_H
        target_sleep_h = C.DEFAULT_SLEEP_H
    else:
        wake_h = _median_or_none([s.wake_h for s in used])
        sleep_h = _median_or_none([s.duration_h for s in used])
        target_sleep_h = min(9.0, max(float(sleep_h), min_target_sleep_h))

    return Baseline(
        wake_h=override_wake_h if override_wake_h is not None else wake_h,
        sleep_h=sleep_h,
        rem_min=_median_or_none([s.rem_min for s in used]),
        deep_min=_median_or_none([s.deep_min for s in used]),
        days_used=len(used),
        is_default=insufficient,
        target_sleep_h=target_sleep_h,
    )
