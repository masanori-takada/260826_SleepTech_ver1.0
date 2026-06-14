"""起床トリガー収集の統合テスト（Feature 003 / FR-003）。

Webhook の会話処理（002）に組み込まれた起床トリガー収集（_collect_today_vitals）を検証する。
「おはよう」等を受けると会話応答の前に当日 run_morning が走り、最新バイタルが保存される。
収集失敗・同意失効は会話を止めない（握りつぶしてログのみ）。
"""

from __future__ import annotations

import handlers.morning_handler as mh
import handlers.webhook_handler as wh
from night_ramen.collectors import ConsentRevokedError
from night_ramen.collectors.self_report import StaticSelfReportSource
from night_ramen.config import load_settings
from night_ramen.memory.client import MemoryClient
from night_ramen.observability import StructuredLogger


class _FakeHealth:
    """run_morning に注入するフェイクの HealthDataSource。"""

    def __init__(self, raw):
        self._raw = raw

    def fetch_raw(self, date):
        if self._raw == "revoked":
            raise ConsentRevokedError("revoked")
        return self._raw


def test_collect_saves_today_vitals(monkeypatch):
    memory = MemoryClient()
    health = _FakeHealth(
        {"sleep_score": 75, "deep_sleep_min": 80, "awakenings": 2, "night_avg_hr": 58, "hrv": 48.0}
    )
    monkeypatch.setattr(
        mh,
        "build_morning_dependencies",
        lambda s: (memory, health, StaticSelfReportSource()),
    )

    wh._collect_today_vitals("2026-06-15", load_settings(), StructuredLogger())

    saved = memory.get_vitals("2026-06-15", "2026-06-15")
    assert len(saved) == 1
    assert saved[0].sleep_score == 75
    assert saved[0].hrv == 48.0


def test_collect_consent_revoked_saves_nothing(monkeypatch):
    memory = MemoryClient()
    monkeypatch.setattr(
        mh,
        "build_morning_dependencies",
        lambda s: (memory, _FakeHealth("revoked"), StaticSelfReportSource()),
    )

    # 例外を投げず（会話は継続）、収集も保存もされない
    wh._collect_today_vitals("2026-06-15", load_settings(), StructuredLogger())

    assert memory.get_vitals("2026-06-15", "2026-06-15") == []


def test_collect_swallows_dependency_error(monkeypatch):
    def _boom(settings):
        raise RuntimeError("secrets unavailable")

    monkeypatch.setattr(mh, "build_morning_dependencies", _boom)

    # 依存構築の失敗を握りつぶし、例外を伝播しない（会話を止めない）
    wh._collect_today_vitals("2026-06-15", load_settings(), StructuredLogger())
