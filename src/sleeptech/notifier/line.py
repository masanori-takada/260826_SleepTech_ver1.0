"""LINE Messaging API への送信（仕様 第9節）。

標準ライブラリのみで実装する（Lambda に外部依存を持ち込まない）。
クイックリプライの回答テキストは自然文と衝突しない固定形式 評価:N とし、
Webhook 側は完全一致だけで判定する。LLM 分類器は使わない。
"""

from __future__ import annotations

import re
from typing import Callable

PUSH_URL = "https://api.line.me/v2/bot/message/push"
FEEDBACK_PREFIX = "評価:"
FEEDBACK_QUESTION = "今日の調子はどうだった？"
FEEDBACK_LABELS: list[tuple[str, int]] = [
    ("1 最悪", 1),
    ("2", 2),
    ("3 ふつう", 3),
    ("4", 4),
    ("5 絶好調", 5),
]

# 「評価:N」(N=1〜5) に完全一致する場合のみ受理する。
# 前方一致にすると「今日の評価:3くらい」のような自然文まで拾ってしまうため、
# 文字列全体に対する完全一致（^...$ アンカー）で判定する。
_FEEDBACK_RE = re.compile(rf"^{re.escape(FEEDBACK_PREFIX)}([1-5])$")


def build_message(text: str, with_feedback: bool) -> dict:
    """LINE の messages 要素を組み立てる。

    with_feedback=True のとき、本文末尾に質問を足し5段階のクイックリプライを付ける。
    LLM 障害時のテンプレート文でも必ず付けること（データ欠落を防ぐため）。
    """
    if not with_feedback:
        return {"type": "text", "text": text}

    return {
        "type": "text",
        "text": f"{text}\n\n{FEEDBACK_QUESTION}",
        "quickReply": {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "message",
                        "label": label,
                        "text": f"{FEEDBACK_PREFIX}{score}",
                    },
                }
                for label, score in FEEDBACK_LABELS
            ]
        },
    }


def parse_feedback(text: str) -> int | None:
    """クイックリプライの回答なら 1-5 を返す。それ以外は None。"""
    m = _FEEDBACK_RE.match((text or "").strip())
    return int(m.group(1)) if m else None


class LinePushClient:
    """LINE Messaging API へ push メッセージを送るクライアント。

    LINE SDK は使わず urllib.request のみで実装する。
    """

    def __init__(
        self,
        channel_access_token: str,
        user_id: str,
        http_post: Callable[[str, dict, dict], None] | None = None,
    ) -> None:
        self._token = channel_access_token
        self._user_id = user_id
        self._post = http_post or _default_http_post

    def push(self, message: dict) -> None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        self._post(PUSH_URL, headers, {"to": self._user_id, "messages": [message]})


class FakePushClient:
    """テスト用。送信内容を sent に貯める。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def push(self, message: dict) -> None:
        self.sent.append(message)


def _default_http_post(url: str, headers: dict, body: dict) -> None:
    import json
    import urllib.request

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — 固定の LINE 端点
        r.read()
