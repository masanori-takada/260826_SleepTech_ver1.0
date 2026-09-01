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


def test_builds_recommended_one_hour_and_two_hour_scenarios():
    advice = tp.recommend_bedtime(22.5, BASELINE, debt_h=0.0)

    scenarios = build_scenarios(advice, BASELINE, debt_h=0.0, offset_pct=0.0, margin_pct=5)

    assert [item.delay_min for item in scenarios] == [0, 60, 120]
    assert scenarios[1].bedtime_h == scenarios[0].bedtime_h + 1.0
    assert scenarios[2].bedtime_h == scenarios[0].bedtime_h + 2.0
    assert all(item.range_low_pct <= item.range_high_pct for item in scenarios)


def test_too_late_only_shows_sleep_now_and_never_recommends_a_future_delay():
    advice = tp.recommend_bedtime(28.0, BASELINE, debt_h=0.0)

    scenarios = build_scenarios(advice, BASELINE, debt_h=0.0, offset_pct=0.0, margin_pct=5)
    text = render_night_scenarios(scenarios, BASELINE)

    assert advice.status == "too_late"
    assert len(scenarios) == 1
    assert "今すぐ就寝" in text
    assert "1時間遅れ" not in text
    assert "2時間遅れ" not in text


def test_rendered_comparison_explains_that_percent_is_an_estimate_not_measured_ability():
    advice = tp.recommend_bedtime(22.5, BASELINE, debt_h=0.0)
    scenarios = build_scenarios(advice, BASELINE, debt_h=0.0, offset_pct=0.0, margin_pct=5)

    text = render_night_scenarios(scenarios, BASELINE)

    assert "推奨" in text
    assert "1時間遅れ" in text
    assert "2時間遅れ" in text
    assert "実測の仕事能力ではありません" not in text
    assert "中途覚醒・HRV・心拍数は現行の%計算には使っていません" not in text


def test_repayment_moves_the_recommended_bedtime_earlier():
    """睡眠負債があれば推奨就寝時刻が前倒しになる。

    返済分を目標睡眠へ上乗せしないと、推奨は常に「起床 - 平常時の目標睡眠」
    に張り付き、負債があっても本文が変わらない（実機で8日連続同じ時刻が出た）。
    """
    debt_h = 3.0
    target_h = tp.tonight_target_sleep_h(BASELINE, debt_h)

    plain = tp.recommend_bedtime(22.5, BASELINE, debt_h)
    repaying = tp.recommend_bedtime(22.5, BASELINE, debt_h, target_sleep_h=target_h)

    assert target_h > BASELINE.effective_target_sleep_h
    assert repaying.bedtime_h < plain.bedtime_h


def test_repayment_is_capped_and_ignored_when_it_is_too_small():
    """一晩で返すのは負債の一部だけ。ごく小さい上乗せは切り捨てる。"""
    assert tp.debt_repayment_h(0.0) == 0.0
    assert tp.debt_repayment_h(0.3) == 0.0          # 6分の前倒しは表示が動かない
    assert tp.debt_repayment_h(3.0) == 1.0
    assert tp.debt_repayment_h(12.0) == 1.0         # 上限で頭打ち


def test_rendered_body_shows_the_debt_and_what_is_repaid_tonight():
    """なぜ今夜だけ推奨が早いのかが本文から読み取れる。"""
    debt_h = 3.0
    target_h = tp.tonight_target_sleep_h(BASELINE, debt_h)
    advice = tp.recommend_bedtime(22.5, BASELINE, debt_h, target_sleep_h=target_h)
    scenarios = build_scenarios(advice, BASELINE, debt_h, offset_pct=0.0, margin_pct=5)

    text = render_night_scenarios(
        scenarios, BASELINE, debt_h=debt_h,
        repay_h=target_h - BASELINE.effective_target_sleep_h,
    )

    assert "睡眠負債：3.0時間" in text
    assert "今夜 1.0時間 返す" in text
    assert "目標睡眠：8.5時間" in text


def test_rendered_body_keeps_the_normal_target_when_there_is_no_debt():
    advice = tp.recommend_bedtime(22.5, BASELINE, debt_h=0.0)
    scenarios = build_scenarios(advice, BASELINE, debt_h=0.0, offset_pct=0.0, margin_pct=5)

    text = render_night_scenarios(scenarios, BASELINE, debt_h=0.0, repay_h=0.0)

    assert "睡眠負債：0.0時間" in text
    assert "目標睡眠：7.5時間" in text
    assert "返す" not in text
