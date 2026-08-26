"""OpenAI クライアントのリトライとエラー処理（仕様 第7節）。"""

import pytest

from sleeptech.llm import client as llm


def _ok_response(text: str = "生成された文章") -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_complete_returns_generated_text():
    calls = []

    def fake_post(url, headers, body):
        calls.append((url, headers, body))
        return _ok_response()

    c = llm.OpenAiClient("sk-test", "gpt-x", http_post=fake_post, sleep=lambda _: None)
    assert c.complete("システム", "ユーザー") == "生成された文章"
    assert len(calls) == 1


def test_request_shape_is_chat_completions():
    captured = {}

    def fake_post(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return _ok_response()

    llm.OpenAiClient("sk-test", "gpt-x", http_post=fake_post, sleep=lambda _: None).complete(
        "SYS", "USR"
    )
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["model"] == "gpt-x"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_retries_twice_then_succeeds():
    attempts = {"n": 0}

    def flaky_post(url, headers, body):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("timeout")
        return _ok_response()

    c = llm.OpenAiClient("sk", "gpt-x", http_post=flaky_post, sleep=lambda _: None)
    assert c.complete("s", "u") == "生成された文章"
    assert attempts["n"] == 3


def test_raises_llm_error_after_exhausting_retries():
    def always_fail(url, headers, body):
        raise OSError("connection refused")

    c = llm.OpenAiClient("sk", "gpt-x", http_post=always_fail, sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")


def test_attempts_exactly_three_times():
    """初回 + リトライ2回 = 3回で打ち切る。"""
    attempts = {"n": 0}

    def always_fail(url, headers, body):
        attempts["n"] += 1
        raise OSError("boom")

    c = llm.OpenAiClient("sk", "gpt-x", http_post=always_fail, sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")
    assert attempts["n"] == 3


def test_malformed_response_raises_llm_error():
    """レスポンス形状が想定外でも例外型は LlmError に揃える。"""
    c = llm.OpenAiClient("sk", "gpt-x", http_post=lambda *a: {"unexpected": 1},
                         sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")


def test_empty_content_raises_llm_error():
    c = llm.OpenAiClient("sk", "gpt-x", http_post=lambda *a: _ok_response("  "),
                         sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")


def test_null_client_always_raises():
    with pytest.raises(llm.LlmError):
        llm.NullClient().complete("s", "u")
