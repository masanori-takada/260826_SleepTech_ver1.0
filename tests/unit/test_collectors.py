"""睡眠ステージの派生と日付変換（仕様 第4節「睡眠レコードから追加で取り出す項目」）。"""

from sleeptech.collectors import vitals
from sleeptech.collectors.google_health_api import (
    GoogleHealthApiSource,
    _derive_sleep_metrics,
    _pick_sleep_for_date,
)
import urllib.parse


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
    got = _derive_sleep_metrics(SLEEP_BLOCK, wake_date="2026-08-27")
    assert got["rem_sleep_min"] == 70


def _sleep_point(end_hour: int, *, nap: bool = False, main: bool = False, duration: int = 8):
    return {
        "sleep": {
            "interval": {
                "startTime": f"2026-08-26T{24 - duration:02d}:00:00Z",
                "endTime": f"2026-08-27T{end_hour:02d}:00:00Z",
                "startUtcOffset": "32400s",
                "endUtcOffset": "32400s",
                "civilEndTime": {"date": {"year": 2026, "month": 8, "day": 27}},
            },
            "metadata": {"nap": nap, "main": main},
            "stages": [],
        }
    }


def test_sleep_selection_excludes_naps_and_is_order_independent():
    nap = _sleep_point(12, nap=True, duration=1)
    short = _sleep_point(7, duration=5)
    main = _sleep_point(8, main=True, duration=8)

    assert _pick_sleep_for_date([main, nap, short], "2026-08-27") is main
    assert _pick_sleep_for_date([short, nap, main], "2026-08-27") is main


def test_sleep_selection_uses_longest_non_nap_when_main_flag_is_absent():
    short = _sleep_point(7, duration=5)
    long = _sleep_point(8, duration=8)

    assert _pick_sleep_for_date([short, long], "2026-08-27") is long


def test_health_list_requests_use_civil_half_open_filter_and_page_budget():
    urls = []

    def get(url, headers):
        urls.append(url)
        return {"dataPoints": [], "nextPageToken": "more"}

    source = GoogleHealthApiSource(lambda: "token", http_get=get)
    source.fetch_raw("2026-08-27")

    assert len(urls) <= 4 * 2
    decoded = [urllib.parse.unquote_plus(url) for url in urls]
    assert all('>= "2026-08-27"' in url for url in decoded)
    assert all('< "2026-08-28"' in url for url in decoded)


def test_health_list_stops_on_time_budget_without_following_unbounded_pages():
    calls = {"n": 0}

    def get(url, headers):
        calls["n"] += 1
        return {"dataPoints": [], "nextPageToken": "more"}

    source = GoogleHealthApiSource(lambda: "token", http_get=get, clock=lambda: 10.0)
    source.fetch_raw("2026-08-27")

    assert calls["n"] <= 8


def test_derives_total_and_deep_sleep():
    got = _derive_sleep_metrics(SLEEP_BLOCK, wake_date="2026-08-27")
    assert got["total_sleep_min"] == 440   # 90+60+70+220
    assert got["deep_sleep_min"] == 60


def test_counts_awakenings_excluding_the_first_stage():
    got = _derive_sleep_metrics(SLEEP_BLOCK, wake_date="2026-08-27")
    assert got["awakenings"] == 1


def test_derives_bedtime_and_wake_hours():
    """就寝・起床時刻を起点日 0:00 基準の経過時間で返す（通常就寝、回帰）。"""
    got = _derive_sleep_metrics(SLEEP_BLOCK, wake_date="2026-08-27")
    assert got["bedtime_h"] == 23.5   # JST 23:30
    assert got["wake_h"] == 31.0      # 翌 7:00


