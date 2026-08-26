"""Process S / Process C の基本性質。"""

import math

from sleeptech.model import constants as C
from sleeptech.model import two_process as tp


def test_pressure_awake_increases_monotonically():
    """覚醒中の睡眠圧は時間とともに単調増加する。"""
    s0 = 0.2
    values = [tp.pressure_awake(s0, h) for h in (0.0, 4.0, 8.0, 16.0)]
    assert values[0] == 0.2
    assert values == sorted(values)
    assert values[-1] < 1.0


def test_pressure_asleep_decays_monotonically():
    """睡眠中の睡眠圧は時間とともに単調減少し、0 に漸近する。"""
    s0 = 0.7
    values = [tp.pressure_asleep(s0, h) for h in (0.0, 2.0, 4.0, 8.0)]
    assert values[0] == 0.7
    assert values == sorted(values, reverse=True)
    assert values[-1] > 0.0


def test_pressure_awake_matches_formula():
    """既知の入力に対する期待値。"""
    got = tp.pressure_awake(0.2, 16.5)
    want = 1.0 - (1.0 - 0.2) * math.exp(-16.5 / C.TAU_RISE)
    assert got == round(want, 6)


def test_circadian_minimum_is_two_hours_before_wake():
    """概日リズムの底は起床の2時間前にある。"""
    wake_h = 31.0  # 翌日 7:00
    t_min = wake_h - 2.0
    at_min = tp.circadian(t_min, wake_h)
    around = [tp.circadian(t_min + d, wake_h) for d in (-1.5, -0.5, 0.5, 1.5)]
    assert all(v > at_min for v in around)


def test_circadian_has_afternoon_dip():
    """起床8時間後（14時台）に副次的な谷がある。"""
    wake_h = 31.0  # 翌日 7:00 → 14:00 は 38.0
    at_dip = tp.circadian(39.0, wake_h)  # 起床+8h = 15:00
    at_morning = tp.circadian(34.0, wake_h)  # 起床+3h = 10:00
    at_evening = tp.circadian(42.0, wake_h)  # 起床+11h = 18:00
    assert at_dip < at_evening
    assert at_dip < at_morning + 0.25  # 谷が主成分の上昇に埋もれていない
