"""prompts.py の指示とガードレール（validator.py）の整合性テスト。

LLM が prompts.py の指示に素直に従って書いた文面が、実際に
validator.validate() を通ることを確認する。ここが壊れると本番で
無言のままテンプレート文にフォールバックし続けることになる。
"""

from __future__ import annotations

import re

from sleeptech.guardrail.validator import Facts, validate
from sleeptech.llm.prompts import (
    MORNING_SYSTEM,
    NIGHT_SYSTEM,
    morning_user,
    night_user,
)
from sleeptech.memory.schemas import (
    Baseline,
    BedtimeAdvice,
    DailyVital,
    HourlyDrop,
    RecoveryPlan,
)

_NOTE = "（睡眠モデルと主観評価による推定の目安）"

# validator.py の禁止パターンをテスト側で複製したもの（非公開名は import しない）。
# validator.py を変更した場合はここも合わせて更新すること。
_FEAR_WORDS = re.compile(r"取り返しがつかない|寿命が縮|手遅れ|命に関わ|危険な状態")
_MEDICAL_WORDS = re.compile(
    r"診断(?:できます|します|です)|(?:不眠症|睡眠時無呼吸症候群|病気)です|"
    r"(?:治療|処方)(?:のために|してください|が必要|を受けて|指示)"
)
_ATTACK_WORDS = re.compile(
    r"だらしない|意志が弱い|情けない|怠惰|ダメな人|無能|クズ|ゴミ|バカ|死ね|人間失格|"
    r"くたばれ|消えろ|生きる価値(?:が)?ない|一生治らない|デブ|気持ち悪い|アホ"
)


def _assert_no_forbidden_words(text: str) -> None:
    assert not _FEAR_WORDS.search(text), f"恐怖を煽る禁止語を含む: {text!r}"
    assert not _MEDICAL_WORDS.search(text), f"医療断定の禁止語を含む: {text!r}"
    assert not _ATTACK_WORDS.search(text), f"人格否定の禁止語を含む: {text!r}"


def _baseline(is_default: bool = False) -> Baseline:
    return Baseline(wake_h=7.0, sleep_h=23.0, days_used=14, is_default=is_default)


def _advice(status: str, bedtime_h: float = 23.5) -> BedtimeAdvice:
    return BedtimeAdvice(
        bedtime_h=bedtime_h,
        mean_drop_pct=12,
        curve=[HourlyDrop(hour=14, focus_pct=15, mood_pct=10)],
        status=status,
    )


def test_night_prompt_protects_wake_and_target_sleep_values():
    prompt = night_user(_advice("ok"), Baseline(wake_h=32.6666667, sleep_h=5.4, target_sleep_h=7.0), 0.0)
    assert "起床目安: 8:40" in prompt
    assert "目標睡眠時間: 7.0時間" in prompt


def test_night_user_all_statuses_have_no_forbidden_words():
    """night_user() が生成する固定文言（status_note を含む）に禁止語がない。"""
    for status in ("ok", "unreachable", "too_late"):
        text = night_user(_advice(status), _baseline(), debt_h=2.0)
        _assert_no_forbidden_words(text)


def test_night_user_default_baseline_note_has_no_forbidden_words():
    """履歴不足の注意書きを含む場合も禁止語がない。"""
    text = night_user(_advice("ok"), _baseline(is_default=True), debt_h=1.0)
    _assert_no_forbidden_words(text)


def test_morning_user_with_and_without_caffeine_has_no_forbidden_words():
    """morning_user() のカフェインあり／なし両方の文言に禁止語がない。"""
    curve = [HourlyDrop(hour=14, focus_pct=15, mood_pct=10)]
    vital = DailyVital(date="2026-08-26", total_sleep_min=390, deep_sleep_min=60, awakenings=1)

    with_caffeine = RecoveryPlan(
        caffeine_times_h=[13.0],
        caffeine_cutoff_h=14.0,
        light_morning_h=7.5,
        light_avoid_after_h=21.0,
    )
    without_caffeine = RecoveryPlan(
        caffeine_times_h=[],
        caffeine_cutoff_h=14.0,
        light_morning_h=7.5,
        light_avoid_after_h=21.0,
    )

    for plan in (with_caffeine, without_caffeine):
        text = morning_user(curve, plan, vital)
        _assert_no_forbidden_words(text)


def test_system_prompts_have_no_forbidden_words():
    """NIGHT_SYSTEM / MORNING_SYSTEM（共通ルール含む）に禁止語がない。"""
    _assert_no_forbidden_words(NIGHT_SYSTEM)
    _assert_no_forbidden_words(MORNING_SYSTEM)


def test_night_user_does_not_offer_forbidden_success_phrase():
    text = night_user(_advice("ok"), _baseline(), 0.0, 3, 13, "low", 2)
    assert "目標を満たせます" not in text
    assert "指定睡眠時間を確保できる時刻" in text


def test_night_and_morning_prompt_lengths_match_required_output():
    assert "夜" in NIGHT_SYSTEM and "400文字以内" in NIGHT_SYSTEM
    assert "朝" in MORNING_SYSTEM and "600文字以内" in MORNING_SYSTEM


def test_night_flow_normal_bedtime_passes():
    """夜フロー・status=ok・bedtime_h < 24.0 の文面がガードレールを通る。"""
    text = (
        "今夜は23:30までに寝るのがおすすめです。"
        "この時刻までに寝れば明日の目標を満たせます。"
        f"明日の主要時間帯の平均低下率は目安{_NOTE}として-12%程度です。"
    )
    facts = Facts(times=[23.5], percents=[12], require_day_prefix=True)
    result = validate(text, facts)
    assert result.passed is False
    assert result.reason == "forbidden_display"


def test_night_flow_next_day_bedtime_passes():
    """夜フロー・bedtime_h >= 24.0（日跨ぎ）の文面が『翌』付きで通る。"""
    text = (
        "翌 1:30までに寝れば、明日の目標を満たせます。"
        f"明日の主要時間帯の平均低下率は目安{_NOTE}として-18%程度です。"
        "早めの休息を心がけましょう。"
    )
    facts = Facts(times=[25.5], percents=[18], require_day_prefix=True)
    result = validate(text, facts)
    assert result.passed is False
    assert result.reason == "forbidden_display"


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


def test_night_flow_too_late_next_day_bedtime_passes():
    """夜フロー・status=too_late・bedtime_h >= 24.0 の指示に従った文面が通る。

    修正前は status_note に禁止語「手遅れ」が含まれており、この指示に
    素直に従った文面は reason="fear" で弾かれていた（Task 10 再レビュー指摘）。
    """
    text = (
        "翌 0:30が今日の目標達成が難しい時間帯です。とにかく今すぐ横になってください。"
        f"低下率の目安{_NOTE}は-12%です。"
    )
    facts = Facts(times=[24.5], percents=[12], require_day_prefix=True)
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


def test_system_prompts_require_range_endpoints_and_confidence():
    assert "範囲の両端" in NIGHT_SYSTEM
    assert "範囲の両端" in MORNING_SYSTEM
    assert "信頼度" in NIGHT_SYSTEM
    assert "信頼度" in MORNING_SYSTEM
