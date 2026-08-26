"""収集した生データを DailyVital に変換する。

Google Health の睡眠レコードは起床日で選択される（_pick_sleep_for_date）。
本プロジェクトの date は起点日（夜メッセージの送信日）なので、ここで1日引く。
取り違えると全レコードが1日ずれる。
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from sleeptech.memory.schemas import DailyVital

JST = timezone(timedelta(hours=9))


def to_daily_vital(raw: dict, wake_date: str) -> DailyVital:
    """生 dict と起床日から DailyVital を組み立てる。date は起床日 − 1日。"""
    origin = (date_cls.fromisoformat(wake_date) - timedelta(days=1)).isoformat()
    known = set(DailyVital.__dataclass_fields__)
    fields = {k: v for k, v in raw.items() if k in known and k != "date"}
    return DailyVital(
        date=origin,
        ingested_at=datetime.now(JST).isoformat(timespec="seconds"),
        **fields,
    )
