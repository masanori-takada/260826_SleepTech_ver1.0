"""生成文の検証（仕様 第8節）。

語句検査と確定値照合を行う。時刻は表記ゆれを正規化してから照合し、
LLM が確定値を改変・捏造していないことを確認する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_MEDICAL = re.compile(
    r"診断(?:できます|します|です)|(?:不眠症|睡眠時無呼吸症候群|病気)です|"
    r"(?:治療|処方)(?:のために|してください|が必要|を受けて|指示)"
)
_FEAR = re.compile(r"取り返しがつかない|寿命が縮|手遅れ|命に関わ|危険な状態")
_DEFINITIVE = re.compile(r"必ず|確実に|絶対に")
_ATTACK = re.compile(
    r"だらしない|意志が弱い|情けない|怠惰|ダメな人|無能|クズ|ゴミ|バカ|死ね|人間失格|"
    r"くたばれ|消えろ|生きる価値(?:が)?ない|一生治らない|デブ|気持ち悪い|アホ"
)

_TIME_RE = re.compile(r"(?<!\d)(?:(?P<day>翌)\s*)?(\d{1,2}):(\d{2})(?!\d)")
_PCT_RE = re.compile(r"(?P<sign>[+-]?)(?P<value>\d{1,3})%")
_RANGE_PCT_RE = re.compile(r"(?P<low>\d{1,3})\s*[〜～]\s*(?P<high>\d{1,3})%")
_HOURLY_RANGE_RE = re.compile(
    r"(?P<hour>\d{1,2})時台[^\d%]{0,20}"
    r"(?P<low>\d{1,3})\s*[〜～]\s*(?P<high>\d{1,3})%"
)
_CONFIDENCE_RE = re.compile(r"信頼度\s*[:：]\s*(?P<label>低|中|高)")
_COUNT_RE = re.compile(r"評価データ\s*(?P<count>\d+)件")
_FORBIDDEN_DISPLAY = re.compile(r"平常時100%|目標を満たせます")
REQUIRED_NOTES = (
    "実測の仕事能力ではありません",
    "中途覚醒・HRV・心拍数は現行の%計算には使っていません",
)
_RELATIVE_NOTE = re.compile(r"(?:本人|あなた)の平常時を\s*100%")
_FULLWIDTH = str.maketrans("０１２３４５６７８９：％－＋−", "0123456789:%-+-")


@dataclass(frozen=True)
class Facts:
    """本文に必須の確定値と、列挙を許可する確定値。

    require_day_prefix=True は夜フロー向けで、24時間を超える時刻を「翌 0:30」
    のように日付プレフィックス付きで照合する。Task 13 の夜フローはこの値を
    True にして日跨ぎ時刻の意味を保持する。朝フローは既定の False で、内部32.0
    を当日「8:00」として照合する。
    """

    times: list[float] = field(default_factory=list)
    percents: list[int] = field(default_factory=list)
    allowed_times: list[float] | None = None
    allowed_percents: list[int] | None = None
    require_day_prefix: bool = False
    confidence: str | None = None
    feedback_count: int | None = None
    required_notes: tuple[str, ...] = ()
    required_ranges: list[tuple[int, int]] | None = None
    required_hourly_ranges: list[tuple[int, tuple[int, int]]] | None = None


@dataclass(frozen=True)
class ValidationResult:
    """文面検証の結果。"""

    passed: bool
    reason: str = ""


def _ampm_to_time(match: re.Match[str]) -> str:
    """有効な午前・午後表記だけを24時間表記へ変換する。"""
    meridiem, hour_text, minute_text = match.groups()
    hour = int(hour_text)
    if not 1 <= hour <= 12:
        return match.group(0)
    converted = hour % 12 + (12 if meridiem == "午後" else 0)
    return f"{converted}:{int(minute_text or 0):02d}"


def _ampm_hour_to_time(match: re.Match[str]) -> str:
    """有効な午前・午後の時だけを24時間表記へ変換する。"""
    meridiem, hour_text = match.groups()
    hour = int(hour_text)
    if not 1 <= hour <= 12:
        return match.group(0)
    converted = hour % 12 + (12 if meridiem == "午後" else 0)
    return f"{converted}:00"


def _ampm_half_to_time(match: re.Match[str]) -> str:
    """有効な午前・午後の「半」を24時間表記へ変換する。"""
    meridiem, hour_text = match.groups()
    hour = int(hour_text)
    if not 1 <= hour <= 12:
        return match.group(0)
    converted = hour % 12 + (12 if meridiem == "午後" else 0)
    return f"{converted}:30"


def _japanese_to_time(match: re.Match[str]) -> str:
    """有効な日本語時刻だけをコロン表記へ変換する。"""
    hour, minute_text = int(match.group(1)), match.group(2)
    if not 0 <= hour <= 23:
        return match.group(0)
    minute = int(minute_text or 0)
    if not 0 <= minute <= 59:
        return match.group(0)
    return f"{hour}:{minute:02d}"


def _japanese_half_to_time(match: re.Match[str]) -> str:
    """有効な日本語の「時半」だけをコロン表記へ変換する。"""
    hour = int(match.group(1))
    return f"{hour}:30" if 0 <= hour <= 23 else match.group(0)


def _japanese_hour_to_time(match: re.Match[str]) -> str:
    """有効な日本語の時だけをコロン表記へ変換する。"""
    hour = int(match.group(1))
    return f"{hour}:00" if 0 <= hour <= 23 else match.group(0)


def normalize(text: str) -> str:
    """時刻表記を HH:MM に寄せ、無効な時刻は時刻化しない。"""
    s = text.translate(_FULLWIDTH)
    s = re.sub(r"(午前|午後)(\d{1,2})時半", _ampm_half_to_time, s)
    s = re.sub(r"(午前|午後)(\d{1,2})時(\d{1,2})分", _ampm_to_time, s)
    s = re.sub(r"(午前|午後)(\d{1,2})時", _ampm_hour_to_time, s)
    s = re.sub(r"(?<!午前)(?<!午後)(\d{1,2})時半", _japanese_half_to_time, s)
    s = re.sub(
        r"(?<!午前)(?<!午後)(\d{1,2})時(\d{1,2})分",
        _japanese_to_time,
        s,
    )
    return re.sub(
        r"(?<!午前)(?<!午後)(\d{1,2})時(?!\d+分|間|台)",
        _japanese_hour_to_time,
        s,
    )


def _time_key(t_h: float) -> str:
    """経過時間を照合キー（24時間内の HH:MM）に変換する。"""
    total = int(round(t_h * 60.0)) % (24 * 60)
    return f"{total // 60}:{total % 60:02d}"


def _extract(
    text: str, require_day_prefix: bool
) -> tuple[set[tuple[str, bool]], set[int], bool, bool, bool, list[tuple[int, int]], list[tuple[int, tuple[int, int]]]]:
    """本文から時刻・%・符号違反・曖昧表現・不正100%を抽出する。"""
    normalized = normalize(text)
    times: set[tuple[str, bool]] = set()
    for match in _TIME_RE.finditer(normalized):
        hour, minute = int(match.group(2)), int(match.group(3))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            continue
        key = f"{hour}:{minute:02d}"
        times.add((key, require_day_prefix and match.group("day") is not None))

    percents: set[int] = set()
    explicit_positive = False
    ambiguous = False
    invalid_100 = False
    relative_spans = [match.span() for match in _RELATIVE_NOTE.finditer(normalized)]
    range_spans = []
    ranges: list[tuple[int, int]] = []
    for match in _RANGE_PCT_RE.finditer(normalized):
        low, high = int(match.group("low")), int(match.group("high"))
        percents.update((low, high))
        ranges.append((low, high))
        range_spans.append(match.span())
    hourly_ranges = [
        (int(match.group("hour")), (int(match.group("low")), int(match.group("high"))))
        for match in _HOURLY_RANGE_RE.finditer(normalized)
    ]
    for match in _PCT_RE.finditer(normalized):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        value = int(match.group("value"))
        sign = match.group("sign")
        if sign == "+":
            explicit_positive = True
        if not sign and value != 100 and not re.match(
            r"\s*(?:の)?(?:低下|低下見込み)", normalized[match.end():]
        ):
            ambiguous = True
        if value == 100 and not any(start <= match.start() < end for start, end in relative_spans):
            invalid_100 = True
        percents.add(value)
    return times, percents, explicit_positive, ambiguous, invalid_100, ranges, hourly_ranges


def validate(text: str, facts: Facts) -> ValidationResult:
    """語句検査と確定値の過不足照合を行う。"""
    if not text or not text.strip():
        return ValidationResult(False, "empty")
    if _FORBIDDEN_DISPLAY.search(text):
        return ValidationResult(False, "forbidden_display")
    for note in facts.required_notes:
        if text.count(note) != 1:
            return ValidationResult(False, "required_note")
    confidence_matches = _CONFIDENCE_RE.findall(text)
    if facts.confidence is not None:
        expected_confidence = {"low": "低", "medium": "中", "high": "高"}.get(
            facts.confidence, facts.confidence
        )
        if confidence_matches != [expected_confidence]:
            return ValidationResult(False, "confidence")
    count_matches = _COUNT_RE.findall(text)
    if facts.feedback_count is not None:
        if count_matches != [str(facts.feedback_count)]:
            return ValidationResult(False, "feedback_count")

    for pattern, reason in (
        (_MEDICAL, "medical"),
        (_FEAR, "fear"),
        (_DEFINITIVE, "definitive"),
        (_ATTACK, "attack"),
    ):
        if pattern.search(text):
            return ValidationResult(False, reason)

    found_times, found_pcts, explicit_positive, ambiguous, invalid_100, found_ranges, found_hourly_ranges = _extract(
        text, facts.require_day_prefix
    )
    if explicit_positive or ambiguous or invalid_100:
        return ValidationResult(False, "numbers")
    if found_pcts and "目安" not in text:
        return ValidationResult(False, "note")
    if found_pcts and "推定の目安" not in normalize(text) and not _RELATIVE_NOTE.search(normalize(text)):
        return ValidationResult(False, "definition")
    if facts.required_ranges is not None:
        if found_ranges != facts.required_ranges:
            return ValidationResult(False, "range")
        if any(low > high for low, high in found_ranges):
            return ValidationResult(False, "range")
    if facts.required_hourly_ranges is not None:
        if found_hourly_ranges != facts.required_hourly_ranges:
            return ValidationResult(False, "hourly_range")
        if any(low > high for _, (low, high) in found_hourly_ranges):
            return ValidationResult(False, "hourly_range")

    wanted_times = {
        (_time_key(time), facts.require_day_prefix and time >= 24.0) for time in facts.times
    }
    wanted_pcts = {abs(int(percent)) for percent in facts.percents}
    allowed_times = (
        {
            (_time_key(time), facts.require_day_prefix and time >= 24.0)
            for time in facts.allowed_times
        }
        if facts.allowed_times is not None
        else wanted_times
    )
    allowed_pcts = (
        {abs(int(percent)) for percent in facts.allowed_percents}
        if facts.allowed_percents is not None
        else wanted_pcts
    )
    if facts.required_ranges is not None and facts.allowed_percents is None:
        allowed_pcts.update(endpoint for pair in facts.required_ranges for endpoint in pair)
    if facts.required_hourly_ranges is not None and facts.allowed_percents is None:
        allowed_pcts.update(
            endpoint
            for _, pair in facts.required_hourly_ranges
            for endpoint in pair
        )
    if _RELATIVE_NOTE.search(normalize(text)):
        allowed_pcts.add(100)

    if not wanted_times.issubset(found_times) or not wanted_pcts.issubset(found_pcts):
        return ValidationResult(False, "numbers")
    if found_times - allowed_times or found_pcts - allowed_pcts:
        return ValidationResult(False, "numbers")
    return ValidationResult(True)
