"""prompts.py の指示とガードレール（validator.py）の整合性テスト。

LLM が prompts.py の指示に素直に従って書いた文面が、実際に
validator.validate() を通ることを確認する。ここが壊れると本番で
無言のままテンプレート文にフォールバックし続けることになる。
"""

from __future__ import annotations

from sleeptech.guardrail.validator import Facts, validate

_NOTE = "（あなたの平常時を100%とした推定の目安）"


def test_night_flow_normal_bedtime_passes():
    """夜フロー・status=ok・bedtime_h < 24.0 の文面がガードレールを通る。"""
    text = (
        "今夜は23:30までに寝るのがおすすめです。"
        "この時刻までに寝れば明日の目標を満たせます。"
        f"明日の主要時間帯の平均低下率は目安{_NOTE}として-12%程度です。"
    )
    facts = Facts(times=[23.5], percents=[12], require_day_prefix=True)
    result = validate(text, facts)
    assert result.passed is True, result.reason


def test_night_flow_next_day_bedtime_passes():
    """夜フロー・bedtime_h >= 24.0（日跨ぎ）の文面が『翌』付きで通る。"""
    text = (
        "翌 1:30までに寝れば、明日の目標を満たせます。"
        f"明日の主要時間帯の平均低下率は目安{_NOTE}として-18%程度です。"
        "早めの休息を心がけましょう。"
    )
    facts = Facts(times=[25.5], percents=[18], require_day_prefix=True)
    result = validate(text, facts)
    assert result.passed is True, result.reason


def test_night_flow_next_day_bedtime_far_passes():
    """夜フロー・bedtime_h = 32.0（翌 8:00）の文面が通る。"""
    text = (
        "翌 8:00までに寝れば、目標に近づけます。"
        f"低下率の目安{_NOTE}は-9%です。"
    )
    facts = Facts(times=[32.0], percents=[9], require_day_prefix=True)
    result = validate(text, facts)
    assert result.passed is True, result.reason


def test_morning_flow_multiple_hours_passes():
    """朝フロー・複数時間帯の低下率を列挙した文面が通る。"""
    text = (
        "昨夜の睡眠は6時間30分でした。"
        "今日の14時台は集中力-15% / 気分-10%、"
        f"18時台は集中力-20% / 気分-14%が目安です{_NOTE}。"
        "カフェインは13:00までに、避光は21:00から始めましょう。"
    )
    facts = Facts(
        times=[13.0, 21.0],
        percents=[15, 10, 20, 14],
        allowed_percents=[15, 10, 20, 14],
        allowed_times=[13.0, 21.0],
    )
    result = validate(text, facts)
    assert result.passed is True, result.reason


def test_unsigned_percent_is_rejected():
    """符号なしの 12% は曖昧表現としてガードレールに弾かれる。"""
    text = (
        "23:30までに寝るのがおすすめです。"
        f"明日の主要時間帯の平均低下率は目安{_NOTE}として12%程度です。"
    )
    facts = Facts(times=[23.5], percents=[12], require_day_prefix=True)
    result = validate(text, facts)
    assert result.passed is False
    assert result.reason == "numbers"
