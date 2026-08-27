"""エンティティ単位の記憶読み書きクライアント。"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta
import time

from sleeptech.memory.schemas import DailyVital, Feedback, Message, Prediction

PK_VITAL = "VITAL"
PK_FEEDBACK = "FEEDBACK"
PK_CONFIG = "CONFIG"
SK_META = "META"


class NotificationClaimBusy(RuntimeError):
    """別実行が通知送信中のleaseを保持している。"""


def _pk_prediction(slot: str) -> str:
    return f"PREDICT#{slot}"


def _pk_message(slot: str) -> str:
    return f"MSG#{slot}"


def _pk_idem(slot: str) -> str:
    return f"IDEM#{slot}"


class InMemoryBackend:
    """テスト用の DynamoBackend 互換実装。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}

    def put(self, pk: str, sk: str, value: dict) -> None:
        self._items[(pk, sk)] = value

    def put_conditional(self, pk: str, sk: str, value: dict) -> bool:
        if (pk, sk) in self._items:
            return False
        self._items[(pk, sk)] = value
        return True

    def claim(self, pk: str, sk: str, value: dict, now: float, lease_seconds: int) -> bool:
        current = self._items.get((pk, sk))
        if current and current.get("status") == "SENT":
            return False
        if current and current.get("lease_until", 0) > now:
            return False
        self._items[(pk, sk)] = {**value, "status": "PENDING", "lease_until": now + lease_seconds}
        return True

    def complete(self, pk: str, sk: str) -> bool:
        item = self._items.get((pk, sk))
        if not item:
            # DynamoBackend.complete() と同様、未 claim の場合は新規 SENT 作成にフォールバックする
            return self.put_conditional(pk, sk, {"status": "SENT"})
        if item.get("status") == "SENT":
            return False
        item = {**item, "status": "SENT"}
        item.pop("lease_until", None)
        self._items[(pk, sk)] = item
        return True

    def get(self, pk: str, sk: str) -> dict | None:
        return self._items.get((pk, sk))

    def get_strong(self, pk: str, sk: str) -> dict | None:
        """DynamoBackend互換のclaim判定用read。インメモリでは通常readと同じ。"""
        return self.get(pk, sk)

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

    def save_vital(self, vital: DailyVital) -> None:
        self._backend.put(PK_VITAL, vital.date, vital.to_dict())

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

    def save_message(self, msg: Message) -> None:
        """仕様 第9節 MSG#NIGHT / MSG#MORNING。送信した本文と検証結果を記録する。"""
        self._backend.put(_pk_message(msg.slot), msg.date, msg.to_dict())

    def get_message(self, date: str, slot: str) -> Message | None:
        raw = self._backend.get(_pk_message(slot), date)
        return Message.from_dict(raw) if raw else None

    def save_feedback(self, fb: Feedback) -> None:
        self._backend.put(PK_FEEDBACK, fb.date, fb.to_dict())

    def get_feedback(self, date: str) -> Feedback | None:
        raw = self._backend.get(PK_FEEDBACK, date)
        return Feedback.from_dict(raw) if raw else None

    def get_feedback_desc(self, limit: int) -> list[Feedback]:
        """フィードバック履歴を新しい順で指定件数だけ返す。"""
        return [
            Feedback.from_dict(raw)
            for raw in self._backend.query_desc(PK_FEEDBACK, limit)
        ]

    def is_notified(self, date: str, slot: str) -> bool:
        item = self._backend.get(_pk_idem(slot), date)
        return bool(item and item.get("status", "SENT") == "SENT")

    def claim_notification(self, date: str, slot: str, now: float | None = None,
                           lease_seconds: int = 90) -> bool:
        now = time.time() if now is None else now
        pk, sk = _pk_idem(slot), date
        claim = getattr(self._backend, "claim", None)
        if callable(claim):
            claimed = claim(pk, sk, {"date": date, "slot": slot}, now, lease_seconds)
        else:
            claimed = self._backend.put_conditional(
                pk, sk, {"date": date, "slot": slot, "status": "PENDING",
                         "lease_until": now + lease_seconds}
            )
        if claimed:
            return True

        get_strong = getattr(self._backend, "get_strong", None)
        current = (get_strong(pk, sk) if callable(get_strong)
                   else self._backend.get(pk, sk))
        if current and current.get("status") == "PENDING" and current.get("lease_until", 0) > now:
            raise NotificationClaimBusy(f"notification claim is leased: {slot}/{date}")
        return False

    def mark_notified(self, date: str, slot: str) -> bool:
        """バックエンドが提供するインタフェースで分岐する（クラス名文字列には依存しない）。"""
        complete = getattr(self._backend, "complete", None)
        if callable(complete):
            return complete(_pk_idem(slot), date)
        # complete() を持たない最小バックエンド向けのフォールバック
        item = self._backend.get(_pk_idem(slot), date)
        if not item:
            return self._backend.put_conditional(
                _pk_idem(slot), date, {"date": date, "slot": slot, "status": "SENT"}
            )
        if item.get("status") == "SENT":
            return False
        item = {**item, "status": "SENT"}
        item.pop("lease_until", None)
        self._backend.put(_pk_idem(slot), date, item)
        return True

    def get_config(self) -> dict:
        return self._backend.get(PK_CONFIG, SK_META) or {}

    def save_config(self, cfg: dict) -> None:
        self._backend.put(PK_CONFIG, SK_META, cfg)
