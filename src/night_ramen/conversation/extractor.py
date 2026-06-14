"""会話からの自己申告抽出（T206 / FR-105）。

固定キーワードパース（既存 parse_ramen_reply）を優先し、判別不能(None)のときのみ
LLM 分類で補完する（併用方針）。判別不能は None。
"""

from __future__ import annotations

from night_ramen.agents.prompts import ModelInvoker
from night_ramen.collectors.self_report import parse_ramen_reply

_CLASSIFY_SYSTEM = (
    "あなたは分類器です。ユーザーの発話が『昨夜（直近の夜）に夜食のラーメンを食べたか』について"
    "何を示しているかを判定し、yes（食べた）/ no（食べなかった・我慢した）/ unknown のいずれか1語だけを"
    "小文字で出力してください。ラーメンの話題でない、または判断できない場合は unknown。"
)


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
