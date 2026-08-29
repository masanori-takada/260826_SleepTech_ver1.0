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
_RANGE_PCT_RE = re.compile(
    r"(?P<low>\d{1,3})\s*(?:[〜～]|[\-－−])\s*"
    r"(?P<high>\d{1,3})\s*%"
)
_HOURLY_RANGE_RE = re.compile(
    r"(?P<hour>\d{1,2})時台[^\d%]{0,20}"
    r"(?P<low>\d{1,3})\s*(?:[〜～]|[\-－−])\s*"
    r"(?P<high>\d{1,3})\s*%"
)
_CONFIDENCE_RE = re.compile(r"信頼度\s*[:：]\s*(?P<label>低|中|高)")
_COUNT_RE = re.compile(r"評価データ\s*(?P<count>\d+)件")
_FORBIDDEN_DISPLAY = re.compile(r"平常時\s*(?:を\s*)?100\s*%|目標を満たせます")
REQUIRED_NOTES = (
    "実測の仕事能力ではありません",
    "中途覚醒・HRV・心拍数は現行の%計算には使っていません",
)
_RELATIVE_NOTE = re.compile(r"(?:本人|あなた)の平常時を\s*100%")
_INVERTED_RANGE = re.compile(r"低下見込み[^。\n]*%\s*(?:ではありません|ではない)")
_INVERTED_NOTE = re.compile(r"[「『]\s*実測の仕事能力ではありません\s*[」』]\s*は\s*誤り")
_PROTECTED_REDEFINITION = re.compile(
    r"(?:実際(?:は|の)|本当(?:は|の)).*(?:実測|目標|起床)|"
    r"(?:実測の仕事能力|注記|低下見込み|信頼度|目標(?:睡眠時間)?|"
    r"起床目安|目標時刻|今から確保).*(?:嘘|誤り|間違い|ではなく|ではない|とは限らない)|"
    r"%(?:は|が).*(?:嘘|誤り|間違い)"
)
_PROTECTED_CONCEPT = re.compile(
    r"(?:実測(?:値|の仕事能力)?\s*(?:です|だ|である)|"
    r"目標(?:睡眠時間)?\s*(?:は|が|＝|=|:|：)\s*\d+(?:\.\d+)?\s*時間|"
    r"(?:低下見込み|信頼度|起床目安|目標時刻|今から確保)\s*(?:は|が|を)\s*(?:嘘|誤り|間違い)|"
    r"%\s*(?:は|が)\s*(?:嘘|誤り|間違い))"
)
_GLOBAL_PROTECTED_NEGATION = re.compile(
    r"(?:上記|上の説明|これら|この説明|上述)[^。\n]*(?:嘘|誤り|間違い|事実ではない|事実でない|事実ではありません)"
)
_POSITIVE_PROTECTED_REDEFINITION = re.compile(
    r"実測の仕事能力を(?:表す|表します|示す|示します)"
    r"|(?:この数値|数値)は仕事能力(?:そのもの)?(?:です|だ|である)"
)
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
    required_confidence: str | None = None
    required_feedback_count: int | None = None
    required_phrases: tuple[str, ...] = ()
    protected_lines: tuple[str, ...] = ()


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
) -> tuple[set[tuple[str, bool]], set[int], bool, bool, bool, list[tuple[int, int]], list[tuple[int, tuple[int, int]]], bool]:
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
    hourly_spans = [match.span() for match in _HOURLY_RANGE_RE.finditer(normalized)]
    all_ranges_bound = all(
        any(start <= range_start and range_end <= end for start, end in hourly_spans)
        for range_start, range_end in [match.span() for match in _RANGE_PCT_RE.finditer(normalized)]
    )
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
    return times, percents, explicit_positive, ambiguous, invalid_100, ranges, hourly_ranges, all_ranges_bound


