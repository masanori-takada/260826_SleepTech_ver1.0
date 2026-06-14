"""agentcore Memory ラッパ（T007 / contracts/memory-schemas.md）。

記憶系（vital/analysis/message/strategy）への唯一のアクセス経路。
不変条件: save_strategy は guardrail_passed=True のみ許可（FR-010）。

agentcore Memory SDK の具体APIは実行環境に依存するため、本クラスは `backend` を注入可能にして
テスト（インメモリ）と本番（agentcore）を切り替えられる設計とする。
"""

from __future__ import annotations

from typing import Protocol

from night_ramen.memory.schemas import (
    AnalysisResult,
    DailyVitalRecord,
    GuardrailResult,
    PersuasionMessage,
    PersuasionStrategy,
    Tone,
)


class MemoryBackend(Protocol):
    """名前空間付きの単純な key-value ストア抽象。"""

    def put(self, namespace: str, key: str, value: dict) -> None: ...
    def get(self, namespace: str, key: str) -> dict | None: ...
    def query(self, namespace: str) -> list[dict]: ...


class InMemoryBackend:
    """テスト/ローカル用のインメモリ実装。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict]] = {}

    def put(self, namespace: str, key: str, value: dict) -> None:
        self._store.setdefault(namespace, {})[key] = value

    def get(self, namespace: str, key: str) -> dict | None:
        return self._store.get(namespace, {}).get(key)

    def query(self, namespace: str) -> list[dict]:
        return list(self._store.get(namespace, {}).values())


class MemoryClient:
    """記憶への読み書き。直接 backend に触れず必ず本クラス経由で操作する。"""

    def __init__(self, backend: MemoryBackend | None = None) -> None:
        self._backend: MemoryBackend = backend or InMemoryBackend()

    # --- vitals ---
    def save_vital(self, record: DailyVitalRecord) -> None:
        record.recompute_missing()
        self._backend.put("vitals", record.date, record.to_dict())

    def get_vitals(self, since: str, until: str) -> list[DailyVitalRecord]:
        rows = [
            r for r in self._backend.query("vitals") if since <= r["date"] <= until
        ]
        rows.sort(key=lambda r: r["date"])
        return [DailyVitalRecord(**r) for r in rows]

    # --- analysis ---
    def save_analysis(self, result: AnalysisResult) -> None:
        self._backend.put("analysis", result.date, result.__dict__)

    # --- strategy ---
    def get_current_strategy(self) -> PersuasionStrategy:
        row = self._backend.get("strategy", "current")
        if row is None:
            return PersuasionStrategy(
                version=1,
                system_prompt="",
                rationale="初期戦略",
                guardrail_passed=True,
            )
        return PersuasionStrategy(**row)

    def save_strategy(self, strategy: PersuasionStrategy) -> None:
        # FR-010 不変条件: 検証通過版のみ保存
        if not strategy.guardrail_passed:
            raise ValueError(
                "guardrail_passed=False の戦略は保存できません（FR-010 不変条件）"
            )
        self._backend.put("strategy", "current", strategy.__dict__)
        self._backend.put("strategy", f"v{strategy.version}", strategy.__dict__)

    # --- messages ---
    def save_message(self, msg: PersuasionMessage) -> None:
        payload = {**msg.__dict__, "tone": msg.tone.value,
                   "guardrail_result": msg.guardrail_result.value}
        self._backend.put("messages", f"{msg.date}", payload)

    def get_recent_messages(self, limit: int) -> list[PersuasionMessage]:
        rows = sorted(
            self._backend.query("messages"), key=lambda r: r["date"], reverse=True
        )[:limit]
        result: list[PersuasionMessage] = []
        for r in rows:
            data = dict(r)
            data["tone"] = Tone(data.get("tone", "neutral"))
            data["guardrail_result"] = GuardrailResult(data.get("guardrail_result", "pass"))
            result.append(PersuasionMessage(**data))
        return result
