"""Lambda ハンドラの入力契約とイベント単位の耐障害性を検証する。"""

import base64
import hashlib
import hmac
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


@pytest.mark.parametrize(
    "event",
    [
        {"scheduledtime": "2026-08-26T13:30:00Z"},
        {"scheduled_time": "2026-08-26T13:30:00Z", "extra": True},
        {"date": "2026-08-26", "now_h": 22.5, "extra": True},
        [],
        "not-an-event-object",
    ],
)
def test_night_handler_rejects_unknown_keys_and_non_mapping_events(monkeypatch, event):
    calls = _patch_night_dependencies(monkeypatch)

    result = nh.handler(event)

    assert result["statusCode"] == 400
    assert calls == []


def _webhook_event(events):
    return _signed_webhook_event(events)


LINE_SECRET = "line-channel-secret"


def _signed_raw_event(body, *, encoded=False, headers=None):
    return _signed_bytes_event(body.encode("utf-8"), encoded=encoded, headers=headers)


def _signed_bytes_event(raw, *, encoded=False, headers=None):
    signature = base64.b64encode(
        hmac.new(LINE_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    ).decode("ascii")
    return {
        "body": base64.b64encode(raw).decode("ascii") if encoded else raw.decode("utf-8"),
        "isBase64Encoded": encoded,
        "headers": {"x-line-signature": signature} if headers is None else headers,
    }


def _signed_webhook_event(events, *, encoded=False, headers=None):
    return _signed_raw_event(
        json.dumps({"events": events}, ensure_ascii=False),
        encoded=encoded,
        headers=headers,
    )


def _patch_webhook_dependencies(monkeypatch):
    settings = _Settings()
    settings.secret_line = "sleeptech/line"
    settings.region = "ap-northeast-1"
    morning_calls = []
    monkeypatch.setattr(wh, "load_settings", lambda: settings)
    monkeypatch.setattr(
        wh.hd,
        "_secret",
        lambda name, region: {
            "channel_secret": LINE_SECRET,
            "channel_access_token": "token",
            "user_id": "user",
        },
    )
    monkeypatch.setattr(wh.hd, "jst_now", lambda: datetime(2026, 8, 27, 8, tzinfo=JST))
    monkeypatch.setattr(wh.hd, "build_morning_deps", lambda actual: "deps")
    monkeypatch.setattr(
        wh,
        "run_morning",
        lambda date, deps: morning_calls.append((date, deps)) or {"delivered": True},
    )
    return morning_calls


def test_webhook_rejects_missing_signature_before_loading_settings(monkeypatch):
    monkeypatch.setattr(
        wh,
        "load_settings",
        lambda: pytest.fail("署名欠落時は設定を読み込まない"),
    )

    result = wh.handler({"body": json.dumps({"events": []}), "headers": {}})

    assert result["statusCode"] == 401


def test_webhook_rejects_mismatched_signature_before_domain_processing(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_webhook_event(
        [{"type": "message", "message": {"type": "text", "text": "おはよう"}}],
        headers={"x-line-signature": "invalid"},
    )

    result = wh.handler(event)

    assert result["statusCode"] == 403
    assert morning_calls == []


@pytest.mark.parametrize("signature", ["あ" * 44, "A B", "!!!!", "A" * 45, 123, b"bytes"])
def test_webhook_rejects_non_strict_signature_as_403_without_domain_processing(
    monkeypatch, signature
):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_webhook_event(
        [{"type": "message", "message": {"type": "text", "text": "おはよう"}}],
        headers={"x-line-signature": signature},
    )

    result = wh.handler(event)

    assert result["statusCode"] == 403
    assert morning_calls == []


def test_webhook_accepts_valid_signature_and_preserves_raw_body(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_webhook_event(
        [{"type": "message", "message": {"type": "text", "text": "おはよう"}}]
    )

    result = wh.handler(event)

    assert result["statusCode"] == 200
    assert morning_calls == [("2026-08-26", "deps")]


def test_webhook_accepts_valid_signature_for_base64_body(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_webhook_event(
        [{"type": "message", "message": {"type": "text", "text": "同期"}}],
        encoded=True,
    )

    result = wh.handler(event)

    assert result["statusCode"] == 200
    assert morning_calls == [("2026-08-26", "deps")]


def test_webhook_rejects_invalid_utf8_after_base64_decode(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_bytes_event(b"\xff\xfe", encoded=True)

    result = wh.handler(event)

    assert result["statusCode"] == 400
    assert morning_calls == []


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "[]",
        json.dumps({"events": "not-a-list"}),
        json.dumps({"other": []}),
    ],
)
def test_webhook_rejects_invalid_payload_shape_after_signature(monkeypatch, body):
    morning_calls = _patch_webhook_dependencies(monkeypatch)

    result = wh.handler(_signed_raw_event(body))

    assert result["statusCode"] == 400
    assert morning_calls == []


def test_webhook_accepts_signed_empty_events_as_validation_probe(monkeypatch):
    _patch_webhook_dependencies(monkeypatch)

    result = wh.handler(_signed_raw_event('{"events":[]}'))

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"handled": []}


def test_webhook_verifies_exact_raw_body_with_formatting_and_escaping(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    raw = '{\n  "escaped": "\\ud83c\\udf19", "emoji": "🌙", "events": []\n}'
    event = _signed_raw_event(raw)

    result = wh.handler(event)

    assert result["statusCode"] == 200
    assert morning_calls == []


def test_webhook_rejects_one_byte_raw_body_change(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_raw_event('{"events":[]}')
    event["body"] = '{"events": []}'

    result = wh.handler(event)

    assert result["statusCode"] == 403
    assert morning_calls == []


def test_webhook_accepts_case_insensitive_signature_header(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_webhook_event(
        [{"type": "message", "message": {"type": "text", "text": "同期"}}],
        headers={"X-Line-Signature": _signature_for_event_body()},
    )

    result = wh.handler(event)

    assert result["statusCode"] == 200
    assert morning_calls == [("2026-08-26", "deps")]


def test_webhook_accepts_single_signature_in_multi_value_headers(monkeypatch):
    morning_calls = _patch_webhook_dependencies(monkeypatch)
    event = _signed_webhook_event(
        [{"type": "message", "message": {"type": "text", "text": "同期"}}],
        headers={},
    )
    event["multiValueHeaders"] = {"X-LINE-SIGNATURE": [_signature_for_event_body()]}

    result = wh.handler(event)

    assert result["statusCode"] == 200
    assert morning_calls == [("2026-08-26", "deps")]


def _signature_for_event_body():
    body = json.dumps(
        {"events": [{"type": "message", "message": {"type": "text", "text": "同期"}}]},
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.b64encode(
        hmac.new(LINE_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")


@pytest.mark.parametrize(
    "event",
    [
        {"headers": {"x-line-signature": "anything"}},
        {"body": "%%%", "isBase64Encoded": True, "headers": {}},
    ],
)
def test_webhook_rejects_missing_or_invalid_body_before_external_processing(monkeypatch, event):
    monkeypatch.setattr(
        wh,
        "load_settings",
        lambda: pytest.fail("不正body時は設定を読み込まない"),
    )

    result = wh.handler(event)

    assert 400 <= result["statusCode"] < 500


def test_webhook_rejects_malformed_header_container_without_raising(monkeypatch):
    monkeypatch.setattr(
        wh,
        "load_settings",
        lambda: pytest.fail("不正headers時は設定を読み込まない"),
    )

    result = wh.handler({"body": "{}", "headers": "not-a-map"})

    assert result["statusCode"] == 401


def test_webhook_rejects_non_boolean_base64_flag(monkeypatch):
    monkeypatch.setattr(
        wh,
        "load_settings",
        lambda: pytest.fail("不正フラグ時は設定を読み込まない"),
    )

    result = wh.handler({"body": "{}", "isBase64Encoded": "true", "headers": {}})

    assert result["statusCode"] == 400


@pytest.mark.parametrize(
    "event",
    [
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
    event = {
        "events": [
            {"type": "message", "message": {"type": "text", "text": "有効"}},
            {"type": "message", "message": {"type": "text", "text": 3}},
            {"type": "message", "message": {}},
        ]
    }

    assert wh._texts(event) == ["有効"]
