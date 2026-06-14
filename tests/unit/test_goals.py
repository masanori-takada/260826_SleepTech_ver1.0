"""目標評価・トーン決定テスト（T026 / FR-005,006,017）。"""

from __future__ import annotations

from night_ramen.goals.evaluator import choose_tone, evaluate
from night_ramen.memory.schemas import DailyVitalRecord, Goal, Tone


def _vitals(flags):
    """flags: list[bool] を date 付き DailyVitalRecord 群へ。True=ラーメン有。"""
    out = []
    for i, f in enumerate(flags):
        out.append(DailyVitalRecord(date=f"2026-06-{i+1:02d}", ramen_last_night=f))
    return out


def test_success_rate_computed():
    goal = Goal(success_rate_target=0.85, weight_loss_target_kg=2.0)
    # 4日中3日我慢成功 → 0.75
    progress = evaluate(goal, _vitals([False, False, False, True]))
    assert progress.success_rate == 0.75
    assert progress.days_evaluated == 4


def test_high_achievement_yields_praise():
    goal = Goal(success_rate_target=0.5)
    progress = evaluate(goal, _vitals([False, False, False, False]))  # 100%
    assert choose_tone(progress) == Tone.PRAISE


def test_large_shortfall_yields_strong_warning():
    goal = Goal(success_rate_target=0.85)
    progress = evaluate(goal, _vitals([True, True, True, False]))  # 0.25 / 0.85 ≈ 0.29
    assert choose_tone(progress) == Tone.STRONG_WARNING


def test_no_reports_yields_neutral():
    goal = Goal()
    progress = evaluate(goal, [DailyVitalRecord(date="2026-06-01")])  # 申告なし
    assert choose_tone(progress) == Tone.NEUTRAL


def test_default_goal_values():
    # 既定目標 85% / 2kg（FR-017）
    goal = Goal()
    assert goal.success_rate_target == 0.85
    assert goal.weight_loss_target_kg == 2.0
