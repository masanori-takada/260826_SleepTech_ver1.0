"""Two-Process Model による翌日パフォーマンス予測（仕様 第5節）。

このモジュールは純粋関数のみで構成する。boto3・環境変数・現在時刻の取得を
含めてはならない。時刻はすべて引数で受け取る。
"""

from __future__ import annotations

import math

from sleeptech.memory.schemas import Baseline, HourlyDrop, SleepInput
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


def performance(alert: float) -> float:
    """覚醒度をパフォーマンス（0-1）に変換する。

    線形ではなくシグモイドを通す。覚醒度が低い領域（午後の谷）で
    睡眠不足の差が増幅され、「14-16時が谷」という体感を再現できる。
    """
    return 1.0 / (1.0 + math.exp(-C.PERF_STEEPNESS * (alert - C.PERF_THRESHOLD)))


def sleep_debt_h(recent: list[SleepInput], baseline: Baseline) -> float:
    """直近の睡眠不足を減衰加重で積算した負債（時間）。

    recent は新しい順に並んだ SleepInput のリスト。先頭ほど重みが大きい。
    """
    total = 0.0
    for i, s in enumerate(recent[: C.DEBT_LOOKBACK_DAYS]):
        deficit = max(0.0, baseline.sleep_h - s.duration_h)
        total += deficit * (C.DEBT_DECAY**i)
    return round(total, 3)


def _rem_deficit_ratio(sleep: SleepInput, baseline: Baseline) -> float:
    """REM 不足の比率（0-1）。REM が無ければ深睡眠で代替、両方無ければ 0。"""
    for actual, base in ((sleep.rem_min, baseline.rem_min), (sleep.deep_min, baseline.deep_min)):
        if actual is not None and base:
            return max(0.0, min(1.0, (base - actual) / base))
    return 0.0


def _wake_pressure(sleep: SleepInput, debt_h: float, baseline: Baseline) -> float:
    """その晩を寝終えた時点の睡眠圧を求める。

    前日の起床時刻は baseline.wake_h - 24 とみなす（前日も同じリズムだった前提）。
    """
    s_prev_wake = min(C.S0_MAX, C.S_WAKE_BASE + C.DEBT_TO_S0 * debt_h)
    awake_h = max(0.0, sleep.bedtime_h - (baseline.wake_h - 24.0))
    s_bed = pressure_awake(s_prev_wake, awake_h)
    return pressure_asleep(s_bed, max(0.0, sleep.duration_h))


def _curve_points(sleep: SleepInput, baseline: Baseline, debt_h: float) -> list[tuple[float, float]]:
    """(時刻 t_h, 覚醒度) の列を CURVE_HOURS 時間分返す。"""
    s_wake = _wake_pressure(sleep, debt_h, baseline)
    points = []
    for i in range(C.CURVE_HOURS):
        t_h = sleep.wake_h + i
        s = pressure_awake(s_wake, float(i))
        points.append((t_h, circadian(t_h, sleep.wake_h) - s))
    return points


def predict_curve(sleep: SleepInput, baseline: Baseline, debt_h: float) -> list[HourlyDrop]:
    """実際の睡眠と平常時を比較し、時間帯別の低下率を返す。

    平常時カーブは「同じ起床時刻で baseline.sleep_h だけ寝た場合」。
    低下率は 0 未満にならないようクランプする（寝過ぎでボーナスは出さない）。
    """
    ideal = SleepInput(
        bedtime_h=sleep.wake_h - baseline.sleep_h,
        wake_h=sleep.wake_h,
        rem_min=baseline.rem_min,
        deep_min=baseline.deep_min,
    )
    actual_points = _curve_points(sleep, baseline, debt_h)
    ideal_points = _curve_points(ideal, baseline, 0.0)
    rem_penalty = C.W_REM * _rem_deficit_ratio(sleep, baseline)

    curve = []
    for (t_h, alert_actual), (_, alert_ideal) in zip(actual_points, ideal_points):
        p_ideal = performance(alert_ideal)
        focus = (p_ideal - performance(alert_actual)) / p_ideal * 100.0
        mood = (p_ideal - performance(alert_actual - rem_penalty)) / p_ideal * 100.0
        curve.append(
            HourlyDrop(
                hour=int(t_h) % 24,
                focus_pct=max(0, min(100, round(focus))),
                mood_pct=max(0, min(100, round(mood))),
            )
        )
    return curve


def mean_drop_in_main_window(curve: list[HourlyDrop], wake_h: float) -> float:
    """主要時間帯（起床+2h 〜 起床+10h）の集中力低下率の平均。

    curve は predict_curve が返す「起床時刻から1時間刻みで CURVE_HOURS 個」の
    連番リストであることを前提とし、インデックス i は wake_h + i 時点に対応する。
    したがって主要時間帯はインデックス MAIN_WINDOW_START_H 〜 MAIN_WINDOW_END_H
    にそのまま対応するため、hour（% 24 された離散値）ではなくインデックスで
    絞り込む。hour で比較すると主要時間帯が日をまたぐ起床時刻（例: 昼過ぎ〜夜の
    起床）で start > end となり、範囲判定が常に false になるバグがあったため
    修正した。wake_h はこの前提の下では不要だが、シグネチャ互換のため残す。
    """
    start_idx = int(C.MAIN_WINDOW_START_H)
    end_idx = int(C.MAIN_WINDOW_END_H)
    picked = [d.focus_pct for d in curve[start_idx : end_idx + 1]]
    if not picked:
        # curve が主要時間帯の開始にすら届かないほど短い場合の防御的フォールバック。
        # 通常運用では CURVE_HOURS(16) > end_idx(10) のため発生しない。
        picked = [d.focus_pct for d in curve]
    return sum(picked) / len(picked)
