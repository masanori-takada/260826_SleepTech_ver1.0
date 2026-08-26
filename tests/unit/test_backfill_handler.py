"""過去睡眠バックフィルハンドラの契約テスト。"""

from datetime import date, timedelta
from io import BytesIO
from urllib.error import HTTPError

import pytest

from sleeptech.collectors import ConsentRevokedError
from sleeptech.collectors.google_auth_helper import get_access_token
from sleeptech.memory.schemas import DailyVital
from handlers import backfill_handler as bh


class FakeMemory:
    def __init__(self, values=None):
        self.values = values or {}
        self.puts = []

    def get(self, pk, sk):
        assert pk == "VITAL"
        return self.values.get(sk)

    def put(self, pk, sk, value):
        self.puts.append((pk, sk, value))

    def merge_json(self, pk, sk, merge):
        current = self.values.get(sk)
        value = merge(dict(current) if current else None)
        self.values[sk] = value
        self.puts.append((pk, sk, value))
        return value


class FakeDeadline:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.expected = []

    def can_start(self, expected_sec):
        self.expected.append(expected_sec)
        return next(self.decisions)


def test_awakenings_is_an_explicit_required_backfill_field():
    assert "awakenings" in bh.REQUIRED_BACKFILL_FIELDS


@pytest.mark.parametrize(
    ("google_awakenings", "expected_awakenings"),
    [(3, 3), (None, 7)],
)
def test_awakenings_nonnull_is_updated_and_null_preserves_existing_value(
    monkeypatch, google_awakenings, expected_awakenings
):
    memory = FakeMemory({"2026-08-01": {"date": "2026-08-01", "awakenings": 7}})

    class Source:
        def fetch_raw(self, wake_date):
            return {
                "total_sleep_min": 420,
                "deep_sleep_min": 60,
                "rem_sleep_min": 90,
                "bedtime_h": 23.0,
                "wake_h": 31.0,
                "awakenings": google_awakenings,
            }

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda: (Source(), memory))
    monkeypatch.setattr(
        bh,
        "to_daily_vital",
        lambda raw, wake_date: DailyVital(date="2026-08-01", awakenings=raw["awakenings"]),
    )

    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert result["updated"] == 1
    assert memory.puts[0][2]["awakenings"] == expected_awakenings


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("rem_sleep_min", 95),
        ("bedtime_h", 22.5),
        ("wake_h", 30.5),
        ("awakenings", 2),
    ],
)
@pytest.mark.parametrize("use_null", [False, True])
def test_required_sleep_fields_update_when_nonnull_and_preserve_when_null(
    monkeypatch, field, new_value, use_null
):
    existing = {
        "date": "2026-08-01", "total_sleep_min": 420, "deep_sleep_min": 60,
        "rem_sleep_min": 80, "bedtime_h": 23.0, "wake_h": 31.0, "awakenings": 1,
    }
    raw = {key: existing[key] for key in (
        "total_sleep_min", "deep_sleep_min", "rem_sleep_min", "bedtime_h", "wake_h", "awakenings"
    )}
    raw[field] = None if use_null else new_value
    memory = FakeMemory({"2026-08-01": existing})

    class Source:
        def fetch_raw(self, wake_date):
            return raw

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda **kwargs: (Source(), memory))
    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert result["updated"] == 1
    assert memory.puts[0][2][field] == (existing[field] if use_null else new_value)


def test_all_sleep_fields_missing_is_no_data_and_does_not_write_or_count_success(monkeypatch):
    existing = {"date": "2026-08-01", "total_sleep_min": 420, "wake_h": 31.0}
    memory = FakeMemory({"2026-08-01": existing})

    class Source:
        def fetch_raw(self, wake_date):
            return {field: None for field in bh.SLEEP_DATA_FIELDS}

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda **kwargs: (Source(), memory))
    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert result["updated"] == 0
    assert result["with_sleep"] == 0
    assert result["no_data"] == 1
    assert memory.puts == []


def test_with_sleep_is_based_on_current_raw_not_existing_sleep(monkeypatch):
    memory = FakeMemory({"2026-08-01": {"date": "2026-08-01", "total_sleep_min": 420, "wake_h": 31.0}})

    class Source:
        def fetch_raw(self, wake_date):
            return {field: None for field in bh.SLEEP_DATA_FIELDS}

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda **kwargs: (Source(), memory))
    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert result["with_sleep"] == 0
    assert result["no_data"] == 1


