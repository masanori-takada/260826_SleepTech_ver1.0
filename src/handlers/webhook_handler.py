"""LINE Webhook Lambda。

役割は2つ。
1. 起床トリガー（「おはよう」等）を検出したら run_morning を前倒し実行する
   （収集だけでなく送信まで行う。冪等は run_morning が担保する）
2. クイックリプライの回答（評価:N）を直近の未回答の夜予測に紐付けて保存する

どちらでもないメッセージは何もしない（ver1.0 に会話機能は無い）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta

from sleeptech.config import load_settings
from sleeptech.memory.schemas import Feedback
from sleeptech.notifier.line import parse_feedback
from sleeptech.observability import StructuredLogger
from sleeptech.workflow.morning import run_morning
from src.handlers import deps as hd

_WAKEUP_RE = re.compile(r"^(おはよう(ございます)?|起きた|おきた|データ更新|同期|計測)$")
# range(FEEDBACK_LOOKBACK_DAYS) で「当日」と「前日」の2日分のみを見る
# （2日前は見ない）。定数名は「遡る日数」ではなく「見る日数」を表す。
FEEDBACK_LOOKBACK_DAYS = 2
FEEDBACK_SAVED_TEXT = "評価{score}を記録しました。次回以降の予測補正に反映します。"
FEEDBACK_UNMATCHED_TEXT = (
    "評価を対応する朝の予測に紐付けられませんでした。次の夜の評価から記録します。"
)


@dataclass(frozen=True)
class FeedbackTarget:
    """評価対象日と対応する朝予測の起点日。"""

    evaluated_date: str
    prediction_date: str


@dataclass(frozen=True)
class AuthorizedText:
    """認可済みイベントの本文と、そのイベント専用のreplyToken。"""

    text: str
    reply_token: str
    webhook_event_id: str | None
    webhook_event_id_present: bool


def _response(status_code: int, error: str) -> dict:
    """Function URL向けのエラーレスポンスを作る。"""
    return {"statusCode": status_code, "body": json.dumps({"error": error})}


def _header(event: dict, name: str) -> object | None:
    """通常ヘッダーと複数値ヘッダーから値を大文字小文字非依存で得る。"""
    target = name.casefold()
    headers = event.get("headers")
    if not isinstance(headers, Mapping):
        headers = {}
    for header_name, value in headers.items():
        if not isinstance(header_name, str) or header_name.casefold() != target:
            continue
        if isinstance(value, (list, tuple)):
            return value[0] if len(value) == 1 else value
        return value

    multi_headers = event.get("multiValueHeaders")
    if not isinstance(multi_headers, Mapping):
        multi_headers = {}
    for header_name, values in multi_headers.items():
        if not isinstance(header_name, str) or header_name.casefold() != target:
            continue
        if isinstance(values, (list, tuple)):
            return values[0] if len(values) == 1 else values
        return values
    return None


def _raw_body(event: dict) -> bytes | None:
    """Function URLイベントのraw bodyを改変せずバイト列へ戻す。"""
    body = event.get("body")
    if not isinstance(body, str):
        return None
    encoded = event.get("isBase64Encoded", False)
    if not isinstance(encoded, bool):
        return None
    if encoded:
        try:
            return base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError):
            return None
    return body.encode("utf-8")


def _verify_line_signature(
    raw_body: bytes, received: object, channel_secret: str
) -> int | None:
    """LINE署名を厳密なBase64として検証し、拒否時のHTTPステータスを返す。"""
    if not isinstance(received, str):
        return 403
    try:
        received_ascii = received.encode("ascii")
    except UnicodeEncodeError:
        return 403
    if len(received_ascii) != 44:
        return 403
    try:
        received_digest = base64.b64decode(received_ascii, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return 403
    if len(received_digest) != hashlib.sha256().digest_size:
        return 403
    if base64.b64encode(received_digest) != received_ascii:
        return 403
    try:
        expected_digest = hmac.new(
            channel_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected_digest, received_digest):
            return 403
    except (TypeError, UnicodeEncodeError):
        return 403
    return None


def is_wakeup_trigger(text: str) -> bool:
    """起床トリガー語か判定する。完全一致のみ（質問文を誤検知しないため）。"""
    return bool(_WAKEUP_RE.match((text or "").strip()))


def resolve_feedback_target(memory, today: str) -> FeedbackTarget | None:
    """当日・前日の夜質問から、前日起点の朝予測を評価対象として返す。"""
    base = date_cls.fromisoformat(today)
    for offset in range(FEEDBACK_LOOKBACK_DAYS):
        evaluated = base - timedelta(days=offset)
        prediction = evaluated - timedelta(days=1)
        evaluated_s = evaluated.isoformat()
        prediction_s = prediction.isoformat()
        message = memory.get_message(evaluated_s, "NIGHT")
        if (message is not None and message.delivered
                and memory.get_prediction(prediction_s, "MORNING") is not None):
            return FeedbackTarget(evaluated_s, prediction_s)
    return None


def feedback_event_processed(memory, event_id: str) -> bool:
    """評価イベントの処理済み状態を確認する。"""
    return memory.is_feedback_event_processed(event_id)


def _texts(payload: dict) -> list[str]:
    """解析済みWebhook payloadからテキストメッセージ本文を取り出す。

    top-level/body/events/message の型を検査し、不正なeventだけを捨てる。
    1件の不正入力で同じbatch内の正常メッセージまで失わない。
    """
    if not isinstance(payload, dict):
        return []
    events = payload.get("events", [])
    if not isinstance(events, list):
        return []

    out: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        if ev.get("type") == "message" and msg.get("type") == "text":
            text = msg.get("text")
            if isinstance(text, str):
                out.append(text)
    return out


def _authorized_texts(payload: dict, user_id: str) -> list[AuthorizedText]:
    """許可した個人ユーザーの本文とreplyTokenをイベント単位で返す。"""
    if not isinstance(user_id, str) or not user_id:
        return []
    out: list[AuthorizedText] = []
    for ev in payload.get("events", []):
        if not isinstance(ev, dict):
            continue
        source = ev.get("source")
        if not isinstance(source, dict) or source.get("type") != "user":
            continue
        source_user_id = source.get("userId")
        if not isinstance(source_user_id, str) or not hmac.compare_digest(
            source_user_id.encode("utf-8"), user_id.encode("utf-8")
        ):
            continue
        msg = ev.get("message")
        if (ev.get("type") == "message" and isinstance(msg, dict)
                and msg.get("type") == "text" and isinstance(msg.get("text"), str)):
            reply_token = ev.get("replyToken")
            out.append(AuthorizedText(
                text=msg["text"],
                reply_token=reply_token if isinstance(reply_token, str) else "",
                webhook_event_id=(
                    ev.get("webhookEventId")
                    if isinstance(ev.get("webhookEventId"), str)
                    and ev.get("webhookEventId").strip()
                    else None
                ),
                webhook_event_id_present="webhookEventId" in ev,
            ))
    return out


def _parse_payload(raw_body: bytes) -> tuple[dict | None, int | None]:
    """raw bodyを一度だけJSON解析し、Function URLの入力契約を検査する。"""
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, 400
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return None, 400
    return payload, None


def handler(event, context=None) -> dict:
    if not isinstance(event, dict):
        return _response(400, "event must be an object")
    raw_body = _raw_body(event)
    if raw_body is None:
        return _response(400, "body must be valid")
    signature = _header(event, "x-line-signature")
    if signature is None:
        return _response(401, "missing LINE signature")

    log = StructuredLogger()
    settings = load_settings()
    line_secret_data = hd._secret(settings.secret_line, settings.region)
    line_secret = line_secret_data.get("channel_secret", "")
    if not isinstance(line_secret, str) or not line_secret:
        return _response(403, "LINE channel secret is not configured")
    verification_status = _verify_line_signature(raw_body, signature, line_secret)
    if verification_status is not None:
        return _response(verification_status, "invalid LINE signature")

    payload, payload_status = _parse_payload(raw_body)
    if payload_status is not None:
        return _response(payload_status, "invalid LINE payload")

    now = hd.jst_now()
    handled = []

    allowed_user_id = line_secret_data.get("user_id", "")
    reply_client = None
    for authorized in _authorized_texts(payload, allowed_user_id):
        score = parse_feedback(authorized.text)
        if score is not None:
            if authorized.webhook_event_id_present and authorized.webhook_event_id is None:
                log.log("feedback_event_invalid")
                continue
            event_id = authorized.webhook_event_id or (
                f"legacy-reply-token:{authorized.reply_token}"
                if authorized.reply_token else None
            )
            if event_id is None:
                log.log("feedback_event_invalid")
                continue
            memory = hd._memory(settings)
            if feedback_event_processed(memory, event_id):
                log.log("feedback_duplicate")
                handled.append("feedback_duplicate")
                continue
            target = resolve_feedback_target(memory, hd.origin_date(now))
            if target:
                saved = memory.save_feedback_once(
                    Feedback(
                        date=target.evaluated_date,
                        evaluated_date=target.evaluated_date,
                        prediction_date=target.prediction_date,
                        score=score,
                        answered_at=now.isoformat(timespec="seconds"),
                        source_event_id=event_id,
                    )
                )
                if not saved:
                    log.log("feedback_duplicate")
                    handled.append("feedback_duplicate")
                    continue
                log.log("feedback_saved", date=target.evaluated_date, score=score)
                if authorized.reply_token:
                    if reply_client is None:
                        reply_client = hd._push_from_secret(line_secret_data)
                    try:
                        reply_client.reply(
                            authorized.reply_token,
                            {"type": "text", "text": FEEDBACK_SAVED_TEXT.format(score=score)},
                        )
                        log.log("feedback_reply_sent", date=target.evaluated_date)
                    except Exception:
                        log.log("feedback_reply_failed", date=target.evaluated_date)
                handled.append("feedback")
            else:
                log.log("feedback_unmatched", score=score)
                if not memory.claim_feedback_event_once(event_id):
                    log.log("feedback_duplicate")
                    handled.append("feedback_duplicate")
                    continue
                if authorized.reply_token:
                    if reply_client is None:
                        reply_client = hd._push_from_secret(line_secret_data)
                    try:
                        reply_client.reply(
                            authorized.reply_token,
                            {"type": "text", "text": FEEDBACK_UNMATCHED_TEXT},
                        )
                        log.log("feedback_reply_sent")
                    except Exception:
                        log.log("feedback_reply_failed")
            continue

        if is_wakeup_trigger(authorized.text):
            date = hd.origin_date(now, morning=True)
            # I2対応: deadline を渡さないと Google API 全取得 + LLM3回試行 + LINE送信が
            # Webhook 応答の中で無制限に走り、数十秒 200 を返せなくなる
            # （LLM の時間予算ガードも deadline が無いと効かない）。
            # night_handler / morning_handler と同じく deadline_from_context を使う。
            deadline = hd.deadline_from_context(context)
            deps = (hd.build_morning_deps(settings, deadline) if deadline is not None
                    else hd.build_morning_deps(settings))
            run_morning(date, deps)
            log.log("wakeup_triggered", date=date)
            handled.append("wakeup")

    return {"statusCode": 200, "body": json.dumps({"handled": handled})}
