"""Google Health API 連携テスト（T022補完 / FR-001,003,012,015）。

ネットワーク・トークン取得・HTTP をフェイク注入し、認証〜パース〜欠損許容を検証する。
"""

from __future__ import annotations

import pytest

from night_ramen.collectors import ConsentRevokedError
from night_ramen.collectors.google_auth_helper import get_access_token
from night_ramen.collectors.google_health import fetch_daily_vitals
from night_ramen.collectors.google_health_api import GoogleHealthApiSource


# ---- OAuth トークン取得 ----

def test_get_access_token_success():
    def poster(url, form):
        assert form["grant_type"] == "refresh_token"
        return {"access_token": "ya29.fake", "expires_in": 3599}

    secret = {"client_id": "c", "client_secret": "s", "refresh_token": "r"}
    assert get_access_token(secret, token_poster=poster) == "ya29.fake"


def test_get_access_token_missing_fields():
    with pytest.raises(ValueError):
        get_access_token({"client_id": "c"}, token_poster=lambda u, f: {})


def test_get_access_token_invalid_grant_raises_permission():
    def poster(url, form):
        return {"error": "invalid_grant"}

    secret = {"client_id": "c", "client_secret": "s", "refresh_token": "expired"}
    with pytest.raises(PermissionError):
        get_access_token(secret, token_poster=poster)


# ---- Health API 取得・パース ----

def _fake_post_factory(responses: dict):
    """data_type 名 → レスポンスJSON のマップから HTTP POST フェイクを作る。"""

    def fake_post(url, headers, body):
        assert headers["Authorization"].startswith("Bearer ")
        for dt, resp in responses.items():
            if f"/dataTypes/{dt}/" in url:
                return resp
        return {"dataPoints": []}

    return fake_post


def test_fetch_raw_maps_metrics():
    responses = {
        "HeartRate": {"dataPoints": [{"value": {"avg": {"bpm": 58.0}}}]},
        "Steps": {"dataPoints": [{"value": {"sum": {"count": 8200}}}]},
        "Weight": {"dataPoints": [{"value": {"latest": {"kilograms": 71.4}}}]},
        "Sleep": {"dataPoints": [{"value": {"sleepScore": 82,
                                            "deepSleepMinutes": 95,
                                            "awakeningsCount": 1}}]},
    }
    source = GoogleHealthApiSource(
        access_token_provider=lambda: "ya29.fake",
        http_post=_fake_post_factory(responses),
    )
    raw = source.fetch_raw("2026-06-14")
    assert raw["night_avg_hr"] == 58.0
    assert raw["next_day_steps"] == 8200
    assert raw["weight_kg"] == 71.4
    assert raw["sleep_score"] == 82
    assert raw["deep_sleep_min"] == 95
    # 取得しなかった型は欠損(None)
    assert raw["body_fat_pct"] is None


def test_fetch_raw_token_failure_is_consent_revoked():
    def bad_token():
        raise PermissionError("invalid_grant")

    source = GoogleHealthApiSource(access_token_provider=bad_token)
    with pytest.raises(ConsentRevokedError):
        source.fetch_raw("2026-06-14")


def test_integration_with_fetch_daily_vitals():
    # 実APIソース → fetch_daily_vitals（異常値除外・欠損記録）まで通す
    responses = {
        "HeartRate": {"dataPoints": [{"value": {"avg": {"bpm": 999}}}]},  # 異常値
        "Sleep": {"dataPoints": [{"value": {"sleepScore": 78}}]},
    }
    source = GoogleHealthApiSource(
        access_token_provider=lambda: "t",
        http_post=_fake_post_factory(responses),
    )
    rec = fetch_daily_vitals("2026-06-14", source)
    assert rec.night_avg_hr is None          # 異常値は除外（FR-015）
    assert "night_avg_hr" in rec.missing_fields
    assert rec.sleep_score == 78
