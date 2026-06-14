"""収集層テスト（T020 / FR-003,012,015）。"""

from __future__ import annotations

import pytest

from night_ramen.collectors import ConsentRevokedError
from night_ramen.collectors.google_health import HealthDataSource, fetch_daily_vitals


class _FakeSource:
    def __init__(self, raw):
        self._raw = raw

    def fetch_raw(self, date):
        if self._raw == "revoked":
            raise ConsentRevokedError("consent revoked")
        return self._raw


def test_partial_missing_is_tolerated():
    src = _FakeSource({"sleep_score": 80})  # 他は欠損
    rec = fetch_daily_vitals("2026-06-14", src)
    assert rec.sleep_score == 80
    assert "hrv" in rec.missing_fields  # 欠損として記録


def test_abnormal_values_are_excluded():
    src = _FakeSource({"sleep_score": 999, "hrv": 55.0})  # 999 は範囲外
    rec = fetch_daily_vitals("2026-06-14", src)
    assert rec.sleep_score is None          # 異常値は除外
    assert "sleep_score" in rec.missing_fields
    assert rec.hrv == 55.0


def test_consent_revoked_propagates():
    src = _FakeSource("revoked")
    with pytest.raises(ConsentRevokedError):
        fetch_daily_vitals("2026-06-14", src)


def test_self_report_linked():
    src = _FakeSource({"ramen_last_night": True, "sleep_score": 60})
    rec = fetch_daily_vitals("2026-06-14", src)
    assert rec.ramen_last_night is True
