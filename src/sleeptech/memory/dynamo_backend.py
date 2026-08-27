"""日付範囲クエリに対応した DynamoDB バックエンド。

PK はエンティティ種別、SK は日付（または ``META``）とする。値は JSON
文字列として保存し、DynamoDB の Decimal と float の相互変換問題を避ける。
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

DEFAULT_VITAL_TTL_DAYS = 365

# M4対応: boto3 既定の connect/read タイムアウト（各60秒）は60秒Lambdaに対して
# 未拘束なので短く切る。DynamoDBはリトライ前提で使うため max_attempts も絞る。
_BOTO_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 2})


class DynamoBackend:
    """DynamoDB resource の Table を再利用する汎用バックエンド。"""

    def __init__(
        self, table_name: str, region: str, vital_ttl_days: int = DEFAULT_VITAL_TTL_DAYS
    ) -> None:
        self._table = boto3.resource(
            "dynamodb", region_name=region, config=_BOTO_CONFIG
        ).Table(table_name)
        self._vital_ttl_days = vital_ttl_days

    @property
    def vital_ttl_days(self) -> int:
        """Vital に適用する保持日数。ほかのエンティティには適用しない。"""
        return self._vital_ttl_days

    def _item(self, pk: str, sk: str, value: dict) -> dict[str, Any]:
        """DynamoDB に渡す native Python 型の項目を作る。

        ``status`` / ``lease_until`` は claim/complete の ConditionExpression が
        トップレベル属性として参照するため、``json`` 本体に加えてトップレベルにも
        同じ値をミラーする。``get()`` は引き続き ``json`` を読むので互換性は保たれる。

        M3対応: このミラーは冪等エンティティ（pk="IDEM#..."）の claim/complete
        条件式が読む属性としてのみ必要。以前は value に "status" キーがあれば
        無条件でミラーしていたため、Prediction.status（"ok"/"too_late"/…）まで
        PREDICT#* 項目のトップレベルに漏れていた。pk が "IDEM#" 始まりのときに
        限定する。
        """
        item: dict[str, Any] = {
            "pk": pk,
            "sk": sk,
            "json": json.dumps(value, ensure_ascii=False, default=str),
        }
        if pk.startswith("IDEM#") and "status" in value:
            item["status"] = value["status"]
        if "lease_until" in value:
            # DynamoDB の Table リソースは float を受け付けないため Decimal に変換する
            item["lease_until"] = Decimal(str(value["lease_until"]))
        if pk == "VITAL":
            item["ttl"] = int(time.time()) + self._vital_ttl_days * 86400
        return item

    def put(self, pk: str, sk: str, value: dict) -> None:
        """値を保存する。TTL は Vital エンティティにだけ付与する。"""
        item = self._item(pk, sk, value)
        self._table.put_item(Item=item)

    def put_conditional(self, pk: str, sk: str, value: dict) -> bool:
        """未登録キーだけを原子的に保存し、条件不成立なら False を返す。"""
        item = self._item(pk, sk, value)
        try:
            self._table.put_item(Item=item, ConditionExpression=Attr("pk").not_exists())
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def put_json_if_match(
        self, pk: str, sk: str, value: dict, expected_json: str | None
    ) -> bool:
        """期待したJSONのままなら保存する楽観ロック付き書込。"""
        item = self._item(pk, sk, value)
        condition = (
            Attr("pk").not_exists()
            if expected_json is None
            else Attr("json").eq(expected_json)
        )
        try:
            self._table.put_item(Item=item, ConditionExpression=condition)
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def _get_json(self, pk: str, sk: str) -> tuple[dict | None, str | None]:
        item = self._table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        if not item or "json" not in item:
            return None, None
        return json.loads(item["json"]), item["json"]

    def merge_json(
        self, pk: str, sk: str, merge, max_retries: int = 3
    ) -> dict:
        """再読込・再マージを伴う有限回のJSON楽観ロック更新。"""
        for _ in range(max_retries + 1):
            current, expected_json = self._get_json(pk, sk)
            merged = merge(current)
            if self.put_json_if_match(pk, sk, merged, expected_json):
                return merged
        raise RuntimeError("DynamoDB JSON更新が競合上限に達しました")

    def claim(self, pk: str, sk: str, value: dict, now: float, lease_seconds: int) -> bool:
        item = self._item(pk, sk, {**value, "status": "PENDING", "lease_until": now + lease_seconds})
        # ConditionExpression の比較値も DynamoDB Table リソースでは Decimal が必要
        now_decimal = Decimal(str(now))
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression=Attr("pk").not_exists() | Attr("lease_until").lt(now_decimal),
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def complete(self, pk: str, sk: str) -> bool:
        """claim 済み（PENDING）のロックを SENT に遷移させる。

        トップレベル属性だけでなく ``json`` 本体も同時に SENT へ同期する
        （``get()``/``is_notified`` は ``json`` を読むため）。idem エンティティは
        pk="IDEM#{slot}", sk=date という形が既知なので、読み直さずに再構成する。
        """
        slot = pk.split("#", 1)[1] if pk.startswith("IDEM#") else pk
        value = {"date": sk, "slot": slot, "status": "SENT"}
        new_json = json.dumps(value, ensure_ascii=False, default=str)
        try:
            self._table.update_item(
                Key={"pk": pk, "sk": sk},
                UpdateExpression="SET #status = :sent, #json = :json REMOVE lease_until",
                ExpressionAttributeNames={"#status": "status", "#json": "json"},
                ExpressionAttributeValues={":sent": "SENT", ":json": new_json},
                ConditionExpression=Attr("status").eq("PENDING"),
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return self.put_conditional(pk, sk, value)
        return True

    def get(self, pk: str, sk: str) -> dict | None:
        """指定キーの JSON 値を返す。"""
        item = self._table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return json.loads(item["json"]) if item and "json" in item else None

    def get_strong(self, pk: str, sk: str) -> dict | None:
        """claim競合判定用に強整合性readを行う。"""
        item = self._table.get_item(
            Key={"pk": pk, "sk": sk}, ConsistentRead=True
        ).get("Item")
        return json.loads(item["json"]) if item and "json" in item else None

    def query_range(self, pk: str, start_sk: str, end_sk: str) -> list[dict]:
        """PK 固定かつ SK が範囲内の値を昇順で全件返す。"""
        return self._query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").between(start_sk, end_sk)
        )

    def query_desc(self, pk: str, limit: int) -> list[dict]:
        """PK 固定の値を SK 降順で最大 ``limit`` 件返す。"""
        if limit <= 0:
            return []
        items: list[dict] = []
        start_key: dict[str, Any] | None = None
        while len(items) < limit:
            query_kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("pk").eq(pk),
                "ScanIndexForward": False,
                "Limit": limit - len(items),
            }
            if start_key is not None:
                query_kwargs["ExclusiveStartKey"] = start_key
            response = self._table.query(**query_kwargs)
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                break
        return [
            json.loads(item["json"])
            for item in items[:limit]
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
