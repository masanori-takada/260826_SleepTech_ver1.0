"""覚醒度→%変換と予測カーブの性質（仕様 第5節・第11節）。"""

import pytest

from sleeptech.memory.schemas import Baseline, SleepInput
from sleeptech.model import two_process as tp

WAKE_H = 31.0  # 翌日 7:00
BASE = Baseline(
    wake_h=WAKE_H, sleep_h=7.5, rem_min=90.0, deep_min=60.0, days_used=14, is_default=False
)


def _sleep(hours: float, rem_min: float = 90.0) -> SleepInput:
    """起床時刻を固定し、睡眠時間だけ変えた入力を作る。"""
    return SleepInput(
        bedtime_h=WAKE_H - hours, wake_h=WAKE_H, rem_min=rem_min, deep_min=60.0
    )


def _at(curve, hour: int) -> int:
    return next(d.focus_pct for d in curve if d.hour == hour)


def test_performance_is_monotonic_in_alertness():
    """覚醒度が高いほどパフォーマンスが高い。"""
    values = [tp.performance(a) for a in (-1.0, -0.5, 0.0, 0.5)]
    assert values == sorted(values)
    assert all(0.0 < v < 1.0 for v in values)


def test_baseline_sleep_yields_zero_drop():
    """平常時と同じ睡眠時間なら低下率は全時間帯 0%。"""
    curve = tp.predict_curve(_sleep(7.5), BASE, debt_h=0.0)
    assert all(d.focus_pct == 0 for d in curve)
    assert all(d.mood_pct == 0 for d in curve)


def test_longer_than_baseline_clamps_to_zero():
    """平常時より長く寝ても低下率は負にならず 0 に張り付く。"""
    curve = tp.predict_curve(_sleep(9.0), BASE, debt_h=0.0)
    assert all(d.focus_pct == 0 for d in curve)


def test_shorter_sleep_drops_more():
    """睡眠が短いほど低下率が大きい（14時で比較）。"""
    d5 = _at(tp.predict_curve(_sleep(5.0), BASE, 0.0), 14)
    d6 = _at(tp.predict_curve(_sleep(6.0), BASE, 0.0), 14)
    d7 = _at(tp.predict_curve(_sleep(7.0), BASE, 0.0), 14)
    assert d5 > d6 > d7


def test_afternoon_dip_is_worse_than_morning():
    """午後の谷（14時）は午前（10時）より落ち込みが大きい。"""
    curve = tp.predict_curve(_sleep(5.0), BASE, 0.0)
    assert _at(curve, 14) > _at(curve, 10)


@pytest.mark.parametrize(
    # 仕様 第5節の期待出力表（C3/I8 対応で実測値に更新済み）の周辺に狭めたレンジ。
    # 実測: 5h -> 10時20%/14時22%、6h -> 10時10%/14時12%。
    "hours,hour,lo,hi",
    [
        (5.0, 10, 17, 23),
        (5.0, 14, 19, 25),
        (6.0, 10, 7, 13),
        (6.0, 14, 9, 15),
    ],
)
def test_drop_is_within_plausible_range(hours, hour, lo, hi):
    """10時・14時の低下率が仕様 第5節の期待出力表の実測値近辺に収まる。"""
    got = _at(tp.predict_curve(_sleep(hours), BASE, 0.0), hour)
    assert lo <= got <= hi


def test_mood_drops_more_than_focus_when_rem_is_short():
    """REM が不足した日は気分カーブの方が大きく落ちる。"""
    curve = tp.predict_curve(_sleep(5.0, rem_min=40.0), BASE, 0.0)
    at14 = next(d for d in curve if d.hour == 14)
    assert at14.mood_pct > at14.focus_pct


def test_mood_equals_focus_when_rem_missing():
    """REM も深睡眠も欠損なら気分カーブは集中力カーブと一致する。"""
    sleep = SleepInput(bedtime_h=WAKE_H - 5.0, wake_h=WAKE_H, rem_min=None, deep_min=None)
    curve = tp.predict_curve(sleep, BASE, 0.0)
    assert all(d.mood_pct == d.focus_pct for d in curve)


def test_sleep_debt_weights_recent_days_more():
    """睡眠負債は直近の日ほど重く効く。"""
    recent_bad = [_sleep(5.0), _sleep(7.5), _sleep(7.5)]
    old_bad = [_sleep(7.5), _sleep(7.5), _sleep(5.0)]
    assert tp.sleep_debt_h(recent_bad, BASE) > tp.sleep_debt_h(old_bad, BASE)


def test_sleep_debt_is_zero_when_well_rested():
    """十分に寝ている日が続けば負債は 0。"""
    assert tp.sleep_debt_h([_sleep(8.0)] * 5, BASE) == 0.0


def test_debt_increases_drop():
    """睡眠負債があると同じ睡眠時間でも低下率が大きくなる。"""
    no_debt = _at(tp.predict_curve(_sleep(6.0), BASE, 0.0), 14)
    with_debt = _at(tp.predict_curve(_sleep(6.0), BASE, 4.0), 14)
    assert with_debt > no_debt


def test_curve_covers_expected_hours():
    """カーブは起床時刻から CURVE_HOURS 時間分を1時間刻みで返す。"""
    curve = tp.predict_curve(_sleep(7.0), BASE, 0.0)
    assert len(curve) == 16
    assert curve[0].hour == 7
    assert curve[-1].hour == 22


def test_mean_drop_in_main_window():
    """主要時間帯（起床+2h〜+10h）の平均低下率を返す。"""
    curve = tp.predict_curve(_sleep(5.0), BASE, 0.0)
    mean = tp.mean_drop_in_main_window(curve, WAKE_H)
    in_window = [d.focus_pct for d in curve if 9 <= d.hour <= 17]
    assert mean == pytest.approx(sum(in_window) / len(in_window))


def test_mean_drop_in_main_window_handles_wake_crossing_midnight():
    """起床時刻が遅く主要時間帯が日をまたぐ場合でも、正しく起床+2h〜+10hを平均する。

    wake_h=38.0（翌日14:00起床）だと主要時間帯（起床+2h=16時〜起床+10h=翌0時）が
    日をまたぎ、hour（0-23の離散値）で範囲判定すると start > end になって
    恒常的に false になり、カーブ全体（16時間分）の平均へ黙ってフォールバックする
    退行がありえる。インデックスベースの実装ではこれが起きないことを確認する。
    """
    wake_h = 38.0  # 翌日 14:00 起床
    baseline = Baseline(
        wake_h=wake_h, sleep_h=7.5, rem_min=90.0, deep_min=60.0, days_used=14, is_default=False
    )
    sleep = SleepInput(bedtime_h=wake_h - 5.0, wake_h=wake_h, rem_min=90.0, deep_min=60.0)
    curve = tp.predict_curve(sleep, baseline, 0.0)

    start_idx = 2
    end_idx = 10
    expected_window = [d.focus_pct for d in curve[start_idx : end_idx + 1]]
    assert len(expected_window) == 9

    mean = tp.mean_drop_in_main_window(curve, wake_h)
    assert mean == pytest.approx(sum(expected_window) / len(expected_window))

    full_curve_mean = sum(d.focus_pct for d in curve) / len(curve)
    assert mean != pytest.approx(full_curve_mean)
