"""フィードバック履歴から予測値を補正する純粋関数。"""

from dataclasses import dataclass


SCORE_DROP_PCT = {1: 30.0, 2: 22.5, 3: 15.0, 4: 7.5, 5: 0.0}
MAX_FEEDBACK = 30
RECENCY = 0.9
SHRINK_K = 7.0
MAX_OFFSET = 15.0


@dataclass(frozen=True)
class Calibration:
    """補正値と、その信頼度表示に必要な情報。"""

    offset_pct: float
    feedback_count: int
    confidence: str
    margin_pct: int


def score_to_drop_pct(score: int) -> float:
    """5段階評価を代理低下率へ変換する。"""
    return SCORE_DROP_PCT[score]


def fit_offset(pairs: list[tuple[int, float]]) -> float:
    """直近履歴から、縮小と上下限を適用した補正値を算出する。"""
    selected = pairs[:MAX_FEEDBACK]
    if not selected:
        return 0.0
    weighted = [
        (score_to_drop_pct(score) - predicted) * (RECENCY ** i)
        for i, (score, predicted) in enumerate(selected)
    ]
    weights = [RECENCY ** i for i in range(len(selected))]
    shrunk = (sum(weighted) / sum(weights)) * len(selected) / (len(selected) + SHRINK_K)
    return round(max(-MAX_OFFSET, min(MAX_OFFSET, shrunk)), 3)


def confidence_for(sleep_days: int, feedback_count: int) -> tuple[str, int]:
    """睡眠履歴とフィードバック数から信頼度と表示マージンを返す。"""
    if sleep_days >= 14 and feedback_count >= 30:
        return "high", 2
    if sleep_days >= 7 and feedback_count >= 7:
        return "medium", 3
    return "low", 5


def apply_offset(value: float, offset_pct: float) -> int:
    """予測値へ補正を加え、0〜100に収めて整数化する。"""
    return round(max(0.0, min(100.0, value + offset_pct)))


def display_range(value: int, margin_pct: int) -> tuple[int, int]:
    """整数予測値の表示範囲を0〜100に収める。"""
    return max(0, value - margin_pct), min(100, value + margin_pct)
