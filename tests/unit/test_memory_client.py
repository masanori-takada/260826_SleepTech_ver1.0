"""記憶クライアントと DynamoDB バックエンドの読み書き。"""

from unittest.mock import Mock, patch

from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.dynamo_backend import DynamoBackend
from sleeptech.memory.schemas import DailyVital, Feedback, Prediction


class _ConditionalCheckFailedException(Exception):
    """DynamoDB の条件不成立を模したテスト例外。"""


def _client() -> MemoryClient:
    return MemoryClient(InMemoryBackend())


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


def test_idempotency_is_per_date_and_slot():
    c = _client()
    assert c.is_notified("2026-08-26", "NIGHT") is False
    assert c.mark_notified("2026-08-26", "NIGHT") is True
    assert c.is_notified("2026-08-26", "NIGHT") is True
    assert c.mark_notified("2026-08-26", "NIGHT") is False
    assert c.is_notified("2026-08-26", "MORNING") is False
    assert c.is_notified("2026-08-27", "NIGHT") is False


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
        backend = DynamoBackend("table", "ap-northeast-1", ttl_days=2)
        backend.put("VITAL", "2026-08-26", {"wake_h": 31.0})

    item = table.put_item.call_args.kwargs["Item"]
    assert item["pk"] == "VITAL"
    assert item["sk"] == "2026-08-26"
    assert item["json"] == '{"wake_h": 31.0}'
    assert "ttl" not in item


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
        backend = DynamoBackend("table", "ap-northeast-1", ttl_days=365)
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
