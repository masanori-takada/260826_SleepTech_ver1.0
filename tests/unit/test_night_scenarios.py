"""夜LINEで比較する就寝シナリオ。"""

from sleeptech.memory.schemas import Baseline
from sleeptech.model import two_process as tp
from sleeptech.model.night_scenarios import build_scenarios, render_night_scenarios


BASELINE = Baseline(
    wake_h=31.0,
    sleep_h=7.5,
    target_sleep_h=7.5,
    rem_min=90,
    deep_min=60,
    days_used=14,
)


def test_builds_recommended_thirty_and_sixty_minute_scenarios():
    advice = tp.recommend_bedtime(22.5, BASELINE, debt_h=0.0)

    scenarios = build_scenarios(advice, BASELINE, debt_h=0.0, offset_pct=0.0, margin_pct=5)

    assert [item.delay_min for item in scenarios] == [0, 30, 60]
    assert scenarios[1].bedtime_h == scenarios[0].bedtime_h + 0.5
    assert scenarios[2].bedtime_h == scenarios[0].bedtime_h + 1.0
    assert all(item.range_low_pct <= item.range_high_pct for item in scenarios)


def test_too_late_only_shows_sleep_now_and_never_recommends_a_future_delay():
    advice = tp.recommend_bedtime(28.0, BASELINE, debt_h=0.0)

    scenarios = build_scenarios(advice, BASELINE, debt_h=0.0, offset_pct=0.0, margin_pct=5)
    text = render_night_scenarios(scenarios, BASELINE)

    assert advice.status == "too_late"
    assert len(scenarios) == 1
    assert "今すぐ就寝" in text
    assert "30分遅れ" not in text
    assert "60分遅れ" not in text


def test_rendered_comparison_explains_that_percent_is_an_estimate_not_measured_ability():
    advice = tp.recommend_bedtime(22.5, BASELINE, debt_h=0.0)
    scenarios = build_scenarios(advice, BASELINE, debt_h=0.0, offset_pct=0.0, margin_pct=5)

    text = render_night_scenarios(scenarios, BASELINE)

    assert "推奨" in text
    assert "30分遅れ" in text
    assert "60分遅れ" in text
    assert "実測の仕事能力ではありません" in text
    assert "中途覚醒・HRV・心拍数は現行の%計算には使っていません" not in text
