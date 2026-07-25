"""説得最適化エージェント（T015,T031 / FR-007,010）。

- generate_message: 戦略＋分析＋トーン＋今夜の話題（指標・切り口）から本文を生成。
- update_strategy: 説得履歴と結果から戦略を自律更新（返り値は未検証。保存前に guardrail 必須）。

毎晩同じ話になるのを防ぐため、生成プロンプトには
「今夜主役にする指標」「今夜の語りの切り口」「直近で使ったので避ける指標」を明示する。
"""

from __future__ import annotations

from datetime import datetime, timezone

from night_ramen.agents.prompts import BASE_PERSUASION_PROMPT, ModelInvoker
from night_ramen.agents.topics import METRIC_META, Angle
from night_ramen.memory.schemas import (
    AnalysisResult,
    DailyVitalRecord,
    PersuasionMessage,
    PersuasionStrategy,
    Tone,
)

# トーンごとの語調ガイド（ガードレール範囲内）
_TONE_GUIDE = {
    Tone.PRAISE: "目標に近づいています。称賛し、その調子を維持できるよう穏やかに励ます。",
    Tone.NEUTRAL: "中立的に、事実ベースで今夜の我慢をやさしく後押しする。",
    Tone.WARNING: "やや目標から遅れています。危機感を持たせつつ、希望を残す。",
    Tone.STRONG_WARNING: (
        "目標から大きく遅れています。強めに警告するが、人格否定・罵倒は決して用いず、"
        "あくまで身体への具体的影響で訴える。"
    ),
}


def generate_message(
    strategy: PersuasionStrategy,
    analysis: AnalysisResult,
    tone: Tone,
    invoker: ModelInvoker,
    model_id: str,
    focus_metrics: list[str] | None = None,
    angle: Angle | None = None,
    avoid_metrics: list[str] | None = None,
) -> str:
    """説得メッセージ本文を生成する（FR-007）。

    focus_metrics / angle / avoid_metrics は night ワークフローが
    説得履歴から選んだ「今夜の話題」。未指定なら従来どおり分析結果のみで生成する。
    """
    system = strategy.system_prompt or BASE_PERSUASION_PROMPT
    focus = focus_metrics or []
    avoid = [m for m in (avoid_metrics or []) if m not in focus]

    sections = [
        f"【分析結果】{analysis.summary}",
        f"【数値根拠（ラーメン有無の比較）】{_render_impacts(analysis.impacts)}",
        f"【指標ごとの推移】{_render_stats(analysis.metric_stats, focus)}",
        f"【我慢の記録】{_render_behavior(analysis.behavior)}",
        f"【トーン指示】{_TONE_GUIDE.get(tone, _TONE_GUIDE[Tone.NEUTRAL])}",
    ]

    if focus:
        sections.append(
            "【今夜の主役データ】" + "、".join(_label(m) for m in focus) +
            "。この指標の実際の数値を必ず本文に入れ、話の中心にすること。"
        )
    if angle is not None:
        sections.append(f"【今夜の切り口】{angle.label} — {angle.instruction}")
    if avoid:
        sections.append(
            "【避ける話題】" + "、".join(_label(m) for m in avoid) +
            "。直近の夜に既に使ったため、今夜は主役にしない（触れるとしても添える程度）。"
        )

    sections.append(
        "\n上記を踏まえ、ユーザーが今夜ラーメンを我慢したくなる短いメッセージを書いてください。"
        "必ず【今夜の主役データ】の具体的な数値に触れ、【今夜の切り口】に沿って語ること。"
        "前回までと同じ言い回し・同じ導入で始めないこと。"
    )
    return invoker(model_id, "\n".join(sections), system).strip()


def _render_impacts(impacts: list[dict]) -> str:
    if not impacts:
        return "（ラーメン有/無の両方の申告が揃っていないため比較できません）"
    return " / ".join(
        f"{i.get('label', i['metric'])}: ラーメン有={i['ramen_avg']}{i.get('unit', '')}, "
        f"無={i['no_ramen_avg']}{i.get('unit', '')}（差 {i['delta']:+}）"
        for i in impacts
    )


def _render_stats(stats: list[dict], focus: list[str]) -> str:
    """指標ごとの推移。主役指標は詳しく、それ以外は1行で（材料は全部渡す）。"""
    if not stats:
        return "（データなし）"

    lines: list[str] = []
    for s in stats:
        unit = s.get("unit", "")
        head = (
            f"{s.get('label', s['metric'])}: 最新 {s['latest']}{unit}"
            f"（{s.get('latest_date')}）, 直近平均 {s['recent_avg']}{unit}"
        )
        if s["metric"] in focus:
            if s.get("trend_delta") is not None:
                head += f", それ以前の平均 {s['prior_avg']}{unit}（差 {s['trend_delta']:+}）"
            if s.get("best") is not None:
                head += f", 自己ベスト {s['best']}{unit}（{s.get('best_date')}）"
            if s.get("worst") is not None:
                head += f", 最悪 {s['worst']}{unit}（{s.get('worst_date')}）"
            head += f", 記録日数 {s['days']}日"
        lines.append("・" + head)
    return "\n" + "\n".join(lines)


def _render_behavior(behavior: dict) -> str:
    if not behavior or not behavior.get("reported_days"):
        return "（ラーメンの自己申告がまだありません）"
    parts = [
        f"申告 {behavior['reported_days']}日中 我慢 {behavior['abstained_days']}日"
        f"（成功率 {behavior['success_rate']:.0%}）",
        f"現在の連続我慢 {behavior['current_abstinence_streak']}日",
    ]
    if behavior.get("last_ramen_date"):
        parts.append(f"最後に食べたのは {behavior['last_ramen_date']}")
    return " / ".join(parts)


def _label(metric: str) -> str:
    meta = METRIC_META.get(metric)
    return meta.label if meta else metric


def update_strategy(
    current: PersuasionStrategy,
    recent_messages: list[PersuasionMessage],
    recent_vitals: list[DailyVitalRecord],
    invoker: ModelInvoker,
    model_id: str,
) -> PersuasionStrategy:
    """説得履歴と結果から戦略を自律更新（FR-010）。

    返り値は guardrail_passed=False（未検証）。呼び出し側が guardrail を通して初めて保存する。
    """
    history = "\n".join(
        f"- {m.date}: tone={m.tone.value}, angle={m.angle or '-'}, "
        f"focus={','.join(m.focus_metrics) or '-'}, delivered={m.delivered}, "
        f"body={m.body[:40]}"
        for m in recent_messages
    )
    outcomes = "\n".join(
        f"- {v.date}: ramen={v.ramen_last_night}, sleep={v.sleep_score}, hrv={v.hrv}"
        for v in recent_vitals
    )
    prompt = (
        "これまでの説得メッセージと、その後のユーザーの行動・バイタル結果です。\n"
        f"[説得履歴]\n{history}\n[結果]\n{outcomes}\n\n"
        "どの説得アプローチ（切り口 angle・主役にした指標 focus）が効果的だったかを踏まえ、"
        "今後の説得方針（システムプロンプト）を改善案として1段落で提案してください。"
        "特定の指標だけを繰り返さず、毎晩ちがう角度から語るための方針も含めること。"
    )
    new_prompt = invoker(model_id, prompt, BASE_PERSUASION_PROMPT).strip()
    return PersuasionStrategy(
        version=current.version + 1,
        system_prompt=new_prompt or current.system_prompt,
        rationale="直近の説得履歴とバイタル結果を反映した自律更新",
        guardrail_passed=False,  # 未検証。保存前に guardrail 必須（FR-010）
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
