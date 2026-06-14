"""agentcore Memory リソースを1回だけ作成する（T201 / 002）。

semantic(事実)・userPreference(嗜好)・summary(要約) の3戦略付きで作成し、ACTIVE 化を待って
MEMORY_ID を出力する。出力IDを CDK の環境変数 NRB_MEMORY_ID に設定する。

使用例:
    python scripts/create_memory.py --name NightRamenMemory --region us-east-1
"""

from __future__ import annotations

import argparse
import sys
import time

import boto3


def create_memory(name: str, region: str, event_expiry_days: int = 90) -> str:
    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)

    strategies = [
        {"semanticMemoryStrategy": {
            "name": "FactExtractor", "namespaces": ["/facts/{actorId}/"]}},
        {"userPreferenceMemoryStrategy": {
            "name": "PreferenceLearner", "namespaces": ["/preferences/{actorId}/"]}},
        {"summaryMemoryStrategy": {
            "name": "SessionSummarizer", "namespaces": ["/summaries/{actorId}/{sessionId}/"]}},
    ]

    # 既存の同名 Memory があれば再利用
    for m in _list_all_memories(ctrl):
        if m.get("name") == name:
            print(f"既存 Memory を再利用: {m['id']}")
            return m["id"]

    resp = ctrl.create_memory(
        name=name,
        description="Night Ramen Breakdown 会話・長期記憶（健康コーチ）",
        eventExpiryDuration=event_expiry_days,
        memoryStrategies=strategies,
    )
    memory = resp["memory"] if "memory" in resp else resp
    memory_id = memory["id"] if "id" in memory else memory["memoryId"]
    print(f"作成開始: {memory_id}（ACTIVE 化を待機…）")
    _wait_active(ctrl, memory_id)
    return memory_id


def _list_all_memories(ctrl) -> list[dict]:
    out, token = [], None
    while True:
        kwargs = {"nextToken": token} if token else {}
        resp = ctrl.list_memories(**kwargs)
        out.extend(resp.get("memories", []))
        token = resp.get("nextToken")
        if not token:
            return out


def _wait_active(ctrl, memory_id: str, timeout_s: int = 420) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = ctrl.get_memory(memoryId=memory_id)["memory"].get("status")
        print(f"  status={status}")
        if status == "ACTIVE":
            return
        if status in ("FAILED", "DELETING"):
            raise RuntimeError(f"Memory 作成失敗: status={status}")
        time.sleep(15)
    raise TimeoutError("ACTIVE 化がタイムアウトしました")


def main(argv=None) -> str:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="NightRamenMemory")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args(argv)
    memory_id = create_memory(args.name, args.region)
    print("")
    print(f"MEMORY_ID={memory_id}")
    print("→ CDK の Webhook Lambda 環境変数 NRB_MEMORY_ID に設定してください。")
    return memory_id


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"エラー: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
