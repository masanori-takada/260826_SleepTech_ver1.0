"""夜に尋ねる5段階評価の領域を決定論的に選ぶ。"""

from datetime import date as date_cls

from sleeptech.memory.schemas import Feedback


DIMENSIONS = ("focus", "sleepiness", "mood", "physical_fatigue")


def select_feedback_dimension(history: list[Feedback], target_date: str) -> str:
    """回答数が最少の領域から、日付で順番を回して1つ選ぶ。"""
    counts = {dimension: 0 for dimension in DIMENSIONS}
    for feedback in history:
        if feedback.dimension in counts:
            counts[feedback.dimension] += 1
    minimum = min(counts.values())
    candidates = [dimension for dimension in DIMENSIONS if counts[dimension] == minimum]
    index = date_cls.fromisoformat(target_date).toordinal() % len(candidates)
    return candidates[index]
