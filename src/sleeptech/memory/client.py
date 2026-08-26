"""エンティティ単位の記憶読み書きクライアント。"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta

from sleeptech.memory.schemas import DailyVital, Feedback, Prediction

VITAL_TTL_DAYS = 365

PK_VITAL = "VITAL"
PK_FEEDBACK = "FEEDBACK"
PK_CONFIG = "CONFIG"
SK_META = "META"


def _pk_prediction(slot: str) -> str:
    return f"PREDICT#{slot}"


def _pk_idem(slot: str) -> str:
    return f"IDEM#{slot}"


class InMemoryBackend:
    """テスト用の DynamoBackend 互換実装。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}

    @property
    def vital_ttl_days(self) -> int:
        """DynamoDB 実装と同じ Vital 保持日数を返す。"""
        return VITAL_TTL_DAYS

    def put(self, pk: str, sk: str, value: dict, ttl_days: int | None = None) -> None:
        self._items[(pk, sk)] = value

    def put_conditional(
        self, pk: str, sk: str, value: dict, ttl_days: int | None = None
    ) -> bool:
        if (pk, sk) in self._items:
            return False
        self._items[(pk, sk)] = value
        return True

    def get(self, pk: str, sk: str) -> dict | None:
        return self._items.get((pk, sk))

    def query_range(self, pk: str, start_sk: str, end_sk: str) -> list[dict]:
        picked = [
            (sk, value)
            for (item_pk, sk), value in self._items.items()
            if item_pk == pk and start_sk <= sk <= end_sk
        ]
        return [value for _, value in sorted(picked, key=lambda entry: entry[0])]

    def query_desc(self, pk: str, limit: int) -> list[dict]:
        picked = [
            (sk, value) for (item_pk, sk), value in self._items.items() if item_pk == pk
        ]
        return [value for _, value in sorted(picked, key=lambda entry: entry[0], reverse=True)[:limit]]


class MemoryClient:
    """スキーマ型とバックエンドの間を変換するクライアント。"""

    def __init__(self, backend) -> None:
        self._backend = backend
        self._vital_ttl_days = getattr(backend, "vital_ttl_days", VITAL_TTL_DAYS)

    def save_vital(self, vital: DailyVital) -> None:
        self._backend.put(PK_VITAL, vital.date, vital.to_dict(), ttl_days=self._vital_ttl_days)

    def get_vital(self, date: str) -> DailyVital | None:
        raw = self._backend.get(PK_VITAL, date)
        return DailyVital.from_dict(raw) if raw else None

    def get_vitals_desc(self, until_date: str, days: int) -> list[DailyVital]:
        """指定日を含む直近 ``days`` 日を新しい順で返す。"""
        start = (date_cls.fromisoformat(until_date) - timedelta(days=days - 1)).isoformat()
        return [
            DailyVital.from_dict(raw)
            for raw in reversed(self._backend.query_range(PK_VITAL, start, until_date))
        ]

    def save_prediction(self, pred: Prediction) -> None:
        self._backend.put(_pk_prediction(pred.slot), pred.date, pred.to_dict())

    def get_prediction(self, date: str, slot: str) -> Prediction | None:
        raw = self._backend.get(_pk_prediction(slot), date)
        return Prediction.from_dict(raw) if raw else None

    def save_feedback(self, fb: Feedback) -> None:
        self._backend.put(PK_FEEDBACK, fb.date, fb.to_dict())

    def get_feedback(self, date: str) -> Feedback | None:
        raw = self._backend.get(PK_FEEDBACK, date)
        return Feedback.from_dict(raw) if raw else None

    def is_notified(self, date: str, slot: str) -> bool:
        return self._backend.get(_pk_idem(slot), date) is not None

    def mark_notified(self, date: str, slot: str) -> bool:
        return self._backend.put_conditional(
            _pk_idem(slot), date, {"date": date, "slot": slot}
        )

    def get_config(self) -> dict:
        return self._backend.get(PK_CONFIG, SK_META) or {}

    def save_config(self, cfg: dict) -> None:
        self._backend.put(PK_CONFIG, SK_META, cfg)
