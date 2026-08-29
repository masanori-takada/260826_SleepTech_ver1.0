"""LINE Messaging API への送信（仕様 第9節）。

標準ライブラリのみで実装する（Lambda に外部依存を持ち込まない）。
クイックリプライの回答テキストは自然文と衝突しない固定形式 評価:N とし、
Webhook 側は完全一致だけで判定する。LLM 分類器は使わない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError

PUSH_URL = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
FEEDBACK_PREFIX = "評価:"
FEEDBACK_QUESTION = "今日の調子はどうだった？"
FEEDBACK_QUESTIONS = {
    "focus": "今日の集中力はどうだった？",
    "sleepiness": "今日の眠気の少なさはどうだった？",
    "mood": "今日の気分はどうだった？",
    "physical_fatigue": "今日の身体の軽さはどうだった？",
}
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
_DOMAIN_FEEDBACK_RE = re.compile(
    rf"^{re.escape(FEEDBACK_PREFIX)}(focus|sleepiness|mood|physical_fatigue):([1-5])$"
)


@dataclass(frozen=True)
class FeedbackAnswer:
    """クイックリプライから復元した評価領域と5段階スコア。"""

    dimension: str | None
    score: int


def build_message(
    text: str, with_feedback: bool, feedback_dimension: str | None = None
) -> dict:
    """LINE の messages 要素を組み立てる。

    with_feedback=True のとき、本文末尾に質問を足し5段階のクイックリプライを付ける。
    LLM 障害時のテンプレート文でも必ず付けること（データ欠落を防ぐため）。
    """
    if not with_feedback:
        return {"type": "text", "text": text}

    question = FEEDBACK_QUESTIONS.get(feedback_dimension, FEEDBACK_QUESTION)
    return {
        "type": "text",
        "text": f"{text}\n\n{question}",
        "quickReply": {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "message",
                        "label": label,
                        "text": (
                            f"{FEEDBACK_PREFIX}{feedback_dimension}:{score}"
                            if feedback_dimension else f"{FEEDBACK_PREFIX}{score}"
                        ),
                    },
                }
                for label, score in FEEDBACK_LABELS
            ]
        },
    }


def parse_feedback(text: str) -> int | None:
    """クイックリプライの回答なら 1-5 を返す。それ以外は None。"""
    answer = parse_feedback_answer(text)
    return answer.score if answer else None


def parse_feedback_answer(text: str) -> FeedbackAnswer | None:
    """新旧クイックリプライを領域付きの回答へ復元する。"""
    normalized = (text or "").strip()
    domain_match = _DOMAIN_FEEDBACK_RE.match(normalized)
    if domain_match:
        return FeedbackAnswer(domain_match.group(1), int(domain_match.group(2)))
    legacy_match = _FEEDBACK_RE.match(normalized)
    if legacy_match:
        return FeedbackAnswer(None, int(legacy_match.group(1)))
    return None


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

    def push(self, message: dict, retry_key: str | None = None) -> None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if retry_key:
            headers["X-Line-Retry-Key"] = retry_key
        try:
            self._post(PUSH_URL, headers, {"to": self._user_id, "messages": [message]})
        except HTTPError as exc:
            if exc.code == 409 and _has_accepted_retry_request_id(exc):
                return
            raise

    def reply(self, reply_token: str, message: dict) -> None:
        """指定されたWebhookイベントへ1件の確認メッセージを返信する。"""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        self._post(REPLY_URL, headers, {
            "replyToken": reply_token,
            "messages": [message],
        })


class FakePushClient:
    """テスト用。送信内容を sent に貯める。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.retry_keys: list[str | None] = []

    def push(self, message: dict, retry_key: str | None = None) -> None:
        self.sent.append(message)
        self.retry_keys.append(retry_key)


def _default_http_post(url: str, headers: dict, body: dict) -> None:
    import json
    import urllib.request

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 — 固定の LINE 端点
            r.read()
    except HTTPError as exc:
        if exc.code == 409 and _has_accepted_retry_request_id(exc):
            return
        raise


def _has_accepted_retry_request_id(error: HTTPError) -> bool:
    """LINEの重複retry key受理応答に含まれる公式ヘッダーを判定する。"""
    return bool(error.headers and error.headers.get("X-Line-Accepted-Request-Id"))
