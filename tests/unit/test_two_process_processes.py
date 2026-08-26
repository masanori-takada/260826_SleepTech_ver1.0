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


def test_circadian_trough_is_in_the_early_morning():
    """概日リズムの谷は起床前の早朝帯にある。

    第2高調波（12時間周期）を加えると谷は起床のちょうど2時間前ではなく
    3時間前後へずれる。位置を1点に固定せず、早朝帯に入ることを検査する。
    """
    wake_h = 31.0  # 翌日 7:00
    samples = [(wake_h - 12.0 + i * 5 / 60.0, ) for i in range(int(24 * 12) + 1)]
    values = [(t, tp.circadian(t, wake_h)) for (t,) in samples]
    trough_t, _ = min(values, key=lambda x: x[1])
    assert wake_h - 5.0 <= trough_t <= wake_h - 1.0


def test_circadian_has_a_real_afternoon_dip():
    """午後に本物の極小がある（単なる右肩上がりの途中ではない）。

    ここが潰れると「14-16時が谷」という本プロダクトの中核が成立しない。
    """
    wake_h = 31.0
    # 起床+4h付近は前夜の谷から立ち上がる途中の局所的な肩に当たり、
    # 数値上まれに窓の端点と午後の谷がほぼ同値になるため、
    # その肩を過ぎた起床+5h 〜 +10h を探索窓とする。
    afternoon = [
        (wake_h + 5.0 + i * 5 / 60.0) for i in range(int(5 * 12) + 1)
    ]  # 起床+5h 〜 +10h
    values = [(t, tp.circadian(t, wake_h)) for t in afternoon]
    dip_t, dip_v = min(values, key=lambda x: x[1])

    # 窓の両端より内側に極小があること（端点なら極小ではない）
    assert afternoon[0] < dip_t < afternoon[-1]
    # 谷が意味のある深さを持つこと
    assert tp.circadian(wake_h + 10.0, wake_h) - dip_v > 0.1
