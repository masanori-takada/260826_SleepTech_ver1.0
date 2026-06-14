"""夜食ラーメン自己申告の取り込み（T023 / FR-002）。

申告は LINE 返信（Webhook 経由）から得て DynamoDB に保存する想定。未申告は None。
保存先: NightRamenTable PK="REPORT", SK="<date>", 属性 ramen(bool)。
"""

from __future__ import annotations

from typing import Protocol


class SelfReportSource(Protocol):
    def get(self, date: str) -> bool | None: ...


class StaticSelfReportSource:
    """テスト/簡易用: 事前に与えた辞書から返す。"""

    def __init__(self, reports: dict[str, bool] | None = None) -> None:
        self._reports = reports or {}

    def get(self, date: str) -> bool | None:
        return self._reports.get(date)


class DynamoSelfReportSource:
    """DynamoDB に保存された自己申告を読み書きする（FR-002）。"""

    def __init__(self, table_name: str, region: str) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get(self, date: str) -> bool | None:
        item = self._table.get_item(Key={"PK": "REPORT", "SK": date}).get("Item")
        if not item or "ramen" not in item:
            return None
        return bool(item["ramen"])

    def put(self, date: str, ramen: bool) -> None:
        self._table.put_item(Item={"PK": "REPORT", "SK": date, "ramen": ramen})


def get_ramen_self_report(date: str, source: SelfReportSource) -> bool | None:
    """夜食ラーメン有無の自己申告（未申告=None, FR-002）。"""
    return source.get(date)


# 返信テキスト → bool 解釈（Webhook で使用）
_YES_WORDS = ("食べた", "たべた", "食べちゃった", "はい", "yes", "あり", "1")
_NO_WORDS = ("食べてない", "食べなかった", "我慢", "がまん", "いいえ", "no", "なし", "0")


def parse_ramen_reply(text: str) -> bool | None:
    """ユーザー返信を ramen 有無に解釈する。判定できなければ None。

    否定（食べてない）を先に判定する（「食べてない」は「食べ」を含むため）。
    """
    t = text.strip().lower()
    if any(w in t for w in _NO_WORDS):
        return False
    if any(w in t for w in _YES_WORDS):
        return True
    return None


# 起床トリガー語（Feature 003）。朝8時の定時収集を待たず当日収集を起こす。
# 自己申告語（_YES_WORDS/_NO_WORDS）とは重複させない。
_WAKEUP_WORDS = (
    "おはよう",
    "おは",
    "起きた",
    "おきた",
    "データ更新",
    "更新",
    "同期",
    "計測",
)


def is_wakeup_trigger(text: str) -> bool:
    """起床トリガー語かどうか。当日の Google Health 収集を起こすために使う（FR-003）。"""
    t = text.strip().lower()
    return any(w in t for w in _WAKEUP_WORDS)
