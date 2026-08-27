"""ガードレール検証（仕様 第8節）。最優先のテスト群。"""

import pytest

from sleeptech.guardrail import validator as v


FACTS = v.Facts(times=[23.5], percents=[8])
NOTE = "（あなたの平常時を100%とした推定の目安）"
OK_TEXT = f"今夜は23:30までに寝れば、明日はベスト比 -8% 以内の見込みです{NOTE}。"


def test_clean_text_passes():
    assert v.validate(OK_TEXT, FACTS).passed is True


@pytest.mark.parametrize("text", [
    f"この症状は睡眠時無呼吸症候群と診断できます。23:30までに寝ましょう。-8%{NOTE}。",
    f"治療のために23:30に就寝してください。-8%{NOTE}。",
    f"あなたは病気です。23:30に寝れば-8%です{NOTE}。",
])
def test_medical_assertions_are_rejected(text):
    """医学的断定を弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "medical"


@pytest.mark.parametrize("text", [
    f"今寝ないと取り返しがつかないことになります。23:30 -8%{NOTE}。",
    f"このままでは寿命が縮みます。23:30 -8%{NOTE}。",
    f"手遅れになる前に23:30に寝てください。-8%{NOTE}。",
])
def test_fear_mongering_is_rejected(text):
    """過度な不安煽りを弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "fear"


@pytest.mark.parametrize("text", ["23:30に寝れば必ず-8%に収まります。", "23:30に寝ないと確実に-8%落ちます。"])
def test_definitive_claims_are_rejected(text):
    """断定表現（必ず・確実に）を弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "definitive"


def test_personal_attacks_are_rejected():
    """人格否定を弾く。"""
    got = v.validate(f"だらしないあなたは23:30に寝るべきです。-8%{NOTE}。", FACTS)
    assert got.passed is False
    assert got.reason == "attack"


@pytest.mark.parametrize(
    "word",
    [
        "無能",
        "クズ",
        "ゴミ",
        "バカ",
        "死ね",
        "人間失格",
        "くたばれ",
        "消えろ",
        "生きる価値がない",
        "生きる価値ない",
        "一生治らない",
        "デブ",
        "気持ち悪い",
        "アホ",
    ],
)
def test_transferred_personal_attacks_are_rejected(word):
    got = v.validate(f"あなたは{word}。23:30に寝るべきです。-8%{NOTE}。", FACTS)
    assert got.passed is False
    assert got.reason == "attack"


def test_missing_time_is_rejected():
    """確定値の時刻が本文に現れなければ不通過。"""
    got = v.validate(f"早めに寝ましょう。明日は-8%の見込みです{NOTE}。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_missing_percent_is_rejected():
    got = v.validate("今夜は23:30までに寝ましょう。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_fabricated_number_is_rejected():
    """指示していない時刻や % が現れたら不通過（捏造）。"""
    got = v.validate(f"23:30に寝れば-8%。ちなみに25:00なら-40%です{NOTE}。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


@pytest.mark.parametrize("text", [
    f"今夜は23時30分までに寝れば-8%以内の見込みです{NOTE}。",
    f"今夜は２３：３０までに寝れば-8%以内の見込みです{NOTE}。",
    f"今夜は午後11時半までに寝れば-8%以内の見込みです{NOTE}。",
])
def test_time_notation_variants_are_accepted(text):
    """表記ゆれを正規化して照合するので通過する。"""
    assert v.validate(text, FACTS).passed is True


def test_normalize_converts_fullwidth_digits():
    assert "23:30" in v.normalize("２３：３０")


def test_empty_text_is_rejected():
    got = v.validate("", FACTS)
    assert got.passed is False


def test_percent_requires_estimate_and_relative_definition():
    got = v.validate("23:30なら-8%です（目安）。", FACTS)
    assert got.passed is False
    assert got.reason == "definition"


@pytest.mark.parametrize("text", ["23:30なら+8%です（目安）。", "23:30なら＋8%です（目安）。"])
def test_explicit_positive_percent_is_rejected(text):
    """正の符号で低下率を反転させる表記を拒否する。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_unsigned_percent_with_explicit_drop_meaning_is_accepted():
    text = f"23:30なら8%低下の見込みです{NOTE}。"
    assert v.validate(text, FACTS).passed is True


