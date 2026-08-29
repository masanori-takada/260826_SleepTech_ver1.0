"""リカバリー提案を LLM-as-Judge で採点する（仕様 第7節の拡張）。

judge は「安全性」ではなく「品質」を見る。安全語句・数値の捏造・1:1構造は
すべて決定論的な検査（guardrail.validator, model.condition）が先に弾いており、
ここを通ってきた案は既に送っても害がない。したがって judge が使えないときは
fail-open（そのまま送る）を採る。判定できたうえで軸が0だったときだけ拒否する。

スコアは絶対的な品質尺度ではなく、退行検知と推移の信号として読む。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

# 各軸の重み。合計1.0。
# 文章の調子（tone）は軸に置かない。人格否定・恐怖訴求・医学的断定は
# guardrail.validator が決定論的に弾いており、judge で二重に見る価値が薄い。
# 最も重いのは feasibility。実機では「15時に仮眠」「朝5:50に屋外光」のように、
# 生理的には正しくても平日の勤務中には実行できない提案が繰り返し出た。
# 実行できない提案は、どれだけ理屈が正しくても価値がゼロになる。
WEIGHTS = {
    "relevance": 0.25,
    "feasibility": 0.40,
    "timing": 0.35,
}
AXES = tuple(WEIGHTS)

# 採用閾値（0〜100）。全軸3で60、全軸4で80になるため、
# 70は「おおむね良好寄り」を要求することを意味する。
PASS_SCORE = 70

_MAX_REASON_CHARS = 60


@dataclass(frozen=True)
class JudgeScore:
    """4軸の整数スコア（0〜5）と短い理由。"""

    relevance: int
    feasibility: int
    timing: int
    reason: str = ""

    @property
    def total(self) -> int:
        """0〜100の総合点。"""
        weighted = sum(WEIGHTS[axis] * getattr(self, axis) for axis in AXES)
        return round(weighted / 5.0 * 100)

    @property
    def eligible(self) -> bool:
        """1軸でも0なら採用対象から外す（判定できた拒否）。"""
        return min(getattr(self, axis) for axis in AXES) > 0

    def as_dict(self) -> dict:
        return {axis: getattr(self, axis) for axis in AXES}


def _as_score(value) -> int | None:
    """整数・文字列・floatを 0〜5 の整数へ寄せる。範囲外や型不正は None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"-?\d+(?:\.0+)?", value):
            return None
        value = float(value)
    if isinstance(value, float):
        if value != int(value):
            return None
        value = int(value)
    if not isinstance(value, int):
        return None
    return value if 0 <= value <= 5 else None


def parse_scores(text: str) -> JudgeScore | None:
    """judge の応答をJSONとして読む。読めなければ None（判定不能）。"""
    if not text:
        return None
    body = re.sub(r"```(?:json)?|```", "", text).strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    values: dict[str, int] = {}
    for axis in AXES:
        score = _as_score(data.get(axis))
        if score is None:
            return None
        values[axis] = score
    reason = data.get("reason", "")
    if not isinstance(reason, str):
        reason = ""
    return JudgeScore(reason=reason[:_MAX_REASON_CHARS], **values)


def score_recovery(llm_client, system: str, user: str, log=None) -> JudgeScore | None:
    """リカバリー節を採点する。LLM障害・解析不能はいずれも None を返す。"""
    try:
        text = llm_client.complete(system, user)
    except Exception as exc:  # noqa: BLE001 — judge の失敗は送信を止めない
        if log is not None:
            log.log("judge_failed", error=str(exc))
        return None
    score = parse_scores(text)
    if score is None and log is not None:
        log.log("judge_unparsable", length=len(text or ""))
    return score
