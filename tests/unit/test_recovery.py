"""カフェインと光曝露の時刻算出（仕様 第6節）。"""

import math

import pytest

from sleeptech.model import constants as C
from sleeptech.model import recovery

WAKE_H = 31.0        # 翌日 7:00
NEXT_BED_H = 47.5    # 翌日 23:30


def test_caffeine_cutoff_leaves_little_in_the_body_at_bedtime():
    """最終摂取可能時刻に飲んだ場合、就寝時の残存率が上限以下になる。"""
    cutoff = recovery.caffeine_cutoff(NEXT_BED_H)
    elapsed = NEXT_BED_H - cutoff
    remaining = 0.5 ** (elapsed / C.CAFFEINE_HALF_LIFE_H)
    assert remaining <= C.CAFFEINE_BEDTIME_RATIO + 1e-9


def test_caffeine_cutoff_is_earlier_for_earlier_bedtime():
    """就寝が早いほど最終摂取可能時刻も早い。"""
    early = recovery.caffeine_cutoff(46.0)
    late = recovery.caffeine_cutoff(48.0)
    assert early < late


def test_two_doses_when_window_is_long():
    """起床〜最終摂取可能時刻が閾値を超える場合は2回に分ける。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=NEXT_BED_H)
    assert len(got.caffeine_times_h) == 2
    assert got.caffeine_times_h[0] == WAKE_H + C.CAFFEINE_FIRST_OFFSET_H
    assert got.caffeine_times_h[1] == WAKE_H + C.PHASE_DIP_OFFSET_H - C.CAFFEINE_PRE_DIP_H


def test_single_dose_when_window_is_short():
    """就寝が早く窓が短い場合は1回のみ。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=WAKE_H + 9.0)
    assert len(got.caffeine_times_h) == 1
    assert got.caffeine_times_h[0] == WAKE_H + C.CAFFEINE_FIRST_OFFSET_H


def test_doses_never_exceed_the_cutoff():
    """推奨する摂取時刻は必ず最終摂取可能時刻以前。"""
    for bed in (WAKE_H + 8.0, WAKE_H + 12.0, WAKE_H + 16.0):
        got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=bed)
        assert all(t <= got.caffeine_cutoff_h for t in got.caffeine_times_h)


def test_no_doses_when_cutoff_is_before_wake():
    """最終摂取可能時刻が起床より前なら、カフェインは推奨しない。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=WAKE_H + 2.0)
    assert got.caffeine_times_h == []


def test_morning_light_is_shortly_after_wake():
    """朝の光曝露は起床直後。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=NEXT_BED_H)
    assert got.light_morning_h == pytest.approx(
        WAKE_H + C.LIGHT_MORNING_WITHIN_MIN / 60.0
    )


def test_light_avoidance_starts_before_bedtime():
    """避光の開始は就寝の LIGHT_AVOID_BEFORE_BED_H 時間前。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=NEXT_BED_H)
    assert got.light_avoid_after_h == NEXT_BED_H - C.LIGHT_AVOID_BEFORE_BED_H


def test_plan_is_deterministic():
    """同じ入力からは必ず同じ結果が出る。"""
    assert recovery.plan(WAKE_H, NEXT_BED_H) == recovery.plan(WAKE_H, NEXT_BED_H)


def test_cutoff_formula_is_exact():
    """最終摂取可能時刻は半減期の式どおり。"""
    got = recovery.caffeine_cutoff(NEXT_BED_H)
    hours = -C.CAFFEINE_HALF_LIFE_H * math.log2(C.CAFFEINE_BEDTIME_RATIO)
    assert got == round(NEXT_BED_H - hours, 3)
