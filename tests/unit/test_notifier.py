"""LINE 通知の1晩1回冪等テスト（T012 / FR-014）。"""

from __future__ import annotations

from night_ramen.notifier.line import (
    DryRunPushClient,
    InMemoryIdempotencyStore,
    send_if_not_notified,
)


def test_sends_once_per_night():
    store = InMemoryIdempotencyStore()
    client = DryRunPushClient()

    first = send_if_not_notified("2026-06-14", "今夜は我慢しましょう", store, client)
    second = send_if_not_notified("2026-06-14", "二通目", store, client)

    assert first is True
    assert second is False
    assert client.sent == ["今夜は我慢しましょう"]  # 2通目は送信されない


def test_different_nights_send_independently():
    store = InMemoryIdempotencyStore()
    client = DryRunPushClient()

    assert send_if_not_notified("2026-06-14", "夜A", store, client) is True
    assert send_if_not_notified("2026-06-15", "夜B", store, client) is True
    assert client.sent == ["夜A", "夜B"]
