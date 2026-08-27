import pytest

from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import Feedback, Prediction
from sleeptech.workflow._calibration import load_calibration


def test_load_calibration_uses_only_matching_morning_predictions():
    memory = MemoryClient(InMemoryBackend())
    memory.save_prediction(Prediction(date="2026-08-26", slot="MORNING", mean_drop_pct=5))
    memory.save_feedback(Feedback(
        date="2026-08-27", evaluated_date="2026-08-27",
        prediction_date="2026-08-26", score=1,
    ))
    got = load_calibration(memory, sleep_days=14)
    assert got.feedback_count == 1
    assert got.offset_pct == pytest.approx(3.125, abs=0.001)
    assert got.confidence == "low"


def test_load_calibration_ignores_missing_prediction():
    memory = MemoryClient(InMemoryBackend())
    memory.save_feedback(Feedback(date="2026-08-27", score=1))
    assert load_calibration(memory, sleep_days=14).feedback_count == 0
