"""睡眠ステージの派生と日付変換（仕様 第4節「睡眠レコードから追加で取り出す項目」）。"""

from sleeptech.collectors import vitals
from sleeptech.collectors.google_health_api import _derive_sleep_metrics


def _stage(stage_type: str, start: str, end: str) -> dict:
    return {"type": stage_type, "startTime": start, "endTime": end}


SLEEP_BLOCK = {
    "interval": {
        "startTime": "2026-08-26T14:30:00Z",   # JST 23:30
        "startUtcOffset": "32400s",
        "endTime": "2026-08-26T22:00:00Z",     # JST 翌 7:00
        "endUtcOffset": "32400s",
    },
    "type": "STAGES",
    "stages": [
        _stage("LIGHT", "2026-08-26T14:30:00Z", "2026-08-26T16:00:00Z"),  # 90分
        _stage("DEEP", "2026-08-26T16:00:00Z", "2026-08-26T17:00:00Z"),   # 60分
        _stage("REM", "2026-08-26T17:00:00Z", "2026-08-26T18:10:00Z"),    # 70分
        _stage("AWAKE", "2026-08-26T18:10:00Z", "2026-08-26T18:20:00Z"),  # 中途覚醒
        _stage("LIGHT", "2026-08-26T18:20:00Z", "2026-08-26T22:00:00Z"),  # 220分
    ],
}


def test_derives_rem_sleep_minutes():
    """REM ステージの合計を出す（転用元には無かった項目）。"""
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["rem_sleep_min"] == 70


def test_derives_total_and_deep_sleep():
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["total_sleep_min"] == 440   # 90+60+70+220
    assert got["deep_sleep_min"] == 60


def test_counts_awakenings_excluding_the_first_stage():
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["awakenings"] == 1


def test_derives_bedtime_and_wake_hours():
    """就寝・起床時刻を起点日 0:00 基準の経過時間で返す。"""
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["bedtime_h"] == 23.5   # JST 23:30
    assert got["wake_h"] == 31.0      # 翌 7:00


def test_empty_stages_yield_all_none():
    got = _derive_sleep_metrics({"interval": {}, "stages": []})
    assert got["total_sleep_min"] is None
    assert got["rem_sleep_min"] is None
    assert got["bedtime_h"] is None


def test_to_daily_vital_shifts_date_back_by_one_day():
    """Google Health は起床日で睡眠を返す。本プロジェクトの date は起点日なので1日引く。"""
    raw = {"total_sleep_min": 440, "wake_h": 31.0, "bedtime_h": 23.5}
    got = vitals.to_daily_vital(raw, wake_date="2026-08-27")
    assert got.date == "2026-08-26"


def test_to_daily_vital_copies_known_fields_only():
    raw = {"total_sleep_min": 440, "hrv": 42.5, "unknown_field": 1}
    got = vitals.to_daily_vital(raw, wake_date="2026-08-27")
    assert got.total_sleep_min == 440
    assert got.hrv == 42.5


def test_to_daily_vital_sets_ingested_at():
    got = vitals.to_daily_vital({}, wake_date="2026-08-27")
    assert got.ingested_at is not None
