"""LLM を使わずに確定値から組むテンプレート文（仕様 第7節）。"""

from __future__ import annotations

from sleeptech.model import two_process as tp
from sleeptech.timefmt import fmt_hm, fmt_hm_plain


_ESTIMATE_NOTE = "※睡眠モデルと主観評価による推定の目安（範囲）です"
_LEGACY_ESTIMATE_NOTE = "※あなたの平常時を100%とした推定の目安です"
_ABILITY_NOTE = "実測の仕事能力ではありません"
_UNUSED_NOTE = "中途覚醒・HRV・心拍数は現行の%計算には使っていません"
_LOW_DATA_NOTE = "※まだデータが少ないため精度は低めです"


def night_text(advice, baseline, debt_h: float, range_low=None, range_high=None,
               confidence="low", feedback_count=0) -> str:
    """夜のテンプレート文を返す。"""
    time_s = fmt_hm(advice.bedtime_h)
    if advice.status == "too_late" and range_low is None:
        available = advice.available_sleep_h if advice.available_sleep_h is not None else 0.0
        hours, minutes = divmod(round(available * 60), 60)
        return "\n".join([
            f"目標時刻は過ぎています。今すぐ寝てください（{time_s}）。",
            f"今から確保できる見込みは{hours}時間{minutes:02d}分です。可能なら起床時刻を調整してください。",
            _LEGACY_ESTIMATE_NOTE,
        ])
    if range_low is not None and range_high is not None:
        label = {"low": "低", "medium": "中", "high": "高"}.get(confidence, confidence)
        if advice.status == "too_late":
            available = advice.available_sleep_h if advice.available_sleep_h is not None else 0.0
            hours, minutes = divmod(round(available * 60), 60)
            return "\n".join([
                f"起床目安: {fmt_hm_plain(baseline.wake_h)}",
                f"目標睡眠時間: {baseline.effective_target_sleep_h:.1f}時間",
                "目標時刻は過ぎています。今すぐ寝てください。",
                f"今から確保できる見込みは{hours}時間{minutes:02d}分です。可能なら起床時刻を調整してください。",
                f"明日の主要時間帯の低下見込みは{range_low}〜{range_high}%です。",
                f"信頼度：{label}（評価データ{feedback_count}件）",
                _ESTIMATE_NOTE, _ABILITY_NOTE, _UNUSED_NOTE,
            ])
        return "\n".join([
            f"起床目安 {fmt_hm_plain(baseline.wake_h)}・{baseline.effective_target_sleep_h:.1f}時間の睡眠を確保するなら、遅くとも{time_s}までに就寝。",
            f"明日の主要時間帯の低下見込みは{range_low}〜{range_high}%です。",
            f"信頼度：{label}（評価データ{feedback_count}件）",
            _ESTIMATE_NOTE,
            _ABILITY_NOTE,
            _UNUSED_NOTE,
        ])
    lines: list[str] = []

    if debt_h > 0.5:
        lines.append(f"睡眠負債が{debt_h:.1f}時間分たまっています。")
    # M1対応: 0%判定をstatus分岐より先に評価する。以前はtoo_late/unreachable
    # 分岐が先に評価され、0%（mean_drop_pct=0）でもそれらのステータスなら
    # 素の "-{0}%" が出て「ベスト比 -0% 程度の見込みです」という不自然な
    # 表記が残っていた（I4対応はokの分岐にしか及んでいなかった）。
    if advice.mean_drop_pct == 0:
        if advice.status == "too_late":
            lines.append(f"今すぐ寝てください（{time_s}）。")
            lines.append("それでも明日はベストに近い一日になりそうです。")
        elif advice.status == "unreachable":
            lines.append("今夜は何時に寝てもベストに近い一日になりそうです。")
            lines.append(f"できるだけ早めに、遅くとも{time_s}には横になってください。")
        else:
            lines.append(f"▶ 今夜は {time_s} までに就寝")
            lines.append("今夜の目安どおりに寝られれば、明日はベストに近い一日になりそうです。")
    elif advice.status == "too_late":
        lines.append(f"今すぐ寝てください（{time_s}）。")
        lines.append(f"それでも明日はベスト比 -{advice.mean_drop_pct}% 程度の見込みです。")
    elif advice.status == "unreachable":
        lines.append(f"今夜は何時に寝てもベスト比 -{advice.mean_drop_pct}% 程度の見込みです。")
        lines.append(f"できるだけ早めに、遅くとも{time_s}には横になってください。")
    else:
        lines.append(f"▶ 今夜は {time_s} までに就寝")
        lines.append(f"守れれば明日は一日ベスト比 -{advice.mean_drop_pct}% 以内の見込みです。")

    lines.append(_LEGACY_ESTIMATE_NOTE)
    if baseline.is_default:
        lines.append(_LOW_DATA_NOTE)
    return "\n".join(lines)


def morning_text(curve, ranges_or_plan, recovery_plan=None, vital=None,
                 confidence=None, feedback_count=0) -> str:
    """朝の予測カーブとリカバリー行動のテンプレート文を返す。"""
    legacy = vital is None
    if legacy:
        vital = recovery_plan
        recovery_plan = ranges_or_plan
        ranges = [(d.focus_pct, d.focus_pct) for d in curve]
    else:
        ranges = ranges_or_plan
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
    # 「谷」は主要時間帯（起床+2h〜+10h）に限定して探す。全区間で探すと睡眠慣性の
    # 影響で起床直後が常に最大になり、「← 谷」が毎回起床時刻に付いてしまうため。
    main_window = tp.main_window_slice(curve) if curve else []
    worst = max(main_window, key=lambda drop: drop.focus_pct) if main_window else None
    for index, drop in enumerate(curve):
        mark = "  ← 谷" if drop is worst and drop.focus_pct > 0 else ""
        low, high = ranges[index]
        lines.append(f"{drop.hour}時台 低下見込み{low}〜{high}%{mark}")

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
    if legacy:
        lines.extend(["", "※あなたの平常時を100%とした推定の目安です"])
    else:
        label = {"low": "低", "medium": "中", "high": "高"}.get(confidence, confidence)
        lines.extend(["", f"信頼度：{label}（評価データ{feedback_count}件）", _ESTIMATE_NOTE,
                      _ABILITY_NOTE, _UNUSED_NOTE])
    return "\n".join(lines)
