"""スキーマと記憶クライアントの不変条件テスト（T008 / FR-003,010,015）。"""

from __future__ import annotations

import pytest

from night_ramen.memory.client import MemoryClient
from night_ramen.memory.schemas import (
    DailyVitalRecord,
    PersuasionStrategy,
)


def test_recompute_missing_detects_none_fields():
    rec = DailyVitalRecord(date="2026-06-14", sleep_score=80, hrv=None)
    rec.recompute_missing()
    assert "hrv" in rec.missing_fields
    assert "sleep_score" not in rec.missing_fields


def test_has_any_vitals():
    empty = DailyVitalRecord(date="2026-06-14")
    assert empty.has_any_vitals is False
    one = DailyVitalRecord(date="2026-06-14", sleep_score=70)
    assert one.has_any_vitals is True


def test_save_and_query_vitals_roundtrip():
    client = MemoryClient()
    client.save_vital(DailyVitalRecord(date="2026-06-10", sleep_score=60))
    client.save_vital(DailyVitalRecord(date="2026-06-12", sleep_score=80))
    got = client.get_vitals("2026-06-01", "2026-06-30")
    assert [g.date for g in got] == ["2026-06-10", "2026-06-12"]


def test_save_strategy_rejects_unvalidated():
    client = MemoryClient()
    bad = PersuasionStrategy(version=2, system_prompt="x", guardrail_passed=False)
    with pytest.raises(ValueError):
        client.save_strategy(bad)


def test_save_strategy_accepts_validated():
    client = MemoryClient()
    good = PersuasionStrategy(version=2, system_prompt="x", guardrail_passed=True)
    client.save_strategy(good)
    assert client.get_current_strategy().version == 2
