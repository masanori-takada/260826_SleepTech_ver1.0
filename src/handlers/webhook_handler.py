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


def resolve_feedback_date(memory, today: str) -> str | None:
    """回答を紐付ける日付を返す。

    直近の未回答の夜予測を、当日と前日の2日分のみ見て探す
    （FEEDBACK_LOOKBACK_DAYS=2 の range(2) のため、2日前は見ない）。
    """
    base = date_cls.fromisoformat(today)
    for i in range(FEEDBACK_LOOKBACK_DAYS):
        d = (base - timedelta(days=i)).isoformat()
        if memory.get_prediction(d, "NIGHT") and memory.get_feedback(d) is None:
            return d
    return None


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


def _authorized_texts(payload: dict, user_id: str) -> list[str]:
    """許可した個人ユーザーのイベントだけ本文を返す。"""
    if not isinstance(user_id, str) or not user_id:
        return []
    out: list[str] = []
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
            out.append(msg["text"])
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
    for text in _authorized_texts(payload, allowed_user_id):
        score = parse_feedback(text)
        if score is not None:
            memory = hd._memory(settings)
            target = resolve_feedback_date(memory, hd.origin_date(now))
            if target:
                memory.save_feedback(
                    Feedback(date=target, score=score,
                             answered_at=now.isoformat(timespec="seconds"))
                )
                log.log("feedback_saved", date=target, score=score)
                handled.append("feedback")
            else:
                log.log("feedback_unmatched", score=score)
            continue

        if is_wakeup_trigger(text):
            date = hd.origin_date(now, morning=True)
            run_morning(date, hd.build_morning_deps(settings))
            log.log("wakeup_triggered", date=date)
            handled.append("wakeup")

    return {"statusCode": 200, "body": json.dumps({"handled": handled})}
