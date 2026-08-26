"""朝 Lambda（EventBridge Scheduler 8:00 JST 起動）。

起点日は前日である点に注意（前夜の夜メッセージと同じ日付を使う）。
送信の冪等は run_morning が内部で担保するため、ここでは判定しない。

最終レビュー対応（優先2）: 以前は event.get("date") が無ければ
hd.jst_now()（壁時計）だけから起点日を導いていた。EventBridge Scheduler は
RetryPolicy未指定だと既定で最大24時間・185回リトライするため、8:00起動が
失敗し続けて日付が変わった後（例: 翌00:30）に成功すると起点日が1日ずれる。
すると
  - 08-26の朝メッセージが永久に欠落する
  - IDEM#MORNING/2026-08-27 を先食いするため、翌08-27の本来の朝メッセージ
    まで already_notified で無言消滅する（2日分の欠落）
夜フローと同じ scheduled_time 契約を受け取り、同じ安全な再解釈ロジック
（hd.resolve_scheduled_time）で起点日を導くことでこれを防ぐ。
CDK側（infra/stacks/sleeptech_stack.py）でも MorningSchedule に
{"scheduled_time": "<aws.scheduler.scheduled-time>"} を渡し、両Scheduleに
RetryPolicyを明示して日跨ぎリトライ自体を起こしにくくしている。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as date_cls

from sleeptech.config import load_settings
from sleeptech.observability import StructuredLogger
from sleeptech.workflow.morning import run_morning
from src.handlers import deps as hd

# 朝フローのcron予定時刻（JST）。scheduled_timeのローカル誤ラベル判定に使う
# （hd.resolve_scheduled_time参照）。
MORNING_CRON_HOUR_JST = 8
MORNING_CRON_MINUTE_JST = 0


def _bad_request(message: str) -> dict:
    return {"statusCode": 400, "body": '{"error": "' + message + '"}'}


def handler(event, context=None) -> dict:
    event = event or {}
    if not isinstance(event, Mapping):
        return _bad_request("event must be an object")

    event_keys = frozenset(event)
    if event_keys not in {frozenset(), frozenset({"scheduled_time"}), frozenset({"date"})}:
        return _bad_request("event contains unknown keys")

    settings = load_settings()

    if "scheduled_time" in event:
        resolved = hd.resolve_scheduled_time(
            event["scheduled_time"], MORNING_CRON_HOUR_JST, MORNING_CRON_MINUTE_JST
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
            date = hd.origin_date(hd.jst_now(), morning=True)
        else:
            # 再試行時の現在時刻ではなく、Schedulerの予定時刻を使う。
            date = hd.origin_date(scheduled, morning=True)
    elif "date" in event:
        value = event["date"]
        if not isinstance(value, str):
            return _bad_request("date must be a string")
        try:
            date_cls.fromisoformat(value)
        except ValueError:
            return _bad_request("date must be a valid ISO date")
        date = value
    else:
        date = hd.origin_date(hd.jst_now(), morning=True)

    deadline = hd.deadline_from_context(context)
    deps = (hd.build_morning_deps(settings, deadline) if deadline is not None
            else hd.build_morning_deps(settings))
    return run_morning(date, deps)
