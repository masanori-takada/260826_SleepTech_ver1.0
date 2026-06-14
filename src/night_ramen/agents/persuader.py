"""説得最適化エージェント（T015,T031 / FR-007,010）。

- generate_message: 戦略＋分析＋トーンから説得メッセージ本文を生成。
- update_strategy: 説得履歴と結果から戦略を自律更新（返り値は未検証。保存前に guardrail 必須）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from night_ramen.agents.prompts import BASE_PERSUASION_PROMPT, ModelInvoker
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
) -> str:
    """説得メッセージ本文を生成する（FR-007）。"""
    system = strategy.system_prompt or BASE_PERSUASION_PROMPT
    prompt = (
        f"【分析結果】{analysis.summary}\n"
        f"【数値根拠】{analysis.impacts}\n"
        f"【トーン指示】{_TONE_GUIDE.get(tone, _TONE_GUIDE[Tone.NEUTRAL])}\n\n"
        "上記を踏まえ、ユーザーが今夜ラーメンを我慢したくなる短いメッセージを書いてください。"
        "必ずバイタルデータの具体的事実に触れること。"
    )
    return invoker(model_id, prompt, system).strip()


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
        f"- {m.date}: tone={m.tone.value}, delivered={m.delivered}, body={m.body[:40]}"
        for m in recent_messages
    )
    outcomes = "\n".join(
        f"- {v.date}: ramen={v.ramen_last_night}, sleep={v.sleep_score}, hrv={v.hrv}"
        for v in recent_vitals
    )
    prompt = (
        "これまでの説得メッセージと、その後のユーザーの行動・バイタル結果です。\n"
        f"[説得履歴]\n{history}\n[結果]\n{outcomes}\n\n"
        "どの説得アプローチが効果的だったかを踏まえ、今後の説得方針（システムプロンプト）を"
        "改善案として1段落で提案してください。"
    )
    new_prompt = invoker(model_id, prompt, BASE_PERSUASION_PROMPT).strip()
    return PersuasionStrategy(
        version=current.version + 1,
        system_prompt=new_prompt or current.system_prompt,
        rationale="直近の説得履歴とバイタル結果を反映した自律更新",
        guardrail_passed=False,  # 未検証。保存前に guardrail 必須（FR-010）
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
