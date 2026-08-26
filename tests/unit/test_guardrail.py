"""ガードレール検証（仕様 第8節）。最優先のテスト群。"""

import pytest

from sleeptech.guardrail import validator as v


FACTS = v.Facts(times=[23.5], percents=[8])
OK_TEXT = "今夜は23:30までに寝れば、明日はベスト比 -8% 以内の見込みです（目安）。"


def test_clean_text_passes():
    assert v.validate(OK_TEXT, FACTS).passed is True


@pytest.mark.parametrize(
    "text",
    [
        "この症状は睡眠時無呼吸症候群と診断できます。23:30までに寝ましょう。-8%（目安）。",
        "治療のために23:30に就寝してください。-8%（目安）。",
        "あなたは病気です。23:30に寝れば-8%です（目安）。",
    ],
)
def test_medical_assertions_are_rejected(text):
    """医学的断定を弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "medical"


@pytest.mark.parametrize(
    "text",
    [
        "今寝ないと取り返しがつかないことになります。23:30 -8%（目安）。",
        "このままでは寿命が縮みます。23:30 -8%（目安）。",
        "手遅れになる前に23:30に寝てください。-8%（目安）。",
    ],
)
def test_fear_mongering_is_rejected(text):
    """過度な不安煽りを弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "fear"


@pytest.mark.parametrize(
    "text",
    [
        "23:30に寝れば必ず-8%に収まります。",
        "23:30に寝ないと確実に-8%落ちます。",
    ],
)
def test_definitive_claims_are_rejected(text):
    """断定表現（必ず・確実に）を弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "definitive"


def test_personal_attacks_are_rejected():
    """人格否定を弾く。"""
    got = v.validate("だらしないあなたは23:30に寝るべきです。-8%（目安）。", FACTS)
    assert got.passed is False
    assert got.reason == "attack"


def test_missing_time_is_rejected():
    """確定値の時刻が本文に現れなければ不通過。"""
    got = v.validate("早めに寝ましょう。明日は-8%の見込みです（目安）。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_missing_percent_is_rejected():
    got = v.validate("今夜は23:30までに寝ましょう。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_fabricated_number_is_rejected():
    """指示していない時刻や % が現れたら不通過（捏造）。"""
    got = v.validate("23:30に寝れば-8%。ちなみに25:00なら-40%です（目安）。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


@pytest.mark.parametrize(
    "text",
    [
        "今夜は23時30分までに寝れば-8%以内の見込みです（目安）。",
        "今夜は２３：３０までに寝れば-8%以内の見込みです（目安）。",
        "今夜は午後11時半までに寝れば-8%以内の見込みです（目安）。",
    ],
)
def test_time_notation_variants_are_accepted(text):
    """表記ゆれを正規化して照合するので通過する。"""
    assert v.validate(text, FACTS).passed is True


def test_normalize_converts_fullwidth_digits():
    assert "23:30" in v.normalize("２３：３０")


def test_empty_text_is_rejected():
    got = v.validate("", FACTS)
    assert got.passed is False


def test_percent_requires_estimate_note():
    facts = v.Facts(times=[23.5], percents=[8])
    got = v.validate("23:30なら-8%です。", facts)
    assert got.passed is False
    assert got.reason == "note"


def test_facts_with_no_percent_only_checks_times():
    """% を指示していない場合は時刻だけ検査する。"""
    facts = v.Facts(times=[7.5], percents=[])
    assert v.validate("7:30に屋外光を浴びましょう。", facts).passed is True


def test_allowed_percents_permit_additional_values():
    """必須は谷の1つだけ、他の時間帯の % は許可値として通す。"""
    facts = v.Facts(times=[], percents=[19], allowed_percents=[6, 19, 11])
    text = "10時台は-6%、14時台は-19%、17時台は-11%の見込みです（目安）。"
    assert v.validate(text, facts).passed is True


def test_allowed_percents_still_reject_unlisted_values():
    """許可リストに無い % は捏造として弾く。"""
    facts = v.Facts(times=[], percents=[19], allowed_percents=[6, 19, 11])
    text = "14時台は-19%、ちなみに深夜は-55%です（目安）。"
    assert v.validate(text, facts).passed is False
