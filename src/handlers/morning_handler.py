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
    from night_ramen.collectors.google_health_api import GoogleHealthApiSource
    from night_ramen.collectors.self_report import StaticSelfReportSource
    from night_ramen.memory.client import MemoryClient

    # Secrets Manager のリフレッシュトークンからアクセストークンを供給する
    def access_token_provider() -> str:
        from night_ramen.collectors.google_auth_helper import get_access_token

        secret = _google_secret(settings)
        return get_access_token(secret)

    health_source = GoogleHealthApiSource(access_token_provider)
    return MemoryClient(), health_source, StaticSelfReportSource()


def _google_secret(settings) -> dict:
    """Google OAuth シークレット（client_id/secret/refresh_token）を取得（FR-011）。"""
    import json

    import boto3

    sm = boto3.client("secretsmanager", region_name=settings.region)
    raw = sm.get_secret_value(SecretId=settings.secret_google)["SecretString"]
    return json.loads(raw)


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
