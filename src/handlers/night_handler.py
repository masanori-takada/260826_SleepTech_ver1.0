"""夜 Lambda（EventBridge Scheduler 22:30 JST 起動）。

Task 16 の Scheduler payload 契約は
``{"scheduled_time": "<aws.scheduler.scheduled-time>"}`` である。
scheduled_time は予定実行時刻のISO文字列としてJSTへ変換し、起点日と
now_hを同じ日時から導出する。互換性のため、dateとnow_hのペア、および
入力なしのmanual用wall-clock fallbackも受け付ける。

C2対応（出荷前レビュー）: AWS公式ドキュメントでは
``<aws.scheduler.scheduled-time>`` は ``2022-03-22T18:59:43Z`` のような
UTCのISO文字列として置換されると明記されているが、実機でしか最終確認
できないため、防御的なフォールバックを入れる。判定基準は
``hd.resolve_scheduled_time`` を参照（最終レビュー対応で「壁時計との
乖離量」から「scheduled_time自体の形」に基づく判定へ変更した）。
NIGHT_ROLLOVER_HOURのロールオーバー処理が0:00をまたぐ再試行遅延を
既に吸収しているため、このフォールバックが起きても要件は満たせる。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date as date_cls

from sleeptech.config import load_settings
from sleeptech.observability import StructuredLogger
from sleeptech.workflow.night import run_night
from src.handlers import deps as hd

# 夜フローのcron予定時刻（JST）。scheduled_timeのローカル誤ラベル判定に使う
# （hd.resolve_scheduled_time参照）。
NIGHT_CRON_HOUR_JST = 22
NIGHT_CRON_MINUTE_JST = 30


def _bad_request(message: str) -> dict:
    return {"statusCode": 400, "body": '{"error": "' + message + '"}'}


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
        resolved = hd.resolve_scheduled_time(
            event["scheduled_time"], NIGHT_CRON_HOUR_JST, NIGHT_CRON_MINUTE_JST
        )
        if resolved is None:
            return _bad_request("scheduled_time must be a timezone-aware ISO timestamp")
        scheduled, mislabeled = resolved
        if mislabeled:
            # scheduled_time がローカル時刻に誤ってZを付けて返ってきた疑いがある
            # （hd.resolve_scheduled_time参照）。壁時計にフォールバックし、その旨を記録する。
            StructuredLogger().log(
                "scheduled_time_local_label_fallback",
                scheduled_time=scheduled.isoformat(),
            )
            scheduled = None
    elif has_pair_field:
        pair = _pair(event)
        if pair is None:
            return _bad_request("date and now_h must be valid together")

    settings = load_settings()
    wall_now = hd.jst_now()
    if scheduled is not None:
        # 再試行時の現在時刻ではなく、Schedulerの予定時刻を使う。
        date = hd.origin_date(scheduled, night=True)
        current_h = hd.now_h(scheduled, night=True)
    elif has_pair_field:
        date, current_h = pair
    else:
        date = hd.origin_date(wall_now, night=True)
        current_h = hd.now_h(wall_now, night=True)
    deadline = hd.deadline_from_context(context)
    deps = (hd.build_night_deps(settings, deadline) if deadline is not None
            else hd.build_night_deps(settings))
    return run_night(date, current_h, deps)