def test_unsigned_percent_without_drop_meaning_is_rejected():
    got = v.validate(f"23:30なら8%です{NOTE}。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_facts_with_no_percent_only_checks_times():
    """% を指示していない場合は時刻だけ検査する。"""
    facts = v.Facts(times=[7.5], percents=[])
    assert v.validate("7:30に屋外光を浴びましょう。", facts).passed is True


def test_allowed_percents_permit_additional_values():
    """必須は谷の1つだけ、他の時間帯の % は許可値として通す。"""
    facts = v.Facts(times=[], percents=[19], allowed_percents=[6, 19, 11])
    text = f"10時台は-6%、14時台は-19%、17時台は-11%の見込みです{NOTE}。"
    assert v.validate(text, facts).passed is True


def test_allowed_percents_still_reject_unlisted_values():
    """許可リストに無い % は捏造として弾く。"""
    facts = v.Facts(times=[], percents=[19], allowed_percents=[6, 19, 11])
    text = f"14時台は-19%、ちなみに深夜は-55%です{NOTE}。"
    assert v.validate(text, facts).passed is False


def test_night_mode_requires_day_prefix_for_next_day_time():
    facts = v.Facts(times=[24.5], percents=[], require_day_prefix=True)
    assert v.validate("翌 0:30に就寝しましょう。", facts).passed is True
    assert v.validate("0:30に就寝しましょう。", facts).passed is False


def test_morning_mode_keeps_plain_time_matching_for_elapsed_hours():
    facts = v.Facts(times=[32.0], percents=[])
    assert v.validate("8:00に屋外光を浴びましょう。", facts).passed is True


@pytest.mark.parametrize("text", ["123:30", "23:300", "23:60"])
def test_invalid_colon_time_does_not_satisfy_required_time(text):
    facts = v.Facts(times=[23.5], percents=[])
    assert v.validate(f"{text}に寝ましょう。", facts).passed is False


@pytest.mark.parametrize("text", ["午前12時", "午前0時", "午後12時", "午後13時"])
def test_ampm_boundary_is_not_misnormalized(text):
    facts = v.Facts(times=[0.0], percents=[])
    got = v.validate(f"{text}に寝ましょう。", facts)
    if text == "午前12時":
        assert got.passed is True
    else:
        assert got.passed is False


def test_safe_medical_referral_is_allowed():
    text = "睡眠時無呼吸症候群が心配なら医師に相談を。"
    assert v.validate(text, v.Facts()).passed is True


@pytest.mark.parametrize("text", ["不眠症です。", "この症状は睡眠時無呼吸症候群と診断できます。"])
def test_personal_medical_diagnosis_is_rejected(text):
    got = v.validate(text, v.Facts())
    assert got.passed is False
    assert got.reason == "medical"


def test_plain_100_percent_without_relative_definition_is_rejected():
    got = v.validate(f"23:30なら-8%です{NOTE}。100%です。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_range_endpoints_are_required_and_extra_percent_is_rejected():
    facts = v.Facts(times=[25.5], percents=[3, 13], allowed_percents=[3, 13])
    assert v.validate("翌 1:30、低下見込み3〜13%。推定の目安です", facts).passed
    assert not v.validate("翌 1:30、低下見込み3〜18%。推定の目安です", facts).passed


_NOTES = (
    "実測の仕事能力ではありません",
    "中途覚醒・HRV・心拍数は現行の%計算には使っていません",
)


def test_confidence_count_and_required_notes_are_required():
    facts = v.Facts(
        times=[25.5], percents=[3, 13], allowed_percents=[3, 13],
        confidence="low", feedback_count=2, required_notes=_NOTES,
    )
    text = "翌 1:30、低下見込み3〜13%。信頼度：低（評価データ2件）。推定の目安です。" + "。".join(_NOTES)
    assert v.validate(text, facts).passed


@pytest.mark.parametrize("bad", [
    "信頼度：中（評価データ2件）",
    "信頼度：低（評価データ3件）",
    "信頼度：低",
    "実測の仕事能力です",
    "中途覚醒・HRV・心拍数は現行の%計算に使っています",
])
def test_confidence_count_and_required_notes_changes_are_rejected(bad):
    facts = v.Facts(
        times=[25.5], percents=[3, 13], allowed_percents=[3, 13],
        confidence="low", feedback_count=2, required_notes=_NOTES,
    )
    text = "翌 1:30、低下見込み3〜13%。信頼度：低（評価データ2件）。推定の目安です。" + "。".join(_NOTES)
    if bad.startswith("信頼度"):
        text = text.replace("信頼度：低（評価データ2件）", bad)
    else:
        text = text.replace(_NOTES[0] if "仕事" in bad else _NOTES[1], bad)
    assert not v.validate(text, facts).passed


def test_night_range_order_is_required():
    facts = v.Facts(times=[25.5], required_ranges=[(3, 13)])
    assert v.validate("翌 1:30、低下見込み3〜13%。推定の目安です", facts).passed
    assert not v.validate("翌 1:30、低下見込み13〜3%。推定の目安です", facts).passed


@pytest.mark.parametrize("ranges", [
    [(7, (3, 13))],
    [(7, (13, 3)), (8, (4, 14))],
    [(7, (3, 13)), (8, (3, 13))],
    [(7, (3, 13)), (8, (4, 14)), (8, (4, 14))],
])
def test_hourly_range_mapping_requires_complete_unique_exact_pairs(ranges):
    facts = v.Facts(required_hourly_ranges=[(7, (3, 13)), (8, (4, 14))])
    text = " ".join(f"{hour}時台 低下見込み{low}〜{high}%" for hour, (low, high) in ranges)
    text += " 推定の目安です"
    assert not v.validate(text, facts).passed


def test_legacy_display_phrases_are_rejected():
    assert not v.validate("低下見込み3〜13%。推定の目安です。平常時100%", v.Facts()).passed
    assert not v.validate("低下見込み3〜13%。推定の目安です。目標を満たせます", v.Facts()).passed
