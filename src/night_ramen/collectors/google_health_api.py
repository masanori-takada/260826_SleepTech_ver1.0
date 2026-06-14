"""Google Health API クライアント（実APIソース / FR-001,003,012）。

Google Health API（Fitbit Web API の次世代、S2S向け）から前夜のバイタルを取得する。
- ベースURL: https://health.googleapis.com/v4
- データ型ID: kebab-case（例: heart-rate, active-energy-burned, heart-rate-variability）
- 取得方法は型により2系統:
    * dailyRollUp（POST）: steps, heart-rate, active-energy-burned, weight, body-fat
    * list（GET）        : heart-rate-variability, sleep（sleep は専用スコープ必須）
- 認証: OAuth 2.0（リフレッシュトークン → アクセストークン）

レスポンス形状は 2026-06-14 に実機（Fitbit Inspire 3）で確認済み:
  dailyRollUp -> {"rollupDataPoints": [{"civilStartTime": {...}, "<camelType>": {"<field>": ...}}]}
  list        -> {"dataPoints": [{"<camelType>": {"<field>": ..., "sampleTime": {"civilTime": {...}}}}]}

テスト容易性のため access_token_provider / http_post / http_get を注入可能にしている。
取得失敗・欠損は None（FR-015）。アクセストークン失効は ConsentRevokedError（FR-012）。
"""

from __future__ import annotations

import urllib.parse
from typing import Callable

from night_ramen.collectors import ConsentRevokedError

BASE_URL = "https://health.googleapis.com/v4"

# --- dailyRollUp 系（POST）: field -> (型ID, camelキー, 値フィールド, キャスト) ---
_ROLLUP_MAP: dict[str, tuple[str, str, str, type]] = {
    "night_avg_hr": ("heart-rate", "heartRate", "beatsPerMinuteAvg", float),
    "next_day_steps": ("steps", "steps", "countSum", int),
    "next_day_calories": ("active-energy-burned", "activeEnergyBurned", "kcalSum", int),
    "weight_kg": ("weight", "weight", "kilogramsAvg", float),
    "body_fat_pct": ("body-fat", "bodyFat", "percentageAvg", float),
}

# --- list 系（GET）: field -> (型ID, camelキー, 値フィールド) ---
# 当夜に複数サンプルがあるため平均をとる。
_LIST_MAP: dict[str, tuple[str, str, str]] = {
    "hrv": ("heart-rate-variability", "heartRateVariability",
            "rootMeanSquareOfSuccessiveDifferencesMilliseconds"),
}

# sleep は専用スコープが必要。スコープ未付与なら 403 → None（FR-015 で許容）。
_SLEEP_TYPE = "sleep"


class GoogleHealthApiSource:
    """Google Health API から fetch_raw(date) -> dict を提供する HealthDataSource 実装。"""

    def __init__(
        self,
        access_token_provider: Callable[[], str],
        http_post: Callable[[str, dict, dict], dict] | None = None,
        http_get: Callable[[str, dict], dict] | None = None,
    ) -> None:
        self._token_provider = access_token_provider
        self._http_post = http_post or _default_http_post
        self._http_get = http_get or _default_http_get

    def fetch_raw(self, date: str) -> dict:
        """指定日のバイタルを生 dict（キー=VITAL_FIELDS 互換）で返す。"""
        try:
            token = self._token_provider()
        except PermissionError as exc:
            raise ConsentRevokedError(str(exc)) from exc

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        raw: dict = {}

        # dailyRollUp 系
        for field, (type_id, camel, value_field, cast) in _ROLLUP_MAP.items():
            raw[field] = self._fetch_rollup(date, type_id, camel, value_field, cast, headers)

        # list 系（HRV）
        for field, (type_id, camel, value_field) in _LIST_MAP.items():
            raw[field] = self._fetch_list_avg(date, type_id, camel, value_field, headers)

        # 睡眠（スコープがあれば取得、無ければ None）
        raw.update(self._fetch_sleep(date, headers))
        return raw

    # ---- dailyRollUp ----
    def _fetch_rollup(self, date, type_id, camel, value_field, cast, headers):
        url = f"{BASE_URL}/users/me/dataTypes/{type_id}/dataPoints:dailyRollUp"
        body = {"range": _date_range(date), "windowSizeDays": 1}
        try:
            resp = self._http_post(url, headers, body)
        except Exception:  # noqa: BLE001 — 個別失敗は欠損許容（FR-015）
            return None
        for point in resp.get("rollupDataPoints", []):
            if _point_date(point.get("civilStartTime")) == date:
                return _cast(point.get(camel, {}).get(value_field), cast)
        # 日付一致が無ければ最初の点を採用（範囲が単日のとき）
        points = resp.get("rollupDataPoints", [])
        if points:
            return _cast(points[0].get(camel, {}).get(value_field), cast)
        return None

    # ---- list（平均集計） ----
    def _fetch_list_avg(self, date, type_id, camel, value_field, headers):
        url = f"{BASE_URL}/users/me/dataTypes/{type_id}/dataPoints?" + urllib.parse.urlencode(
            {"pageSize": 1000}
        )
        try:
            resp = self._http_get(url, headers)
        except Exception:  # noqa: BLE001
            return None
        vals = []
        for point in resp.get("dataPoints", []):
            block = point.get(camel, {})
            civ = block.get("sampleTime", {}).get("civilTime")
            if civ is None or _civil_date_str(civ) == date:
                v = block.get(value_field)
                if v is not None:
                    vals.append(v)
        return round(sum(vals) / len(vals), 2) if vals else None

    # ---- sleep（list、スコープ必須） ----
    def _fetch_sleep(self, date, headers):
        url = f"{BASE_URL}/users/me/dataTypes/{_SLEEP_TYPE}/dataPoints?" + urllib.parse.urlencode(
            {"pageSize": 50}
        )
        try:
            resp = self._http_get(url, headers)
        except Exception:  # noqa: BLE001 — 403(スコープ未付与) 含め欠損許容
            return {"sleep_score": None, "deep_sleep_min": None, "awakenings": None}
        for point in resp.get("dataPoints", []):
            block = point.get("sleep", {})
            return {
                "sleep_score": block.get("sleepScore"),
                "deep_sleep_min": block.get("deepSleepMinutes"),
                "awakenings": block.get("awakeningsCount"),
            }
        return {"sleep_score": None, "deep_sleep_min": None, "awakenings": None}


# --- helpers ---

def _date_range(date: str) -> dict:
    """CivilDateTime の閉-開区間 [date, date+1日)。"""
    from datetime import date as date_cls, timedelta

    d = date_cls.fromisoformat(date)
    nxt = d + timedelta(days=1)
    return {
        "start": {"date": {"year": d.year, "month": d.month, "day": d.day}},
        "end": {"date": {"year": nxt.year, "month": nxt.month, "day": nxt.day}},
    }


def _point_date(civil_dt: dict | None) -> str | None:
    if not civil_dt:
        return None
    return _civil_date_str(civil_dt)


def _civil_date_str(civil_dt: dict) -> str | None:
    d = civil_dt.get("date") if civil_dt else None
    if not d:
        return None
    try:
        return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"
    except KeyError:
        return None


def _cast(value, cast: type):
    if value is None:
        return None
    try:
        return cast(float(value)) if cast is int else cast(value)
    except (TypeError, ValueError):
        return None


def _default_http_post(url: str, headers: dict, body: dict) -> dict:
    import json
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 — 固定の Google 端点
        return json.loads(r.read().decode("utf-8"))


def _default_http_get(url: str, headers: dict) -> dict:
    import json
    import urllib.request

    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))
