"""夜 Lambda（EventBridge 22:30 JST 起動）。

payload 例: {"date": "2026-08-26", "now_h": 22.5}（省略時は現在時刻から決定）
"""

from __future__ import annotations

from sleeptech.config import load_settings
from sleeptech.workflow.night import run_night
from src.handlers import deps as hd


def handler(event, context=None) -> dict:
    event = event or {}
    settings = load_settings()
    now = hd.jst_now()
    # 起点日と now_h は同じ判定（night=True）から導く。片方だけ直すと
    # 0:00 を越えて実行されたときに不整合になる（起点日は前日なのに
    # now_h が前日 0:00 からの経過時間になっていない、等）。
    date = event.get("date") or hd.origin_date(now, night=True)
    current_h = event.get("now_h")
    if current_h is None:
        current_h = hd.now_h(now, night=True)
    return run_night(date, float(current_h), hd.build_night_deps(settings))
