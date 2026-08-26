"""推奨就寝時刻の探索と3つの境界ケース（仕様 第5節「推奨就寝時刻の解法」）。"""

from sleeptech.memory.schemas import Baseline
from sleeptech.model import constants as C
from sleeptech.model import two_process as tp

BASE = Baseline(
    wake_h=31.0, sleep_h=7.5, rem_min=90.0, deep_min=60.0, days_used=14, is_default=False
)


def test_returns_ok_status_in_normal_case():
    """22:30 時点で十分に間に合う場合は status='ok'。"""
    got = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=0.0)
    assert got.status == "ok"
    assert 22.5 <= got.bedtime_h <= BASE.wake_h - C.MIN_SLEEP_BEFORE_WAKE_H
    assert got.mean_drop_pct <= C.TARGET_DROP_PCT


def test_does_not_recommend_less_than_baseline_sleep_when_possible():
    """全区間で閾値を満たす場合、平常時睡眠時間を確保できる時刻を推奨する。

    最も遅い候補（起床4時間前）に飛びつくと過小睡眠を勧めることになる。
    """
    got = tp.recommend_bedtime(now_h=18.0, baseline=BASE, debt_h=0.0)
    assert got.bedtime_h <= BASE.wake_h - BASE.sleep_h


def test_unreachable_when_debt_is_overwhelming():
    """負債が大きく、どんなに早く寝ても閾値に届かない場合は status='unreachable'。

    now_h=22.5（起床8.5時間前）だと、debt_h をいくら大きくしても S0_MAX の
    クランプと睡眠時間の指数減衰（TAU_DECAY）により低下率は約3.2%で頭打ちに
    なり、閾値10%を常に下回ってしまう（= 必ず ok になる）。これは constants.py
    のコメントにある通りモデルの正しい挙動であり、「debt_h=30 なら22.5時でも
    unreachable になるはず」というブリーフのテスト前提が誤っていたため、
    起床までの残り時間がより短い now_h=24.5 に修正した（PHASE_MIN_OFFSET_H の
    テスト修正と同じ理由によるテスト側の修正）。
    """
    got = tp.recommend_bedtime(now_h=24.5, baseline=BASE, debt_h=30.0)
    assert got.status == "unreachable"
    assert got.mean_drop_pct > C.TARGET_DROP_PCT


def test_unreachable_still_returns_the_best_available_time():
    """届かない場合でも、区間内で最も低下率が小さい時刻を返す。"""
    got = tp.recommend_bedtime(now_h=24.5, baseline=BASE, debt_h=30.0)
    limit = BASE.wake_h - C.MIN_SLEEP_BEFORE_WAKE_H
    assert 24.5 <= got.bedtime_h <= limit
    later = tp.predict_curve_from_bedtime(got.bedtime_h + 1.0, BASE, 30.0)
    assert got.mean_drop_pct <= tp.mean_drop_in_main_window(later, BASE.wake_h)


def test_too_late_when_now_is_past_the_window():
    """既に探索区間の終端を過ぎていれば status='too_late' で「今すぐ」を返す。"""
    now = BASE.wake_h - 2.0  # 起床2時間前
    got = tp.recommend_bedtime(now_h=now, baseline=BASE, debt_h=0.0)
    assert got.status == "too_late"
    assert got.bedtime_h == now


def test_curve_is_returned_for_the_recommended_time():
    """返り値のカーブは推奨就寝時刻で寝た場合のもの。"""
    got = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=0.0)
    expected = tp.predict_curve_from_bedtime(got.bedtime_h, BASE, 0.0)
    assert got.curve == expected


def test_scan_is_deterministic():
    """同じ入力からは必ず同じ結果が出る。"""
    a = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=1.5)
    b = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=1.5)
    assert a == b
