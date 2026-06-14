"""Google Health API クライアント（実APIソース / FR-001,003,012）。

Google Health API（Fitbit Web API の次世代、S2S向け）から前夜のバイタルを取得する。
- ベースURL: https://health.googleapis.com/v4
- 取得: POST /v4/users/me/dataTypes/{DataType}/dataPoints:dailyRollUp
- 認証: OAuth 2.0（リフレッシュトークン → アクセストークン）

テスト容易性のため、アクセストークン取得とHTTP呼び出しを注入可能にしている。

NOTE: データ型名とレスポンスJSONの正確なフィールドパスは、初回は公式 codelab
("Make your first Google Health API call") の実レスポンスで検証すること。
本実装は防御的パース（不明な形は None=欠損として許容, FR-015）で、マッピングを
_DATA_TYPE_MAP に集約しているため、実レスポンスに合わせてここだけ調整すればよい。
"""

from __future__ import annotations

from typing import Callable

from night_ramen.collectors import ConsentRevokedError

BASE_URL = "https://health.googleapis.com/v4"

# 我々の VITAL_FIELDS ←→ Google Health API のデータ型 と値抽出のマッピング。
# value_key: dailyRollUp レスポンスの集計値から取り出すキー（実レスポンスで要確認）。
_DATA_TYPE_MAP: dict[str, dict] = {
    "night_avg_hr": {"data_type": "HeartRate", "agg": "avg", "value_key": "bpm"},
    "hrv": {"data_type": "HeartRateVariability", "agg": "avg", "value_key": "rmssd"},
    "respiration_rate": {"data_type": "RespiratoryRate", "agg": "avg", "value_key": "breathsPerMinute"},
    "skin_temp_delta": {"data_type": "SkinTemperature", "agg": "avg", "value_key": "delta"},
    "weight_kg": {"data_type": "Weight", "agg": "latest", "value_key": "kilograms"},
    "body_fat_pct": {"data_type": "BodyFat", "agg": "latest", "value_key": "percentage"},
    "next_day_steps": {"data_type": "Steps", "agg": "sum", "value_key": "count"},
    "next_day_calories": {"data_type": "ActiveEnergyBurned", "agg": "sum", "value_key": "kilocalories"},
    # 睡眠系は Sleep データ型の集計から複数フィールドを派生（_extract_sleep で処理）
}

# 睡眠は1データ型から複数指標を取り出すため別扱い
_SLEEP_DATA_TYPE = "Sleep"


class GoogleHealthApiSource:
    """Google Health API から fetch_raw(date) -> dict を提供する HealthDataSource 実装。"""

    def __init__(
        self,
        access_token_provider: Callable[[], str],
        http_post: Callable[[str, dict, dict], dict] | None = None,
    ) -> None:
        """
        access_token_provider: 呼ぶたびに有効なアクセストークン文字列を返す。
        http_post: (url, headers, json_body) -> レスポンスJSON。テストで注入可能。
        """
        self._token_provider = access_token_provider
        self._http_post = http_post or _default_http_post

    def fetch_raw(self, date: str) -> dict:
        """指定日の前夜バイタルを生 dict（キー=VITAL_FIELDS 互換）で返す。

        アクセストークン取得失敗（リフレッシュトークン失効）は同意失効とみなす（FR-012）。
        個別データ型の取得失敗は欠損として握りつぶす（FR-015）。
        """
        try:
            token = self._token_provider()
        except PermissionError as exc:
            # invalid_grant 等 → 同意失効として上位で収集停止（FR-012）
            raise ConsentRevokedError(str(exc)) from exc

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        raw: dict = {}

        for field, spec in _DATA_TYPE_MAP.items():
            raw[field] = self._fetch_one(date, spec, headers)

        # 睡眠系（sleep_score / deep_sleep_min / awakenings）
        raw.update(self._fetch_sleep(date, headers))
        return raw

    def _fetch_one(self, date: str, spec: dict, headers: dict):
        url = f"{BASE_URL}/users/me/dataTypes/{spec['data_type']}/dataPoints:dailyRollUp"
        body = _daily_rollup_body(date)
        try:
            resp = self._http_post(url, headers, body)
        except Exception:  # noqa: BLE001 — 個別失敗は欠損許容（FR-015）
            return None
        return _extract_value(resp, spec["agg"], spec["value_key"])

    def _fetch_sleep(self, date: str, headers: dict) -> dict:
        url = f"{BASE_URL}/users/me/dataTypes/{_SLEEP_DATA_TYPE}/dataPoints:dailyRollUp"
        body = _daily_rollup_body(date)
        try:
            resp = self._http_post(url, headers, body)
        except Exception:  # noqa: BLE001
            return {"sleep_score": None, "deep_sleep_min": None, "awakenings": None}
        return _extract_sleep(resp)


def _daily_rollup_body(date: str) -> dict:
    """dailyRollUp のリクエストボディ（指定日の civil day 1日分）。"""
    return {
        "localDateRange": {
            "startDate": _civil_date(date),
            "endDate": _civil_date(date),
        }
    }


def _civil_date(date: str) -> dict:
    y, m, d = (int(x) for x in date.split("-"))
    return {"year": y, "month": m, "day": d}


def _extract_value(resp: dict, agg: str, value_key: str):
    """dailyRollUp レスポンスから集計値を防御的に取り出す。

    想定形: {"dataPoints": [{"value": {<value_key>: ..., "<agg>": {...}}}]}
    実レスポンスに応じて要調整。取り出せなければ None（欠損）。
    """
    try:
        points = resp.get("dataPoints") or resp.get("rollups") or []
        if not points:
            return None
        value = points[0].get("value", points[0])
        # 集計値が入れ子（avg/sum/latest）の場合と直値の場合の双方に対応
        agg_block = value.get(agg) if isinstance(value, dict) else None
        if isinstance(agg_block, dict) and value_key in agg_block:
            return agg_block[value_key]
        if isinstance(value, dict) and value_key in value:
            return value[value_key]
        return None
    except (AttributeError, IndexError, TypeError):
        return None


def _extract_sleep(resp: dict) -> dict:
    """Sleep データ型から睡眠スコア・深い睡眠・中途覚醒を派生（要実レスポンス確認）。"""
    try:
        points = resp.get("dataPoints") or []
        value = points[0].get("value", {}) if points else {}
    except (AttributeError, IndexError, TypeError):
        value = {}
    return {
        "sleep_score": value.get("sleepScore"),
        "deep_sleep_min": value.get("deepSleepMinutes"),
        "awakenings": value.get("awakeningsCount"),
    }


def _default_http_post(url: str, headers: dict, body: dict) -> dict:
    """本番用: urllib で Health API へ POST。"""
    import json
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 — 固定の Google 端点
        return json.loads(r.read().decode("utf-8"))
