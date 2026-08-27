"""ワークフローから補正履歴を組み立てる処理。"""

import math
from numbers import Real

from sleeptech.model.calibration import (
    MAX_FEEDBACK,
    Calibration,
    confidence_for,
    fit_offset,
)


def _is_finite_real(value: object) -> bool:
    """boolを除く有限な実数か判定する。"""
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def load_calibration(memory, sleep_days: int) -> Calibration:
    """対応するMORNING予測があるフィードバックだけで補正を算出する。"""
    pairs = []
    for feedback in memory.get_feedback_desc(MAX_FEEDBACK):
        prediction = memory.get_prediction(feedback.effective_prediction_date, "MORNING")
        if prediction is None:
            continue
        predicted = (
            prediction.calibrated_mean_drop_pct
            if prediction.calibrated_mean_drop_pct is not None
            else prediction.mean_drop_pct
        )
        if not _is_finite_real(feedback.score) or feedback.score not in (1, 2, 3, 4, 5):
            continue
        if not _is_finite_real(predicted) or not 0 <= predicted <= 100:
            continue
        pairs.append((feedback.score, float(predicted)))
    confidence, margin = confidence_for(sleep_days, len(pairs))
    return Calibration(fit_offset(pairs), len(pairs), confidence, margin)
