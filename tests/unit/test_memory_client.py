"""記憶クライアントと DynamoDB バックエンドの読み書き。"""

from unittest.mock import Mock, patch

from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.dynamo_backend import DynamoBackend
from sleeptech.memory.schemas import DailyVital, Feedback, Prediction


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
    c.mark_notified("2026-08-26", "NIGHT")
    assert c.is_notified("2026-08-26", "NIGHT") is True
    assert c.is_notified("2026-08-26", "MORNING") is False
    assert c.is_notified("2026-08-27", "NIGHT") is False


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


def test_dynamo_put_wraps_values_as_json_and_applies_ttl():
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
    assert item["ttl"] == 173800
