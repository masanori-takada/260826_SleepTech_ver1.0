"""Webhook の起床トリガーとフィードバック取り込み（仕様 第9節）。"""

from datetime import datetime, timedelta, timezone

import pytest

from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import Prediction
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


def test_feedback_binds_to_the_latest_unanswered_night_prediction():
    """回答は直近の未回答 PREDICT#NIGHT に紐付ける。"""
    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-25", slot="NIGHT"))
    m.save_prediction(Prediction(date="2026-08-26", slot="NIGHT"))
    assert wh.resolve_feedback_date(m, today="2026-08-26") == "2026-08-26"


def test_feedback_falls_back_to_previous_day():
    """当日の夜メッセージがまだ無ければ前日に遡る。"""
    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-25", slot="NIGHT"))
    assert wh.resolve_feedback_date(m, today="2026-08-26") == "2026-08-25"


def test_feedback_does_not_go_back_more_than_two_days():
    """2日より前には遡らない。"""
    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-20", slot="NIGHT"))
    assert wh.resolve_feedback_date(m, today="2026-08-26") is None


def test_feedback_skips_already_answered_days():
    """既に回答済みの日には紐付けない。"""
    from sleeptech.memory.schemas import Feedback

    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-25", slot="NIGHT"))
    m.save_prediction(Prediction(date="2026-08-26", slot="NIGHT"))
    m.save_feedback(Feedback(date="2026-08-26", score=3))
    assert wh.resolve_feedback_date(m, today="2026-08-26") == "2026-08-25"


def test_feedback_returns_none_when_no_prediction_exists():
    m = MemoryClient(InMemoryBackend())
    assert wh.resolve_feedback_date(m, today="2026-08-26") is None
