"""Webhook の起床トリガーとフィードバック取り込み（仕様 第9節）。"""

from datetime import datetime, timedelta, timezone

import pytest

from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import Feedback, Message, Prediction
from src.handlers import deps as hd
from src.handlers import webhook_handler as wh

JST = timezone(timedelta(hours=9))


@pytest.mark.parametrize(
    "text", ["おはよう", "おはようございます", "起きた", "データ更新", "同期", "計測"]
)
def test_wakeup_triggers_are_detected(text):
    assert wh.is_wakeup_trigger(text) is True


@pytest.mark.parametrize("text", ["こんばんは", "評価:3", "ラーメン食べた", "", "おはようの意味は？"])
def test_non_triggers_are_ignored(text):
    assert wh.is_wakeup_trigger(text) is False


def test_origin_date_at_night_is_today():
    """22:30 の起点日は今日。"""
    now = datetime(2026, 8, 26, 22, 30, tzinfo=JST)
    assert hd.origin_date(now) == "2026-08-26"


def test_origin_date_in_the_morning_is_yesterday():
    """8:00 の起点日は前日（前夜のメッセージと同じ日付にするため）。"""
    now = datetime(2026, 8, 27, 8, 0, tzinfo=JST)
    assert hd.origin_date(now, morning=True) == "2026-08-26"


def test_now_h_converts_to_elapsed_hours():
    now = datetime(2026, 8, 26, 22, 30, tzinfo=JST)
    assert hd.now_h(now) == 22.5


def test_night_origin_date_at_2230_is_today():
    """22:30 起動: 起点日は当日、now_h はそのまま。"""
    now = datetime(2026, 8, 26, 22, 30, tzinfo=JST)
    assert hd.origin_date(now, night=True) == "2026-08-26"
    assert hd.now_h(now, night=True) == 22.5


def test_night_origin_date_at_0030_rolls_back_to_previous_day():
    """0:30 起動（深夜にずれ込んだ場合）: 起点日は前日、now_h は 24.5。"""
    now = datetime(2026, 8, 27, 0, 30, tzinfo=JST)
    assert hd.origin_date(now, night=True) == "2026-08-26"
    assert hd.now_h(now, night=True) == 24.5


def test_night_origin_date_at_0359_rolls_back_to_previous_day():
    """3:59 起動（区切り直前）: 起点日は前日、now_h は約 27.983。"""
    now = datetime(2026, 8, 27, 3, 59, tzinfo=JST)
    assert hd.origin_date(now, night=True) == "2026-08-26"
    assert hd.now_h(now, night=True) == pytest.approx(27.9833, abs=1e-3)


def test_night_origin_date_at_0400_is_boundary_today():
    """4:00 起動（区切りちょうど）: 起点日は当日、now_h は 4.0。"""
    now = datetime(2026, 8, 27, 4, 0, tzinfo=JST)
    assert hd.origin_date(now, night=True) == "2026-08-27"
    assert hd.now_h(now, night=True) == 4.0


def test_night_origin_date_across_month_boundary():
    """月初またぎ: 9/1 0:30 起動の起点日は 8/31。"""
    now = datetime(2026, 9, 1, 0, 30, tzinfo=JST)
    assert hd.origin_date(now, night=True) == "2026-08-31"


def test_night_and_morning_use_same_date_normal_case():
    """回帰: 8/26 22:30 の夜と 8/27 8:00 の朝が同じ date を使う。"""
    night_now = datetime(2026, 8, 26, 22, 30, tzinfo=JST)
    morning_now = datetime(2026, 8, 27, 8, 0, tzinfo=JST)
    assert hd.origin_date(night_now, night=True) == hd.origin_date(morning_now, morning=True) == "2026-08-26"


def test_night_and_morning_use_same_date_when_night_rolls_over_midnight():
    """回帰: 深夜にずれ込んだ夜（8/27 0:30）でも朝（8/27 8:00）と同じ date になる。"""
    night_now = datetime(2026, 8, 27, 0, 30, tzinfo=JST)
    morning_now = datetime(2026, 8, 27, 8, 0, tzinfo=JST)
    assert hd.origin_date(night_now, night=True) == hd.origin_date(morning_now, morning=True) == "2026-08-26"


def test_feedback_targets_previous_origin_morning_prediction():
    m = MemoryClient(InMemoryBackend())
    m.save_message(Message(date="2026-08-27", slot="NIGHT", delivered=True))
    m.save_prediction(Prediction(date="2026-08-26", slot="MORNING", mean_drop_pct=8))

    got = wh.resolve_feedback_target(m, today="2026-08-27")

    assert got.evaluated_date == "2026-08-27"
    assert got.prediction_date == "2026-08-26"