def validate(text: str, facts: Facts) -> ValidationResult:
    """語句検査と確定値の過不足照合を行う。"""
    if not text or not text.strip():
        return ValidationResult(False, "empty")
    normalized_text = normalize(text)
    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    segments = [segment.strip() for segment in re.split(r"[。．\n]", normalized_text) if segment.strip()]
    # 保護行は文字列の一部を探すのではなく、ラベル単位で許可された正規行と
    # 完全一致させる。したがって保護ラベルを含む自由文（否定・別数値を含む）は
    # 通過せず、自由に生成できるのは保護ラベルを持たない行だけになる。
    if facts.protected_lines:
        def canonical_line(value: str) -> str:
            value = re.sub(r"\s*←\s*谷\s*$", "", value)
            return re.sub(r"\s+", "", value.translate(_FULLWIDTH)).rstrip("。．")

        def label_of(value: str) -> str:
            canonical = canonical_line(value)
            # 保護行のラベルは行頭の固定形式だけを認識する。自由文中の
            # 「低下見込み」まで保護行扱いにすると、正常な説明文を拒否する。
            if re.match(r"^(?:\d+時台)?低下見込み", canonical):
                return "低下見込み"
            for label in ("信頼度", "実測の仕事能力ではありません",
                          "起床目安", "目標睡眠時間", "目標時刻は過ぎています",
                          "今から確保できる見込み"):
                if canonical.startswith(label):
                    return label
            return value

        allowed: dict[str, set[str]] = {}
        for protected_line in facts.protected_lines:
            allowed.setdefault(label_of(protected_line), set()).add(canonical_line(protected_line))
        actual = [(label_of(line), canonical_line(line)) for line in lines]
        for label, permitted in allowed.items():
            found = [value for actual_label, value in actual if actual_label == label]
            if len(found) != len(permitted) or any(value not in permitted for value in found):
                return ValidationResult(False, "protected_line")
        # 保護事実をラベルなしの自由行で再定義することも許可しない。
        # 正規の保護行自体は完全一致済みなので除外する。
        allowed_lines = {
            canonical_line(value) for value in facts.protected_lines
        }
        for line in lines:
            if canonical_line(line) not in allowed_lines and _PROTECTED_REDEFINITION.search(line):
                return ValidationResult(False, "protected_line")
            # 保護概念を含む行は、保護行の完全一致以外を一切許可しない。
            # これにより「実測値です」「目標は5時間」などの別行再定義も防ぐ。
            if canonical_line(line) not in allowed_lines and _PROTECTED_CONCEPT.search(line):
                return ValidationResult(False, "protected_line")
            # 注記「実測の仕事能力ではありません」を残したまま、別行で
            # 実測値・仕事能力だと肯定する意味反転も拒否する。ただし正規注記
            # 自体は allowed_lines として既に通過している。
            if canonical_line(line) not in allowed_lines and _POSITIVE_PROTECTED_REDEFINITION.search(line):
                return ValidationResult(False, "protected_line")
        if _GLOBAL_PROTECTED_NEGATION.search(normalized_text):
            return ValidationResult(False, "semantic_inversion")
    if _INVERTED_RANGE.search(text) or _INVERTED_NOTE.search(text):
        return ValidationResult(False, "semantic_inversion")
    for phrase in facts.required_phrases:
        exact_lines = [line.strip().rstrip("。．") for line in text.splitlines()]
        if exact_lines.count(phrase) != 1:
            return ValidationResult(False, "required_phrase")
    if _FORBIDDEN_DISPLAY.search(normalized_text):
        return ValidationResult(False, "forbidden_display")
    for note in facts.required_notes:
        if text.count(note) != 1:
            return ValidationResult(False, "required_note")
    confidence_matches = _CONFIDENCE_RE.findall(text)
    expected_confidence_value = (
        facts.required_confidence
        if facts.required_confidence is not None
        else facts.confidence
    )
    if expected_confidence_value is not None:
        expected_confidence = {"low": "低", "medium": "中", "high": "高"}.get(
            expected_confidence_value, expected_confidence_value
        )
        if confidence_matches != [expected_confidence]:
            return ValidationResult(False, "confidence")
    count_matches = _COUNT_RE.findall(text)
    expected_feedback_count = (
        facts.required_feedback_count
        if facts.required_feedback_count is not None
        else facts.feedback_count
    )
    if expected_feedback_count is not None:
        if count_matches != [str(expected_feedback_count)]:
            return ValidationResult(False, "feedback_count")

    for pattern, reason in (
        (_MEDICAL, "medical"),
        (_FEAR, "fear"),
        (_DEFINITIVE, "definitive"),
        (_ATTACK, "attack"),
    ):
        if pattern.search(text):
            return ValidationResult(False, reason)

    numeric_text = text
    for phrase in facts.required_phrases:
        numeric_text = numeric_text.replace(phrase, "")
    found_times, found_pcts, explicit_positive, ambiguous, invalid_100, found_ranges, found_hourly_ranges, all_ranges_bound = _extract(
        numeric_text, facts.require_day_prefix
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
        if not all_ranges_bound or found_hourly_ranges != facts.required_hourly_ranges:
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


RECOVERY_HEADING = "今日のリカバリー"
_RECOVERY_ITEM_RE = re.compile(r"^・(?P<label>[^:：]+)[:：]\s*(?P<body>.+)$")
_PLAIN_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")


def validate_recovery_structure(
    section: str,
    expected: tuple[tuple[str, tuple[float, ...]], ...],
) -> ValidationResult:
    """リカバリー節が対象領域と1:1で、提示済みの時刻だけを使っているか検査する。

    expected は (領域ラベル, その領域で使ってよい時刻) を表示順に並べたもの。
    「何を何時に」を担保するため、各行に時刻が1つ以上あることも要求する。
    """
    if not expected:
        return ValidationResult(False, "recovery_unexpected")
    lines = [line.strip() for line in normalize(section).splitlines() if line.strip()]
    if not lines or lines[0] != RECOVERY_HEADING:
        return ValidationResult(False, "recovery_heading")
    if lines.count(RECOVERY_HEADING) != 1:
        return ValidationResult(False, "recovery_heading")

    items = lines[1:]
    if len(items) != len(expected):
        return ValidationResult(False, "recovery_count")

    for item, (label, allowed) in zip(items, expected):
        match = _RECOVERY_ITEM_RE.match(item)
        if match is None:
            return ValidationResult(False, "recovery_format")
        if match.group("label").strip() != label:
            return ValidationResult(False, "recovery_label")
        body = match.group("body")
        found = [f"{int(h)}:{int(m):02d}" for h, m in _PLAIN_TIME_RE.findall(body)]
        if not found:
            return ValidationResult(False, "recovery_time_missing")
        permitted = {_time_key(value) for value in allowed}
        if any(key not in permitted for key in found):
            return ValidationResult(False, "recovery_time_domain")
    return ValidationResult(True)
