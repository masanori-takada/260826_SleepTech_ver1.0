"""夜食ラーメン自己申告の取り込み（T023 / FR-002）。

申告は LINE 返信（Webhook 経由）から得て DynamoDB に保存する想定。未申告は None。
保存先: NightRamenTable PK="REPORT", SK="<date>", 属性 ramen(bool)。
"""

from __future__ import annotations

import re
from datetime import date as _date, timedelta as _td
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

# 申告ではなく質問・指摘・抗議を示す語。これらを含む場合は None を返し LLM 判定に委ねる。
# 「食べたことになってる？」のような確認メッセージで誤検知しないために必要。
_NON_REPORT_WORDS = ("ことになってる", "ことになった", "になってる", "になってた",
                     "なんで", "なぜ", "どういうこと", "おかしい")


def parse_ramen_reply(text: str) -> bool | None:
    """ユーザー返信を ramen 有無に解釈する。判定できなければ None。

    否定（食べてない）を先に判定する（「食べてない」は「食べ」を含むため）。
    質問・指摘文脈（「食べたことになってる？」等）は None を返し LLM に委ねる。
    """
    t = text.strip().lower()
    if any(w in t for w in _NON_REPORT_WORDS):
        return None
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


# 過去日付の申告（記録漏れ補完）。発話から「いつの夜の分か」を決定的に抽出する。
# 相対語（N日前を優先）→明示(M/D, M月D日)→D日のみ、の順で判定。
_REL_WORDS = {
    "おととい": 2,
    "一昨日": 2,
    "昨夜": 1,
    "昨日": 1,
    "きのう": 1,
    "ゆうべ": 1,
    "今日": 0,
    "本日": 0,
    "きょう": 0,
}
_NDAYS_RE = re.compile(r"(\d+)\s*日前")
_MD_RE = re.compile(r"(\d{1,2})\s*[/月]\s*(\d{1,2})")
_DONLY_RE = re.compile(r"(\d{1,2})\s*日(?!前)")


def _resolve_md(base: _date, mo: int, d: int) -> str | None:
    """月日(M/D)を ISO 日付に。today の年で解釈し、未来なら前年（直近の過去）。"""
    try:
        cand = _date(base.year, mo, d)
    except ValueError:
        return None
    if cand > base:
        try:
            cand = _date(base.year - 1, mo, d)
        except ValueError:
            return None
    return cand.isoformat()


def _resolve_d(base: _date, d: int) -> str | None:
    """日(D)のみを ISO 日付に。today の月で解釈し、未来なら前月。"""
    try:
        cand = _date(base.year, base.month, d)
    except ValueError:
        return None
    if cand > base:
        y, mo = (base.year, base.month - 1) if base.month > 1 else (base.year - 1, 12)
        try:
            cand = _date(y, mo, d)
        except ValueError:
            return None
    return cand.isoformat()


def parse_report_date(text: str, today: str) -> str | None:
    """発話から申告対象日(ISO)を決定的に抽出する。見つからなければ None。

    対応: 相対（今日/昨日/おととい/N日前）・明示（M/D・M月D日・D日）。
    明示日付は today を基準に直近の過去として解決する。
    """
    t = text.strip()
    base = _date.fromisoformat(today)
    # 相対: 「N日前」を最優先（「3日前」など）
    m = _NDAYS_RE.search(t)
    if m:
        return (base - _td(days=int(m.group(1)))).isoformat()
    # 相対語（おとといを昨日より先に評価）
    for word, off in _REL_WORDS.items():
        if word in t:
            return (base - _td(days=off)).isoformat()
    # 明示 M/D・M月D日
    m = _MD_RE.search(t)
    if m:
        return _resolve_md(base, int(m.group(1)), int(m.group(2)))
    # 明示 D日 のみ
    m = _DONLY_RE.search(t)
    if m:
        return _resolve_d(base, int(m.group(1)))
    return None
