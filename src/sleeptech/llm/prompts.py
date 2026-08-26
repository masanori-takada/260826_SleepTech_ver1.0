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
    "- % は「あなたの平常時を100%とした推定の目安」である旨を添えること（省略しないこと）。\n"
    "- 低下率はマイナス符号を付けて `-12%` の形で書くこと（符号の省略は不可）。"
    "`12%` のように符号なしで書いてはならない。\n"
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


def night_user(advice: BedtimeAdvice, baseline: Baseline, debt_h: float) -> str:
    """夜フローのユーザープロンプト。確定値のみを列挙する。"""
    status_note = {
        "ok": "この時刻までに寝れば目標を満たせる",
        "unreachable": "何時に寝ても目標には届かない。早めの就寝を促す",
        "too_late": "目標達成が難しい時間帯。今すぐ寝るよう促す",
    }[advice.status]

    lines = [f"推奨就寝時刻: {fmt_hm(advice.bedtime_h)}"]
    # I4対応: 低下率0%のときは % を本文に書かせない（「0%」の明記を必須にすると
    # 平常な晩ほど不自然な文章を強いることになり、ガードレールにも落ちやすくなる）。
    if advice.mean_drop_pct != 0:
        lines.append(f"明日の主要時間帯の平均低下率: -{advice.mean_drop_pct}%")
    else:
        lines.append("明日の主要時間帯の平均低下率: ほぼ低下なし（%の数値は本文に書かないこと）")
    lines.append(f"状況: {status_note}")
    lines.append(f"睡眠負債: {debt_h:.1f}時間")
    if baseline.is_default:
        lines.append("注意: 履歴が少なく精度が低いことを一言添えること")
    return "\n".join(lines)


def morning_user(
    curve: list[HourlyDrop], recovery_plan: RecoveryPlan, vital: DailyVital
) -> str:
    """朝フローのユーザープロンプト。確定値のみを列挙する。"""
    lines: list[str] = []

    if vital.total_sleep_min is not None:
        h, m = divmod(int(vital.total_sleep_min), 60)
        lines.append(f"昨夜の睡眠: {h}時間{m}分")
    if vital.deep_sleep_min is not None:
        lines.append(f"深い睡眠: {vital.deep_sleep_min}分")
    if vital.awakenings is not None:
        lines.append(f"中途覚醒: {vital.awakenings}回")

    lines.append("今日の時間帯別の低下率:")
    for d in curve:
        lines.append(f"  {d.hour}時台: 集中力 -{d.focus_pct}% / 気分 -{d.mood_pct}%")

    if recovery_plan.caffeine_times_h:
        times = "、".join(fmt_hm_plain(t) for t in recovery_plan.caffeine_times_h)
        lines.append(f"カフェイン推奨時刻: {times}")
        lines.append(f"カフェイン最終摂取時刻: {fmt_hm_plain(recovery_plan.caffeine_cutoff_h)}")
    else:
        lines.append("カフェインは今日は勧めない")

    lines.append(f"朝の屋外光: {fmt_hm_plain(recovery_plan.light_morning_h)}までに10分")
    lines.append(f"避光の開始: {fmt_hm_plain(recovery_plan.light_avoid_after_h)}")
    return "\n".join(lines)
