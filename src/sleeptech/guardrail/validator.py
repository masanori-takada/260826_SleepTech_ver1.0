"""生成文の検証（仕様 第8節）。

検査は語句検査と数値照合の2種類で行う。数値照合の前に時刻表記を正規化し、
LLM が確定値を改変・捏造していないことを確認する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_MEDICAL = re.compile(r"診断(でき|し)|治療|症候群|病気です|処方")
_FEAR = re.compile(r"取り返しがつかない|寿命が縮|手遅れ|命に関わ|危険な状態")
_DEFINITIVE = re.compile(r"必ず|確実に|絶対に")
_ATTACK = re.compile(r"だらしない|意志が弱い|情けない|怠惰|ダメな人")

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_PCT_RE = re.compile(r"-?(\d{1,3})%")
_FULLWIDTH = str.maketrans("０１２３４５６７８９：％－", "0123456789:%-")


@dataclass(frozen=True)
class Facts:
    """本文に必須の確定値と、列挙を許可する確定値。"""

    times: list[float] = field(default_factory=list)
    percents: list[int] = field(default_factory=list)
    allowed_times: list[float] | None = None
    allowed_percents: list[int] | None = None


@dataclass(frozen=True)
class ValidationResult:
    """文面検証の結果。"""

    passed: bool
    reason: str = ""


def normalize(text: str) -> str:
    """時刻表記を HH:MM に寄せる。"""
    s = text.translate(_FULLWIDTH)
    s = re.sub(r"午後(\d{1,2})時半", lambda m: f"{int(m.group(1)) + 12}:30", s)
    s = re.sub(
        r"午後(\d{1,2})時(\d{1,2})分",
        lambda m: f"{int(m.group(1)) + 12}:{int(m.group(2)):02d}",
        s,
    )
    s = re.sub(r"午後(\d{1,2})時", lambda m: f"{int(m.group(1)) + 12}:00", s)
    s = re.sub(r"午前(\d{1,2})時半", lambda m: f"{int(m.group(1))}:30", s)
    s = re.sub(r"午前(\d{1,2})時", lambda m: f"{int(m.group(1))}:00", s)
    s = re.sub(r"(\d{1,2})時半", lambda m: f"{int(m.group(1))}:30", s)
    s = re.sub(
        r"(\d{1,2})時(\d{1,2})分",
        lambda m: f"{int(m.group(1))}:{int(m.group(2)):02d}",
        s,
    )
    return re.sub(r"(\d{1,2})時(?!間|台)", lambda m: f"{int(m.group(1))}:00", s)


def _time_key(t_h: float) -> str:
    """経過時間を照合キー（24時間内の HH:MM）に変換する。"""
    total = int(round(t_h * 60.0)) % (24 * 60)
    return f"{total // 60}:{total % 60:02d}"


def _extract(text: str) -> tuple[set[str], set[int]]:
    """本文から正規化済みの時刻とパーセントを抽出する。"""
    normalized = normalize(text)
    times = {f"{int(hour)}:{minute}" for hour, minute in _TIME_RE.findall(normalized)}
    percents = {int(percent) for percent in _PCT_RE.findall(normalized)}
    return times, percents


def validate(text: str, facts: Facts) -> ValidationResult:
    """語句検査と確定値の過不足照合を行う。"""
    if not text or not text.strip():
        return ValidationResult(False, "empty")

    for pattern, reason in (
        (_MEDICAL, "medical"),
        (_FEAR, "fear"),
        (_DEFINITIVE, "definitive"),
        (_ATTACK, "attack"),
    ):
        if pattern.search(text):
            return ValidationResult(False, reason)

    found_times, found_pcts = _extract(text)
    if found_pcts and "目安" not in text:
        return ValidationResult(False, "note")

    wanted_times = {_time_key(time) for time in facts.times}
    wanted_pcts = {abs(int(percent)) for percent in facts.percents}
    allowed_times = (
        {_time_key(time) for time in facts.allowed_times}
        if facts.allowed_times is not None
        else wanted_times
    )
    allowed_pcts = (
        {abs(int(percent)) for percent in facts.allowed_percents}
        if facts.allowed_percents is not None
        else wanted_pcts
    )
    # 平常時を100%とする注記は予測値ではないため照合対象から除外する。
    if re.search(r"平常時を\s*100%", normalize(text)):
        allowed_pcts.add(100)

    if not wanted_times.issubset(found_times) or not wanted_pcts.issubset(found_pcts):
        return ValidationResult(False, "numbers")
    if found_times - allowed_times or found_pcts - allowed_pcts:
        return ValidationResult(False, "numbers")
    return ValidationResult(True)
