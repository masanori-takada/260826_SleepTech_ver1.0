"""朝の収集ループ（T024 / US2 / FR-001,002,012,013）。

収集 → 自己申告紐づけ → 記憶保存。同意失効時は収集を停止する。
"""

from __future__ import annotations

from night_ramen.collectors import ConsentRevokedError
from night_ramen.collectors.google_health import HealthDataSource, fetch_daily_vitals
from night_ramen.collectors.self_report import SelfReportSource, get_ramen_self_report
from night_ramen.memory.client import MemoryClient
from night_ramen.memory.schemas import DailyVitalRecord
from night_ramen.observability import StructuredLogger


def run_morning(
    date: str,
    memory: MemoryClient,
    health_source: HealthDataSource,
    report_source: SelfReportSource,
) -> DailyVitalRecord | None:
    """朝ルーティン。保存したレコードを返す。同意失効時は None。"""
    log = StructuredLogger()
    log.log("morning_start", date=date)

    try:
        record = fetch_daily_vitals(date, health_source)
    except ConsentRevokedError:
        # 同意失効 → 以後の収集を停止（FR-012）
        log.log("consent_revoked", date=date)
        return None

    # 自己申告を紐づけ（health 側に無ければ別ソースから, FR-002）
    if record.ramen_last_night is None:
        record.ramen_last_night = get_ramen_self_report(date, report_source)

    memory.save_vital(record)
    log.log(
        "morning_saved",
        date=date,
        missing=len(record.missing_fields),
        ramen=record.ramen_last_night,
    )
    return record
