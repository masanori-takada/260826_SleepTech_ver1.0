"""会話からの自己申告抽出（T206 / FR-105）。

固定キーワードパース（既存 parse_ramen_reply）を優先し、判別不能(None)のときのみ
LLM 分類で補完する（併用方針）。判別不能は None。
"""

from __future__ import annotations

import re
from datetime import date as _date, timedelta as _td

from night_ramen.agents.prompts import ModelInvoker
from night_ramen.collectors.self_report import parse_ramen_reply, parse_report_date

_CLASSIFY_SYSTEM = (
    "あなたは分類器です。ユーザーの発話が『昨夜（直近の夜）に夜食のラーメンを食べたか』について"
    "何を示しているかを判定し、yes（食べた）/ no（食べなかった・我慢した）/ unknown のいずれか1語だけを"
    "小文字で出力してください。ラーメンの話題でない、または判断できない場合は unknown。"
)

# 申告対象日の抽出（過去日付の記録漏れ補完）。日付が明示/相対で書かれていない発話のみ LLM に委ねる。
_DATE_SYSTEM = (
    "あなたは日付抽出器です。ユーザーの発話が『いつの夜の夜食ラーメンの話か』を判定します。"
    "今日の日付を基準に、対象日を YYYY-MM-DD の形式で1つだけ出力してください。"
    "発話に日付やいつの話かの手掛かりが無ければ今日の日付をそのまま出力します。"
    "余計な語は一切付けず、日付のみを出力してください。"
)
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def extract_ramen_report(
    text: str, invoker: ModelInvoker | None = None, model_id: str = ""
) -> bool | None:
    """夜食ラーメン有無を bool で返す。判別不能は None（FR-105）。

    1) 固定パース parse_ramen_reply（高速・決定的）。
    2) None かつ invoker があれば LLM 分類で補完。
    """
    fixed = parse_ramen_reply(text)
    if fixed is not None:
        return fixed
    if invoker is None or not model_id:
        return None
    try:
        out = invoker(model_id, f"発話: {text}", _CLASSIFY_SYSTEM).strip().lower()
    except Exception:  # noqa: BLE001 — 補完失敗時は記録しない
        return None
    if out.startswith("yes"):
        return True
    if out.startswith("no"):
        return False
    return None


def _clamp_report_date(candidate: str, today: str, max_age_days: int = 365) -> str:
    """対象日の妥当性を担保する。未来 or 保持期間より古い場合は today に丸める。"""
    try:
        base = _date.fromisoformat(today)
        cand = _date.fromisoformat(candidate)
    except ValueError:
        return today
    if cand > base or cand < base - _td(days=max_age_days):
        return today
    return cand.isoformat()


def resolve_report_date(
    text: str, today: str, invoker: ModelInvoker | None = None, model_id: str = ""
) -> str:
    """申告対象日(ISO)を解決する。明示/相対の正規表現を優先し、無ければ LLM、最後は today。

    1) parse_report_date（決定的・高速）。
    2) None かつ invoker があれば LLM で抽出（曖昧な自然語に対応）。
    3) どちらも不可なら today。
    いずれも未来・超過去は today に丸める（_clamp_report_date / FR-016 保持期間と整合）。
    """
    fixed = parse_report_date(text, today)
    if fixed is not None:
        return _clamp_report_date(fixed, today)
    if invoker is None or not model_id:
        return today
    try:
        out = invoker(model_id, f"今日は {today}。発話: {text}", _DATE_SYSTEM).strip()
    except Exception:  # noqa: BLE001 — 抽出失敗時は today に倒す
        return today
    m = _ISO_RE.search(out)
    if not m:
        return today
    return _clamp_report_date(m.group(0), today)
