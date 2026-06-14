"""ガードレール検証の必須テスト（T009 / Constitution 原則 II / FR-008,009,015）。

契約（contracts/agent-interfaces.md C5）に基づく:
- 差別的表現 → 不合格
- 目標/仕様からの逸脱 → 不合格
- 人格否定・過激な罵倒（正論の暴力）→ 不合格
- 健全な個別化メッセージ → 合格
- 検証内部例外 → fail-closed（不合格）
- 3回連続不合格 → 固定安全文面（fallback）
"""

from __future__ import annotations

import pytest

from night_ramen.guardrail.validator import (
    GuardrailContext,
    validate,
    generate_safe_message,
)
from night_ramen.guardrail.fallback import SAFE_FALLBACK_MESSAGE
from night_ramen.memory.schemas import GuardrailResult


@pytest.fixture
def ctx() -> GuardrailContext:
    return GuardrailContext(goal_summary="成功率85% / 減量2kg")


def test_discriminatory_language_fails(ctx):
    verdict = validate("お前みたいなデブは人間как失格だ、くたばれ", ctx)
    assert verdict.passed is False
    assert verdict.reasons


def test_personal_attack_is_blocked(ctx):
    # 正論の暴力：人格否定・過激な罵倒
    verdict = validate("意志が弱い無能なお前は一生治らない。生きる価値がない。", ctx)
    assert verdict.passed is False


def test_goal_deviation_fails(ctx):
    # 目標（ラーメンを断つ）から逸脱した指示
    verdict = validate("今夜はラーメンを好きなだけ食べて構いません。", ctx)
    assert verdict.passed is False


def test_healthy_personalized_message_passes(ctx):
    text = (
        "昨夜ラーメンを食べた日は睡眠スコアが12点下がり、HRVも落ちていました。"
        "今夜は我慢して、明日の自分を軽くしてあげましょう。応援しています。"
    )
    verdict = validate(text, ctx)
    assert verdict.passed is True
    assert verdict.reasons == []


def test_validation_is_fail_closed_on_exception(ctx):
    # LLM 検査が例外を投げても安全側（不合格）に倒れる
    def boom(_text: str) -> bool:
        raise RuntimeError("LLM unavailable")

    ctx_with_failing_llm = GuardrailContext(
        goal_summary=ctx.goal_summary, llm_check=boom
    )
    verdict = validate("健全に見えるメッセージ", ctx_with_failing_llm)
    assert verdict.passed is False


def test_generate_safe_message_falls_back_after_failures(ctx):
    # 常に不合格になる生成器 → 2回再生成しても落ちるので固定安全文面
    def bad_generator() -> str:
        return "無能なお前はくたばれ"

    body, result = generate_safe_message(bad_generator, ctx, max_retries=2)
    assert result == GuardrailResult.FALLBACK
    assert body == SAFE_FALLBACK_MESSAGE


def test_generate_safe_message_returns_passing_text(ctx):
    good = (
        "昨夜のデータでは深い睡眠が短くなっていました。今夜は一杯我慢して、"
        "気持ちよく朝を迎えましょう。"
    )

    def good_generator() -> str:
        return good

    body, result = generate_safe_message(good_generator, ctx, max_retries=2)
    assert result == GuardrailResult.PASS
    assert body == good


def test_fallback_message_is_itself_safe(ctx):
    # 固定安全文面自体がガードレールを通過すること（自己無撞着）
    verdict = validate(SAFE_FALLBACK_MESSAGE, ctx)
    assert verdict.passed is True
