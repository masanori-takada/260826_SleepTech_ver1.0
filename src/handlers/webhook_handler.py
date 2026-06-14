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
from night_ramen.collectors.self_report import (
    DynamoSelfReportSource,
    is_wakeup_trigger,
    parse_ramen_reply,
)
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


def _fmt(value, suffix: str = "") -> str:
    """欠損（None）は「—」、値があれば単位付きで表示する。"""
    return f"{value}{suffix}" if value is not None else "—"


def format_vital_summary(record) -> str:
    """収集した DailyVitalRecord を短い日本語サマリに整形する（Feature 003）。

    全欠損（デバイス未同期など）の場合は再試行を促すメッセージを返す。
    総睡眠時間は現スキーマに無いため睡眠スコア・深い睡眠で代替する。
    """
    if not record.has_any_vitals:
        return "まだ同期されてないみたい。少し経ってからもう一度試してね🙏"
    ramen = record.ramen_last_night
    ramen_txt = "—" if ramen is None else ("あり" if ramen else "なし")
    return (
        "今朝のデータ取れたよ✅\n"
        f"睡眠スコア: {_fmt(record.sleep_score)} / "
        f"深い睡眠: {_fmt(record.deep_sleep_min, '分')} / "
        f"中途覚醒: {_fmt(record.awakenings, '回')}\n"
        f"平均心拍: {_fmt(record.night_avg_hr)} / HRV: {_fmt(record.hrv)}\n"
        f"昨夜のラーメン: {ramen_txt}"
    )


def _handle_wakeup(date: str, settings, reply_token: str, token: str, log: StructuredLogger) -> None:
    """起床トリガー: 8時の定時収集を待たず当日バイタルを今すぐ収集して返信する（FR-003）。

    朝ループ（run_morning）と同じ依存・処理を再利用する。save_vital は上書きのため
    後続の8時定時収集と二重に走っても冪等で安全。
    """
    from handlers.morning_handler import build_morning_dependencies
    from night_ramen.workflow.morning import run_morning

    try:
        memory, health_source, report_source = build_morning_dependencies(settings)
        record = run_morning(date, memory, health_source, report_source)
    except Exception as exc:  # noqa: BLE001 — 失敗しても返信で穏やかに伝える
        log.log("webhook_wakeup_failed", error=type(exc).__name__)
        _safe_reply(reply_token, "データの取得に失敗しちゃった。少し経ってからもう一度試してね🙏", token, log)
        return

    if record is None:
        # 同意失効（FR-012 を継承）
        log.log("webhook_wakeup_consent_revoked", date=date)
        _safe_reply(reply_token, "Google Health の連携が切れているみたい。設定を確認してね🔗", token, log)
        return

    log.log("webhook_wakeup_collected", date=date, missing=len(record.missing_fields))
    _safe_reply(reply_token, format_vital_summary(record), token, log)


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
            # 起床トリガー語なら 8 時を待たず当日収集を起こす（Feature 003 / FR-003）
            if is_wakeup_trigger(text):
                log.log("webhook_wakeup_trigger", date=date)
                _handle_wakeup(date, settings, reply_token, token, log)
                continue
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
