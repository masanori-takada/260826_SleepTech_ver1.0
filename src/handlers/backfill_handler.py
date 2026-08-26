"""通知を発生させずに過去の睡眠データを再取得するLambda。"""

from __future__ import annotations

from datetime import date as date_cls, timedelta
from typing import Callable

from sleeptech.collectors import ConsentRevokedError
from sleeptech.collectors.google_auth_helper import get_access_token
from sleeptech.collectors.google_health_api import GoogleHealthApiSource
from sleeptech.collectors.vitals import to_daily_vital
from sleeptech.config import load_settings
from sleeptech.llm.client import ExecutionDeadline
from sleeptech.memory.dynamo_backend import DynamoBackend
from handlers.deps import deadline_from_context

MAX_DAYS = 30
BACKFILL_DAY_BUDGET_SEC = 30.0
VITAL_PK = "VITAL"
SLEEP_DATA_FIELDS = (
    "total_sleep_min",
    "deep_sleep_min",
    "rem_sleep_min",
    "bedtime_h",
    "wake_h",
    "awakenings",
)
# Googleから再取得してVITALへ反映する項目。awakeningsも必須対象に含める。
REQUIRED_BACKFILL_FIELDS = frozenset(
    {
        "total_sleep_min",
        "deep_sleep_min",
        "rem_sleep_min",
        "awakenings",
        "bedtime_h",
        "wake_h",
        "hrv",
        "skin_temp_delta",
        "respiration_rate",
        "night_avg_hr",
        "weight_kg",
        "body_fat_pct",
    }
)


def cached_access_token_provider(secret: dict) -> Callable[[], str]:
    """1回のLambda呼び出し中だけアクセストークンを再利用する。"""
    token: str | None = None

    def provider() -> str:
        nonlocal token
        if token is None:
            token = get_access_token(secret)
        return token

    return provider


def build_backfill_dependencies(
    deadline: ExecutionDeadline | None = None,
) -> tuple[GoogleHealthApiSource, DynamoBackend]:
    """バックフィル専用のGoogleソースとDynamoDBバックエンドを構築する。"""
    from handlers.deps import _secret

    settings = load_settings()
    secret = _secret(settings.secret_google, settings.region)
    source = GoogleHealthApiSource(cached_access_token_provider(secret), deadline=deadline)
    return source, DynamoBackend(settings.table, settings.region)


def _parse_range(event: object) -> tuple[date_cls, date_cls] | None:
    if not isinstance(event, dict) or set(event) != {"start_date", "end_date"}:
        return None
    start_value = event.get("start_date")
    end_value = event.get("end_date")
    if not isinstance(start_value, str) or not isinstance(end_value, str):
        return None
    try:
        start = date_cls.fromisoformat(start_value)
        end = date_cls.fromisoformat(end_value)
    except ValueError:
        return None
    if end < start or (end - start).days + 1 > MAX_DAYS:
        return None
    return start, end


def _merge_vital(existing: dict | None, fetched: dict) -> dict:
    """既存JSONを基礎に、取得できたGoogle値だけを上書きする。"""
    merged = dict(existing or {})
    for key, value in fetched.items():
        if key in REQUIRED_BACKFILL_FIELDS and value is not None:
            merged[key] = value
    merged["date"] = fetched["date"]
    if "ingested_at" not in merged and fetched.get("ingested_at") is not None:
        merged["ingested_at"] = fetched["ingested_at"]
    return merged


def _failure(origin_date: str, error: str) -> dict[str, str]:
    return {"date": origin_date, "error": error}


def run_backfill(
    start: date_cls,
    end: date_cls,
    source: GoogleHealthApiSource,
    memory: DynamoBackend,
    deadline: ExecutionDeadline | None = None,
) -> dict:
    """指定範囲を1日ずつ処理し、失敗しても次の日へ進む。"""
    result = {
        "requested": (end - start).days + 1,
        "updated": 0,
        "with_sleep": 0,
        "no_data": 0,
        "failed": 0,
        "failures": [],
        "stopped_early": False,
        "next_date": None,
    }
    current = start
    while current <= end:
        origin = current.isoformat()
        wake_date = (current + timedelta(days=1)).isoformat()
        if deadline is not None and not deadline.can_start(BACKFILL_DAY_BUDGET_SEC):
            result["stopped_early"] = True
            result["next_date"] = origin
            return result
        try:
            raw = source.fetch_raw(wake_date)
            if all(raw.get(field) is None for field in REQUIRED_BACKFILL_FIELDS):
                result["no_data"] += 1
                current += timedelta(days=1)
                continue
            with_sleep = raw.get("total_sleep_min") is not None and raw.get("wake_h") is not None
            vital = to_daily_vital(raw, wake_date)
            memory.merge_json(
                VITAL_PK,
                origin,
                lambda existing: _merge_vital(existing, vital.to_dict()),
            )
            result["updated"] += 1
            if with_sleep:
                result["with_sleep"] += 1
        except ConsentRevokedError:
            result["failed"] += 1
            result["failures"].append(_failure(origin, "consent_revoked"))
        except Exception as exc:  # 日別に隔離し、秘密値を結果へ出さない
            result["failed"] += 1
            result["failures"].append(_failure(origin, type(exc).__name__))
        current += timedelta(days=1)
    return result


def handler(event, context=None):
    """Lambdaエントリポイント。入力不正は外部依存を呼ばずに返す。"""
    parsed = _parse_range(event)
    if parsed is None:
        return {"statusCode": 400, "body": "invalid date range"}
    deadline = deadline_from_context(context)
    if deadline is None:
        source, memory = build_backfill_dependencies()
    else:
        source, memory = build_backfill_dependencies(deadline=deadline)
    return run_backfill(*parsed, source, memory, deadline=deadline)
