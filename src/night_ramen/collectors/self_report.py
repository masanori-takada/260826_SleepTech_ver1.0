"""夜食ラーメン自己申告の取り込み（T023 / FR-002）。

申告は外部入力（LINE 応答や簡易フォーム）から得る想定。未申告は None。
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


def get_ramen_self_report(date: str, source: SelfReportSource) -> bool | None:
    """夜食ラーメン有無の自己申告（未申告=None, FR-002）。"""
    return source.get(date)
