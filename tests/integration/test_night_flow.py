"""夜ループの統合テスト（T011 / US1 Independent Test / FR-007,008,009,014,015）。

Bedrock/LINE はフェイク注入。ダミーのバイタルで「生成→検証→送信」を検証する。
"""

from __future__ import annotations

import pytest

from night_ramen.agents.prompts import make_fake_invoker
from night_ramen.config import load_settings
from night_ramen.memory.client import MemoryClient
from night_ramen.memory.schemas import DailyVitalRecord, Goal, GuardrailResult
from night_ramen.notifier.line import DryRunPushClient, InMemoryIdempotencyStore
from night_ramen.workflow.night import NightDependencies, run_night


def _seed_vitals(memory: MemoryClient) -> None:
    # ラーメンを食べた日は睡眠が悪い、食べない日は良い、という対比データ
    memory.save_vital(DailyVitalRecord(date="2026-06-10", ramen_last_night=True,
                                       sleep_score=58, hrv=35.0, deep_sleep_min=40,
                                       next_day_steps=4000, weight_kg=72.0))
    memory.save_vital(DailyVitalRecord(date="2026-06-11", ramen_last_night=False,
                                       sleep_score=82, hrv=55.0, deep_sleep_min=90,
                                       next_day_steps=9000, weight_kg=71.5))
    memory.save_vital(DailyVitalRecord(date="2026-06-12", ramen_last_night=True,
                                       sleep_score=60, hrv=38.0, deep_sleep_min=45,
                                       next_day_steps=4500, weight_kg=72.1))


def _deps(memory, push, store, responder):
    return NightDependencies(
        settings=load_settings(),
        memory=memory,
        invoker=make_fake_invoker(responder),
        goal=Goal(success_rate_target=0.85, weight_loss_target_kg=2.0, baseline_weight_kg=73.0),
        idempotency=store,
        push_client=push,
    )


def test_night_flow_sends_personalized_message():
    memory = MemoryClient()
    _seed_vitals(memory)
    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()

    # フェイク LLM: 分析要約と、バイタルに言及した健全な説得文を返す
    def responder(model_id, prompt, system):
        if "悪影響を簡潔に要約" in prompt or "アナリスト" in system:
            return "ラーメンを食べた日は睡眠スコアとHRVが低下しています。"
        return ("昨夜ラーメンを食べた日は睡眠スコアが下がり、HRVも低下していました。"
                "今夜は我慢して、明日の歩数を増やしましょう。応援しています。")

    msg = run_night("2026-06-13", _deps(memory, push, store, responder))

    assert msg.delivered is True
    assert msg.guardrail_result == GuardrailResult.PASS
    assert len(push.sent) == 1
    assert msg.referenced_metrics  # バイタルに言及している（SC-004）


def test_night_flow_blocks_unsafe_message_and_falls_back():
    memory = MemoryClient()
    _seed_vitals(memory)
    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()

    # フェイク LLM: 常に人格否定（不合格）→ 固定安全文面にフォールバック
    def responder(model_id, prompt, system):
        if "アナリスト" in system:
            return "分析要約"
        return "意志が弱い無能なお前はくたばれ"

    msg = run_night("2026-06-13", _deps(memory, push, store, responder))

    assert msg.guardrail_result == GuardrailResult.FALLBACK
    # 危険文は送信されず、安全文面が送られる
    assert "くたばれ" not in push.sent[0]
    assert msg.delivered is True


def test_night_flow_no_data_uses_safe_fallback():
    memory = MemoryClient()  # データなし
    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()

    def responder(model_id, prompt, system):
        return "（呼ばれないはず）"

    msg = run_night("2026-06-13", _deps(memory, push, store, responder))

    assert msg.guardrail_result == GuardrailResult.FALLBACK
    assert msg.delivered is True


def test_night_flow_is_idempotent_within_night():
    memory = MemoryClient()
    _seed_vitals(memory)
    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()

    def responder(model_id, prompt, system):
        if "アナリスト" in system or "要約" in prompt:
            return "分析要約"
        return "睡眠スコアが下がっていました。今夜は我慢しましょう。"

    run_night("2026-06-13", _deps(memory, push, store, responder))
    second = run_night("2026-06-13", _deps(memory, push, store, responder))

    assert second.delivered is False  # 2回目は送信されない（FR-014）
    assert len(push.sent) == 1
