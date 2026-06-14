"""自己申告の解釈・署名検証テスト（US2 / FR-002,011）。"""

from __future__ import annotations

from night_ramen.collectors.self_report import is_wakeup_trigger, parse_ramen_reply
from night_ramen.notifier.line import verify_line_signature


def test_parse_affirmative():
    assert parse_ramen_reply("食べた") is True
    assert parse_ramen_reply("はい、食べちゃった") is True
    assert parse_ramen_reply("yes") is True


def test_parse_negative():
    assert parse_ramen_reply("食べてない") is False
    assert parse_ramen_reply("我慢した") is False
    assert parse_ramen_reply("no") is False


def test_negative_precedence_over_substring():
    # 「食べてない」は「食べ」を含むが、否定として正しく判定される
    assert parse_ramen_reply("昨日は食べてないよ") is False


def test_parse_unknown():
    assert parse_ramen_reply("こんにちは") is None
    assert parse_ramen_reply("") is None


def test_wakeup_trigger_detected():
    # 起床トリガー語（Feature 003）
    assert is_wakeup_trigger("おはよう") is True
    assert is_wakeup_trigger("おはよう☀️") is True
    assert is_wakeup_trigger("データ更新して") is True
    assert is_wakeup_trigger("同期") is True
    assert is_wakeup_trigger("起きたよ") is True


def test_wakeup_trigger_not_for_self_report_or_chitchat():
    # 自己申告語・雑談はトリガーにしない（誤爆防止）
    assert is_wakeup_trigger("食べた") is False
    assert is_wakeup_trigger("我慢した") is False
    assert is_wakeup_trigger("こんばんは") is False
    assert is_wakeup_trigger("") is False


def test_signature_verification():
    import base64
    import hashlib
    import hmac

    secret = "test_channel_secret"
    body = b'{"events":[]}'
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    sig = base64.b64encode(mac).decode()

    assert verify_line_signature(body, sig, secret) is True
    assert verify_line_signature(body, "wrong", secret) is False
    assert verify_line_signature(b'{"tampered":1}', sig, secret) is False
