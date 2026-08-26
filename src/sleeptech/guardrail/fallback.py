"""LLM を使わずに確定値から組むテンプレート文（仕様 第7節）。"""

from __future__ import annotations

from sleeptech.timefmt import fmt_hm_plain


_ESTIMATE_NOTE = "※あなたの平常時を100%とした推定の目安です"
_LOW_DATA_NOTE = "※まだデータが少ないため精度は低めです"


def night_text(advice, baseline, debt_h: float) -> str:
    """夜のテンプレート文を返す。"""
    time_s = fmt_hm_plain(advice.bedtime_h)
    lines: list[str] = []

    if debt_h > 0.5:
        lines.append(f"睡眠負債が{debt_h:.1f}時間分たまっています。")
    if advice.status == "too_late":
        lines.append(f"今すぐ寝てください（{time_s}）。")
        lines.append(f"それでも明日はベスト比 -{advice.mean_drop_pct}% 程度の見込みです。")
    elif advice.status == "unreachable":
        lines.append(f"今夜は何時に寝てもベスト比 -{advice.mean_drop_pct}% 程度の見込みです。")
        lines.append(f"できるだけ早めに、遅くとも{time_s}には横になってください。")
    else:
        lines.append(f"▶ 今夜は {time_s} までに就寝")
        lines.append(f"守れれば明日は一日ベスト比 -{advice.mean_drop_pct}% 以内の見込みです。")

    lines.append(_ESTIMATE_NOTE)
    if baseline.is_default:
        lines.append(_LOW_DATA_NOTE)
    return "\n".join(lines)


def morning_text(curve, recovery_plan, vital) -> str:
    """朝の予測カーブとリカバリー行動のテンプレート文を返す。"""
    lines: list[str] = []
    if vital.total_sleep_min is not None:
        hours, minutes = divmod(int(vital.total_sleep_min), 60)
        extras = []
        if vital.deep_sleep_min is not None:
            extras.append(f"深い睡眠{vital.deep_sleep_min}分")
        if vital.awakenings is not None:
            extras.append(f"中途覚醒{vital.awakenings}回")
        head = f"昨夜は {hours}時間{minutes}分"
        lines.append(f"{head}（{'・'.join(extras)}）" if extras else head)

    lines.append("")
    lines.append("今日の見込み")
    worst = max(curve, key=lambda drop: drop.focus_pct) if curve else None
    for drop in curve:
        mark = "  ← 谷" if drop is worst and drop.focus_pct > 0 else ""
        lines.append(f"  {drop.hour}時台  -{drop.focus_pct}%{mark}")

    lines.append("")
    lines.append("リカバリー")
    if recovery_plan.caffeine_times_h:
        times = "と".join(fmt_hm_plain(time) for time in recovery_plan.caffeine_times_h)
        cutoff = fmt_hm_plain(recovery_plan.caffeine_cutoff_h)
        lines.append(f"☕ カフェインは{times}に。{cutoff}以降は今夜の入眠を邪魔するので避けて。")
    else:
        lines.append("☕ カフェインは今日は控えめに。今夜の入眠に響きます。")
    light = fmt_hm_plain(recovery_plan.light_morning_h)
    avoid = fmt_hm_plain(recovery_plan.light_avoid_after_h)
    lines.append(f"☀ {light}までに屋外光を10分。{avoid}以降は画面を暗くすると巻き返せます。")
    lines.extend(["", _ESTIMATE_NOTE])
    return "\n".join(lines)
