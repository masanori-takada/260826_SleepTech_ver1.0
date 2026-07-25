"""分析エージェントの数値根拠のテスト（FR-004）。

説得メッセージが幅広い指標に触れられるよう、
全 VITAL_FIELDS 分の根拠が作られることを検証する。
"""

from __future__ import annotations

from night_ramen.agents.analyzer import (
    _derive_impacts,
    analyze,
    derive_behavior,
    derive_metric_stats,
)
from night_ramen.agents.prompts import make_fake_invoker
from night_ramen.memory.schemas import VITAL_FIELDS, DailyVitalRecord


def _full_record(date: str, ramen: bool, k: float) -> DailyVitalRecord:
    """全指標が埋まったレコード。k で良い日/悪い日の差をつける。"""
    return DailyVitalRecord(
        date=date,
        ramen_last_night=ramen,
        sleep_score=int(60 + 20 * k),
        total_sleep_min=int(360 + 60 * k),
        awakenings=int(4 - 2 * k),
        deep_sleep_min=int(50 + 40 * k),
        night_avg_hr=float(62 - 5 * k),
        hrv=float(35 + 20 * k),
        skin_temp_delta=float(0.6 - 0.4 * k),
        respiration_rate=float(16.5 - 1.0 * k),
        weight_kg=float(72.5 - 0.5 * k),
        body_fat_pct=float(24.0 - 1.0 * k),
        next_day_steps=int(4000 + 5000 * k),
        next_day_calories=int(1800 + 500 * k),
    )


def _series() -> list[DailyVitalRecord]:
    out = []
    for i in range(1, 15):
        ramen = i % 2 == 1
        out.append(_full_record(f"2026-07-{i:02d}", ramen, 0.0 if ramen else 1.0))
    return out


def test_impacts_cover_all_vital_fields():
    impacts = _derive_impacts(_series())
    # 以前は4指標だけ比較していたため、説得の話題が固定されていた
    assert {i["metric"] for i in impacts} == set(VITAL_FIELDS)
    for i in impacts:
        assert i["label"] and "delta" in i


def test_metric_stats_available_without_ramen_reports():
    # 自己申告がまったく無くても、指標ごとの推移は必ず作れる
    vitals = [
        DailyVitalRecord(date=f"2026-07-{i:02d}", sleep_score=60 + i, weight_kg=72.0 - i * 0.1)
        for i in range(1, 11)
    ]
    assert _derive_impacts(vitals) == []  # 比較はできない

    stats = {s["metric"]: s for s in derive_metric_stats(vitals)}
    assert set(stats) == {"sleep_score", "weight_kg"}

    sleep = stats["sleep_score"]
    assert sleep["latest"] == 70
    assert sleep["latest_date"] == "2026-07-10"
    assert sleep["days"] == 10
    assert sleep["trend_delta"] is not None  # 直近7日とそれ以前を比較できる
    assert sleep["best"] == 70              # 大きいほど良い
    assert sleep["worst"] == 61

    weight = stats["weight_kg"]
    assert weight["best"] == 71.0           # 小さいほど良い
    assert weight["worst"] == 71.9


def test_metric_stats_skip_best_for_directionless_metric():
    vitals = [DailyVitalRecord(date="2026-07-01", skin_temp_delta=0.4)]
    stat = derive_metric_stats(vitals)[0]
    assert stat["metric"] == "skin_temp_delta"
    assert stat["higher_is_better"] is None
    assert stat["best"] is None


def test_behavior_summary_tracks_streak():
    vitals = [
        DailyVitalRecord(date="2026-07-01", ramen_last_night=True),
        DailyVitalRecord(date="2026-07-02", ramen_last_night=False),
        DailyVitalRecord(date="2026-07-03", ramen_last_night=True),
        DailyVitalRecord(date="2026-07-04", ramen_last_night=False),
        DailyVitalRecord(date="2026-07-05", ramen_last_night=False),
    ]
    behavior = derive_behavior(vitals)
    assert behavior["reported_days"] == 5
    assert behavior["abstained_days"] == 3
    assert behavior["success_rate"] == 0.6
    assert behavior["current_abstinence_streak"] == 2
    assert behavior["last_ramen_date"] == "2026-07-03"


def test_behavior_summary_without_reports():
    assert derive_behavior([DailyVitalRecord(date="2026-07-01")]) == {"reported_days": 0}


def test_analyze_attaches_stats_and_behavior():
    result = analyze(
        "2026-07-15",
        _series(),
        make_fake_invoker(lambda m, p, s: "要約"),
        "fake-model",
    )
    assert {s["metric"] for s in result.metric_stats} == set(VITAL_FIELDS)
    assert result.behavior["reported_days"] == 14
