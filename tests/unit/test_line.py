"""LINE メッセージ構築とフィードバック判定（仕様 第9節「フィードバックの取り込み」）。"""

import pytest
from urllib.error import HTTPError

from sleeptech.notifier import line


def test_plain_message_has_no_quick_reply():
    msg = line.build_message("本文", with_feedback=False)
    assert msg["type"] == "text"
    assert msg["text"] == "本文"
    assert "quickReply" not in msg


def test_feedback_message_has_five_options():
    msg = line.build_message("本文", with_feedback=True)
    items = msg["quickReply"]["items"]
    assert len(items) == 5


def test_feedback_actions_use_the_fixed_prefix():
    """回答テキストは 評価:1〜評価:5 の固定形式。"""
    items = line.build_message("本文", with_feedback=True)["quickReply"]["items"]
    texts = [i["action"]["text"] for i in items]
    assert texts == [f"{line.FEEDBACK_PREFIX}{n}" for n in (1, 2, 3, 4, 5)]


def test_feedback_prompt_is_appended_to_the_body():
    msg = line.build_message("本文", with_feedback=True)
    assert "本文" in msg["text"]
    assert "調子" in msg["text"]


def test_domain_feedback_encodes_focus_in_question_and_answer_payload():
    msg = line.build_message("本文", with_feedback=True, feedback_dimension="focus")

    assert "集中力" in msg["text"]
    texts = [item["action"]["text"] for item in msg["quickReply"]["items"]]
    assert texts == [f"評価:focus:{score}" for score in (1, 2, 3, 4, 5)]


def test_parse_domain_feedback_returns_dimension_and_score():
    answer = line.parse_feedback_answer("評価:sleepiness:4")

    assert answer.dimension == "sleepiness"
    assert answer.score == 4


def test_parse_domain_feedback_rejects_unknown_dimension():
    assert line.parse_feedback_answer("評価:unknown:4") is None


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_parse_feedback_accepts_valid_scores(n):
    assert line.parse_feedback(f"{line.FEEDBACK_PREFIX}{n}") == n


def test_parse_feedback_tolerates_surrounding_whitespace():
    assert line.parse_feedback(f"  {line.FEEDBACK_PREFIX}3  ") == 3


@pytest.mark.parametrize(
    "text",
    ["3", "評価", "評価:0", "評価:6", "評価:abc", "おはよう", "", "今日の評価:3くらい"],
)
def test_parse_feedback_rejects_everything_else(text):
    """自然文や範囲外を誤って取り込まない。"""
    assert line.parse_feedback(text) is None


def test_push_client_posts_expected_payload():
    captured = {}

    def fake_post(url, headers, body):
        captured.update({"url": url, "headers": headers, "body": body})

    c = line.LinePushClient("token", "U123", http_post=fake_post)
    c.push(line.build_message("本文", with_feedback=False))

    assert captured["url"] == line.PUSH_URL
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["body"]["to"] == "U123"
    assert captured["body"]["messages"][0]["text"] == "本文"


def test_fake_push_client_records_messages():
    c = line.FakePushClient()
    c.push(line.build_message("A", with_feedback=False))
    c.push(line.build_message("B", with_feedback=True))
    assert len(c.sent) == 2
    assert c.sent[0]["text"] == "A"


def test_push_client_uses_retry_key_on_every_request():
    captured = {}

    def fake_post(url, headers, body):
        captured["headers"] = headers

    c = line.LinePushClient("token", "U123", http_post=fake_post)
    key = "12345678-1234-5678-1234-567812345678"
    c.push(line.build_message("本文", with_feedback=False), retry_key=key)

    assert captured["headers"]["X-Line-Retry-Key"] == key


def test_reply_uses_reply_endpoint_and_token():
    calls = []
    client = line.LinePushClient(
        "token", "U1", http_post=lambda url, headers, body: calls.append((url, headers, body))
    )

    client.reply("reply-token", {"type": "text", "text": "記録しました"})

    assert calls[0][0] == line.REPLY_URL
    assert calls[0][2] == {
        "replyToken": "reply-token",
        "messages": [{"type": "text", "text": "記録しました"}],
    }


def test_default_http_post_accepts_line_duplicate_retry_key(monkeypatch):
    """LINE が重複 retry key を受理済みと返す409は送信成功として扱う。"""
    error = HTTPError(
        line.PUSH_URL, 409, "Conflict",
        {"X-Line-Accepted-Request-Id": "accepted-request-id"}, None,
    )

    def fail_urlopen(request, timeout):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    line._default_http_post(line.PUSH_URL, {}, {})


def test_default_http_post_does_not_swallow_generic_409(monkeypatch):
    """重複受理ヘッダーのない一般409は上位へ伝播する。"""
    error = HTTPError(line.PUSH_URL, 409, "Conflict", {}, None)

    def fail_urlopen(request, timeout):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    with pytest.raises(HTTPError):
        line._default_http_post(line.PUSH_URL, {}, {})
