"""Lambda 共通の依存組み立てと時刻ユーティリティ。

起点日の決め方に注意する。夜 22:30 の起点日は今日だが、
朝 9:30 の起点日は前日である（前夜のメッセージと同じ日付にするため）。

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


def resolve_scheduled_time(
    value, cron_hour: int, cron_minute: int
) -> tuple[datetime, bool] | None:
    """EventBridge Scheduler の ``<aws.scheduler.scheduled-time>`` 文字列を解決する。

    戻り値は (JSTへ正しく変換した瞬時, ローカル時刻へのZ誤ラベル疑いフラグ)。
    パース不能（文字列でない・ISO形式でない・タイムゾーン情報が無い）場合は None。

    最終レビュー対応: 前回の修正は「壁時計との乖離が7時間を超えたら
    タイムゾーン誤りとみなして壁時計にフォールバックする」という対称な
    閾値判定だったが、EventBridge Scheduler はRetryPolicy未指定だと既定で
    最大24時間・185回リトライするため、7時間超の乖離は「タイムゾーン誤り」
    ではなく「正規の長時間リトライ」でも普通に起こり得る。これを掴んで
    scheduled_timeを誤って捨てると、本来22:30に届くはずの夜メッセージが
    翌朝の壁時計時刻を起点日にしてしまい、その晩のIDEM#NIGHTキーを
    先食いして本来のメッセージが無言消滅する退行を招いた。

    そこで判定基準を「壁時計との乖離量」ではなく「scheduled_time自体の形」に
    変更する。タイムゾーン表記がUTC（Z / +00:00）であり、かつその数字を
    そのままJST時刻とみなすとcronの予定時刻（夜22:30・朝9:30）と厳密に
    一致する場合だけ、「ローカル時刻に誤ってZを付けて返してきた」疑いあり
    と判定する。scheduled_timeは常にcronの予定時刻そのものであり
    （実行が遅延してもscheduled_timeの値自体は変わらない）、この判定は
    壁時計にも乖離量にも依存しないため、リトライがどれだけ長時間・
    大量に発生しても誤検出しない。
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    scheduled = parsed.astimezone(JST)
    naive_as_jst = parsed.replace(tzinfo=JST)
    mislabeled = (
        parsed.utcoffset() == timedelta(0)
        and naive_as_jst.hour == cron_hour
        and naive_as_jst.minute == cron_minute
    )
    return scheduled, mislabeled


# M4対応: boto3 の既定タイムアウト（connect/read 各60秒）は60秒Lambdaに対して
# 未拘束（1回のAPI呼び出しだけでタイムアウト予算を使い切りかねない）。
# connect/read を短く切り、リトライ回数も絞る。
_BOTO_CONFIG_KW = {"connect_timeout": 2, "read_timeout": 3, "retries": {"max_attempts": 2}}


def _boto_config():
    from botocore.config import Config

    return Config(**_BOTO_CONFIG_KW)


def _secret(name: str, region: str) -> dict:
    import boto3

    sm = boto3.client("secretsmanager", region_name=region, config=_boto_config())
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
    return _push_from_secret(s)


def _push_from_secret(secret_data: dict):
    """取得済みLINE secretから送信クライアントを組み立てる。"""
    from sleeptech.notifier.line import LinePushClient

    return LinePushClient(
        secret_data.get("channel_access_token", ""),
        secret_data.get("user_id", ""),
    )


def _health_source(settings, deadline=None):
    from sleeptech.collectors.google_auth_helper import get_access_token
    from sleeptech.collectors.google_health_api import GoogleHealthApiSource

    def provider() -> str:
        return get_access_token(_secret(settings.secret_google, settings.region))

    # I1対応: deadline を渡すと、残り時間が1リクエスト分を割った時点で
    # 以降のGoogle API呼び出しを打ち切り、取得済みデータだけで集計する
    # （無ければ欠損として安全に扱う既存フォールバックに乗る）。
    return GoogleHealthApiSource(provider, deadline=deadline)


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
        health_source=_health_source(settings, deadline),
        llm_client=_llm(settings, deadline),
        push_client=_push(settings),
        deadline=deadline,
    )
