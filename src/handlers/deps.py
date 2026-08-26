"""Lambda 共通の依存組み立てと時刻ユーティリティ。

起点日の決め方に注意する。夜 22:30 の起点日は今日だが、
朝 8:00 の起点日は前日である（前夜のメッセージと同じ日付にするため）。

さらに、夜フロー（EventBridge 22:30 JST 起動）は Lambda のリトライ・
スロットリング・コールドスタート遅延によって 0:00 を越えて実行される
ことがある。そのため夜フローには「その夜の区切り時刻」
（NIGHT_ROLLOVER_HOUR）を導入し、区切り前は「前日の夜の続き」として
扱う。区切りを 4 時にしているのは、推奨就寝時刻の探索区間の終端が
「起床の4時間前」であり、常識的な起床時刻（6〜9時）を考えると
4 時より後に夜フローが走ることは想定されないため。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# 夜フローの区切り時刻（JST）。この時刻未満は「前日の夜」の続きとみなす。
# 理由: 推奨就寝時刻の探索区間の終端は「起床の4時間前」であり、常識的な
# 起床時刻（6〜9時）を考えると 4 時より後に夜フローが走ることは想定され
# ないため。
NIGHT_ROLLOVER_HOUR = 4


def jst_now() -> datetime:
    return datetime.now(JST)


def origin_date(now: datetime, morning: bool = False, night: bool = False) -> str:
    """起点日を返す。

    morning=True のときは前日（朝フローの挙動は変更しない）。
    night=True のときは、NIGHT_ROLLOVER_HOUR より前なら「前日の夜」の
    続きとみなして前日を返す（0:00 を越えて実行された場合の救済）。
    """
    if morning:
        d = now.date() - timedelta(days=1)
    elif night and now.hour < NIGHT_ROLLOVER_HOUR:
        d = now.date() - timedelta(days=1)
    else:
        d = now.date()
    return d.isoformat()


def now_h(now: datetime, night: bool = False) -> float:
    """起点日 0:00 からの経過時間（時）。

    night=True のときは、NIGHT_ROLLOVER_HOUR より前なら「前日 0:00 から
    の経過時間」として 24.0 を加算する（origin_date(night=True) が返す
    起点日と整合させるため）。
    """
    if night and now.hour < NIGHT_ROLLOVER_HOUR:
        return round(now.hour + now.minute / 60.0 + 24.0, 4)
    return round(now.hour + now.minute / 60.0, 4)


def _secret(name: str, region: str) -> dict:
    import boto3

    sm = boto3.client("secretsmanager", region_name=region)
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _memory(settings):
    from sleeptech.memory.client import MemoryClient
    from sleeptech.memory.dynamo_backend import DynamoBackend

    return MemoryClient(DynamoBackend(settings.table, settings.region))


def _llm(settings, deadline=None):
    from sleeptech.llm.client import OpenAiClient

    key = _secret(settings.secret_openai, settings.region).get("api_key", "")
    return OpenAiClient(key, settings.llm_model, deadline=deadline)


def deadline_from_context(context):
    """Lambda contextの残り時間を単調時計の期限へ変換する。"""
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(remaining):
        return None
    from sleeptech.llm.client import ExecutionDeadline
    return ExecutionDeadline(time.monotonic() + max(0, remaining()) / 1000.0)


def _push(settings):
    from sleeptech.notifier.line import LinePushClient

    s = _secret(settings.secret_line, settings.region)
    return LinePushClient(s.get("channel_access_token", ""), s.get("user_id", ""))


def _health_source(settings):
    from sleeptech.collectors.google_auth_helper import get_access_token
    from sleeptech.collectors.google_health_api import GoogleHealthApiSource

    def provider() -> str:
        return get_access_token(_secret(settings.secret_google, settings.region))

    return GoogleHealthApiSource(provider)


def build_night_deps(settings, deadline=None):
    from sleeptech.workflow.night import NightDeps

    return NightDeps(
        settings=settings,
        memory=_memory(settings),
        llm_client=_llm(settings, deadline),
        push_client=_push(settings),
        deadline=deadline,
    )


def build_morning_deps(settings, deadline=None):
    from sleeptech.workflow.morning import MorningDeps

    return MorningDeps(
        settings=settings,
        memory=_memory(settings),
        health_source=_health_source(settings),
        llm_client=_llm(settings, deadline),
        push_client=_push(settings),
        deadline=deadline,
    )
