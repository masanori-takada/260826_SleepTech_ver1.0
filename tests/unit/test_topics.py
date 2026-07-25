"""話題ローテーションのテスト（FR-007 / SC-004）。

「毎晩コメントが同じ話題になる」不具合の再発防止:
 - 全 VITAL_FIELDS が話題候補として定義されていること
 - 前夜に扱った指標は翌夜の主役から外れること
 - 履歴を重ねると全指標・複数の切り口に行き渡ること
"""

from __future__ import annotations

from night_ramen.agents import topics
from night_ramen.memory.schemas import (
    VITAL_FIELDS,
    GuardrailResult,
    PersuasionMessage,
)


def _msg(date: str, focus: list[str], angle: str = "", passed: bool = True) -> PersuasionMessage:
    return PersuasionMessage(
        date=date,
        body="",
        guardrail_result=GuardrailResult.PASS if passed else GuardrailResult.FALLBACK,
        focus_metrics=focus,
        angle=angle,
    )


def test_every_vital_field_has_topic_metadata():
    # 定義漏れがあるとその指標は永久に話題にならない
    assert set(topics.METRIC_META) == set(VITAL_FIELDS)
    for field, meta in topics.METRIC_META.items():
        assert meta.label and meta.keywords, field


def test_detect_metrics_covers_newly_added_fields():
    text = (
        "総睡眠時間が短く、中途覚醒も増えています。体脂肪率と皮膚温にも変化が出ています。"
    )
    hits = topics.detect_metrics(text)
    assert "total_sleep_min" in hits
    assert "awakenings" in hits
    assert "body_fat_pct" in hits
    assert "skin_temp_delta" in hits


def test_focus_excludes_previous_night_metrics():
    available = list(VITAL_FIELDS)
    history = [_msg("2026-07-10", ["sleep_score", "hrv"])]

    focus = topics.select_focus_metrics("2026-07-11", available, history, count=2)

    assert focus  # 何かは選ばれる
    assert not set(focus) & {"sleep_score", "hrv"}


def test_focus_prefers_never_used_metrics():
    available = list(VITAL_FIELDS)
    used = ["sleep_score", "hrv", "deep_sleep_min", "next_day_steps"]
    history = [
        _msg("2026-07-09", used[:2]),
        _msg("2026-07-10", used[2:]),
    ]

    focus = topics.select_focus_metrics("2026-07-11", available, history, count=2)

    assert not set(focus) & set(used)


def test_fallback_messages_do_not_consume_topics():
    available = ["weight_kg", "hrv"]
    history = [
        _msg("2026-07-09", ["hrv"]),                      # 実際に届いた
        _msg("2026-07-10", ["weight_kg"], passed=False),  # 固定安全文面＝ユーザーは見ていない
    ]

    focus = topics.select_focus_metrics("2026-07-11", available, history, count=1)

    # 前夜のフォールバックは「使った話題」に数えないので、weight_kg は未使用扱いで選ばれる
    assert focus == ["weight_kg"]


def test_focus_falls_back_when_few_metrics_available():
    # 指標が1つしかなければ、前夜と同じでも選ばざるを得ない（空を返さない）
    history = [_msg("2026-07-10", ["weight_kg"])]
    focus = topics.select_focus_metrics("2026-07-11", ["weight_kg"], history, count=2)
    assert focus == ["weight_kg"]


def test_rotation_covers_all_metrics_over_time():
    available = list(VITAL_FIELDS)
    history: list[PersuasionMessage] = []
    seen: set[str] = set()

    for day in range(1, 15):
        date = f"2026-07-{day:02d}"
        focus = topics.select_focus_metrics(date, available, history, count=2)
        assert focus
        seen.update(focus)
        history.append(_msg(date, focus))

    # 2週間で12指標すべてに触れられる（従来は4指標で頭打ちだった）
    assert seen == set(VITAL_FIELDS)


def test_angle_rotates_and_respects_data_requirements():
    focus = ["sleep_score", "deep_sleep_min"]  # 体組成・活動量は今夜の主役ではない
    history: list[PersuasionMessage] = []
    chosen: list[str] = []

    for day in range(1, 9):
        date = f"2026-07-{day:02d}"
        angle = topics.select_angle(date, focus, has_reports=False, recent_messages=history)
        chosen.append(angle.id)
        history.append(_msg(date, [], angle=angle.id))

    # 主役指標と噛み合わない切り口は選ばれない
    assert "body_composition" not in chosen
    assert "next_day_cost" not in chosen
    assert "autonomic" not in chosen
    # 自己申告が無いので比較・連続記録の切り口も選ばれない
    assert "contrast" not in chosen
    assert "streak" not in chosen
    # 睡眠指標が主役なので、眠りの中身の切り口は候補に入る
    assert "sleep_architecture" in chosen
    # 連日で切り口が変わる
    assert all(a != b for a, b in zip(chosen, chosen[1:]))
    assert len(set(chosen)) >= 4


def test_recently_used_metrics_window():
    history = [
        _msg("2026-07-10", ["hrv"]),
        _msg("2026-07-05", ["weight_kg"]),  # 3日より前なので対象外
    ]
    used = topics.recently_used_metrics("2026-07-11", history, days=3)
    assert used == ["hrv"]
