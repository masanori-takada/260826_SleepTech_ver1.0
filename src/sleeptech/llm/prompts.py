"""システムプロンプトと入力整形（仕様 第7節）。

LLM の役割は確定値の文章化のみ。数値・時刻を生成させてはならない。
プロンプトに埋め込んだ値以外の数値が本文に現れたら、ガードレールが弾く。
"""

from __future__ import annotations

from sleeptech.memory.schemas import (
    Baseline,
    BedtimeAdvice,
    DailyVital,
    HourlyDrop,
    RecoveryPlan,
)
from sleeptech.timefmt import fmt_hm, fmt_hm_plain

_COMMON_RULES = (
    "厳守事項:\n"
    "- 与えられた数値と時刻だけを使うこと。新しい数値・時刻を作ってはならない。\n"
    "- 与えられた数値は省略せず本文に含めること。\n"
    "- % は睡眠モデルと主観評価による推定幅で、実測の仕事能力ではない旨を添えること。\n"
    "- 範囲の両端と信頼度を変更・省略しないこと。\n"
    "- 「実測の仕事能力ではありません」「中途覚醒・HRV・心拍数は現行の%計算には使っていません」"
    "の両注記を省略・改変しないこと。\n"
    "- 時刻は与えられた表記のまま書くこと。「翌」が付いている時刻から「翌」を"
    "落としてはならない。\n"
    "- 医学的な診断・治療の断定をしないこと。\n"
    "- 「必ず」「確実に」「絶対に」は使わないこと。\n"
    "- 不安を過度に煽らないこと。人格を否定しないこと。\n"
    "- 日本語で、200文字以内。箇条書きを1〜2行使ってよい。\n"
)

NIGHT_SYSTEM = (
    "あなたは睡眠コーチです。与えられた推奨就寝時刻と予測低下率を、"
    "短く前向きな日本語のメッセージに整形してください。\n" + _COMMON_RULES
)

MORNING_SYSTEM = (
    "あなたは睡眠コーチです。与えられた前夜の睡眠実績・時間帯別の予測低下率・"
    "リカバリー行動の時刻を、短く実行しやすい日本語のメッセージに整形してください。\n"
    + _COMMON_RULES
)


def night_user(advice: BedtimeAdvice, baseline: Baseline, debt_h: float,
               range_low=None, range_high=None, confidence=None, feedback_count=None) -> str:
    """夜フローのユーザープロンプト。確定値のみを列挙する。"""
    status_note = {
        "ok": "指定睡眠時間を確保できる時刻までに就寝する",
        "unreachable": "指定睡眠時間を確保できる時刻に届かないため早めに就寝する",
        "too_late": "指定睡眠時間の確保が難しい時間帯のため今すぐ就寝する",
    }[advice.status]

    lines = [f"推奨就寝時刻: {fmt_hm(advice.bedtime_h)}"]
    # I4対応: 低下率0%のときは % を本文に書かせない（「0%」の明記を必須にすると
    # 平常な晩ほど不自然な文章を強いることになり、ガードレールにも落ちやすくなる）。
    if range_low is not None and range_high is not None:
        lines.append(f"明日の主要時間帯の低下見込み: {range_low}〜{range_high}%")
        label = {"low": "低", "medium": "中", "high": "高"}.get(confidence, confidence)
        lines.append(f"信頼度: {label}（評価データ{feedback_count}件）")
    elif advice.mean_drop_pct != 0:
        lines.append(f"明日の主要時間帯の平均低下率: -{advice.mean_drop_pct}%")
    else:
        lines.append("明日の主要時間帯の平均低下率: ほぼ低下なし（%の数値は本文に書かないこと）")
    lines.append(f"状況: {status_note}")
    lines.append(f"睡眠負債: {debt_h:.1f}時間")
    if baseline.is_default:
        lines.append("注意: 履歴が少なく精度が低いことを一言添えること")
    if range_low is not None and range_high is not None:
        lines.extend([
            "実測の仕事能力ではありません",
            "中途覚醒・HRV・心拍数は現行の%計算には使っていません",
        ])
    return "\n".join(lines)


def morning_user(curve: list[HourlyDrop], recovery_plan: RecoveryPlan, vital: DailyVital,
                 ranges=None, confidence=None, feedback_count=None) -> str:
    """朝フローのユーザープロンプト。確定値のみを列挙する。"""
    lines: list[str] = []

    if vital.total_sleep_min is not None:
        h, m = divmod(int(vital.total_sleep_min), 60)
        lines.append(f"昨夜の睡眠: {h}時間{m}分")
    if vital.deep_sleep_min is not None:
        lines.append(f"深い睡眠: {vital.deep_sleep_min}分")
    if vital.awakenings is not None:
        lines.append(f"中途覚醒: {vital.awakenings}回")

    lines.append("今日の時間帯別の低下見込み:")
    for i, d in enumerate(curve):
        if ranges is None:
            lines.append(f"  {d.hour}時台: 集中力 -{d.focus_pct}% / 気分 -{d.mood_pct}%")
        else:
            low, high = ranges[i]
            lines.append(f"  {d.hour}時台: 集中力 {low}〜{high}%")
    if ranges is not None:
        label = {"low": "低", "medium": "中", "high": "高"}.get(confidence, confidence)
        lines.append(f"信頼度: {label}（評価データ{feedback_count}件）")
        lines.extend([
            "実測の仕事能力ではありません",
            "中途覚醒・HRV・心拍数は現行の%計算には使っていません",
        ])

    if recovery_plan.caffeine_times_h:
        times = "、".join(fmt_hm_plain(t) for t in recovery_plan.caffeine_times_h)
        lines.append(f"カフェイン推奨時刻: {times}")
        lines.append(f"カフェイン最終摂取時刻: {fmt_hm_plain(recovery_plan.caffeine_cutoff_h)}")
    else:
        lines.append("カフェインは今日は勧めない")

    lines.append(f"朝の屋外光: {fmt_hm_plain(recovery_plan.light_morning_h)}までに10分")
    lines.append(f"避光の開始: {fmt_hm_plain(recovery_plan.light_avoid_after_h)}")
    return "\n".join(lines)
