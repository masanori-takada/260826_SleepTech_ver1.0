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

from sleeptech.collectors import ConsentRevokedError

BASE_URL = "https://health.googleapis.com/v4"

# --- dailyRollUp 系（POST）: field -> (型ID, camelキー, 値フィールド, キャスト, スケール) ---
# スケール: APIが返す単位からストア単位への変換係数（通常1.0、体重のみグラム→kg で0.001）
_ROLLUP_MAP: dict[str, tuple[str, str, str, type, float]] = {
    "night_avg_hr": ("heart-rate", "heartRate", "beatsPerMinuteAvg", float, 1.0),
    "next_day_steps": ("steps", "steps", "countSum", int, 1.0),
    "next_day_calories": ("active-energy-burned", "activeEnergyBurned", "kcalSum", int, 1.0),
    "weight_kg": ("weight", "weight", "weightGramsAvg", float, 0.001),      # API単位=グラム
    "body_fat_pct": ("body-fat", "bodyFat", "bodyFatPercentageAvg", float, 1.0),
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
        for field, (type_id, camel, value_field, cast, scale) in _ROLLUP_MAP.items():
            raw[field] = self._fetch_rollup(date, type_id, camel, value_field, cast, scale, headers)

        # list 系（HRV）
        for field, (type_id, camel, value_field) in _LIST_MAP.items():
            raw[field] = self._fetch_list_avg(date, type_id, camel, value_field, headers)

        # 睡眠（スコープがあれば取得、無ければ None）
        raw.update(self._fetch_sleep(date, headers))
        return raw

    # ---- dailyRollUp ----
    def _fetch_rollup(self, date, type_id, camel, value_field, cast, scale, headers):
        url = f"{BASE_URL}/users/me/dataTypes/{type_id}/dataPoints:dailyRollUp"
        body = {"range": _date_range(date), "windowSizeDays": 1}
        try:
            resp = self._http_post(url, headers, body)
        except Exception:  # noqa: BLE001 — 個別失敗は欠損許容（FR-015）
            return None
        for point in resp.get("rollupDataPoints", []):
            if _point_date(point.get("civilStartTime")) == date:
                return _scale(_cast(point.get(camel, {}).get(value_field), cast), scale)
        # 日付一致が無ければ最初の点を採用（範囲が単日のとき）
        points = resp.get("rollupDataPoints", [])
        if points:
            return _scale(_cast(points[0].get(camel, {}).get(value_field), cast), scale)
        return None

    # ---- list（平均集計）---- ページネーション対応
    def _fetch_list_avg(self, date, type_id, camel, value_field, headers):
        """全ページを取得してから date に一致するポイントだけ平均する。

        Google Health API は pageSize を指定しても1ページに少数しか返さない場合があり、
        nextPageToken でページネーションしないと直近分しか取れない（実測: HRV 1000件/ページ→12ページ）。
        """
        vals = []
        next_token = None
        while True:
            params: dict = {"pageSize": 1000}
            if next_token:
                params["pageToken"] = next_token
            url = f"{BASE_URL}/users/me/dataTypes/{type_id}/dataPoints?" + urllib.parse.urlencode(params)
            try:
                resp = self._http_get(url, headers)
            except Exception:  # noqa: BLE001
                break
            for point in resp.get("dataPoints", []):
                block = point.get(camel, {})
                civ = block.get("sampleTime", {}).get("civilTime")
                if civ is None or _civil_date_str(civ) == date:
                    v = block.get(value_field)
                    if v is not None:
                        vals.append(v)
            next_token = resp.get("nextPageToken")
            if not next_token:
                break
        return round(sum(vals) / len(vals), 2) if vals else None

    # ---- sleep（list、スコープ必須）---- ページネーション対応
    def _fetch_sleep(self, date, headers):
        """睡眠は STAGES 配列で返るため、深い睡眠時間・中途覚醒回数を派生する。

        実機(Fitbit Inspire 3)レスポンス: sleep.{interval, type:"STAGES", stages:[{type,startTime,endTime}]}
        sleep_score は本データ型に含まれないため None（別データ型が必要なら今後追加）。
        ページネーション必須: pageSize=500 でも1ページ13〜25件しか返らず nextPageToken あり（実測）。
        全ページを取得してから対象日を探すことで6ヶ月分以上をカバーする。
        """
        empty = {"sleep_score": None, "total_sleep_min": None,
                 "deep_sleep_min": None, "awakenings": None}
        all_points: list = []
        next_token = None
        while True:
            params: dict = {"pageSize": 500}
            if next_token:
                params["pageToken"] = next_token
            url = f"{BASE_URL}/users/me/dataTypes/{_SLEEP_TYPE}/dataPoints?" + urllib.parse.urlencode(params)
            try:
                resp = self._http_get(url, headers)
            except Exception:  # noqa: BLE001 — 403(スコープ未付与) 含め欠損許容
                return empty
            all_points.extend(resp.get("dataPoints", []))
            next_token = resp.get("nextPageToken")
            if not next_token:
                break

        point = _pick_sleep_for_date(all_points, date)
        if point is None:
            return empty
        return _derive_sleep_metrics(point.get("sleep", {}))


# --- sleep helpers ---

def _local_date(iso_utc: str, offset_str: str | None) -> str | None:
    """UTC ISO 時刻 + UTCオフセット秒 → ローカル日付(YYYY-MM-DD)。"""
    from datetime import datetime, timedelta

    if not iso_utc:
        return None
    try:
        t = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        off = int(str(offset_str).rstrip("s")) if offset_str else 0
        return (t + timedelta(seconds=off)).date().isoformat()
    except (ValueError, TypeError):
        return None


def _minutes_between(a: str, b: str) -> float:
    from datetime import datetime

    ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
    tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return (tb - ta).total_seconds() / 60.0


def _pick_sleep_for_date(points: list, date: str):
    """睡眠記録のうち、ローカル起床日(=interval.endTime)が date のものを選ぶ。

    一致するものがなければ None（欠損扱い）。フォールバックで別日付のデータを
    返すと全日付に同一値が記録される誤りを防ぐ。
    """
    for p in points:
        interval = p.get("sleep", {}).get("interval", {})
        if _local_date(interval.get("endTime"), interval.get("endUtcOffset")) == date:
            return p
    return None


def _derive_sleep_metrics(sleep_block: dict) -> dict:
    """STAGES 配列から総睡眠時間・深い睡眠時間(分)・中途覚醒回数を派生。

    総睡眠時間 = AWAKE 以外（LIGHT/DEEP/REM 等）のステージ時間の合計。
    """
    stages = sleep_block.get("stages") or []
    if not stages:
        return {"sleep_score": None, "total_sleep_min": None,
                "deep_sleep_min": None, "awakenings": None}

    total_min = 0.0
    deep_min = 0.0
    awakenings = 0
    for i, s in enumerate(stages):
        stype = (s.get("type") or "").upper()
        start, end = s.get("startTime"), s.get("endTime")
        if not (start and end):
            continue
        dur = _minutes_between(start, end)
        if stype == "AWAKE":
            if i > 0:  # 睡眠開始(先頭)以外の AWAKE を中途覚醒としてカウント
                awakenings += 1
        else:
            total_min += dur  # LIGHT/DEEP/REM 等は睡眠時間に算入
            if stype == "DEEP":
                deep_min += dur

    return {
        "sleep_score": None,  # STAGES データには含まれない（必要なら別データ型）
        "total_sleep_min": int(round(total_min)),
        "deep_sleep_min": int(round(deep_min)),
        "awakenings": awakenings,
    }


# --- rollup helpers ---

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


def _scale(value, factor: float):
    """単位変換係数を適用する（factor=1.0 のときは無変換）。"""
    if value is None or factor == 1.0:
        return value
    return round(value * factor, 3)


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
