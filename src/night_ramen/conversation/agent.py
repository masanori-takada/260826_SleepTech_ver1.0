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
    today: str = ""   # 今日の日付(JST)。「昨日」等の相対表現の解決に用いる
    just_recorded: str = ""   # 今回の発話で記録した申告（例「6/12のラーメン: あり」）。確認応答用


def _fmt_minutes(m: int | None) -> str:
    """分 → 「7時間12分」表記。None は「記録なし」。"""
    if m is None:
        return "記録なし"
    return f"{m // 60}時間{m % 60}分"


def _day_line(v: DailyVitalRecord) -> str:
    """1日分を日別の読みやすい1行に整形（特定日の質問に答えられるよう日付付き）。

    体重・体脂肪率・夜間平均心拍も含める。これらが欠けていると会話エージェントが
    値を参照できず「連携できていない」と誤って答えてしまうため（測定日のみ値、他は「—」）。
    """
    ramen = {True: "ラーメン有", False: "我慢", None: "申告なし"}[v.ramen_last_night]
    sleep = _fmt_minutes(v.total_sleep_min)
    deep = f"{v.deep_sleep_min}分" if v.deep_sleep_min is not None else "—"
    hrv = f"{v.hrv}ms" if v.hrv is not None else "—"
    awak = f"{v.awakenings}回" if v.awakenings is not None else "—"
    steps = v.next_day_steps if v.next_day_steps is not None else "—"
    hr = f"{v.night_avg_hr:.0f}" if v.night_avg_hr is not None else "—"
    weight = f"{v.weight_kg}kg" if v.weight_kg is not None else "—"
    fat = f"{v.body_fat_pct}%" if v.body_fat_pct is not None else "—"
    return (f"{v.date}: 睡眠{sleep}(深い{deep}/中途覚醒{awak}) "
            f"HRV{hrv} 夜間心拍{hr} 体重{weight} 体脂肪{fat} 翌日歩数{steps} {ramen}")


def summarize_vitals(vitals: list[DailyVitalRecord], recent_days: int = 30) -> str:
    """直近バイタルを日別の文脈文字列へ整形（FR-104）。

    「昨日の睡眠時間は？」のような特定日の質問に答えられるよう、
    直近数日を日付付きで列挙する。体重・体脂肪率は毎日測るとは限らないため、
    直近の測定値を末尾に別途明示し、「最近の体重は？」に即答できるようにする。
    """
    usable = [v for v in vitals if v.has_any_vitals]
    if not usable:
        return "（バイタルデータはまだ十分にありません）"
    recent = usable[-recent_days:]
    lines = "\n".join(_day_line(v) for v in recent)
    ramen_days = sum(1 for v in recent if v.ramen_last_night is True)

    # 体重・体脂肪は測定日が散発的なため、期間内の最新測定値を明示（質問への即答用）
    latest_w = next((v for v in reversed(recent) if v.weight_kg is not None), None)
    latest_f = next((v for v in reversed(recent) if v.body_fat_pct is not None), None)
    extras = []
    if latest_w is not None:
        extras.append(f"最新体重 {latest_w.weight_kg}kg（{latest_w.date}測定）")
    if latest_f is not None:
        extras.append(f"最新体脂肪 {latest_f.body_fat_pct}%（{latest_f.date}測定）")
    extra_line = ("\n" + " / ".join(extras)) if extras else ""

    return (f"直近{len(recent)}日のバイタル（新しい行ほど最近）:\n{lines}\n"
            f"（この期間でラーメン{ramen_days}回）{extra_line}")


def build_context(
    recent_turns, ltm_snippets, vitals, goal: Goal, today: str = "", just_recorded: str = ""
) -> ConversationContext:
    return ConversationContext(
        recent_turns=recent_turns,
        ltm_snippets=ltm_snippets,
        vitals_summary=summarize_vitals(vitals),
        goal_summary=f"成功率目標 {goal.success_rate_target:.0%} / 減量 {goal.weight_loss_target_kg}kg",
        today=today,
        just_recorded=just_recorded,
    )


def generate_reply(
    user_text: str, ctx: ConversationContext, invoker: ModelInvoker, model_id: str
) -> str:
    """健康コーチ応答を生成（FR-101,112）。未検証。送信前に guardrail 必須。"""
    history = "\n".join(f"{t['role']}: {t['text']}" for t in ctx.recent_turns)
    ltm = "\n".join(f"- {s}" for s in ctx.ltm_snippets) or "（特になし）"
    today_line = f"【今日の日付】{ctx.today}（「昨日」はこの前日を指す）\n" if ctx.today else ""
    recorded_line = (
        f"【今ユーザーが記録した申告】{ctx.just_recorded}。これを一言で確認してから返答する。\n"
        if ctx.just_recorded
        else ""
    )
    prompt = (
        f"{today_line}"
        f"{recorded_line}"
        f"【ユーザーについて記憶していること】\n{ltm}\n\n"
        f"【最近の会話】\n{history or '（なし）'}\n\n"
        f"【バイタルデータ（日付別。睡眠時間や歩数などの質問にはこの数値で答える）】\n{ctx.vitals_summary}\n\n"
        f"【目標】{ctx.goal_summary}\n\n"
        f"【ユーザーの今のメッセージ】{user_text}\n\n"
        "健康コーチとして、上記を踏まえた返答をしてください。"
        "睡眠時間・歩数など具体的な数値を聞かれたら、上のバイタルデータから該当日の値を答えてください。"
        "データに無い項目は正直に『まだ取れていない』と伝えてください。"
    )
    return invoker(model_id, prompt, HEALTH_COACH_PROMPT).strip()
