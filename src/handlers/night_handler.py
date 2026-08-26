"""夜 Lambda（EventBridge 22:30 JST 起動）。

payload 例: {"date": "2026-08-26", "now_h": 22.5}。

EventBridge の遅延・再試行で4時を越える場合、wall clockだけでは元の夜の
起点日を復元できない。Task 16以降のproduction payloadはこのdateとnow_hの
ペアを明示的に渡す。両方ない場合だけmanual用のwall-clock fallbackを使い、
片方だけのoverrideは400相当で拒否する。
"""

from __future__ import annotations

from sleeptech.config import load_settings
from sleeptech.workflow.night import run_night
from src.handlers import deps as hd


def handler(event, context=None) -> dict:
    event = event or {}
    date_override = event.get("date")
    now_h_override = event.get("now_h")
    has_date = date_override is not None
    has_now_h = now_h_override is not None
    if has_date != has_now_h:
        return {
            "statusCode": 400,
            "body": '{"error": "date and now_h must be provided together"}',
        }

    settings = load_settings()
    if has_date:
        # 明示payloadは遅延実行でも元の夜を特定できる契約として優先する。
        date = date_override
        current_h = float(now_h_override)
    else:
        now = hd.jst_now()
        date = hd.origin_date(now, night=True)
        current_h = hd.now_h(now, night=True)
    return run_night(date, float(current_h), hd.build_night_deps(settings))
