"""夜・朝フロー共通ヘルパー（_common.py）の検証。

I3: X-Line-Retry-Key は LINE Messaging API 仕様上「UUID (version 4) の文字列」
でなければならない。旧実装は uuid5（version 5）を渡しており、バージョン検証が
行われた場合に全 push が 400 になる実害最大の欠陥だった。
"""

from __future__ import annotations

import uuid

from sleeptech.workflow._common import retry_key


def test_retry_key_is_uuid_v4_shaped():
    """version 属性が 4 であること（LINE 仕様の必須条件）。"""
    key = retry_key("2026-08-26", "NIGHT")
    parsed = uuid.UUID(key)
    assert parsed.version == 4
    assert parsed.variant == uuid.RFC_4122


def test_retry_key_is_deterministic_for_same_input():
    """同じ <date>/<slot> からは常に同じキーが出る（Lambda再試行での二重送信防止）。"""
    a = retry_key("2026-08-26", "NIGHT")
    b = retry_key("2026-08-26", "NIGHT")
    assert a == b


def test_retry_key_differs_by_date_or_slot():
    night_key = retry_key("2026-08-26", "NIGHT")
    other_date = retry_key("2026-08-27", "NIGHT")
    other_slot = retry_key("2026-08-26", "MORNING")
    assert night_key != other_date
    assert night_key != other_slot


def test_retry_key_example_value_is_v4_shaped_string():
    """実例: 生成される文字列そのものが v4 の形状（3グループ目が'4'始まり）を持つ。"""
    key = retry_key("2026-08-26", "NIGHT")
    groups = key.split("-")
    assert len(groups) == 5
    assert groups[2][0] == "4"
    assert groups[3][0] in "89ab"  # RFC4122 variant ビット
