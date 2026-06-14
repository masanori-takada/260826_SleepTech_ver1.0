"""Webhook 会話統合テスト（T210 / FR-101,105,106）。

_handle_message を依存注入で検証。LINE送信・Memory・DDBはフェイク。
特に「危険な応答が送信されない（ガードレール）」を確認。
"""

from __future__ import annotations

import sys
import types

import pytest

from night_ramen.agents.prompts import make_fake_invoker
from night_ramen.memory.client import MemoryClient
from night_ramen.memory.schemas import DailyVitalRecord, Goal


class _FakeReport:
    def __init__(self):
        self.saved = {}

    def put(self, date, ramen):
        self.saved[date] = ramen


class _FakeMemory:
    def __init__(self):
        self.turns = []

    def get_recent_turns(self, a, s, k=6):
        return []

    def retrieve_ltm(self, a, s, query, top_k=3):
        return ["ユーザーは夜勤明けに食べたくなる傾向"]

    def write_turn(self, a, s, role, text):
        self.turns.append((role, text))


def _vitals_client():
    c = MemoryClient()
    c.save_vital(DailyVitalRecord(date="2026-06-13", ramen_last_night=True, hrv=38.0,
                                  deep_sleep_min=45, next_day_steps=4000))
    return c


@pytest.fixture
def captured_replies(monkeypatch):
    """notifier.line.reply_message をフェイク化して送信本文を捕捉。"""
    sent = []
    fake = types.ModuleType("night_ramen.notifier.line")

    def reply_message(reply_token, text, token):
        sent.append(text)

    # 既存モジュールの関数だけ差し替え（他関数は本物を温存するため属性パッチ）
    import night_ramen.notifier.line as real
    monkeypatch.setattr(real, "reply_message", reply_message)
    return sent


def _make_settings(model="m"):
    from night_ramen.config import load_settings
    s = load_settings()
    return s


def _deps(invoker):
    return {
        "invoker": invoker,
        "report": _FakeReport(),
        "vitals_client": _vitals_client(),
        "memory": _FakeMemory(),
        "goal": Goal(),
    }


def _event(text):
    return {"type": "message", "message": {"type": "text", "text": text},
            "replyToken": "rt", "source": {"userId": "U_test"}}


def test_conversation_reply_and_report(captured_replies, monkeypatch):
    from night_ramen.config import load_settings
    from importlib import import_module
    wh = import_module("handlers.webhook_handler")
    log = _Log()

    def responder(model_id, prompt, system):
        if "分類器" in system:
            return "no"   # 自己申告: 我慢した
        return "昨夜はHRVが少し下がっていたね。今夜はあたたかいお茶で乗り切ろう。応援してるよ。"

    deps = _deps(make_fake_invoker(responder))
    wh._handle_message(_event("夜中我慢できたよ、つらかったけど"), deps, "tok", load_settings(), log)

    assert captured_replies, "返信が送信されていない"
    assert "応援してる" in captured_replies[0]
    assert deps["report"].saved.get(wh._today_jst()) is False   # 我慢=false 記録
    assert ("USER", "夜中我慢できたよ、つらかったけど") in deps["memory"].turns


def test_dangerous_reply_is_blocked(captured_replies):
    from night_ramen.config import load_settings
    from importlib import import_module
    wh = import_module("handlers.webhook_handler")
    log = _Log()

    # エージェントが危険発言を返しても、ガードレールで安全文面に差し替わる
    def responder(model_id, prompt, system):
        if "分類器" in system:
            return "unknown"
        return "意志が弱い無能なお前は一生治らない"

    deps = _deps(make_fake_invoker(responder))
    wh._handle_message(_event("最近だらけてる"), deps, "tok", load_settings(), log)

    assert captured_replies
    assert "無能" not in captured_replies[0]   # 危険応答は送信されない（FR-106）


def test_report_saved_to_past_date(captured_replies):
    """「6/12 食べた」のような過去日付指定は当日でなく該当日に記録される（記録漏れ補完）。"""
    from night_ramen.config import load_settings
    from importlib import import_module
    wh = import_module("handlers.webhook_handler")
    log = _Log()

    def responder(model_id, prompt, system):
        if "分類器" in system:
            return "yes"
        if "日付抽出器" in system:
            return "2026-06-12"   # 正規表現で確定するため通常呼ばれない（保険）
        return "6/12のラーメンありを記録したよ。教えてくれてありがとう。"

    deps = _deps(make_fake_invoker(responder))
    wh._handle_message(_event("6/12 食べた"), deps, "tok", load_settings(), log)

    saved = deps["report"].saved
    assert len(saved) == 1
    (date_key, val), = saved.items()
    assert date_key.endswith("-06-12")          # 6/12 に保存（年は実行時 today 依存）
    assert val is True
    assert date_key != wh._today_jst()          # 当日ではなく過去日付


def test_report_no_date_defaults_today(captured_replies):
    """日付指定が無い従来発話は当日に記録される（回帰・完全互換）。"""
    from night_ramen.config import load_settings
    from importlib import import_module
    wh = import_module("handlers.webhook_handler")
    log = _Log()

    def responder(model_id, prompt, system):
        if "分類器" in system:
            return "yes"
        return "記録したよ。"

    deps = _deps(make_fake_invoker(responder))
    wh._handle_message(_event("食べちゃった"), deps, "tok", load_settings(), log)

    assert deps["report"].saved.get(wh._today_jst()) is True


class _Log:
    def log(self, *a, **k):
        pass
