"""自己申告の解釈・署名検証テスト（US2 / FR-002,011）。"""

from __future__ import annotations

from night_ramen.collectors.self_report import (
    is_wakeup_trigger,
    parse_ramen_reply,
    parse_report_date,
)
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


def test_parse_report_date_relative():
    # 相対表現（過去日付の記録漏れ補完）
    assert parse_report_date("おととい食べた", "2026-06-15") == "2026-06-13"
    assert parse_report_date("昨日は我慢した", "2026-06-15") == "2026-06-14"
    assert parse_report_date("今日のラーメン", "2026-06-15") == "2026-06-15"
    assert parse_report_date("3日前に食べちゃった", "2026-06-15") == "2026-06-12"


def test_parse_report_date_explicit():
    assert parse_report_date("6/12 あり", "2026-06-15") == "2026-06-12"
    assert parse_report_date("6月12日は食べた", "2026-06-15") == "2026-06-12"
    assert parse_report_date("12日のやつ", "2026-06-15") == "2026-06-12"
    # 未来の月日は直近の過去（前年）として解釈
    assert parse_report_date("12/30の分", "2026-06-15") == "2025-12-30"


def test_parse_report_date_none_when_no_date():
    assert parse_report_date("食べた", "2026-06-15") is None
    assert parse_report_date("お腹すいたな", "2026-06-15") is None
