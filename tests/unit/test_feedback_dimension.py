"""夜に尋ねるコンディション領域の選択。"""

from sleeptech.memory.schemas import Feedback
from sleeptech.model.feedback_dimension import select_feedback_dimension


def test_selects_a_least_answered_dimension():
    history = [
        Feedback(date="2026-08-25", score=4, dimension="focus"),
        Feedback(date="2026-08-24", score=3, dimension="focus"),
        Feedback(date="2026-08-23", score=5, dimension="sleepiness"),
    ]

    selected = select_feedback_dimension(history, "2026-08-26")

    assert selected in {"mood", "physical_fatigue"}


def test_legacy_feedback_does_not_bias_domain_rotation():
    history = [Feedback(date="2026-08-25", score=4)]

    selected = select_feedback_dimension(history, "2026-08-26")

    assert selected in {"focus", "sleepiness", "mood", "physical_fatigue"}
