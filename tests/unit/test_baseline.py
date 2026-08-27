"""個人ベースラインの算出（仕様 第10節「履歴が3日未満」）。"""

from sleeptech.memory.schemas import SleepInput
from sleeptech.model import baseline
from sleeptech.model import constants as C


def _night(bedtime_h: float, wake_h: float, rem: float = 90.0) -> SleepInput:
    return SleepInput(bedtime_h=bedtime_h, wake_h=wake_h, rem_min=rem, deep_min=60.0)


def _sleep(hours: float, wake_h: float) -> SleepInput:
    """起床時刻を固定し、睡眠時間だけを指定した入力を作る。"""
    return SleepInput(bedtime_h=wake_h - hours, wake_h=wake_h)


def test_uses_defaults_when_history_is_empty():
    """履歴ゼロなら既定値を使い、is_default が立つ。"""
    got = baseline.compute([])
    assert got.wake_h == C.DEFAULT_WAKE_H
    assert got.sleep_h == C.DEFAULT_SLEEP_H
    assert got.is_default is True
    assert got.days_used == 0


def test_uses_defaults_when_history_is_too_short():
    """3日未満なら既定値。"""
    got = baseline.compute([_night(23.0, 31.0), _night(23.5, 31.0)])
    assert got.is_default is True
    assert got.days_used == 2


def test_uses_median_when_history_is_sufficient():
    """3日以上あれば中央値を使い、既定値へは落とさない。"""
    nights = [_night(23.0, 31.0), _night(23.5, 31.5), _night(24.0, 30.5)]
    got = baseline.compute(nights)
    assert got.is_default is False
    assert got.days_used == 3
    assert got.wake_h == 31.0  # 30.5, 31.0, 31.5 の中央値
    assert got.sleep_h == 8.0  # 6.5, 8.0, 8.0 の中央値（brief記載の 7.5 は平均値と混同した誤り）


def test_ignores_days_beyond_lookback():
    """BASELINE_LOOKBACK_DAYS を超える古い日は無視する。"""
    nights = [_night(23.0, 31.0)] * C.BASELINE_LOOKBACK_DAYS + [_night(20.0, 28.0)] * 5
    got = baseline.compute(nights)
    assert got.days_used == C.BASELINE_LOOKBACK_DAYS
    assert got.wake_h == 31.0


def test_override_wins_over_computed_value():
    """設定による上書きが実績より優先される。"""
    nights = [_night(23.0, 31.0)] * 5
    got = baseline.compute(nights, override_wake_h=30.0)
    assert got.wake_h == 30.0
    assert got.is_default is False


def test_override_applies_even_with_no_history():
    """履歴ゼロでも上書き値は反映される。"""
    got = baseline.compute([], override_wake_h=30.0)
    assert got.wake_h == 30.0
    assert got.sleep_h == C.DEFAULT_SLEEP_H
    assert got.is_default is True


def test_rem_baseline_ignores_missing_values():
    """REM が欠損した日は中央値の計算から除外する。"""
    nights = [_night(23.0, 31.0, rem=100.0), _night(23.0, 31.0, rem=80.0),
              SleepInput(bedtime_h=23.0, wake_h=31.0, rem_min=None, deep_min=None)]
    got = baseline.compute(nights)
    assert got.rem_min == 90.0


def test_rem_baseline_is_none_when_all_missing():
    """全日で REM が欠損なら None。"""
    nights = [SleepInput(bedtime_h=23.0, wake_h=31.0, rem_min=None, deep_min=None)] * 4
    got = baseline.compute(nights)
    assert got.rem_min is None
    assert got.deep_min is None


def test_short_habit_is_not_used_as_target():
    """短い習慣睡眠は目標睡眠に採用せず、最低7時間を使う。"""
    recent = [_sleep(hours=5.4, wake_h=32.68) for _ in range(13)]
    got = baseline.compute(recent, min_target_sleep_h=7.0)
    assert got.sleep_h == 5.4
    assert got.target_sleep_h == 7.0
    assert got.effective_target_sleep_h == 7.0