def test_total_and_deep_partial_data_is_written_even_when_core_sleep_fields_missing(monkeypatch):
    memory = FakeMemory({"2026-08-01": {"date": "2026-08-01", "total_sleep_min": 420}})

    class Source:
        def fetch_raw(self, wake_date):
            return {
                "total_sleep_min": 420,
                "deep_sleep_min": 60,
                "rem_sleep_min": None,
                "bedtime_h": None,
                "wake_h": None,
                "awakenings": None,
            }

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda **kwargs: (Source(), memory))
    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert result["no_data"] == 0
    assert result["updated"] == 1
    assert result["with_sleep"] == 0
    assert memory.puts[0][2]["total_sleep_min"] == 420


@pytest.mark.parametrize(
    ("raw", "field", "expected"),
    [
        ({"hrv": 42.5}, "hrv", 42.5),
        ({"total_sleep_min": 390}, "total_sleep_min", 390),
    ],
)
def test_single_nonnull_google_field_is_written_as_partial_data(monkeypatch, raw, field, expected):
    memory = FakeMemory({"2026-08-01": {"date": "2026-08-01", "old_fitbit": "keep"}})

    class Source:
        def fetch_raw(self, wake_date):
            return raw

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda **kwargs: (Source(), memory))
    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert result["no_data"] == 0
    assert result["updated"] == 1
    assert memory.puts[0][2][field] == expected
    assert memory.puts[0][2]["old_fitbit"] == "keep"


def test_stops_before_a_day_when_deadline_cannot_cover_one_day(monkeypatch):
    deadline = FakeDeadline([True, False])
    calls = []

    class Source:
        def fetch_raw(self, wake_date):
            calls.append(wake_date)
            return {"total_sleep_min": 420, "wake_h": 31.0}

    memory = FakeMemory()
    captured = {}
    monkeypatch.setattr(bh, "deadline_from_context", lambda context: deadline)
    def build_dependencies(**kwargs):
        captured["deadline"] = kwargs["deadline"]
        return Source(), memory

    monkeypatch.setattr(bh, "build_backfill_dependencies", build_dependencies)

    class Context:
        def get_remaining_time_in_millis(self):
            return 60000

    result = bh.handler(
        {"start_date": "2026-08-01", "end_date": "2026-08-03"}, Context()
    )

    assert captured["deadline"] is deadline
    assert calls == ["2026-08-02"]
    assert result["stopped_early"] is True
    assert result["next_date"] == "2026-08-02"
    assert result["requested"] == 3


def test_build_dependencies_passes_deadline_to_google_source(monkeypatch):
    settings = type("Settings", (), {
        "secret_google": "sleeptech/google-oauth",
        "region": "ap-northeast-1",
        "table": "SleepTechTable",
    })()
    captured = {}
    deadline = object()

    monkeypatch.setattr(bh, "load_settings", lambda: settings)
    monkeypatch.setattr("handlers.deps._secret", lambda name, region: {"refresh_token": "opaque"})
    monkeypatch.setattr(bh, "DynamoBackend", lambda table, region: "memory")

    def source_factory(provider, **kwargs):
        captured["deadline"] = kwargs["deadline"]
        return "source"

    monkeypatch.setattr(bh, "GoogleHealthApiSource", source_factory)
    assert bh.build_backfill_dependencies(deadline=deadline) == ("source", "memory")
    assert captured["deadline"] is deadline


@pytest.mark.parametrize(
    "event",
    [
        {"start_date": "2024-02-01", "end_date": "2024-03-01"},  # 30日
        {"start_date": "2024-12-15", "end_date": "2025-01-13"},  # 年跨ぎ30日
    ],
)
def test_accepts_calendar_ranges_up_to_30_days(event):
    assert bh._parse_range(event) is not None


@pytest.mark.parametrize(
    "event",
    [
        {"start_date": "2024-02-01", "end_date": "2024-03-02"},
        {"start_date": "2024-03-02", "end_date": "2024-03-01"},
        {"start_date": "2024-02-30", "end_date": "2024-03-01"},
        {"start_date": "2024/02/01", "end_date": "2024-02-01"},
    ],
)
def test_rejects_over_limit_reverse_and_invalid_calendar_ranges(event):
    assert bh._parse_range(event) is None


def _http_error(code, body):
    return HTTPError(
        "https://oauth2.googleapis.com/token", code, "failure", {}, BytesIO(body.encode())
    )


def _source_with_oauth_http_error(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)
    return bh.GoogleHealthApiSource(
        lambda: get_access_token({
            "client_id": "id", "client_secret": "secret", "refresh_token": "opaque"
        })
    )


def test_google_oauth_400_invalid_grant_is_reported_as_consent_revoked(monkeypatch):
    source = _source_with_oauth_http_error(
        monkeypatch, _http_error(400, '{"error":"invalid_grant"}')
    )

    with pytest.raises(ConsentRevokedError):
        source.fetch_raw("2026-08-02")


