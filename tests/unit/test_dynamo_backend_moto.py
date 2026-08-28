"""moto で実際の DynamoDB ConditionExpression 評価を伴う claim/complete のテスト。

Mock テーブルでは条件式が一切評価されず C1（lease が本番で永久ロックする不具合）を
検出できなかった。moto は本物の DynamoDB API 互換のインメモリ実装なので、
ConditionExpression の成否を実際に評価させて検証する。
"""

from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from sleeptech.memory.client import MemoryClient, NotificationClaimBusy
from sleeptech.memory.dynamo_backend import DynamoBackend

TABLE_NAME = "sleeptech-test"
REGION = "ap-northeast-1"


@pytest.fixture
def dynamo_table():
    """moto 上に本番と同じ pk/sk キースキーマのテーブルを作る。"""
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


@pytest.fixture
def backend(dynamo_table) -> DynamoBackend:
    return DynamoBackend(TABLE_NAME, REGION)


def test_claim_writes_status_and_lease_until_as_top_level_attributes(backend):
    """C1 再発防止: put_item が生成する Item に status/lease_until がトップレベルで含まれること。"""
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

    assert backend.claim("IDEM#NIGHT", "2026-08-26", {"date": "2026-08-26", "slot": "NIGHT"},
                          now=1000.0, lease_seconds=90) is True

    item = table.get_item(Key={"pk": "IDEM#NIGHT", "sk": "2026-08-26"})["Item"]
    assert item["status"] == "PENDING"
    assert item["lease_until"] == pytest.approx(1090.0)


def test_full_claim_send_failure_relock_and_complete_cycle(backend):
    """本番シナリオ: claim成功 → 送信失敗(complete なし) → lease失効前は再claim不可 →
    lease失効後は再claim可 → complete後は is_notified が True になる。"""
    memory = MemoryClient(backend)
    date, slot = "2026-08-26", "NIGHT"

    # 1. claim 成功
    assert memory.claim_notification(date, slot, now=1000.0, lease_seconds=90) is True

    # 2. 送信失敗を模して complete せずに終了（何もしない）

    # 3. lease 失効前の再 claim → False
    with pytest.raises(NotificationClaimBusy):
        memory.claim_notification(date, slot, now=1050.0, lease_seconds=90)

    # 4. lease 失効後の再 claim → True
    assert memory.claim_notification(date, slot, now=1100.0, lease_seconds=90) is True

    # 5. complete → True、以降 is_notified が True
    assert memory.mark_notified(date, slot) is True
    assert memory.is_notified(date, slot) is True

    # complete 後は再 claim できない（既に SENT）
    assert memory.claim_notification(date, slot, now=99999.0, lease_seconds=90) is False


def test_complete_without_prior_claim_falls_back_to_conditional_create(backend):
    """claim 未実行（item が存在しない）状態での complete は put_conditional にフォールバックし、
    新規に SENT レコードを作る（既存の意図された挙動。以降は再claim不可）。"""
    memory = MemoryClient(backend)
    assert memory.mark_notified("2026-08-26", "NIGHT") is True
    assert memory.is_notified("2026-08-26", "NIGHT") is True
    # 既に SENT のため、同じキーへの complete 再実行は失敗する
    assert memory.mark_notified("2026-08-26", "NIGHT") is False


def test_mark_notified_is_idempotent_after_completion(backend):
    memory = MemoryClient(backend)
    date, slot = "2026-08-26", "MORNING"
    assert memory.claim_notification(date, slot, now=0.0, lease_seconds=90) is True
    assert memory.mark_notified(date, slot) is True
    # 二重呼び出しは条件不成立で False になる（すでに SENT のため PENDING 条件を満たさない）
    assert memory.mark_notified(date, slot) is False
    assert memory.is_notified(date, slot) is True


def test_merge_json_retries_after_conflict_and_keeps_concurrent_field(backend, monkeypatch):
    backend.put("VITAL", "2026-08-01", {"date": "2026-08-01", "old": "keep"})
    original = backend.put_json_if_match
    calls = {"count": 0}

    def conflict_once(pk, sk, value, expected_json):
        if calls["count"] == 0:
            calls["count"] += 1
            backend.put("VITAL", "2026-08-01", {
                "date": "2026-08-01", "old": "keep", "other_process": "preserve"
            })
            return False
        return original(pk, sk, value, expected_json)

    monkeypatch.setattr(backend, "put_json_if_match", conflict_once)
    merged = backend.merge_json(
        "VITAL", "2026-08-01", lambda current: {**(current or {}), "backfill": 2}
    )

    assert merged["other_process"] == "preserve"
    assert merged["backfill"] == 2


def test_merge_json_creates_new_item_conditionally(backend):
    merged = backend.merge_json(
        "VITAL", "2026-08-02", lambda current: {"date": "2026-08-02", "value": 1}
    )

    assert merged == {"date": "2026-08-02", "value": 1}
    assert backend.get("VITAL", "2026-08-02")["value"] == 1


def test_resource_meta_client_transact_write_accepts_native_python_items(backend):
    """resource.meta.clientの実オブジェクトがnative Python型をmarshalすることを検証する。"""
    client = backend._table.meta.client
    expected = {
        "TransactItems": [
            {"Put": {"TableName": TABLE_NAME,
                      "Item": {"pk": "x", "sk": "y", "value": 1},
                      "ConditionExpression": "attribute_not_exists(pk)"}}
        ]
    }
    stubber = Stubber(client)
    stubber.add_response("transact_write_items", {}, expected)

    with stubber:
        client.transact_write_items(**expected)


