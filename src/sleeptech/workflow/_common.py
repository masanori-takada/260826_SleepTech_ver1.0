"""夜フロー・朝フローで共通の送信ヘルパー（Retry-Key 生成と push 呼び出し）。

night.py / morning.py で完全に重複していたロジックをここへ集約する。
"""

from __future__ import annotations

import inspect
import uuid

# LINE Messaging API の X-Line-Retry-Key は「UUID (version 4) の文字列」を要求する
# （I3対応）。一方でこのアプリは同じ <date>/<slot> に対して常に同じキーを返す
# 決定論性が必要（Lambda の再試行で二重送信を防ぐため）。
# uuid5(NAMESPACE_URL, ...) は決定論的だが version=5 の形状になり、LINE 側で
# バージョン検証が行われると全 push が 400 になる実害が最大の欠陥だった。
# 対策: uuid5 が生成した16バイトはそのまま流用し、version ニブルと variant
# ビットだけを v4 の形状に立て直す。入力バイト列は変えないため決定論性は保たれ、
# 同時に `uuid.UUID(bytes=..., version=4)` の標準ライブラリ処理で v4 形状になる。
def retry_key(date: str, slot: str) -> str:
    """<date>/<slot> から決定論的な UUID v4 形状の文字列を作る。"""
    seed = uuid.uuid5(uuid.NAMESPACE_URL, f"sleeptech:v2:{date}:{slot}")
    return str(uuid.UUID(bytes=seed.bytes, version=4))


def push(deps, message: dict, key: str) -> None:
    """push_client.push を呼ぶ。retry_key 引数の有無はシグネチャで判定する。"""
    if "retry_key" in inspect.signature(deps.push_client.push).parameters:
        deps.push_client.push(message, retry_key=key)
    else:
        deps.push_client.push(message)


def save_message(
    deps,
    date: str,
    slot: str,
    body: str,
    guardrail_passed: bool,
    regen_count: int,
    delivered: bool,
    log,
) -> None:
    """仕様 第9節 MSG#NIGHT / MSG#MORNING（I5対応）。

    push は既に成功済みなので、ここで失敗しても伝播させず記録してログに残すだけ
    にする（save_prediction と同じ方針。送信済みなのに例外で再送されると
    二重送信になるため）。
    """
    from sleeptech.memory.schemas import Message

    try:
        deps.memory.save_message(
            Message(
                date=date,
                slot=slot,
                body=body,
                guardrail_passed=guardrail_passed,
                regen_count=regen_count,
                delivered=delivered,
            )
        )
    except Exception as exc:  # noqa: BLE001 — 送信成功後の失敗は伝播させない
        log.log("save_message_failed", error=str(exc))