@pytest.mark.parametrize("code", [400, 429, 500])
def test_google_oauth_non_invalid_grant_http_errors_are_not_consent_revoked(monkeypatch, code):
    source = _source_with_oauth_http_error(
        monkeypatch, _http_error(code, '{"error":"temporary_failure"}')
    )

    with pytest.raises(HTTPError) as caught:
        source.fetch_raw("2026-08-02")
    assert caught.value.code == code


def test_repeating_same_range_preserves_fields_and_is_value_stable(monkeypatch):
    memory = FakeMemory({"2026-08-01": {"date": "2026-08-01", "old_fitbit": "keep"}})

    class Source:
        def fetch_raw(self, wake_date):
            return {"hrv": 42.5, "weight_kg": 60.0}

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda **kwargs: (Source(), memory))
    event = {"start_date": "2026-08-01", "end_date": "2026-08-01"}
    first = bh.handler(event)
    first_value = dict(memory.values["2026-08-01"])
    second = bh.handler(event)
    second_value = dict(memory.values["2026-08-01"])

    assert first["updated"] == second["updated"] == 1
    assert second_value == first_value
    assert second_value["old_fitbit"] == "keep"


def test_rejects_missing_or_invalid_date_range_before_dependencies(monkeypatch):
    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda: pytest.fail("依存構築不要"))

    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-31"})

    assert result["statusCode"] == 400


def test_backfills_using_wake_date_and_merges_only_non_null_values(monkeypatch):
    old = {
        "date": "2026-08-01",
        "total_sleep_min": 400,
        "wake_h": 31.0,
        "old_fitbit_field": "keep",
    }
    memory = FakeMemory({"2026-08-01": old})
    calls = []

    class Source:
        def fetch_raw(self, wake_date):
            calls.append(wake_date)
            return {"total_sleep_min": None, "deep_sleep_min": 80, "wake_h": 30.5}

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda: (Source(), memory))
    monkeypatch.setattr(bh, "to_daily_vital", lambda raw, wake_date: DailyVital(
        date="2026-08-01", total_sleep_min=raw["total_sleep_min"],
        deep_sleep_min=raw["deep_sleep_min"], wake_h=raw["wake_h"],
    ))

    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert calls == ["2026-08-02"]
    assert result["requested"] == 1
    assert result["updated"] == 1
    assert result["with_sleep"] == 0
    assert result["no_data"] == 0
    saved = memory.puts[0]
    assert saved[0:2] == ("VITAL", "2026-08-01")
    assert saved[2]["total_sleep_min"] == 400
    assert saved[2]["deep_sleep_min"] == 80
    assert saved[2]["old_fitbit_field"] == "keep"


def test_continues_after_a_day_failure_and_reports_each_failure(monkeypatch):
    memory = FakeMemory()
    calls = []

    class Source:
        def fetch_raw(self, wake_date):
            calls.append(wake_date)
            if wake_date == "2026-08-03":
                raise RuntimeError("temporary")
            return {"total_sleep_min": 420, "wake_h": 31.0}

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda: (Source(), memory))
    monkeypatch.setattr(bh, "to_daily_vital", lambda raw, wake_date: DailyVital(
        date=(date.fromisoformat(wake_date) - timedelta(days=1)).isoformat(),
        total_sleep_min=raw["total_sleep_min"], wake_h=raw["wake_h"],
    ))

    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-03"})

    assert calls == ["2026-08-02", "2026-08-03", "2026-08-04"]
    assert result["requested"] == 3
    assert result["updated"] == 2
    assert result["failed"] == 1
    assert result["failures"][0]["date"] == "2026-08-02"


def test_marks_oauth_consent_revocation_explicitly_without_secret_details(monkeypatch):
    memory = FakeMemory()

    class Source:
        def fetch_raw(self, wake_date):
            raise ConsentRevokedError("refresh token secret must not be returned")

    monkeypatch.setattr(bh, "build_backfill_dependencies", lambda: (Source(), memory))

    result = bh.handler({"start_date": "2026-08-01", "end_date": "2026-08-01"})

    assert result["failed"] == 1
    assert result["failures"] == [{"date": "2026-08-01", "error": "consent_revoked"}]
    assert "refresh token" not in str(result)


def test_access_token_provider_is_cached_for_one_invocation(monkeypatch):
    calls = []
    monkeypatch.setattr(bh, "get_access_token", lambda secret: calls.append(secret) or "token")

    provider = bh.cached_access_token_provider({"refresh_token": "opaque"})

    assert provider() == "token"
    assert provider() == "token"
    assert calls == [{"refresh_token": "opaque"}]
