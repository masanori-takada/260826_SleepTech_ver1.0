"""会話からの自己申告抽出テスト（T207 / FR-105）。"""

from __future__ import annotations

from night_ramen.agents.prompts import make_fake_invoker
from night_ramen.conversation.extractor import extract_ramen_report, resolve_report_date


def test_fixed_parse_takes_priority():
    # 固定パースで判別できる場合は LLM を呼ばない
    def boom(model_id, prompt, system):
        raise AssertionError("LLM は呼ばれないはず")

    assert extract_ramen_report("我慢した", make_fake_invoker(boom), "m") is False
    assert extract_ramen_report("食べた", make_fake_invoker(boom), "m") is True


def test_llm_completes_when_fixed_unknown():
    # 固定パースが None のとき LLM 補完
    def yes(model_id, prompt, system):
        return "yes"

    # 「夜中につい手が出ちゃってさ」→ 固定パース不可 → LLMがyes
    assert extract_ramen_report("夜中につい手が出ちゃってさ", make_fake_invoker(yes), "m") is True


def test_llm_no_classification():
    def no(model_id, prompt, system):
        return "no"

    assert extract_ramen_report("ぐっと我慢して水飲んだよ", make_fake_invoker(no), "m") is False


def test_unknown_returns_none():
    def unknown(model_id, prompt, system):
        return "unknown"

    assert extract_ramen_report("今日は仕事が忙しかった", make_fake_invoker(unknown), "m") is None


def test_no_invoker_returns_none_when_unparseable():
    # invoker 無し かつ 固定パース不可 → None（記録しない）
    assert extract_ramen_report("こんばんは") is None


def test_resolve_date_regex_first_no_llm():
    # 明示/相対が取れたら LLM は呼ばない
    def boom(model_id, prompt, system):
        raise AssertionError("正規表現で確定したら LLM は呼ばないはず")

    assert resolve_report_date("6/12 食べた", "2026-06-15", make_fake_invoker(boom), "m") == "2026-06-12"
    assert resolve_report_date("おととい我慢した", "2026-06-15", make_fake_invoker(boom), "m") == "2026-06-13"


def test_resolve_date_llm_fallback():
    # 正規表現で取れない曖昧表現は LLM 抽出
    def iso(model_id, prompt, system):
        return "対象日は 2026-06-10 です"

    assert resolve_report_date("この前の水曜の夜さ", "2026-06-15", make_fake_invoker(iso), "m") == "2026-06-10"


def test_resolve_date_defaults_today():
    # invoker 無し・日付手掛かり無し → today
    assert resolve_report_date("食べた", "2026-06-15") == "2026-06-15"


def test_resolve_date_clamps_future_and_too_old():
    def future(model_id, prompt, system):
        return "2099-01-01"

    def ancient(model_id, prompt, system):
        return "2000-01-01"

    # 未来・保持期間超過は today に丸める
    assert resolve_report_date("いつかの夜", "2026-06-15", make_fake_invoker(future), "m") == "2026-06-15"
    assert resolve_report_date("ずっと前の夜", "2026-06-15", make_fake_invoker(ancient), "m") == "2026-06-15"
