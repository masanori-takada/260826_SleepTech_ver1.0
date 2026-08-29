"""カフェインと光曝露の推奨時刻を決定論的に算出する（仕様 第6節）。

純粋関数のみ。現在時刻や外部 I/O には触れない。
"""

from __future__ import annotations

from dataclasses import dataclass
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


def _round_5min(t_h: float) -> float:
    """時刻を5分グリッドへ丸める。

    timefmt._split は half-up、guardrail の _time_key は banker's rounding で
    分を求めるため、t_h * 60 がちょうど .5 になると表示とガードレール照合が
    1分ずれて「材料に載せた時刻なのに捏造扱い」になる。5分グリッドなら
    t_h * 60 が常に整数になり、この経路自体が消える。
    """
    return round(t_h * 12.0) / 12.0


@dataclass(frozen=True)
class RecoveryTimes:
    """リカバリー提案で使ってよい時刻のメニュー。

    平日の勤務スケジュール（始業・昼休み・退勤・夕食）に沿った時刻だけを
    載せる。生理モデルが最適と言う時刻でも、勤務中に実行できないものは
    提案の材料にしない。
    """

    wake_h: float
    commute_light_h: float   # 通勤で外光を浴びる
    focus_block_h: float     # 集中して作業できる時間帯の開始
    caffeine_times_h: tuple[float, ...]
    lunch_nap_h: float       # 昼休みに目を閉じる
    desk_break_h: float      # 席でできる小休憩
    dip_h: float             # 集中力の谷
    caffeine_cutoff_h: float
    exercise_h: float        # 退勤後のまとまった運動
    dinner_h: float          # 家族との夕食
    light_avoid_after_h: float
    bath_h: float
    winddown_h: float
    bedtime_h: float


def _day_base(wake_h: float) -> float:
    """起床時刻が属する日の 0:00（起点日 0:00 からの経過時間）。"""
    return 24.0 * math.floor(wake_h / 24.0)


def times(
    wake_h: float,
    next_bedtime_h: float,
    dip_h: float,
    plan: RecoveryPlan,
) -> RecoveryTimes:
    """起床・就寝目標・集中力の谷と勤務スケジュールから、使える時刻を揃える。

    dip_h は予測カーブが示した実際の谷。定数の午後の谷では代用しない
    （起床時刻が動くと谷も動くため）。
    """
    day = _day_base(wake_h)
    # 外光は通勤時に浴びる。起床直後の屋外はそもそも家にいて実行しにくい。
    commute_light = max(
        wake_h + 0.25, day + C.WORK_START_H - C.COMMUTE_LIGHT_BEFORE_WORK_H
    )
    # 仮眠は昼休みの中だけ。勤務時間中には置けない。
    lunch_nap = day + C.LUNCH_START_H + C.LUNCH_NAP_AFTER_START_H
    # 谷の直前の小休憩は席でできる範囲。勤務時間内に収める。
    desk_break = min(
        max(dip_h - C.DESK_BREAK_BEFORE_DIP_H, day + C.WORK_START_H),
        day + C.LEAVE_OFFICE_H,
    )
    # カフェインは1日1回まで。始業後の立ち上がりに寄せ、遮断時刻は超えない。
    first_caffeine = (
        plan.caffeine_times_h[0] if plan.caffeine_times_h else day + C.WORK_START_H
    )
    caffeine = max(first_caffeine, day + C.WORK_START_H)
    caffeine_times = (caffeine,) if caffeine <= plan.caffeine_cutoff_h else ()
    return RecoveryTimes(
        wake_h=_round_5min(wake_h),
        commute_light_h=_round_5min(commute_light),
        focus_block_h=_round_5min(
            max(wake_h + C.FOCUS_BLOCK_OFFSET_H, day + C.WORK_START_H)
        ),
        caffeine_times_h=tuple(_round_5min(value) for value in caffeine_times),
        lunch_nap_h=_round_5min(lunch_nap),
        desk_break_h=_round_5min(desk_break),
        dip_h=_round_5min(dip_h),
        caffeine_cutoff_h=_round_5min(plan.caffeine_cutoff_h),
        exercise_h=_round_5min(day + C.LEAVE_OFFICE_H + C.EXERCISE_AFTER_LEAVE_H),
        dinner_h=_round_5min(day + C.DINNER_H),
        light_avoid_after_h=_round_5min(plan.light_avoid_after_h),
        bath_h=_round_5min(next_bedtime_h - C.BATH_BEFORE_BED_H),
        winddown_h=_round_5min(next_bedtime_h - C.WINDDOWN_BEFORE_BED_H),
        bedtime_h=_round_5min(next_bedtime_h),
    )
