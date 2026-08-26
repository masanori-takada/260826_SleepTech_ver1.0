"""夜 Lambda（EventBridge Scheduler 22:30 JST 起動）。

Task 16 の Scheduler payload 契約は
``{"scheduled_time": "<aws.scheduler.scheduled-time>"}`` である。
scheduled_time は予定実行時刻のISO文字列としてJSTへ変換し、起点日と
now_hを同じ日時から導出する。互換性のため、dateとnow_hのペア、および
入力なしのmanual用wall-clock fallbackも受け付ける。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date as date_cls
from datetime import datetime

from sleeptech.config import load_settings
from sleeptech.workflow.night import run_night
from src.handlers import deps as hd


def _bad_request(message: str) -> dict:
    return {"statusCode": 400, "body": '{"error": "' + message + '"}'}


def _scheduled_datetime(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(hd.JST)


def _pair(event: Mapping) -> tuple[str, float] | None:
    date = event.get("date")
    now_h = event.get("now_h")
    if not isinstance(date, str):
        return None
    try:
        date_cls.fromisoformat(date)
        current_h = float(now_h)
    except (TypeError, ValueError):
        return None
    if isinstance(now_h, bool) or not math.isfinite(current_h):
        return None
    return date, current_h


def handler(event, context=None) -> dict:
    if not isinstance(event, Mapping):
        return _bad_request("event must be an object")

    event_keys = frozenset(event)
    if event_keys not in {
        frozenset(),
        frozenset({"scheduled_time"}),
        frozenset({"date", "now_h"}),
    }:
        return _bad_request("event contains unknown keys")

    has_scheduled = "scheduled_time" in event
    has_date = "date" in event
    has_now_h = "now_h" in event
    has_pair_field = has_date or has_now_h
    if has_scheduled and has_pair_field:
        return _bad_request("scheduled_time cannot be combined with date or now_h")
    if has_pair_field and not (has_date and has_now_h):
        return _bad_request("date and now_h must be provided together")

    scheduled = None
    if has_scheduled:
        scheduled = _scheduled_datetime(event["scheduled_time"])
        if scheduled is None:
            return _bad_request("scheduled_time must be a timezone-aware ISO timestamp")
    elif has_pair_field:
        pair = _pair(event)
        if pair is None:
            return _bad_request("date and now_h must be valid together")

    settings = load_settings()
    if scheduled is not None:
        # 再試行時の現在時刻ではなく、Schedulerの予定時刻を使う。
        date = hd.origin_date(scheduled, night=True)
        current_h = hd.now_h(scheduled, night=True)
    elif has_pair_field:
        date, current_h = pair
    else:
        now = hd.jst_now()
        date = hd.origin_date(now, night=True)
        current_h = hd.now_h(now, night=True)
    return run_night(date, current_h, hd.build_night_deps(settings))
