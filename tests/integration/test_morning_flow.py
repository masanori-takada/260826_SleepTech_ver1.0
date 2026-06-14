"""朝ループの統合テスト（T021 / US2 / FR-001,002,012）。"""

from __future__ import annotations

from night_ramen.collectors import ConsentRevokedError
from night_ramen.collectors.self_report import StaticSelfReportSource
from night_ramen.memory.client import MemoryClient
from night_ramen.workflow.morning import run_morning


class _Source:
    def __init__(self, raw):
        self._raw = raw

    def fetch_raw(self, date):
        if self._raw == "revoked":
            raise ConsentRevokedError("revoked")
        return self._raw


def test_morning_collects_and_persists():
    memory = MemoryClient()
    health = _Source({"sleep_score": 75, "hrv": 50.0})
    reports = StaticSelfReportSource({"2026-06-14": True})

    rec = run_morning("2026-06-14", memory, health, reports)

    assert rec is not None
    assert rec.ramen_last_night is True  # 自己申告が紐づく
    saved = memory.get_vitals("2026-06-14", "2026-06-14")
    assert len(saved) == 1
    assert saved[0].sleep_score == 75


def test_morning_stops_on_consent_revoked():
    memory = MemoryClient()
    health = _Source("revoked")
    reports = StaticSelfReportSource()

    rec = run_morning("2026-06-14", memory, health, reports)

    assert rec is None  # 収集停止
    assert memory.get_vitals("2026-06-14", "2026-06-14") == []
