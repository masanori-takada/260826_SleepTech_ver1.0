"""Lambda 共通の依存組み立てと時刻ユーティリティ。

起点日の決め方に注意する。夜 22:30 の起点日は今日だが、
朝 8:00 の起点日は前日である（前夜のメッセージと同じ日付にするため）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def jst_now() -> datetime:
    return datetime.now(JST)


def origin_date(now: datetime, morning: bool = False) -> str:
    """起点日を返す。morning=True のときは前日。"""
    d = now.date() - timedelta(days=1) if morning else now.date()
    return d.isoformat()


def now_h(now: datetime) -> float:
    """起点日 0:00 からの経過時間（時）。"""
    return round(now.hour + now.minute / 60.0, 4)


def _secret(name: str, region: str) -> dict:
    import boto3

    sm = boto3.client("secretsmanager", region_name=region)
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _memory(settings):
    from sleeptech.memory.client import MemoryClient
    from sleeptech.memory.dynamo_backend import DynamoBackend

    return MemoryClient(DynamoBackend(settings.table, settings.region))


def _llm(settings):
    from sleeptech.llm.client import OpenAiClient

    key = _secret(settings.secret_openai, settings.region).get("api_key", "")
    return OpenAiClient(key, settings.llm_model)


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


def build_night_deps(settings):
    from sleeptech.workflow.night import NightDeps

    return NightDeps(
        settings=settings,
        memory=_memory(settings),
        llm_client=_llm(settings),
        push_client=_push(settings),
    )


def build_morning_deps(settings):
    from sleeptech.workflow.morning import MorningDeps

    return MorningDeps(
        settings=settings,
        memory=_memory(settings),
        health_source=_health_source(settings),
        llm_client=_llm(settings),
        push_client=_push(settings),
    )
