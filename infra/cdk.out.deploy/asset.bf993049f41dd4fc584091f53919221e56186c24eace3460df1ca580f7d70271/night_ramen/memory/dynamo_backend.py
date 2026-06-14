"""DynamoDB バックエンドの記憶ストア（FR-002,013 / 原則III,IV）。

朝Lambda（収集）と夜Lambda（通知）が状態を共有するための永続層。
MemoryBackend プロトコル（put/get/query）を NightRamenTable 上に実装する。

物理キー:
  PK = "MEM#<namespace>", SK = "<key>", 属性 json = 値の JSON 文字列, ttl = 失効(任意)

値は JSON 文字列で保存し、DynamoDB の数値型(Decimal)変換問題を回避する。
"""

from __future__ import annotations

import json
import time
from typing import Any


class DynamoMemoryBackend:
    """NightRamenTable を用いた MemoryBackend 実装。"""

    def __init__(self, table_name: str, region: str, ttl_days: int | None = 365) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self._ttl_days = ttl_days

    def put(self, namespace: str, key: str, value: dict) -> None:
        item: dict[str, Any] = {
            "PK": f"MEM#{namespace}",
            "SK": key,
            "json": json.dumps(value, ensure_ascii=False, default=str),
        }
        if self._ttl_days:
            item["ttl"] = int(time.time()) + self._ttl_days * 86400
        self._table.put_item(Item=item)

    def get(self, namespace: str, key: str) -> dict | None:
        resp = self._table.get_item(Key={"PK": f"MEM#{namespace}", "SK": key})
        item = resp.get("Item")
        return json.loads(item["json"]) if item and "json" in item else None

    def query(self, namespace: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        items: list[dict] = []
        kwargs = {"KeyConditionExpression": Key("PK").eq(f"MEM#{namespace}")}
        while True:
            resp = self._table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return [json.loads(it["json"]) for it in items if "json" in it]
