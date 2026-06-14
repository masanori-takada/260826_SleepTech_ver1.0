"""朝ループの Lambda ハンドラ（T025 / EventBridge 08:00 JST 起動）。

payload 例: {"date": "2026-06-14"}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from night_ramen.config import load_settings


def _today_jst() -> str:
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).date().isoformat()


def build_morning_dependencies(settings):
    """本番依存（Google Health ソース・自己申告ソース・Memory）を組み立てる。"""
    from night_ramen.collectors.self_report import StaticSelfReportSource
    from night_ramen.memory.client import MemoryClient

    # 実運用では Google Health API クライアント・自己申告ストアを注入する。
    # ここでは骨組みとして Memory と空の自己申告ソースを返す。
    return MemoryClient(), _GoogleHealthSource(settings), StaticSelfReportSource()


class _GoogleHealthSource:
    """Secrets Manager のトークンで Google Health から生データを取得する本番ソース。"""

    def __init__(self, settings) -> None:
        self._settings = settings

    def fetch_raw(self, date: str) -> dict:
        # 実 API 連携は実装時に確定（research D7）。
        # トークンは Secrets Manager から取得し、平文ログには出さない（FR-011）。
        raise NotImplementedError(
            "Google Health API 連携は実環境設定後に実装します（research.md D7 / 未解決事項）"
        )


def handler(event, context=None):
    event = event or {}
    settings = load_settings()
    date = event.get("date") or _today_jst()

    memory, health_source, report_source = build_morning_dependencies(settings)
    from night_ramen.workflow.morning import run_morning

    record = run_morning(date, memory, health_source, report_source)
    return {
        "date": date,
        "collected": record is not None,
        "missing_fields": (record.missing_fields if record else None),
    }
