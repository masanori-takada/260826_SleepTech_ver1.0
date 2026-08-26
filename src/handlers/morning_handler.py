"""朝 Lambda（EventBridge 8:00 JST 起動）。

起点日は前日である点に注意（前夜の夜メッセージと同じ日付を使う）。
送信の冪等は run_morning が内部で担保するため、ここでは判定しない。
"""

from __future__ import annotations

from sleeptech.config import load_settings
from sleeptech.workflow.morning import run_morning
from src.handlers import deps as hd


def handler(event, context=None) -> dict:
    event = event or {}
    settings = load_settings()
    date = event.get("date") or hd.origin_date(hd.jst_now(), morning=True)
    deadline = hd.deadline_from_context(context)
    deps = (hd.build_morning_deps(settings, deadline) if deadline is not None
            else hd.build_morning_deps(settings))
    return run_morning(date, deps)
