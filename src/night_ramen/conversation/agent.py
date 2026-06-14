"""健康コーチ会話エージェント（T208 / FR-101,104,112）。

長期記憶(LTM)・短期会話(STM)・バイタル要約・目標を文脈に、Bedrock で応答を生成する。
返り値は未検証。送信前に必ず guardrail を通すこと（FR-106）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from night_ramen.agents.prompts import ModelInvoker
from night_ramen.memory.schemas import DailyVitalRecord, Goal

HEALTH_COACH_PROMPT = (
    "あなたはユーザーの深夜の夜食ラーメン断ちを支える、思いやりのある健康コーチです。"
    "睡眠・体調・自律神経・夜食ラーメンに焦点を当て、ユーザーのバイタルデータや過去の会話を踏まえて、"
    "短く具体的で前向きな返答をします。雑談に逸れても、自然に体調や夜食の話題へ戻してください。"
    "人格否定・罵倒・差別的表現は決して用いず、相手を追い詰めず希望を持たせること。"
    "医療診断は行わず、必要なら専門家への相談を促してください。返答は2〜4文程度で簡潔に。"
)

# ガードレール不合格時の安全な会話フォールバック
SAFE_CONVERSATION_FALLBACK = (
    "うまく言葉にできなかったみたい、ごめんね。"
    "よかったら、今の体調や昨夜の眠りについて教えてくれる？一緒に考えよう。"
)


@dataclass
class ConversationContext:
    recent_turns: list[dict] = field(default_factory=list)   # STM [{role,text}]
    ltm_snippets: list[str] = field(default_factory=list)    # LTM
    vitals_summary: str = ""
    goal_summary: str = ""


def summarize_vitals(vitals: list[DailyVitalRecord]) -> str:
    """直近バイタルを短い文脈文字列へ要約（FR-104）。"""
    usable = [v for v in vitals if v.has_any_vitals]
    if not usable:
        return "（バイタルデータはまだ十分にありません）"
    latest = usable[-1]
    ramen_days = sum(1 for v in usable if v.ramen_last_night is True)
    parts = []
    if latest.hrv is not None:
        parts.append(f"直近HRV {latest.hrv}ms")
    if latest.deep_sleep_min is not None:
        parts.append(f"深い睡眠 {latest.deep_sleep_min}分")
    if latest.next_day_steps is not None:
        parts.append(f"翌日歩数 {latest.next_day_steps}")
    parts.append(f"直近{len(usable)}日でラーメン{ramen_days}回")
    return " / ".join(parts)


def build_context(recent_turns, ltm_snippets, vitals, goal: Goal) -> ConversationContext:
    return ConversationContext(
        recent_turns=recent_turns,
        ltm_snippets=ltm_snippets,
        vitals_summary=summarize_vitals(vitals),
        goal_summary=f"成功率目標 {goal.success_rate_target:.0%} / 減量 {goal.weight_loss_target_kg}kg",
    )


def generate_reply(
    user_text: str, ctx: ConversationContext, invoker: ModelInvoker, model_id: str
) -> str:
    """健康コーチ応答を生成（FR-101,112）。未検証。送信前に guardrail 必須。"""
    history = "\n".join(f"{t['role']}: {t['text']}" for t in ctx.recent_turns)
    ltm = "\n".join(f"- {s}" for s in ctx.ltm_snippets) or "（特になし）"
    prompt = (
        f"【ユーザーについて記憶していること】\n{ltm}\n\n"
        f"【最近の会話】\n{history or '（なし）'}\n\n"
        f"【バイタル傾向】{ctx.vitals_summary}\n"
        f"【目標】{ctx.goal_summary}\n\n"
        f"【ユーザーの今のメッセージ】{user_text}\n\n"
        "健康コーチとして、上記を踏まえた返答をしてください。"
    )
    return invoker(model_id, prompt, HEALTH_COACH_PROMPT).strip()
