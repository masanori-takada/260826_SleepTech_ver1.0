"""LINE Webhook Lambda。

役割は2つ。
1. 起床トリガー（「おはよう」等）を検出したら run_morning を前倒し実行する
   （収集だけでなく送信まで行う。冪等は run_morning が担保する）
2. クイックリプライの回答（評価:N）を直近の未回答の夜予測に紐付けて保存する

どちらでもないメッセージは何もしない（ver1.0 に会話機能は無い）。
"""

from __future__ import annotations

import json
import re
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


def _texts(event) -> list[str]:
    """Webhook イベントからテキストメッセージ本文を取り出す。

    top-level/body/events/message の型を検査し、不正なeventだけを捨てる。
    1件の不正入力で同じbatch内の正常メッセージまで失わない。
    """
    if not isinstance(event, dict):
        return []
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError, AttributeError):
        return []

    if not isinstance(body, dict):
        return []
    events = body.get("events", [])
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


def handler(event, context=None) -> dict:
    log = StructuredLogger()
    settings = load_settings()
    now = hd.jst_now()
    handled = []

    for text in _texts(event or {}):
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