def test_transient_transaction_cancellation_is_propagated(backend):
    client = backend._table.meta.client
    feedback = {"date": "2026-08-28", "score": 1}
    expected_transact = {
        "TransactItems": [
            {"Put": {"TableName": TABLE_NAME,
                      "Item": {"pk": "FEEDBACK", "sk": "2026-08-28",
                                "json": '{"date": "2026-08-28", "score": 1}'},
                          }},
            {"Put": {"TableName": TABLE_NAME,
                      "Item": {"pk": "IDEM#FEEDBACK", "sk": "event-transient",
                                "status": "PROCESSED",
                                "json": '{"event_id": "event-transient", "status": "PROCESSED"}'},
                      "ConditionExpression": "attribute_not_exists(pk)"}},
        ]
    }
    stubber = Stubber(client)
    stubber.add_client_error(
        "transact_write_items", "TransactionCanceledException",
        expected_params=expected_transact,
    )
    stubber.add_response("get_item", {}, {
        "TableName": TABLE_NAME,
        "Key": {"pk": "IDEM#FEEDBACK", "sk": "event-transient"},
        "ConsistentRead": True,
    })

    with stubber, pytest.raises(client.exceptions.TransactionCanceledException):
        backend.put_feedback_once(feedback, "event-transient")


def test_feedback_transaction_condition_collision_returns_false(backend):
    feedback = {"date": "2026-08-28", "score": 1}

    assert backend.put_feedback_once(feedback, "event-1") is True
    assert backend.put_feedback_once(feedback, "event-1") is False
    assert backend.put_feedback_once({"date": "2026-08-28", "score": 5}, "event-2") is True


def test_feedback_old_event_is_marker_only_and_cannot_overwrite_newer_feedback(backend):
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    newer = {"date": "2026-08-28", "score": 5, "answered_at": "2026-08-28T14:10:00Z"}
    older = {"date": "2026-08-28", "score": 1, "answered_at": "2026-08-28T14:00:00Z"}

    assert backend.put_feedback_once(newer, "event-new") is True
    assert backend.put_feedback_once(older, "event-old") is False
    assert backend.get("FEEDBACK", "2026-08-28")["score"] == 5
    assert backend.get("IDEM#FEEDBACK", "event-old")["status"] == "PROCESSED"
    assert table.get_item(Key={"pk": "FEEDBACK", "sk": "2026-08-28"})["Item"]["answered_at"] == newer["answered_at"]


def test_feedback_old_then_new_updates_and_same_event_is_noop(backend):
    old = {"date": "2026-08-29", "score": 1, "answered_at": "2026-08-29T14:00:00Z"}
    new = {"date": "2026-08-29", "score": 5, "answered_at": "2026-08-29T14:10:00Z"}

    assert backend.put_feedback_once(old, "event-old") is True
    assert backend.put_feedback_once(new, "event-new") is True
    assert backend.put_feedback_once({**new, "score": 2}, "event-new") is False
    assert backend.get("FEEDBACK", "2026-08-29")["score"] == 5


def test_feedback_newer_event_retries_after_transaction_conflict(backend):
    """競合取消後、強整合readで相手が古いままだと一度だけ再試行する。"""
    client = backend._table.meta.client
    incoming = {"date": "2026-08-30", "score": 5, "answered_at": "2026-08-30T14:10:00Z"}
    stubber = Stubber(client)
    empty = {"TableName": TABLE_NAME, "Key": {"pk": "FEEDBACK", "sk": "2026-08-30"}, "ConsistentRead": True}
    marker = {"TableName": TABLE_NAME, "Key": {"pk": "IDEM#FEEDBACK", "sk": "event-retry"}, "ConsistentRead": True}
    stubber.add_response("get_item", {}, empty)
    stubber.add_client_error("transact_write_items", "TransactionCanceledException")
    stubber.add_response("get_item", {}, marker)
    stubber.add_response("get_item", {}, empty)
    stubber.add_response("transact_write_items", {})
    with stubber:
        assert backend.put_feedback_once(incoming, "event-retry") is True


def test_feedback_mixed_timezone_uses_numeric_epoch_condition(backend):
    """旧JST文字列と新UTC文字列を実時刻順で比較する。"""
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    legacy = {"date": "2026-08-31", "score": 1,
              "answered_at": "2026-08-31T23:00:00+09:00"}
    newer = {"date": "2026-08-31", "score": 5,
             "answered_at": "2026-08-31T14:10:00.000Z"}
    # 実デプロイ済み旧項目を再現するため、epoch属性なしで直接投入する。
    table.put_item(Item={
        "pk": "FEEDBACK", "sk": "2026-08-31",
        "json": '{"date": "2026-08-31", "score": 1, "answered_at": "2026-08-31T23:00:00+09:00"}',
        "answered_at": legacy["answered_at"],
    })
    before = table.get_item(Key={"pk": "FEEDBACK", "sk": "2026-08-31"})["Item"]
    assert "answered_at_epoch_ms" not in before
    assert backend.put_feedback_once(newer, "event-mixed-zone") is True
    item = table.get_item(Key={"pk": "FEEDBACK", "sk": "2026-08-31"})["Item"]
    assert item["answered_at_epoch_ms"] == 1788185400000
    assert backend.get("FEEDBACK", "2026-08-31")["score"] == 5
    older = {**newer, "score": 2, "answered_at": "2026-08-31T14:00:00Z"}
    assert backend.put_feedback_once(older, "event-old-zone") is False
    assert backend.get("FEEDBACK", "2026-08-31")["score"] == 5
