"""Lambda ハンドラの入力契約とイベント単位の耐障害性を検証する。"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.handlers import deps as hd
from src.handlers import night_handler as nh
from src.handlers import webhook_handler as wh

JST = timezone(timedelta(hours=9))


class _Settings:
    pass


def _patch_night_dependencies(monkeypatch):
    settings = _Settings()
    calls = []
    monkeypatch.setattr(nh, "load_settings", lambda: settings)
    monkeypatch.setattr(nh.hd, "build_night_deps", lambda actual: "deps")
    monkeypatch.setattr(nh, "run_night", lambda date, now_h, deps: calls.append((date, now_h, deps)) or {"ok": True})
    return calls


@pytest.mark.parametrize("event", [{"date": "2026-08-26"}, {"now_h": 28.5}])
def test_night_handler_rejects_partial_event_override(monkeypatch, event):
    calls = _patch_night_dependencies(monkeypatch)

    result = nh.handler(event)

    assert result["statusCode"] == 400
    assert calls == []


def test_night_handler_uses_explicit_pair_for_delayed_eventbridge_run(monkeypatch):
    calls = _patch_night_dependencies(monkeypatch)
    monkeypatch.setattr(
        nh.hd,
        "jst_now",
        lambda: datetime(2026, 8, 27, 4, 30, tzinfo=JST),
    )

    result = nh.handler({"date": "2026-08-26", "now_h": 28.5})

    assert result == {"ok": True}
    assert calls == [("2026-08-26", 28.5, "deps")]


def test_night_handler_derives_scheduled_time_in_jst_for_retry(monkeypatch):
    calls = _patch_night_dependencies(monkeypatch)
    monkeypatch.setattr(
        nh.hd,
        "jst_now",
        lambda: datetime(2026, 8, 27, 4, 30, tzinfo=JST),
    )

    result = nh.handler({"scheduled_time": "2026-08-26T13:30:00Z"})

    assert result == {"ok": True}
    assert calls == [("2026-08-26", 22.5, "deps")]


def test_night_handler_derives_offset_scheduled_time_in_jst(monkeypatch):
    calls = _patch_night_dependencies(monkeypatch)

    nh.handler({"scheduled_time": "2026-08-26T15:30:00+02:00"})

    assert calls == [("2026-08-26", 22.5, "deps")]


def test_night_handler_uses_wall_clock_for_manual_fallback(monkeypatch):
    calls = _patch_night_dependencies(monkeypatch)
    monkeypatch.setattr(
        nh.hd,
        "jst_now",
        lambda: datetime(2026, 8, 26, 22, 30, tzinfo=JST),
    )

    nh.handler({})

    assert calls == [("2026-08-26", 22.5, "deps")]


@pytest.mark.parametrize(
    "event",
    [
        {"scheduled_time": "2026-08-26T13:30:00Z", "date": "2026-08-26", "now_h": 22.5},
        {"scheduled_time": "2026-08-26T13:30:00Z", "date": "2026-08-26"},
        {"scheduled_time": "2026-08-26T13:30:00Z", "now_h": 22.5},
        {"scheduled_time": 123},
        {"scheduled_time": "not-an-iso-time"},
    ],
)
def test_night_handler_rejects_invalid_or_mixed_scheduled_input(monkeypatch, event):
    calls = _patch_night_dependencies(monkeypatch)

    result = nh.handler(event)

    assert result["statusCode"] == 400
    assert calls == []


def _webhook_event(events):
    return {"body": json.dumps({"events": events}, ensure_ascii=False)}


def _patch_webhook_dependencies(monkeypatch):
    settings = _Settings()
    morning_calls = []
    monkeypatch.setattr(wh, "load_settings", lambda: settings)
    monkeypatch.setattr(wh.hd, "jst_now", lambda: datetime(2026, 8, 27, 8, tzinfo=JST))
    monkeypatch.setattr(wh.hd, "build_morning_deps", lambda actual: "deps")
    monkeypatch.setattr(
        wh,
        "run_morning",
        lambda date, deps: morning_calls.append((date, deps)) or {"delivered": True},
    )
    return morning_calls


@pytest.mark.parametrize(
    "event",
    [
        {"body": "not-json"},
        {"body": "[]"},
        {"body": json.dumps({"events": "not-a-list"})},
        _webhook_event([{"type": "message", "message": []}]),
        _webhook_event([{"type": "message", "message": {"type": "text", "text": 3}}]),
    ],
)
def test_webhook_handler_skips_malformed_input_without_failing(monkeypatch, event):
    morning_calls = _patch_webhook_dependencies(monkeypatch)

    result = wh.handler(event)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"handled": []}
    assert morning_calls == []


def test_webhook_handler_keeps_valid_events_when_one_event_is_malformed(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _webhook_event(
        [
            {"type": "message", "message": []},
            {"type": "message", "message": {"type": "text", "text": "おはよう"}},
        ]
    )

    result = wh.handler(event)

    assert json.loads(result["body"]) == {"handled": ["wakeup"]}
    assert morning_calls == [("2026-08-26", "deps")]


def test_webhook_handler_processes_multiple_valid_events(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _webhook_event(
        [
            {"type": "message", "message": {"type": "text", "text": "おはよう"}},
            {"type": "message", "message": {"type": "text", "text": "同期"}},
        ]
    )

    result = wh.handler(event)

    assert json.loads(result["body"]) == {"handled": ["wakeup", "wakeup"]}
    assert morning_calls == [("2026-08-26", "deps"), ("2026-08-26", "deps")]


def test_texts_returns_only_string_texts_from_valid_events():
    event = _webhook_event(
        [
            {"type": "message", "message": {"type": "text", "text": "有効"}},
            {"type": "message", "message": {"type": "text", "text": 3}},
            {"type": "message", "message": {}},
        ]
    )

    assert wh._texts(event) == ["有効"]
