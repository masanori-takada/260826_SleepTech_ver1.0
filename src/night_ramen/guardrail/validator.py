"""送信前ガードレール検証（T010 / Constitution 原則 II / FR-008,009,015）。

2層構成:
 1. 決定的チェック: 禁止語・人格否定・目標逸脱パターンの機械検査（外部依存なしで常に実行）。
 2. 任意の LLM チェック: GuardrailContext.llm_check が与えられれば実行（差別・有害・逸脱の高度判定）。

すべて fail-closed: 判定不能・例外時は「不合格」とみなす。
contracts/agent-interfaces.md C5 を実装。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from night_ramen.guardrail.fallback import SAFE_FALLBACK_MESSAGE
from night_ramen.memory.schemas import GuardrailResult


@dataclass
class GuardrailContext:
    """検証に必要な文脈。

    llm_check: text を受け取り True=安全/False=危険 を返す任意の検査関数。
               例外を投げた場合は fail-closed（不合格）として扱う。
    """

    goal_summary: str = ""
    llm_check: Callable[[str], bool] | None = None


@dataclass
class GuardrailVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)


# (a) 差別的・不適切な語、(c) 人格否定・過激な罵倒（正論の暴力）
_ABUSE_PATTERNS = [
    r"くたばれ",
    r"死ね",
    r"消えろ",
    r"生きる価値(が)?ない",
    r"人間失格",
    r"一生治らない",
    r"無能",
    r"クズ",
    r"ゴミ",
    r"デブ",  # 体型を貶める語
    r"気持ち悪い",
    r"バカ",
    r"アホ",
]

# (b) 目標（夜食ラーメンを断つ）からの逸脱＝「食べることを推奨・許可する」表現のみを検知。
#     「今夜はラーメンを我慢しよう」等の正当なコーチング発言を誤検知しないよう、
#     “推奨/許可” の語を伴う場合に限定する（会話機能で誤検知が判明したため精緻化）。
_DEVIATION_PATTERNS = [
    r"ラーメンを?\s*(好きなだけ|たくさん|どんどん)?\s*食べ(て|ても)(いい|構いません|大丈夫|OK)",
    r"ラーメン.{0,8}食べ(ちゃ|て)(いい|ましょう|よう！)",
    r"我慢しなくて(いい|よい|大丈夫|OK)",
    r"食べても(問題|大丈夫|いい)",
    r"(食べ|ラーメン).{0,6}(我慢しなくて|気にしなくて)(いい|大丈夫)",
]

_ABUSE_RE = re.compile("|".join(_ABUSE_PATTERNS))
_DEVIATION_RE = re.compile("|".join(_DEVIATION_PATTERNS))


def _deterministic_checks(text: str) -> list[str]:
    """決定的チェック。違反理由のリストを返す（空なら合格）。"""
    reasons: list[str] = []
    if not text or not text.strip():
        reasons.append("空のメッセージ")
        return reasons
    if _ABUSE_RE.search(text):
        reasons.append("差別的表現または人格否定・過激な罵倒（正論の暴力）を含む")
    if _DEVIATION_RE.search(text):
        reasons.append("目標（夜食ラーメンを断つ）から逸脱する内容を含む")
    return reasons


def validate(text: str, context: GuardrailContext) -> GuardrailVerdict:
    """送信前検証（FR-008）。fail-closed（FR-015）。"""
    reasons = _deterministic_checks(text)

    # 任意の LLM 検査層。例外は fail-closed。
    if context.llm_check is not None:
        try:
            if not context.llm_check(text):
                reasons.append("LLM 安全検査により危険と判定")
        except Exception as exc:  # noqa: BLE001 — fail-closed が目的
            reasons.append(f"検査不能のため安全側に却下: {exc!r}")

    return GuardrailVerdict(passed=not reasons, reasons=reasons)


def generate_safe_message(
    generator: Callable[[], str],
    context: GuardrailContext,
    max_retries: int = 2,
) -> tuple[str, GuardrailResult]:
    """生成→検証を最大 max_retries 回まで再試行（FR-009）。

    1回目を含め (max_retries + 1) 回まで生成を試み、いずれも不合格なら固定安全文面を返す。
    返り値: (本文, PASS|FALLBACK)。
    """
    attempts = max_retries + 1
    for _ in range(attempts):
        try:
            candidate = generator()
        except Exception:  # noqa: BLE001 — 生成失敗もフォールバックへ
            break
        if validate(candidate, context).passed:
            return candidate, GuardrailResult.PASS

    return SAFE_FALLBACK_MESSAGE, GuardrailResult.FALLBACK
