"""Google Health バイタル取得（T022 / FR-001,003,012,015）。

OAuth トークンは Secrets Manager から取得する想定。外部APIの具体スキーマは実行時に確定するため、
取得処理を `HealthDataSource` プロトコルに抽象化し、テストはフェイクを注入する。
異常値は除外し missing_fields に記録、欠損は欠損として保存（処理は失敗させない）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from night_ramen.collectors import ConsentRevokedError
from night_ramen.memory.schemas import VITAL_FIELDS, DailyVitalRecord

# 異常値の許容レンジ（測定エラー由来の値を除外, FR-015）
_VALID_RANGES = {
    "sleep_score": (0, 100),
    "awakenings": (0, 50),
    "deep_sleep_min": (0, 600),
    "night_avg_hr": (20, 200),
    "hrv": (1, 300),
    "skin_temp_delta": (-5, 5),
    "respiration_rate": (4, 60),
    "weight_kg": (20, 400),
    "body_fat_pct": (1, 80),
    "next_day_steps": (0, 100000),
    "next_day_calories": (0, 20000),
}


class HealthDataSource(Protocol):
    """指定日の生バイタル（dict）を返す。同意失効時は ConsentRevokedError を送出。"""

    def fetch_raw(self, date: str) -> dict: ...


def _sanitize(field: str, value):
    """異常値を None 化（除外）。範囲未定義はそのまま。"""
    if value is None:
        return None
    lo, hi = _VALID_RANGES.get(field, (None, None))
    if lo is None:
        return value
    try:
        return value if lo <= value <= hi else None
    except TypeError:
        return None


def fetch_daily_vitals(date: str, source: HealthDataSource) -> DailyVitalRecord:
    """指定日の前夜バイタルを取得し DailyVitalRecord に整形（FR-001,003）。

    同意失効は ConsentRevokedError をそのまま伝播（FR-012）。
    一部指標の欠損・異常値は許容（FR-015）。
    """
    raw = source.fetch_raw(date)  # ConsentRevokedError はここで送出されうる
    record = DailyVitalRecord(date=date, ingested_at=datetime.now(timezone.utc).isoformat())
    record.ramen_last_night = raw.get("ramen_last_night")
    for field in VITAL_FIELDS:
        setattr(record, field, _sanitize(field, raw.get(field)))
    record.recompute_missing()
    return record
