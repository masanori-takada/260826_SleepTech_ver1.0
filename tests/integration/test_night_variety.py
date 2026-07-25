"""連夜の説得メッセージの多様性を検証する（FR-007 / SC-004）。

回帰対象: 23:00 のコメントが毎晩ほぼ同じ指標・同じ切り口になっていた問題。
14 晩まわして「前夜と違うデータを見ている」「保存済みデータに満遍なく触れる」ことを確認する。
"""

from __future__ import annotations

import re

from night_ramen.agents import topics
from night_ramen.agents.prompts import make_fake_invoker
from night_ramen.config import load_settings
from night_ramen.memory.client import MemoryClient
from night_ramen.memory.schemas import VITAL_FIELDS, DailyVitalRecord, Goal, GuardrailResult
from night_ramen.notifier.line import DryRunPushClient, InMemoryIdempotencyStore
from night_ramen.workflow.night import NightDependencies, run_night

_FOCUS_RE = re.compile(r"【今夜の主役データ】(.+?)。")
_ANGLE_RE = re.compile(r"【今夜の切り口】(.+?) —")


def _seed_30_days(memory: MemoryClient) -> None:
    """全12指標が埋まった30日分。ラーメンありの日は数値が悪い。"""
    for i in range(1, 31):
        ramen = i % 3 == 0
        k = 0.0 if ramen else 1.0
        memory.save_vital(
            DailyVitalRecord(
                date=f"2026-06-{i:02d}",
                ramen_last_night=ramen,
                sleep_score=int(60 + 20 * k),
                total_sleep_min=int(360 + 60 * k),
                awakenings=int(4 - 2 * k),
                deep_sleep_min=int(50 + 40 * k),
                night_avg_hr=float(62 - 5 * k),
                hrv=float(35 + 20 * k),
                skin_temp_delta=float(0.6 - 0.4 * k),
                respiration_rate=float(16.5 - k),
                weight_kg=float(72.5 - 0.02 * i),
                body_fat_pct=float(24.0 - 0.01 * i),
                next_day_steps=int(4000 + 5000 * k),
                next_day_calories=int(1800 + 500 * k),
            )
        )


def _responder(model_id: str, prompt: str, system: str) -> str:
    """指示された主役データと切り口をそのまま本文に反映するフェイク LLM。"""
    if "アナリスト" in system:
        return "ラーメンを食べた日は各指標が悪化しています。"
    focus = _FOCUS_RE.search(prompt)
    angle = _ANGLE_RE.search(prompt)
    labels = focus.group(1) if focus else "睡眠スコア"
    return (
        f"今夜は{angle.group(1) if angle else '実測値'}の話です。"
        f"{labels}の数値を見てみましょう。今夜は我慢して、明日の自分を軽くしましょう。"
    )


def _deps(memory, push, store) -> NightDependencies:
    return NightDependencies(
        settings=load_settings(),
        memory=memory,
        invoker=make_fake_invoker(_responder),
        goal=Goal(success_rate_target=0.85, weight_loss_target_kg=2.0, baseline_weight_kg=73.0),
        idempotency=store,
        push_client=push,
    )


def _run_two_weeks() -> list:
    memory = MemoryClient()
    _seed_30_days(memory)
    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()

    messages = []
    for day in range(1, 15):
        msg = run_night(f"2026-07-{day:02d}", _deps(memory, push, store))
        assert msg.guardrail_result == GuardrailResult.PASS
        messages.append(msg)
    return messages


def test_consecutive_nights_look_at_different_data():
    messages = _run_two_weeks()
    for prev, cur in zip(messages, messages[1:]):
        assert cur.focus_metrics, f"{cur.date} の主役データが空"
        overlap = set(prev.focus_metrics) & set(cur.focus_metrics)
        assert not overlap, f"{prev.date} と {cur.date} で話題が重複: {overlap}"


def test_two_weeks_cover_every_stored_metric():
    messages = _run_two_weeks()
    covered: set[str] = set()
    for msg in messages:
        covered.update(msg.focus_metrics)
    # 保存されている12指標すべてが、2週間のうちに主役として登場する
    assert covered == set(VITAL_FIELDS)


def test_message_bodies_actually_mention_the_rotated_metrics():
    messages = _run_two_weeks()
    mentioned: set[str] = set()
    for msg in messages:
        assert msg.referenced_metrics, f"{msg.date} の本文がバイタルに触れていない"
        mentioned.update(msg.referenced_metrics)
    # 本文レベルでも 4 指標固定ではなく広く行き渡る（SC-004）
    assert len(mentioned) >= 10


def test_angles_rotate_across_nights():
    messages = _run_two_weeks()
    angles = [m.angle for m in messages]
    assert all(a for a in angles)
    assert all(a != b for a, b in zip(angles, angles[1:])), f"切り口が連続で同じ: {angles}"
    assert len(set(angles)) >= 6


def test_rerunning_same_night_keeps_the_same_topic():
    """同一日の再実行で話題が入れ替わらない（履歴に自分自身を数えない）。"""
    memory = MemoryClient()
    _seed_30_days(memory)
    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()

    first = run_night("2026-07-01", _deps(memory, push, store))
    second = run_night("2026-07-01", _deps(memory, push, store))

    assert second.focus_metrics == first.focus_metrics
    assert second.angle == first.angle
    assert second.delivered is False  # 送信は1晩1回のまま（FR-014）


def test_topic_selection_survives_history_without_new_fields():
    """旧スキーマ（focus_metrics/angle 無し）の履歴が残っていても動く。"""
    memory = MemoryClient()
    _seed_30_days(memory)
    memory._backend.put(  # noqa: SLF001 — 旧レコードの再現
        "messages",
        "2026-06-30",
        {
            "date": "2026-06-30",
            "body": "睡眠スコアが下がっていました。",
            "tone": "neutral",
            "guardrail_result": "pass",
            "regeneration_count": 0,
            "referenced_metrics": ["sleep_score"],
            "delivered": True,
            "sent_at": "2026-06-30T14:00:00+00:00",
        },
    )
    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()

    msg = run_night("2026-07-01", _deps(memory, push, store))

    assert msg.guardrail_result == GuardrailResult.PASS
    assert "sleep_score" not in msg.focus_metrics  # 前夜の話題は避ける


def test_available_metrics_limits_topics_to_stored_data():
    """記録が無い指標は話題にしない（存在しない数値を語らせない）。"""
    memory = MemoryClient()
    for i in range(1, 11):
        memory.save_vital(
            DailyVitalRecord(date=f"2026-06-{i:02d}", sleep_score=60 + i, hrv=40.0 + i)
        )
    vitals = memory.get_vitals("2026-06-01", "2026-06-30")
    assert topics.available_metrics(vitals) == ["sleep_score", "hrv"]

    push = DryRunPushClient()
    store = InMemoryIdempotencyStore()
    msg = run_night("2026-07-01", _deps(memory, push, store))

    assert set(msg.focus_metrics) <= {"sleep_score", "hrv"}
