"""記憶クライアントと DynamoDB バックエンドの読み書き。"""

from unittest.mock import Mock, patch

import pytest

from sleeptech.memory.client import InMemoryBackend, MemoryClient, NotificationClaimBusy
from sleeptech.memory.dynamo_backend import DynamoBackend
from sleeptech.memory.schemas import DailyVital, Feedback, Message, Prediction


class _ConditionalCheckFailedException(Exception):
    """DynamoDB の条件不成立を模したテスト例外。"""


def _client() -> MemoryClient:
    return MemoryClient(InMemoryBackend())


class _ThreeArgBackend:
    """Brief の put(pk, sk, value) 注入契約だけを実装する最小fake。"""

    def __init__(self) -> None:
        self.items = {}

    def put(self, pk, sk, value):
        self.items[(pk, sk)] = value

    def get(self, pk, sk):
        return self.items.get((pk, sk))

    def query_range(self, pk, start_sk, end_sk):
        return []


def test_save_vital_accepts_three_argument_backend_put_contract():
    backend = _ThreeArgBackend()
    vital = DailyVital(date="2026-08-26", total_sleep_min=330)

    MemoryClient(backend).save_vital(vital)

    assert backend.items[("VITAL", "2026-08-26")] == vital.to_dict()


def test_save_and_get_vital():
    c = _client()
    v = DailyVital(date="2026-08-26", total_sleep_min=330, wake_h=31.0)
    c.save_vital(v)
    assert c.get_vital("2026-08-26") == v


def test_get_vital_returns_none_when_absent():
    assert _client().get_vital("2026-08-26") is None


def test_get_vitals_desc_returns_newest_first():
    """新しい順に並べて返す（ベースライン算出がこの順序を前提にする）。"""
    c = _client()
    for d in ("2026-08-20", "2026-08-21", "2026-08-22"):
        c.save_vital(DailyVital(date=d, total_sleep_min=400, wake_h=31.0))
    got = c.get_vitals_desc("2026-08-22", days=14)
    assert [v.date for v in got] == ["2026-08-22", "2026-08-21", "2026-08-20"]


def test_get_vitals_desc_excludes_days_outside_the_window():
    """指定日数より古いレコードは含めない。"""
    c = _client()
    for d in ("2026-08-01", "2026-08-25", "2026-08-26"):
        c.save_vital(DailyVital(date=d, total_sleep_min=400, wake_h=31.0))
    got = c.get_vitals_desc("2026-08-26", days=7)
    assert [v.date for v in got] == ["2026-08-26", "2026-08-25"]


def test_save_and_get_prediction_by_slot():
    """同じ日付でも NIGHT と MORNING は別レコード。"""
    c = _client()
    c.save_prediction(Prediction(date="2026-08-26", slot="NIGHT", mean_drop_pct=8))
    c.save_prediction(Prediction(date="2026-08-26", slot="MORNING", mean_drop_pct=19))
    assert c.get_prediction("2026-08-26", "NIGHT").mean_drop_pct == 8
    assert c.get_prediction("2026-08-26", "MORNING").mean_drop_pct == 19


def test_save_and_get_message_by_slot():
    """I5対応: 仕様 第9節 MSG#NIGHT / MSG#MORNING。本文と検証結果を保存・取得できる。"""
    c = _client()
    c.save_message(Message(
        date="2026-08-26", slot="NIGHT", body="今夜は23:30までに就寝を。",
        guardrail_passed=True, regen_count=0, delivered=True,
    ))
    c.save_message(Message(
        date="2026-08-26", slot="MORNING", body="おはようございます。",
        guardrail_passed=False, regen_count=2, delivered=True,
    ))
    night_msg = c.get_message("2026-08-26", "NIGHT")
    morning_msg = c.get_message("2026-08-26", "MORNING")
    assert night_msg.body == "今夜は23:30までに就寝を。"
    assert night_msg.guardrail_passed is True
    assert night_msg.regen_count == 0
    assert morning_msg.guardrail_passed is False
    assert morning_msg.regen_count == 2


def test_get_message_returns_none_when_absent():
    c = _client()
    assert c.get_message("2026-08-26", "NIGHT") is None


def test_idempotency_is_per_date_and_slot():
    c = _client()
    assert c.is_notified("2026-08-26", "NIGHT") is False
    assert c.mark_notified("2026-08-26", "NIGHT") is True
    assert c.is_notified("2026-08-26", "NIGHT") is True
    assert c.mark_notified("2026-08-26", "NIGHT") is False
    assert c.is_notified("2026-08-26", "MORNING") is False
    assert c.is_notified("2026-08-27", "NIGHT") is False


def test_notification_claim_is_atomic_and_lease_can_be_reclaimed():
    c = _client()
    assert c.claim_notification("2026-08-26", "NIGHT", now=100, lease_seconds=30) is True
    with pytest.raises(NotificationClaimBusy):
        c.claim_notification("2026-08-26", "NIGHT", now=110, lease_seconds=30)
    assert c.claim_notification("2026-08-26", "NIGHT", now=131, lease_seconds=30) is True


