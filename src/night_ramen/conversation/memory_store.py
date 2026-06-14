"""agentcore Memory ラッパ（T204 / FR-102,103,107）。

boto3 の bedrock-agentcore data plane を直接呼ぶ（SDK非依存）。実機検証済みAPI:
- write_turn  : CreateEvent(payload=[{conversational:{content:{text},role}}])
- get_recent_turns: ListEvents → events[].payload[].conversational
- retrieve_ltm : RetrieveMemoryRecords(namespace, searchCriteria={searchQuery, topK})

ユーザー単位分離: actorId=LINE userId、名前空間に {actorId} を埋め込む（FR-107）。
例外は fail-soft（記憶が欠けても会話は継続）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# LTM 名前空間テンプレート（create_memory の戦略と一致させる）
_LTM_NAMESPACES = (
    "/facts/{actor_id}/",
    "/preferences/{actor_id}/",
    "/summaries/{actor_id}/{session_id}/",
)


class AgentCoreMemory:
    def __init__(self, memory_id: str, region: str, data_client: Any | None = None) -> None:
        self._memory_id = memory_id
        if data_client is not None:
            self._data = data_client
        else:
            import boto3

            self._data = boto3.client("bedrock-agentcore", region_name=region)

    def write_turn(self, actor_id: str, session_id: str, role: str, text: str) -> None:
        """会話ターンを STM へ保存（CreateEvent）。role は 'USER' / 'ASSISTANT'。"""
        if not text:
            return
        try:
            self._data.create_event(
                memoryId=self._memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[{"conversational": {"content": {"text": text}, "role": role}}],
            )
        except Exception:  # noqa: BLE001 — fail-soft
            pass

    def get_recent_turns(self, actor_id: str, session_id: str, k: int = 6) -> list[dict]:
        """直近 k ターンを [{role, text}] で返す（古い順）。失敗時は空。"""
        try:
            resp = self._data.list_events(
                memoryId=self._memory_id, actorId=actor_id, sessionId=session_id, maxResults=k
            )
        except Exception:  # noqa: BLE001
            return []
        turns: list[dict] = []
        for ev in resp.get("events", []):
            for item in ev.get("payload", []):
                conv = item.get("conversational")
                if conv:
                    turns.append(
                        {"role": conv.get("role", ""), "text": conv.get("content", {}).get("text", "")}
                    )
        return turns[-k:]

    def retrieve_ltm(self, actor_id: str, session_id: str, query: str, top_k: int = 3) -> list[str]:
        """facts/preferences/summaries を横断 semantic 検索し、文脈スニペットを返す（FR-103,104）。

        LTM 抽出は非同期・結果整合のため、空でも許容（fail-soft）。
        """
        snippets: list[str] = []
        for tmpl in _LTM_NAMESPACES:
            ns = tmpl.format(actor_id=actor_id, session_id=session_id)
            try:
                resp = self._data.retrieve_memory_records(
                    memoryId=self._memory_id,
                    namespace=ns,
                    searchCriteria={"searchQuery": query, "topK": top_k},
                )
            except Exception:  # noqa: BLE001
                continue
            for rec in resp.get("memoryRecords", []) or resp.get("records", []):
                text = _extract_record_text(rec)
                if text:
                    snippets.append(text)
        return snippets


def _extract_record_text(rec: dict) -> str:
    """memory record から本文テキストを防御的に取り出す（実レスポンスに合わせ調整可）。"""
    content = rec.get("content")
    if isinstance(content, dict):
        return content.get("text") or content.get("value") or ""
    if isinstance(content, str):
        return content
    return rec.get("text", "")
