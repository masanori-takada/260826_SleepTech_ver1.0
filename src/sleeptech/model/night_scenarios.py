"""就寝時刻の選択肢と翌日の低下見込みを決定論的に比較する。"""

from __future__ import annotations

from dataclasses import dataclass
import math

from sleeptech.memory.schemas import Baseline, BedtimeAdvice
from sleeptech.model.calibration import display_range
from sleeptech.model import two_process as tp
from sleeptech.timefmt import fmt_hm


@dataclass(frozen=True)
class BedtimeScenario:
    """推奨時刻からの遅れと、その場合の主要時間帯の推定低下幅。"""

    delay_min: int
    bedtime_h: float
    range_low_pct: int
    range_high_pct: int
    sleep_now: bool = False


def build_scenarios(
    advice: BedtimeAdvice,
    baseline: Baseline,
    debt_h: float,
    offset_pct: float,
    margin_pct: int,
) -> tuple[BedtimeScenario, ...]:
    """推奨、1時間遅れ、2時間遅れの比較を作る。手遅れ時は現在だけを返す。"""
    delays = (0,) if advice.status == "too_late" else (0, 60, 120)
    scenarios: list[BedtimeScenario] = []
    for delay_min in delays:
        bedtime_h = advice.bedtime_h + delay_min / 60.0
        if bedtime_h >= baseline.wake_h:
            continue
        if delay_min == 0:
            corrected = advice.mean_drop_pct
        else:
            curve = tp.predict_curve_from_bedtime(bedtime_h, baseline, debt_h)
            raw_mean = tp.mean_drop_in_main_window(curve, baseline.wake_h)
            corrected = math.ceil(max(0.0, min(100.0, raw_mean + offset_pct)))
        low, high = display_range(corrected, margin_pct)
        scenarios.append(
            BedtimeScenario(
                delay_min=delay_min,
                bedtime_h=round(bedtime_h, 3),
                range_low_pct=low,
                range_high_pct=high,
                sleep_now=advice.status == "too_late",
            )
        )
    return tuple(scenarios)


def _delay_label(delay_min: int) -> str:
    """遅れ幅を「1時間遅れ」「30分遅れ」のように日本語表記へ変換する。"""
    hours, minutes = divmod(delay_min, 60)
    if hours and minutes:
        return f"{hours}時間{minutes}分遅れ"
    if hours:
        return f"{hours}時間遅れ"
    return f"{minutes}分遅れ"


def render_night_scenarios(
    scenarios: tuple[BedtimeScenario, ...],
    baseline: Baseline,
) -> str:
    """夜LINE向けに就寝シナリオを短く表示する。"""
    lines = [
        f"起床目安：{fmt_hm(baseline.wake_h)}",
        f"目標睡眠：{baseline.effective_target_sleep_h:.1f}時間",
        "",
        "今夜の選択肢",
    ]
    for scenario in scenarios:
        if scenario.sleep_now:
            label = "今すぐ就寝"
        elif scenario.delay_min == 0:
            label = "推奨"
        else:
            label = _delay_label(scenario.delay_min)
        lines.append(
            f"・{label} {fmt_hm(scenario.bedtime_h)} → "
            f"明日の主要時間帯 {scenario.range_low_pct}〜{scenario.range_high_pct}%低下見込み"
        )
    lines.extend([
        "",
        "※%は睡眠モデルと主観評価による推定幅です。実測の仕事能力ではありません。",
    ])
    return "\n".join(lines)
