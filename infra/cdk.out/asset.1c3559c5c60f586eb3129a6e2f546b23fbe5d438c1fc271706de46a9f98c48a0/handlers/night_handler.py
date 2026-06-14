"""夜ループの Lambda ハンドラ（T018 / EventBridge 23:00 JST 起動）。

payload 例: {"date": "2026-06-14", "dry_run": true}
本番依存（agentcore Memory・Bedrock・LINE・DynamoDB）の組み立ては build_night_dependencies に集約。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from night_ramen.config import load_settings


def _today_jst() -> str:
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).date().isoformat()


def build_night_dependencies(settings, dry_run: bool):
    """本番依存を組み立てる（遅延 import で boto3/SDK を必要時のみロード）。"""
    import boto3

    from night_ramen.agents.prompts import boto3_invoker
    from night_ramen.memory.client import MemoryClient
    from night_ramen.memory.schemas import Goal
    from night_ramen.notifier.line import (
        DryRunPushClient,
        InMemoryIdempotencyStore,
        LinePushClient,
    )
    from night_ramen.workflow.night import NightDependencies

    # 目標を DynamoDB から取得（無ければ既定 85%/2kg, FR-017）
    ddb = boto3.resource("dynamodb", region_name=settings.region)
    table = ddb.Table(settings.dynamodb_table)
    goal = _load_goal(table, settings)

    # 通知冪等ストア（DynamoDB）。dry_run でも冪等は効かせる。
    store = _DynamoIdempotencyStore(table, settings) if not dry_run else InMemoryIdempotencyStore()

    # 送信クライアント
    if dry_run:
        push = DryRunPushClient()
    else:
        creds = _line_credentials(settings)
        push = LinePushClient(creds["channel_access_token"], creds["user_id"])

    return NightDependencies(
        settings=settings,
        memory=MemoryClient(),  # 既定 InMemory。agentcore 接続時は backend を注入
        invoker=boto3_invoker(settings.region),
        goal=goal,
        idempotency=store,
        push_client=push,
    )


def _load_goal(table, settings):
    from night_ramen.memory.schemas import Goal

    try:
        item = table.get_item(Key={"PK": "GOAL", "SK": "current"}).get("Item")
    except Exception:
        item = None
    if not item:
        return Goal(
            success_rate_target=settings.default_success_rate_target,
            weight_loss_target_kg=settings.default_weight_loss_target_kg,
        )
    return Goal(
        success_rate_target=float(item.get("success_rate_target", settings.default_success_rate_target)),
        weight_loss_target_kg=float(item.get("weight_loss_target_kg", settings.default_weight_loss_target_kg)),
        baseline_weight_kg=(float(item["baseline_weight_kg"]) if item.get("baseline_weight_kg") else None),
    )


def _line_credentials(settings) -> dict:
    import json
    import boto3

    sm = boto3.client("secretsmanager", region_name=settings.region)
    secret = sm.get_secret_value(SecretId=settings.secret_line)["SecretString"]
    return json.loads(secret)


class _DynamoIdempotencyStore:
    """DynamoDB による通知冪等（NOTIFY#date, TTL）。"""

    def __init__(self, table, settings) -> None:
        self._table = table
        self._ttl_days = 7

    def is_notified(self, date: str) -> bool:
        item = self._table.get_item(Key={"PK": "NOTIFY", "SK": date}).get("Item")
        return bool(item and item.get("notified"))

    def mark_notified(self, date: str) -> None:
        import time

        self._table.put_item(
            Item={
                "PK": "NOTIFY",
                "SK": date,
                "notified": True,
                "ttl": int(time.time()) + self._ttl_days * 86400,
            }
        )


def handler(event, context=None):
    """EventBridge / 手動起動のエントリ。"""
    event = event or {}
    settings = load_settings()
    date = event.get("date") or _today_jst()
    dry_run = bool(event.get("dry_run", False))

    deps = build_night_dependencies(settings, dry_run)
    from night_ramen.workflow.night import run_night

    msg = run_night(date, deps)
    return {
        "date": date,
        "delivered": msg.delivered,
        "guardrail_result": msg.guardrail_result.value,
        "tone": msg.tone.value,
        "dry_run": dry_run,
    }