def test_derives_bedtime_and_wake_hours_when_bedtime_itself_crosses_midnight():
    """就寝自体が起点日をまたぐ夜（起点日の翌日 1:30 就寝）でも絶対位置がずれない。

    旧実装の `wake_h <= bedtime_h なら +24` というヒューリスティックは
    このケースを扱えず、bedtime_h=1.5, wake_h=9.0 という24時間ずれた値を返していた。
    """
    block = {
        "interval": {
            "startTime": "2026-08-26T16:30:00Z",   # JST 8/27 1:30
            "startUtcOffset": "32400s",
            "endTime": "2026-08-27T00:00:00Z",      # JST 8/27 9:00
            "endUtcOffset": "32400s",
        },
        "type": "STAGES",
        "stages": [],
    }
    got = _derive_sleep_metrics(block, wake_date="2026-08-27")
    assert got["bedtime_h"] == 25.5
    assert got["wake_h"] == 33.0


def test_wake_h_does_not_bimodalize_across_mixed_bedtimes():
    """23:30就寝の夜と1:30就寝の夜が混在しても wake_h の跳びが24時間にならない。

    ベースラインの起床時刻中央値算出（model/baseline.py）は wake_h を直接使うため、
    ここで24時間の跳びが出ると二峰分布になり中央値が無意味になる（今回の欠陥の核心）。
    """
    normal_night = _derive_sleep_metrics(SLEEP_BLOCK, wake_date="2026-08-27")
    late_night_block = {
        "interval": {
            "startTime": "2026-08-26T16:30:00Z",   # JST 8/27 1:30
            "startUtcOffset": "32400s",
            "endTime": "2026-08-27T00:00:00Z",      # JST 8/27 9:00
            "endUtcOffset": "32400s",
        },
        "type": "STAGES",
        "stages": [],
    }
    late_night = _derive_sleep_metrics(late_night_block, wake_date="2026-08-27")
    assert late_night["wake_h"] - normal_night["wake_h"] == 2.0


def test_duration_h_is_correct_for_both_bedtime_patterns():
    """wake_h - bedtime_h（duration_h相当）が両ケースで正しい（7.5時間）。"""
    normal_night = _derive_sleep_metrics(SLEEP_BLOCK, wake_date="2026-08-27")
    assert normal_night["wake_h"] - normal_night["bedtime_h"] == 7.5

    late_night_block = {
        "interval": {
            "startTime": "2026-08-26T16:30:00Z",
            "startUtcOffset": "32400s",
            "endTime": "2026-08-27T00:00:00Z",
            "endUtcOffset": "32400s",
        },
        "type": "STAGES",
        "stages": [],
    }
    late_night = _derive_sleep_metrics(late_night_block, wake_date="2026-08-27")
    assert late_night["wake_h"] - late_night["bedtime_h"] == 7.5


def test_empty_stages_still_derive_bedtime_and_wake():
    """stages が空でも interval が有効なら bedtime_h / wake_h は導出する。

    起床時刻は推奨就寝時刻の逆算に使う最重要フィールドのため、
    ステージ由来の項目（total_sleep_min 等）が無くても欠損させない。
    """
    block = {
        "interval": {
            "startTime": "2026-08-26T14:30:00Z",
            "startUtcOffset": "32400s",
            "endTime": "2026-08-26T22:00:00Z",
            "endUtcOffset": "32400s",
        },
        "stages": [],
    }
    got = _derive_sleep_metrics(block, wake_date="2026-08-27")
    assert got["bedtime_h"] == 23.5
    assert got["wake_h"] == 31.0
    assert got["total_sleep_min"] is None
    assert got["deep_sleep_min"] is None
    assert got["rem_sleep_min"] is None
    assert got["awakenings"] is None


def test_missing_interval_yields_all_none():
    """interval 欠損時は全項目 None（ステージ由来 + bedtime/wake とも）。"""
    got = _derive_sleep_metrics({"interval": {}, "stages": []}, wake_date="2026-08-27")
    assert got["total_sleep_min"] is None
    assert got["deep_sleep_min"] is None
    assert got["rem_sleep_min"] is None
    assert got["awakenings"] is None
    assert got["bedtime_h"] is None
    assert got["wake_h"] is None


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
