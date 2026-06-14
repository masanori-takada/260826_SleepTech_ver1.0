"""LINE 通知と1晩1回の冪等制御（T016 / FR-007,014）。

冪等フラグは IdempotencyStore 抽象に委譲（本番=DynamoDB、テスト=インメモリ）。
実送信は PushClient 抽象に委譲（本番=LINE SDK、テスト=フェイク、dry-run=送信なし）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class IdempotencyStore(Protocol):
    """日付単位の通知済みフラグ（FR-014）。"""

    def is_notified(self, date: str) -> bool: ...
    def mark_notified(self, date: str) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._sent: set[str] = set()

    def is_notified(self, date: str) -> bool:
        return date in self._sent

    def mark_notified(self, date: str) -> None:
        self._sent.add(date)


class PushClient(Protocol):
    """メッセージ送信の抽象。"""

    def push(self, text: str) -> None: ...


class LinePushClient:
    """本番用: LINE Messaging API（push message）。

    標準ライブラリ(urllib)のみで実装し、Lambda へ外部依存を持ち込まない
    （boto3 + stdlib だけでデプロイ可能にする / 原則IV のシンプルさ）。
    エンドポイント: POST https://api.line.me/v2/bot/message/push
    """

    PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, channel_access_token: str, user_id: str) -> None:
        self._token = channel_access_token
        self._user_id = user_id

    def push(self, text: str) -> None:
        import json
        import urllib.request

        body = json.dumps(
            {"to": self._user_id, "messages": [{"type": "text", "text": text}]}
        ).encode("utf-8")
        req = urllib.request.Request(
            self.PUSH_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — 固定の LINE 端点
            r.read()


class DryRunPushClient:
    """dry-run: 実送信せず記録のみ。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def push(self, text: str) -> None:
        self.sent.append(text)


def send_if_not_notified(
    date: str,
    text: str,
    store: IdempotencyStore,
    client: PushClient,
) -> bool:
    """当夜未通知のときのみ送信し True を返す。送信済みなら False（FR-014 冪等）。"""
    if store.is_notified(date):
        return False
    client.push(text)
    store.mark_notified(date)
    return True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