def test_claim_conflict_uses_backend_strong_read_when_available():
    """claim失敗後のPENDING判定は専用strong readを使う。"""
    class StrongReadBackend(InMemoryBackend):
        def __init__(self):
            super().__init__()
            self.strong_read_calls = []

        def get_strong(self, pk, sk):
            self.strong_read_calls.append((pk, sk))
            return self.get(pk, sk)

    backend = StrongReadBackend()
    client = MemoryClient(backend)
    client.claim_notification("2026-08-26", "NIGHT", now=100, lease_seconds=30)

    with pytest.raises(NotificationClaimBusy):
        client.claim_notification("2026-08-26", "NIGHT", now=110, lease_seconds=30)

    assert backend.strong_read_calls == [("IDEM#NIGHT", "2026-08-26")]


def test_mark_notified_completes_claim_and_is_notified_ignores_pending():
    c = _client()
    assert c.claim_notification("2026-08-26", "NIGHT", now=100, lease_seconds=30) is True
    assert c.is_notified("2026-08-26", "NIGHT") is False
    assert c.mark_notified("2026-08-26", "NIGHT") is True
    assert c.is_notified("2026-08-26", "NIGHT") is True


def test_mark_notified_propagates_non_conditional_errors():
    class FailingBackend(InMemoryBackend):
        def put_conditional(self, pk, sk, value, ttl_days=None):
            raise RuntimeError("DynamoDB unavailable")

    try:
        MemoryClient(FailingBackend()).mark_notified("2026-08-26", "NIGHT")
    except RuntimeError as exc:
        assert str(exc) == "DynamoDB unavailable"
    else:
        raise AssertionError("非 Conditional 例外が伝播していない")


def test_save_and_get_feedback():
    c = _client()
    c.save_feedback(Feedback(date="2026-08-26", score=4))
    assert c.get_feedback("2026-08-26").score == 4


def test_feedback_is_overwritten_by_later_answer():
    """同じ日付に複数回答が来たら最後で上書きする。"""
    c = _client()
    c.save_feedback(Feedback(date="2026-08-26", score=2))
    c.save_feedback(Feedback(date="2026-08-26", score=5))
    assert c.get_feedback("2026-08-26").score == 5


def test_feedback_desc_is_newest_first_and_limited():
    c = _client()
    for day in ("2026-08-25", "2026-08-26", "2026-08-27"):
        c.save_feedback(Feedback(date=day, score=3))
    assert [f.date for f in c.get_feedback_desc(2)] == ["2026-08-27", "2026-08-26"]


def test_save_feedback_once_rejects_same_event_but_overwrites_with_new_event():
    c = _client()
    first = Feedback(date="2026-08-27", score=1, source_event_id="event-1")
    same_event = Feedback(date="2026-08-27", score=2, source_event_id="event-1")
    second = Feedback(date="2026-08-27", score=5, source_event_id="event-2")

    assert c.save_feedback_once(first) is True
    assert c.save_feedback_once(same_event) is False
    assert c.save_feedback_once(second) is True
    assert c.get_feedback("2026-08-27").score == 5


def test_concurrent_feedback_events_leave_one_latest_or_first_record():
    """同時イベントは各イベントmarkerを一度だけ処理し、日付レコードを壊さない。"""
    from concurrent.futures import ThreadPoolExecutor

    c = _client()
    feedbacks = [Feedback(date="2026-08-27", score=i, source_event_id=f"event-{i}") for i in range(1, 6)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(c.save_feedback_once, feedbacks))
    assert all(results)
    assert c.get_feedback("2026-08-27").score in range(1, 6)


def test_claim_feedback_event_once_is_atomic_for_unmatched_events():
    c = _client()

    assert c.claim_feedback_event_once("event-1") is True
    assert c.claim_feedback_event_once("event-1") is False


def test_config_defaults_to_empty_dict():
    assert _client().get_config() == {}


def test_config_round_trip():
    c = _client()
    c.save_config({"override_wake_h": 30.0})
    assert c.get_config()["override_wake_h"] == 30.0


def test_dynamo_query_range_uses_between_and_follows_pages():
    table = Mock()
    table.query.side_effect = [
        {"Items": [{"json": '{"date": "2026-08-20"}'}], "LastEvaluatedKey": {"pk": "VITAL", "sk": "2026-08-20"}},
        {"Items": [{"json": '{"date": "2026-08-21"}'}]},
    ]
    resource = Mock()
    resource.Table.return_value = table
    with patch("boto3.resource", return_value=resource):
        backend = DynamoBackend("table", "ap-northeast-1")

    got = backend.query_range("VITAL", "2026-08-20", "2026-08-26")

    assert got == [{"date": "2026-08-20"}, {"date": "2026-08-21"}]
    first_call = table.query.call_args_list[0]
    assert first_call.kwargs["KeyConditionExpression"].get_expression()["operator"] == "AND"
    assert first_call.kwargs["KeyConditionExpression"].get_expression()["values"][1]._values[1:] == (
        "2026-08-20",
        "2026-08-26",
    )
    assert table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
        "pk": "VITAL",
        "sk": "2026-08-20",
    }


