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

# 実機（Fitbit Inspire 3, 2026-06-14 確認）のレスポンス形状を再現するフェイク

def _fake_post_factory(rollups: dict):
    """型ID → rollupDataPoints レスポンス のマップから dailyRollUp POST フェイクを作る。"""

    def fake_post(url, headers, body):
        assert headers["Authorization"].startswith("Bearer ")
        for type_id, resp in rollups.items():
            if f"/dataTypes/{type_id}/" in url:
                return resp
        return {"rollupDataPoints": []}

    return fake_post


def _fake_get_factory(lists: dict):
    """型ID → dataPoints レスポンス のマップから list GET フェイクを作る。"""

    def fake_get(url, headers):
        for type_id, resp in lists.items():
            if f"/dataTypes/{type_id}/" in url:
                return resp
        return {"dataPoints": []}

    return fake_get


def _rollup(camel, field, value, date="2026-06-14"):
    y, m, d = (int(x) for x in date.split("-"))
    return {"rollupDataPoints": [
        {"civilStartTime": {"date": {"year": y, "month": m, "day": d}},
         camel: {field: value}}
    ]}


def test_fetch_raw_maps_real_shapes():
    rollups = {
        "heart-rate": _rollup("heartRate", "beatsPerMinuteAvg", 58.5),
        "steps": _rollup("steps", "countSum", "8200"),            # countSum は文字列
        "active-energy-burned": _rollup("activeEnergyBurned", "kcalSum", 752.5),
    }
    lists = {
        "heart-rate-variability": {"dataPoints": [
            {"heartRateVariability": {
                "sampleTime": {"civilTime": {"date": {"year": 2026, "month": 6, "day": 14}}},
                "rootMeanSquareOfSuccessiveDifferencesMilliseconds": 40.0}},
            {"heartRateVariability": {
                "sampleTime": {"civilTime": {"date": {"year": 2026, "month": 6, "day": 14}}},
                "rootMeanSquareOfSuccessiveDifferencesMilliseconds": 50.0}},
        ]},
    }
    source = GoogleHealthApiSource(
        access_token_provider=lambda: "ya29.fake",
        http_post=_fake_post_factory(rollups),
        http_get=_fake_get_factory(lists),
    )
    raw = source.fetch_raw("2026-06-14")
    assert raw["night_avg_hr"] == 58.5
    assert raw["next_day_steps"] == 8200          # 文字列→int
    assert raw["next_day_calories"] == 752        # float→int
    assert raw["hrv"] == 45.0                      # (40+50)/2
    # データの無い型は欠損(None)
    assert raw["weight_kg"] is None
    assert raw["sleep_score"] is None


def test_sleep_stages_are_derived():
    # STAGES 配列から深い睡眠時間と中途覚醒回数を派生（実機レスポンス形状）
    sleep_resp = {"dataPoints": [{"sleep": {
        "interval": {"endTime": "2026-06-14T23:36:00Z", "endUtcOffset": "0s"},
        "type": "STAGES",
        "stages": [
            {"type": "AWAKE", "startTime": "2026-06-14T00:00:00Z", "endTime": "2026-06-14T00:05:00Z"},
            {"type": "LIGHT", "startTime": "2026-06-14T00:05:00Z", "endTime": "2026-06-14T00:30:00Z"},
            {"type": "DEEP",  "startTime": "2026-06-14T00:30:00Z", "endTime": "2026-06-14T01:30:00Z"},
            {"type": "AWAKE", "startTime": "2026-06-14T01:30:00Z", "endTime": "2026-06-14T01:35:00Z"},
            {"type": "REM",   "startTime": "2026-06-14T01:35:00Z", "endTime": "2026-06-14T02:00:00Z"},
        ],
    }}]}
    source = GoogleHealthApiSource(
        access_token_provider=lambda: "t",
        http_post=_fake_post_factory({}),
        http_get=_fake_get_factory({"sleep": sleep_resp}),
    )
    raw = source.fetch_raw("2026-06-14")
    assert raw["deep_sleep_min"] == 60       # DEEP 1時間
    # 総睡眠 = LIGHT(25) + DEEP(60) + REM(25) = 110分（AWAKE は除外）
    assert raw["total_sleep_min"] == 110
    assert raw["awakenings"] == 1            # 先頭以外の AWAKE 1回
    assert raw["sleep_score"] is None        # STAGES には含まれない


def test_fetch_raw_token_failure_is_consent_revoked():
    def bad_token():
        raise PermissionError("invalid_grant")

    source = GoogleHealthApiSource(access_token_provider=bad_token)
    with pytest.raises(ConsentRevokedError):
        source.fetch_raw("2026-06-14")


def test_integration_with_fetch_daily_vitals():
    # 異常値(心拍999)は除外され欠損記録される（FR-015）
    rollups = {"heart-rate": _rollup("heartRate", "beatsPerMinuteAvg", 999)}
    source = GoogleHealthApiSource(
        access_token_provider=lambda: "t",
        http_post=_fake_post_factory(rollups),
        http_get=_fake_get_factory({}),
    )
    rec = fetch_daily_vitals("2026-06-14", source)
    assert rec.night_avg_hr is None
    assert "night_avg_hr" in rec.missing_fields
