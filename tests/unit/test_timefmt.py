"""時刻表記（仕様 第4節「日付の規約」）。"""

import pytest

from sleeptech import timefmt


@pytest.mark.parametrize(
    "value,want",
    [
        (23.5, "23:30"),
        (22.0, "22:00"),
        (7.25, "7:15"),
        (24.5, "翌 0:30"),
        (25.0, "翌 1:00"),
        (31.0, "翌 7:00"),
        (47.5, "翌 23:30"),
        (48.0, "翌々 0:00"),
    ],
)
def test_fmt_hm(value, want):
    assert timefmt.fmt_hm(value) == want


@pytest.mark.parametrize("value,want", [(24.5, "0:30"), (31.0, "7:00"), (23.5, "23:30")])
def test_fmt_hm_plain_drops_day_prefix(value, want):
    assert timefmt.fmt_hm_plain(value) == want


def test_never_renders_hour_24_or_greater():
    """24時以降を 24:30 のように表記してはならない。"""
    for v in (24.0, 24.5, 25.75, 31.0, 47.5):
        assert not timefmt.fmt_hm(v).lstrip("翌々 ").startswith("24")


def test_to_h_round_trip():
    assert timefmt.to_h(23, 30) == 23.5
    assert timefmt.to_h(0, 30, next_day=True) == 24.5
    assert timefmt.fmt_hm(timefmt.to_h(0, 30, next_day=True)) == "翌 0:30"


def test_minutes_are_rounded_to_the_nearest_minute():
    assert timefmt.fmt_hm(23.0 + 29.6 / 60.0) == "23:30"
