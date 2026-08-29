"""リカバリー提案の採点（LLM-as-Judge）。"""

from sleeptech.llm.judge import PASS_SCORE, JudgeScore, parse_scores


def _payload(**kwargs) -> str:
    values = {"relevance": 4, "actionability": 4, "time_fit": 4}
    values.update(kwargs)
    import json

    return json.dumps(values, ensure_ascii=False)


def test_total_is_a_weighted_percentage():
    """全軸4なら80、全軸3なら60。閾値はその間にある。"""
    assert JudgeScore(4, 4, 4).total == 80
    assert JudgeScore(3, 3, 3).total == 60
    assert JudgeScore(5, 5, 5).total == 100
    assert 60 < PASS_SCORE <= 80


def test_time_fit_carries_the_most_weight():
    """時刻の妥当性を最重視する。1点の差が他の軸より大きく効く。"""
    from sleeptech.llm.judge import WEIGHTS

    assert WEIGHTS["time_fit"] == max(WEIGHTS.values())
    assert "tone" not in WEIGHTS
    assert sum(WEIGHTS.values()) == 1.0
    base = JudgeScore(3, 3, 3).total
    assert JudgeScore(3, 3, 4).total - base > JudgeScore(4, 3, 3).total - base
    assert JudgeScore(3, 3, 4).total - base > JudgeScore(3, 4, 3).total - base


def test_any_zero_axis_makes_the_candidate_ineligible():
    """1軸でも0なら採用対象から外す。"""
    assert JudgeScore(4, 4, 4).eligible is True
    assert JudgeScore(0, 5, 5).eligible is False
    assert JudgeScore(5, 5, 0).eligible is False


def test_parses_plain_json():
    score = parse_scores(_payload())
    assert score is not None
    assert score.relevance == 4


def test_parses_json_inside_a_code_fence_and_prose():
    text = "採点しました。\n```json\n" + _payload(time_fit=5) + "\n```\n以上です。"

    score = parse_scores(text)

    assert score is not None
    assert score.time_fit == 5


def test_coerces_string_and_float_scores():
    score = parse_scores('{"relevance":"3","actionability":3.0,"time_fit":3}')

    assert score is not None
    assert score.relevance == 3
    assert score.actionability == 3


def test_rejects_missing_out_of_range_and_non_json():
    assert parse_scores('{"relevance":4,"actionability":4}') is None
    assert parse_scores(_payload(time_fit=6)) is None
    assert parse_scores(_payload(relevance=-1)) is None
    assert parse_scores("採点できません") is None
    assert parse_scores("") is None
    assert parse_scores('{"relevance":3.5,"actionability":4,"time_fit":4}') is None


def test_reason_is_kept_short():
    score = parse_scores(_payload(reason="あ" * 200))

    assert score is not None
    assert len(score.reason) <= 60


def test_deterministic_fallback_does_not_clear_the_bar_by_itself():
    """定型文の水準（全軸3相当）では閾値に届かない。

    閾値が「定型文より明確に良い」を意味することを固定する。
    """
    assert JudgeScore(3, 3, 3).total < PASS_SCORE
