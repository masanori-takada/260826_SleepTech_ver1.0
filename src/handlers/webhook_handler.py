"""LINE Webhook ハンドラ（US2 自己申告取り込み / FR-002）。

Lambda Function URL（公開HTTPS）で LINE のメッセージイベントを受け取り、
ユーザーの返信を「夜食ラーメン有無」に解釈して DynamoDB へ保存、確認を返信する。

セキュリティ: X-Line-Signature を channel_secret で検証（FR-011 / 原則III）。
channel_secret が未設定なら検証をスキップ（警告ログ）。本番では必ず設定すること。

日付の扱い: 申告は「当日(JST)の前夜」を表すものとして今日の日付に記録する。
（朝の収集 run_morning(date) が同じ date の REPORT を読み取り紐づける）
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from night_ramen.config import load_settings
from night_ramen.collectors.self_report import DynamoSelfReportSource, parse_ramen_reply
from night_ramen.notifier.line import reply_message, verify_line_signature
from night_ramen.observability import StructuredLogger


def _safe_reply(reply_token: str, text: str, token: str, log: StructuredLogger) -> None:
    """返信はベストエフォート（失敗しても記録保存を妨げない）。"""
    if not (reply_token and token):
        return
    try:
        reply_message(reply_token, text, token)
    except Exception as exc:  # noqa: BLE001
        log.log("webhook_reply_failed", error=type(exc).__name__)


def _today_jst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _line_secret(settings) -> dict:
    import boto3

    sm = boto3.client("secretsmanager", region_name=settings.region)
    return json.loads(sm.get_secret_value(SecretId=settings.secret_line)["SecretString"])


def _raw_body(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8")


def _header(event: dict, name: str) -> str:
    headers = event.get("headers") or {}
    # Function URL はヘッダ名を小文字化する
    return headers.get(name) or headers.get(name.lower()) or ""


def handler(event, context=None):
    """LINE Webhook エントリ（Lambda Function URL）。"""
    log = StructuredLogger()
    settings = load_settings()
    secret = _line_secret(settings)
    body = _raw_body(event)

    # 署名検証（channel_secret があれば必須）
    channel_secret = secret.get("channel_secret")
    if channel_secret:
        sig = _header(event, "X-Line-Signature")
        if not verify_line_signature(body, sig, channel_secret):
            log.log("webhook_bad_signature")
            return {"statusCode": 403, "body": "invalid signature"}
    else:
        log.log("webhook_signature_skipped", reason="channel_secret 未設定")

    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": "bad json"}

    report_source = DynamoSelfReportSource(settings.dynamodb_table, settings.region)
    token = secret.get("channel_access_token", "")
    date = _today_jst()

    for ev in payload.get("events", []):
        if ev.get("type") != "message" or ev.get("message", {}).get("type") != "text":
            continue
        text = ev["message"].get("text", "")
        ramen = parse_ramen_reply(text)
        reply_token = ev.get("replyToken", "")

        if ramen is None:
            log.log("webhook_unparsed", text_len=len(text))
            _safe_reply(reply_token, "「食べた」か「我慢した」で教えてください🍜", token, log)
            continue

        report_source.put(date, ramen)
        log.log("webhook_report_saved", date=date, ramen=ramen)
        msg = (
            "正直に教えてくれてありがとう。明日はきっと体が軽いよ💪"
            if ramen
            else "我慢できたんだね、素晴らしい！その調子🌙"
        )
        _safe_reply(
            reply_token,
            f"記録しました（昨夜のラーメン: {'あり' if ramen else 'なし'}）。{msg}",
            token,
            log,
        )

    return {"statusCode": 200, "body": "ok"}
