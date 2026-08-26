"""日付範囲クエリに対応した DynamoDB バックエンド。

PK はエンティティ種別、SK は日付（または ``META``）とする。値は JSON
文字列として保存し、DynamoDB の Decimal と float の相互変換問題を避ける。
"""

from __future__ import annotations

import json
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


class DynamoBackend:
    """DynamoDB resource の Table を再利用する汎用バックエンド。"""

    def __init__(self, table_name: str, region: str, ttl_days: int | None = None) -> None:
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self._ttl_days = ttl_days

    def put(self, pk: str, sk: str, value: dict) -> None:
        """値を JSON 文字列でラップして保存する。"""
        item: dict[str, Any] = {
            "pk": pk,
            "sk": sk,
            "json": json.dumps(value, ensure_ascii=False, default=str),
        }
        if self._ttl_days is not None:
            item["ttl"] = int(time.time()) + self._ttl_days * 86400
        self._table.put_item(Item=item)

    def get(self, pk: str, sk: str) -> dict | None:
        """指定キーの JSON 値を返す。"""
        item = self._table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return json.loads(item["json"]) if item and "json" in item else None

    def query_range(self, pk: str, start_sk: str, end_sk: str) -> list[dict]:
        """PK 固定かつ SK が範囲内の値を昇順で全件返す。"""
        return self._query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").between(start_sk, end_sk)
        )

    def query_desc(self, pk: str, limit: int) -> list[dict]:
        """PK 固定の値を SK 降順で最大 ``limit`` 件返す。"""
        response = self._table.query(
            KeyConditionExpression=Key("pk").eq(pk),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [
            json.loads(item["json"])
            for item in response.get("Items", [])
            if "json" in item
        ]

    def _query(self, **kwargs: Any) -> list[dict]:
        """1MB 上限による Query のページを最後まで取得する。"""
        items: list[dict] = []
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return [
            json.loads(item["json"])
            for item in items
            if "json" in item
        ]
