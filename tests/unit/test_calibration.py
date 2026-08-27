import pytest

from sleeptech.model.calibration import (
    apply_offset,
    confidence_for,
    display_range,
    fit_offset,
    score_to_drop_pct,
)


@pytest.mark.parametrize("score,want", [(5, 0), (4, 7.5), (3, 15), (2, 22.5), (1, 30)])
def test_score_mapping(score, want):
    assert score_to_drop_pct(score) == want


def test_one_feedback_is_shrunk_but_nonzero():
    assert fit_offset([(1, 5.0)]) == pytest.approx(3.125, abs=0.001)


def test_offset_is_capped_both_directions():
    assert fit_offset([(1, 0.0)] * 30) == 15.0
    assert fit_offset([(5, 100.0)] * 30) == -15.0


@pytest.mark.parametrize(
    "sleep_days,count,want",
    [(6, 30, ("low", 5)), (14, 2, ("low", 5)),
     (14, 7, ("medium", 3)), (14, 30, ("high", 2))],
)
def test_confidence(sleep_days, count, want):
    assert confidence_for(sleep_days, count) == want


def test_display_range_clamps_to_zero_and_hundred():
    assert display_range(3, 5) == (0, 8)
    assert display_range(98, 5) == (93, 100)
