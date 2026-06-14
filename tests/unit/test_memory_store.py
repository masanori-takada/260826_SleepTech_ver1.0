"""agentcore Memory ラッパのテスト（T205 / FR-102,107）。boto3 をフェイク注入。"""

from __future__ import annotations

from night_ramen.conversation.memory_store import AgentCoreMemory


class _FakeData:
    def __init__(self, events=None, raise_on=None):
        self._events = events or []
        self.created = []
        self._raise_on = raise_on or set()

    def create_event(self, **kw):
        if "create" in self._raise_on:
            raise RuntimeError("boom")
        self.created.append(kw)
        return {"event": {"eventId": "e1"}}

    def list_events(self, **kw):
        if "list" in self._raise_on:
            raise RuntimeError("boom")
        return {"events": self._events}

    def retrieve_memory_records(self, **kw):
        if "retrieve" in self._raise_on:
            raise RuntimeError("boom")
        return {"memoryRecords": [{"content": {"text": f"rec for {kw['namespace']}"}}]}


def _mem(fake):
    return AgentCoreMemory(memory_id="M", region="us-east-1", data_client=fake)


def test_write_turn_calls_create_event():
    fake = _FakeData()
    _mem(fake).write_turn("U1", "s1", "USER", "こんばんは")
    assert len(fake.created) == 1
    assert fake.created[0]["actorId"] == "U1"
    assert fake.created[0]["payload"][0]["conversational"]["content"]["text"] == "こんばんは"


def test_write_turn_skips_empty():
    fake = _FakeData()
    _mem(fake).write_turn("U1", "s1", "USER", "")
    assert fake.created == []


def test_get_recent_turns_parses_payload():
    events = [
        {"payload": [{"conversational": {"role": "USER", "content": {"text": "やあ"}}}]},
        {"payload": [{"conversational": {"role": "ASSISTANT", "content": {"text": "こんばんは"}}}]},
    ]
    turns = _mem(_FakeData(events=events)).get_recent_turns("U1", "s1", k=6)
    assert turns == [{"role": "USER", "text": "やあ"}, {"role": "ASSISTANT", "text": "こんばんは"}]


def test_retrieve_ltm_queries_namespaces():
    snippets = _mem(_FakeData()).retrieve_ltm("U1", "s1", query="睡眠", top_k=3)
    # 3名前空間（facts/preferences/summaries）から1件ずつ
    assert len(snippets) == 3
    assert any("/facts/U1/" in s for s in snippets)


def test_failures_are_fail_soft():
    # 例外時も会話を止めない（空/no-op）
    fake = _FakeData(raise_on={"create", "list", "retrieve"})
    m = _mem(fake)
    m.write_turn("U1", "s1", "USER", "x")          # 例外を飲み込む
    assert m.get_recent_turns("U1", "s1") == []
    assert m.retrieve_ltm("U1", "s1", "q") == []
