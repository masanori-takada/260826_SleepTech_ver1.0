"""モデル呼び出し基盤とシステムプロンプト（T013 / FR-004,007 / 原則V）。

Bedrock Runtime(Converse API) 呼び出しを `ModelInvoker` プロトコルに抽象化し、
テストではフェイクを注入できるようにする。本番は boto3 を用いる。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol

# 説得生成のベースとなるシステムプロンプト（戦略未確立時の初期値）
BASE_PERSUASION_PROMPT = (
    "あなたはユーザーが深夜の夜食ラーメンを断つのを支える、思いやりのあるコーチです。"
    "ユーザーのバイタルデータ（睡眠・HRV・翌日の活動量など）の事実に基づいて、"
    "今夜ラーメンを我慢したくなるような、短く具体的で前向きなメッセージを書いてください。"
    "人格否定・罵倒・差別的表現は決して用いず、相手を追い詰めず、希望を持たせること。"
)

# 分析エージェントのシステムプロンプト
ANALYSIS_PROMPT = (
    "あなたは健康データアナリストです。提供されたバイタルデータの時系列から、"
    "夜食ラーメンを食べた日と食べなかった日の差を比較し、睡眠品質・自律神経(HRV)・"
    "代謝・呼吸・翌日の活動量への悪影響を簡潔に推論・数値化してください。"
    "断定しすぎず、データが乏しい場合はその旨を述べること。"
)


class ModelInvoker(Protocol):
    """model_id とプロンプトを受け取り、生成テキストを返す。"""

    def __call__(self, model_id: str, prompt: str, system: str = "") -> str: ...


def boto3_invoker(region: str) -> ModelInvoker:
    """本番用: Bedrock Runtime Converse API を呼ぶ ModelInvoker を生成。"""
    import boto3  # 遅延 import（テスト時は不要）

    client = boto3.client("bedrock-runtime", region_name=region)

    def _invoke(model_id: str, prompt: str, system: str = "") -> str:
        kwargs: dict = {
            "modelId": model_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 1024, "temperature": 0.7},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        resp = client.converse(**kwargs)
        return resp["output"]["message"]["content"][0]["text"]

    return _invoke


def make_fake_invoker(responder: Callable[[str, str, str], str]) -> ModelInvoker:
    """テスト用: (model_id, prompt, system) -> text のフェイク。"""

    def _invoke(model_id: str, prompt: str, system: str = "") -> str:
        return responder(model_id, prompt, system)

    return _invoke


def vitals_to_prompt_payload(records: list) -> str:
    """バイタルレコード群を分析用の JSON 文字列に整形。"""
    return json.dumps(
        [r.to_dict() if hasattr(r, "to_dict") else r for r in records],
        ensure_ascii=False,
        default=str,
    )