def test_dynamo_put_wraps_values_without_implicit_ttl():
    table = Mock()
    resource = Mock()
    resource.Table.return_value = table
    with patch("boto3.resource", return_value=resource), patch("time.time", return_value=1000):
        backend = DynamoBackend("table", "ap-northeast-1", vital_ttl_days=2)
        backend.put("VITAL", "2026-08-26", {"wake_h": 31.0})
        backend.put("FEEDBACK", "2026-08-26", {"score": 4})

    vital_item = table.put_item.call_args_list[0].kwargs["Item"]
    feedback_item = table.put_item.call_args_list[1].kwargs["Item"]
    assert vital_item["pk"] == "VITAL"
    assert vital_item["sk"] == "2026-08-26"
    assert vital_item["json"] == '{"wake_h": 31.0}'
    assert vital_item["ttl"] == 1000 + 2 * 86400
    assert "ttl" not in feedback_item


def test_dynamo_conditional_put_returns_true_or_false_for_typed_failure():
    table = Mock()
    table.meta.client.exceptions.ConditionalCheckFailedException = _ConditionalCheckFailedException
    resource = Mock()
    resource.Table.return_value = table
    with patch("boto3.resource", return_value=resource):
        backend = DynamoBackend("table", "ap-northeast-1")

    assert backend.put_conditional("IDEM#NIGHT", "2026-08-26", {"slot": "NIGHT"}) is True
    table.put_item.side_effect = _ConditionalCheckFailedException()
    assert backend.put_conditional("IDEM#NIGHT", "2026-08-26", {"slot": "NIGHT"}) is False
    condition = table.put_item.call_args_list[0].kwargs["ConditionExpression"]
    assert condition.get_expression()["operator"] == "attribute_not_exists"


def test_dynamo_strong_read_requests_consistent_read():
    """claim競合判定用のDynamo readは強整合性を指定する。"""
    table = Mock()
    table.get_item.return_value = {"Item": {"json": '{"status": "PENDING"}'}}
    resource = Mock()
    resource.Table.return_value = table
    with patch("boto3.resource", return_value=resource):
        backend = DynamoBackend("table", "ap-northeast-1")

    assert backend.get_strong("IDEM#NIGHT", "2026-08-26") == {"status": "PENDING"}
    assert table.get_item.call_args.kwargs["ConsistentRead"] is True


def test_dynamo_conditional_put_propagates_non_conditional_error():
    table = Mock()
    table.meta.client.exceptions.ConditionalCheckFailedException = _ConditionalCheckFailedException
    table.put_item.side_effect = RuntimeError("DynamoDB unavailable")
    resource = Mock()
    resource.Table.return_value = table
    with patch("boto3.resource", return_value=resource):
        backend = DynamoBackend("table", "ap-northeast-1")

    try:
        backend.put_conditional("IDEM#NIGHT", "2026-08-26", {"slot": "NIGHT"})
    except RuntimeError as exc:
        assert str(exc) == "DynamoDB unavailable"
    else:
        raise AssertionError("非 Conditional 例外が伝播していない")


def test_memory_client_vital_only_receives_365_day_ttl():
    table = Mock()
    table.meta.client.exceptions.ConditionalCheckFailedException = _ConditionalCheckFailedException
    resource = Mock()
    resource.Table.return_value = table
    with patch("boto3.resource", return_value=resource), patch("time.time", return_value=1000):
        backend = DynamoBackend("table", "ap-northeast-1", vital_ttl_days=365)
        client = MemoryClient(backend)
        client.save_vital(DailyVital(date="2026-08-26", total_sleep_min=330))
        client.save_prediction(Prediction(date="2026-08-26", slot="NIGHT"))
        client.save_feedback(Feedback(date="2026-08-26", score=4))
        client.save_config({"override_wake_h": 30.0})
        client.mark_notified("2026-08-26", "NIGHT")

    put_items = [call.kwargs["Item"] for call in table.put_item.call_args_list]
    assert put_items[0]["ttl"] == 1000 + 365 * 86400
    assert all("ttl" not in item for item in put_items[1:])


def test_query_desc_continues_when_short_page_has_last_key():
    table = Mock()
    table.query.side_effect = [
        {"Items": [{"json": '{"date": "2026-08-26"}'}], "LastEvaluatedKey": {"pk": "VITAL", "sk": "2026-08-26"}},
        {"Items": [{"json": '{"date": "2026-08-25"}'}, {"json": '{"date": "2026-08-24"}'}]},
    ]
    resource = Mock()
    resource.Table.return_value = table
    with patch("boto3.resource", return_value=resource):
        backend = DynamoBackend("table", "ap-northeast-1")

    assert backend.query_desc("VITAL", limit=2) == [
        {"date": "2026-08-26"},
        {"date": "2026-08-25"},
    ]
    assert table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
        "pk": "VITAL",
        "sk": "2026-08-26",
    }
    assert table.query.call_args_list[1].kwargs["Limit"] == 1
