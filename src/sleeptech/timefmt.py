"""起点日 0:00 からの経過時間（float, 単位=時）と表示文字列の相互変換。

日付をまたぐ時刻を「24:30」と書いてはならない（仕様 第4節）。
翌日は「翌 0:30」、翌々日は「翌々 23:30」と表記する。
"""

from __future__ import annotations

_DAY_PREFIX = ("", "翌 ", "翌々 ")


def to_h(hour: int, minute: int = 0, next_day: bool = False) -> float:
    """時分から経過時間へ。next_day=True で 24 時間を加算する。"""
    return hour + minute / 60.0 + (24.0 if next_day else 0.0)


def _split(t_h: float) -> tuple[int, int, int]:
    """経過時間を (日数, 時, 分) に分解する。分は四捨五入。"""
    total_min = int(round(t_h * 60.0))
    days, rem = divmod(total_min, 24 * 60)
    hour, minute = divmod(rem, 60)
    return days, hour, minute


def fmt_hm(t_h: float) -> str:
    """表示用の時刻文字列。日付をまたぐ場合は「翌」を前置する。"""
    _, hour, minute = _split(t_h)
    days = 0 if t_h < 24.0 else int(round(t_h / 24.0))
    prefix = _DAY_PREFIX[days] if days < len(_DAY_PREFIX) else f"{days}日後 "
    return f"{prefix}{hour}:{minute:02d}"


def fmt_hm_plain(t_h: float) -> str:
    """日付プレフィックスを付けない時刻文字列。"""
    _, hour, minute = _split(t_h)
    return f"{hour}:{minute:02d}"
