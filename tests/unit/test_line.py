"""LINE メッセージ構築とフィードバック判定（仕様 第9節「フィードバックの取り込み」）。"""

import pytest

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
