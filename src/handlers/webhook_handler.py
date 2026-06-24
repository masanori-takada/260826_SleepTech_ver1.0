"""LINE Webhook ハンドラ（自己申告取り込み ＋ 対話エージェント / FR-002, FR-101〜112, Feature 003）。

Lambda Function URL（公開HTTPS）で LINE メッセージを受け取り:
 1. 署名検証（FR-108）
 2. 自己申告抽出（固定パース＋会話抽出の併用, FR-105）→ DynamoDB REPORT 保存
 2.5 起床トリガー（「おはよう」等）なら 8 時を待たず当日バイタルを先行収集（Feature 003 / FR-003）
 3. 健康コーチ会話エージェントで応答生成（LTM/STM/バイタル/目標を文脈, FR-101,104）
 4. **ガードレール検証**（送信前必須, FR-106）
 5. LINE 返信 ＋ 会話ターンを agentcore Memory へ保存（FR-102,103）

NRB_MEMORY_ID 未設定時は記憶機能を無効化し、会話のみ（degrade）。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from night_ramen.config import load_settings
from night_ramen.observability import StructuredLogger


def _today_jst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _line_secret(settings) -> dict:
    import boto3

    sm = boto3.client("secretsmanager", region_name=settings.region)
    return json.loads(sm.get_secret_value(SecretId=settings.secret_line)["SecretString"])


def _load_goal(settings):
    import boto3

    from night_ramen.memory.schemas import Goal

    try:
        table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.dynamodb_table)
        item = table.get_item(Key={"PK": "GOAL", "SK": "current"}).get("Item")
    except Exception:  # noqa: BLE001
        item = None
    if not item:
        return Goal(
            success_rate_target=settings.default_success_rate_target,
            weight_loss_target_kg=settings.default_weight_loss_target_kg,
        )
    return Goal(
        success_rate_target=float(item.get("success_rate_target", settings.default_success_rate_target)),
        weight_loss_target_kg=float(item.get("weight_loss_target_kg", settings.default_weight_loss_target_kg)),
    )


def _raw_body(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8")


def _header(event: dict, name: str) -> str:
    headers = event.get("headers") or {}
    return headers.get(name) or headers.get(name.lower()) or ""


def _collect_today_vitals(date: str, settings, log: StructuredLogger) -> None:
    """起床トリガー: 朝8時の定時収集を待たず当日バイタルを今すぐ収集する（Feature 003 / FR-003）。

    朝ループ（run_morning）と同じ依存・処理を再利用する。save_vital は上書きのため
    後続の8時定時収集と二重に走っても冪等で安全。文脈構築の前に呼ぶことで、会話エージェントが
    収集直後の最新データを踏まえて応答できる。

    失敗・同意失効は会話継続を妨げないよう握りつぶしてログのみ残す（FR-012 は朝ループ側で停止判断）。
    """
    try:
        from handlers.morning_handler import build_morning_dependencies
        from night_ramen.workflow.morning import run_morning

        memory, health_source, report_source = build_morning_dependencies(settings)
        record = run_morning(date, memory, health_source, report_source)
        log.log("webhook_wakeup_collected", date=date, ok=record is not None)
    except Exception as exc:  # noqa: BLE001 — 収集失敗で会話を止めない
        log.log("webhook_wakeup_failed", error=type(exc).__name__)


def handler(event, context=None):
    """LINE Webhook エントリ（Lambda Function URL）。"""
    log = StructuredLogger()
    settings = load_settings()
    secret = _line_secret(settings)
    body = _raw_body(event)

    # 1. 署名検証（FR-108）
    from night_ramen.notifier.line import verify_line_signature

    channel_secret = secret.get("channel_secret")
    if channel_secret:
        if not verify_line_signature(body, _header(event, "X-Line-Signature"), channel_secret):
            log.log("webhook_bad_signature")
            return {"statusCode": 403, "body": "invalid signature"}
    else:
        log.log("webhook_signature_skipped", reason="channel_secret 未設定")

    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": "bad json"}

    token = secret.get("channel_access_token", "")
    deps = _build_deps(settings)

    for ev in payload.get("events", []):
        if ev.get("type") != "message" or ev.get("message", {}).get("type") != "text":
            continue
        _handle_message(ev, deps, token, settings, log)

    return {"statusCode": 200, "body": "ok"}


def _build_deps(settings):
    """会話処理に必要な依存を組み立てる（遅延 import で boto3 等を必要時のみ）。"""
    from night_ramen.agents.prompts import boto3_invoker
    from night_ramen.collectors.self_report import DynamoSelfReportSource
    from night_ramen.conversation.memory_store import AgentCoreMemory
    from night_ramen.memory.client import MemoryClient
    from night_ramen.memory.dynamo_backend import DynamoMemoryBackend

    memory = AgentCoreMemory(settings.memory_id, settings.region) if settings.memory_id else None
    return {
        "invoker": boto3_invoker(settings.region),
        "report": DynamoSelfReportSource(settings.dynamodb_table, settings.region),
        "vitals_client": MemoryClient(
            DynamoMemoryBackend(settings.dynamodb_table, settings.region, settings.vital_retention_days)
        ),
        "memory": memory,
        "goal": _load_goal(settings),
    }


def _handle_message(ev, deps, token, settings, log) -> None:
    from datetime import date as date_cls, timedelta as td

    from night_ramen.collectors.self_report import is_wakeup_trigger
    from night_ramen.conversation.agent import (
        SAFE_CONVERSATION_FALLBACK,
        build_context,
        generate_reply,
    )
    from night_ramen.conversation.extractor import extract_ramen_report, resolve_report_date
    from night_ramen.guardrail.validator import GuardrailContext, generate_safe_message
    from night_ramen.memory.schemas import GuardrailResult
    from night_ramen.notifier.line import reply_message

    text = ev["message"].get("text", "")
    reply_token = ev.get("replyToken", "")
    actor_id = ev.get("source", {}).get("userId", "unknown")
    date = _today_jst()
    session_id = f"line-{date}"
    invoker = deps["invoker"]
    model = settings.models.conversation_model

    # 2. 自己申告抽出（併用）→ REPORT 保存（FR-105）。分類は高速な Haiku を使用。
    #    日付が明示/相対で書かれていれば過去日付に記録（記録漏れ補完）。無ければ当日(date)。
    ramen = extract_ramen_report(text, invoker, settings.models.classifier_model)
    just_recorded = ""
    if ramen is not None:
        report_date = resolve_report_date(text, date, invoker, settings.models.classifier_model)
        try:
            deps["report"].put(report_date, ramen)
            log.log("webhook_report_saved", date=report_date, ramen=ramen)
            just_recorded = f"{report_date} のラーメン: {'あり' if ramen else 'なし'}"
        except Exception:  # noqa: BLE001
            log.log("webhook_report_save_failed")
        # 当日申告なら vitals を即時更新（朝の質問ボタン押下後に ramen_last_night を反映）
        if report_date == date:
            _collect_today_vitals(date, settings, log)

    # 2.5 起床トリガー: 8時を待たず当日バイタルを先行収集（Feature 003 / FR-003）。
    #     文脈構築の前に収集するので、会話エージェントが当日の最新データを踏まえて応答する。
    if is_wakeup_trigger(text):
        log.log("webhook_wakeup_trigger", date=date)
        _collect_today_vitals(date, settings, log)

    # 3. 文脈構築（LTM/STM/バイタル/目標, FR-102,103,104）
    memory = deps["memory"]
    recent_turns = memory.get_recent_turns(actor_id, session_id, k=6) if memory else []
    ltm = memory.retrieve_ltm(actor_id, session_id, query=text, top_k=3) if memory else []
    # 月次サマリ用に記録開始(2025-12)まで遡る。保持は365日のため370日前まで取れば全保持分を網羅。
    since = (date_cls.fromisoformat(date) - td(days=370)).isoformat()
    vitals = deps["vitals_client"].get_vitals(since, date)
    ctx = build_context(recent_turns, ltm, vitals, deps["goal"], today=date, just_recorded=just_recorded)

    # 4. 応答生成 → ガードレール（送信前必須, FR-106）
    g_ctx = GuardrailContext(goal_summary=ctx.goal_summary)
    body, result = generate_safe_message(
        lambda: generate_reply(text, ctx, invoker, model), g_ctx, settings.max_regenerations
    )
    if result == GuardrailResult.FALLBACK:
        body = SAFE_CONVERSATION_FALLBACK  # 会話向けの安全フォールバック
    log.log("webhook_reply_generated", guardrail=result.value, ramen_recorded=ramen is not None)

    # 5. 返信 ＋ 会話ターンを Memory へ（ベストエフォート）
    if reply_token and token:
        try:
            reply_message(reply_token, body, token)
        except Exception:  # noqa: BLE001
            log.log("webhook_reply_failed")
    if memory:
        memory.write_turn(actor_id, session_id, "USER", text)
        memory.write_turn(actor_id, session_id, "ASSISTANT", body)
