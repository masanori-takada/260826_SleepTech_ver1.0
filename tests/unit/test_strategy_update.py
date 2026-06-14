"""戦略の自律更新テスト（T030 / FR-010 不変条件）。"""

from __future__ import annotations

import pytest

from night_ramen.agents.persuader import update_strategy
from night_ramen.agents.prompts import make_fake_invoker
from night_ramen.memory.client import MemoryClient
from night_ramen.memory.schemas import PersuasionStrategy


def test_update_reflects_history_and_is_unverified():
    current = PersuasionStrategy(version=1, system_prompt="旧方針", guardrail_passed=True)

    def responder(model_id, prompt, system):
        # 履歴を踏まえた新方針を返す
        return "睡眠データへの言及が効果的だったため、今後は睡眠を強調する。"

    new = update_strategy(current, [], [], make_fake_invoker(responder), "model")

    assert new.version == 2
    assert "睡眠" in new.system_prompt
    assert new.guardrail_passed is False  # 未検証（保存前に guardrail 必須）


def test_unverified_strategy_cannot_be_saved():
    memory = MemoryClient()
    new = PersuasionStrategy(version=2, system_prompt="x", guardrail_passed=False)
    with pytest.raises(ValueError):
        memory.save_strategy(new)