def test_feedback_target_stays_on_today_after_it_was_already_answered():
    """別イベントの再回答でも、既に回答済みだから前日へ移動しない。"""
    m = MemoryClient(InMemoryBackend())
    m.save_message(Message(date="2026-08-27", slot="NIGHT", delivered=True))
    m.save_prediction(Prediction(date="2026-08-26", slot="MORNING", mean_drop_pct=8))
    m.save_feedback(Feedback(date="2026-08-27", score=1, source_event_id="event-1"))
    m.save_message(Message(date="2026-08-26", slot="NIGHT", delivered=True))
    m.save_prediction(Prediction(date="2026-08-25", slot="MORNING", mean_drop_pct=8))

    got = wh.resolve_feedback_target(m, today="2026-08-27")

    assert got == wh.FeedbackTarget("2026-08-27", "2026-08-26")


def test_feedback_target_does_not_fallback_to_answered_yesterday_when_today_has_no_candidate():
    """当日候補がないとき、回答済みの前日へ別イベントを誤紐付けしない。"""
    m = MemoryClient(InMemoryBackend())
    m.save_message(Message(date="2026-08-27", slot="NIGHT", delivered=True))
    m.save_prediction(Prediction(date="2026-08-26", slot="MORNING"))
    m.save_feedback(Feedback(date="2026-08-27", score=1, source_event_id="old"))

    assert wh.resolve_feedback_target(m, today="2026-08-28") is None


def test_feedback_target_falls_back_to_unanswered_yesterday():
    m = MemoryClient(InMemoryBackend())
    m.save_message(Message(date="2026-08-27", slot="NIGHT", delivered=True))
    m.save_prediction(Prediction(date="2026-08-26", slot="MORNING"))

    assert wh.resolve_feedback_target(m, today="2026-08-28") == wh.FeedbackTarget(
        "2026-08-27", "2026-08-26"
    )


def test_feedback_never_targets_same_day_night_prediction():
    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-27", slot="NIGHT"))

    assert wh.resolve_feedback_target(m, today="2026-08-27") is None


def test_feedback_event_is_detected_as_processed_before_candidate_fallback():
    m = MemoryClient(InMemoryBackend())
    m.save_feedback(Feedback(date="2026-08-27", score=1, source_event_id="event-1"))

    assert wh.feedback_event_processed(m, "event-1") is True


def test_feedback_event_date_uses_line_timestamp_in_jst():
    """処理が翌日へ遅延してもLINEイベント発生日の候補へ保存する。"""
    assert wh.event_origin_date(1787918400000, "2026-08-29") == "2026-08-28"


def test_feedback_event_date_falls_back_to_processing_date_without_timestamp():
    assert wh.event_origin_date(None, "2026-08-29") == "2026-08-29"


def test_feedback_event_date_handles_utc_jst_boundary():
    # 2026-08-28 14:59:59Z = 8/28 23:59:59 JST, 15:00:00Z = 8/29 00:00 JST
    assert wh.event_origin_date(1787929199000, "2026-08-29") == "2026-08-28"
    assert wh.event_origin_date(1787929200000, "2026-08-29") == "2026-08-29"


def test_feedback_event_date_ignores_out_of_range_timestamp():
    assert wh.event_origin_date(-1, "2026-08-29") == "2026-08-29"


# NOTE(出荷前レビュー Minor 対応): 以前ここにあった3件のテスト
# （test_texts_returns_empty_list_when_body_is_json_array 等）は
# wh._texts({"body": "..."}) を呼んでいたが、_texts はパース済み payload
# （{"events": [...]}）を受け取る関数であり、キー "body" は参照しない。
# そのため入力は常に events キーを持たず、実質的に何を渡しても [] が
# 返るだけの無効なテストだった（JSON配列・非リストevents・非dict要素への
# 防御を検証したつもりで、実際には何も検証していなかった）。
# _texts 自体は _authorized_texts に置き換え済みでプロダクションコードから
# 呼ばれていないデッドコードであり（tests/unit/test_handlers.py の
# test_texts_returns_only_string_texts_from_valid_events が正しい入力形式で
# 契約を検証済み）、この3件は書き直さずテストごと削除した
# （_texts 自体の削除は別の出荷前レビュー指摘のデッドコード整理タスクの範囲）。
