"""夜フローの統合（仕様 第4節「データフロー（夜）」）。"""

from dataclasses import replace

import pytest

from sleeptech.config import load_settings
from sleeptech.llm.client import LlmError, NullClient
from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import DailyVital
from sleeptech.notifier.line import FakePushClient, parse_feedback
from sleeptech.workflow import night

NOW_H = 22.5  # 22:30
DATE = "2026-08-26"


class StubLlm:
    """プロンプトの確定値をそのまま含む文章を返す偽 LLM。

    NOTE(Task 13 調整): night_user() の実際の出力形式に合わせて調整した。
    - 「明日の主要...」の行の値には符号（例: "-8%"）が既に含まれるため、
      文面組み立て側で符号を二重に付けない。
    - guardrail.validator は本文に「(本人|あなた)の平常時を100%」という
      相対基準の説明文が無いと %値を「definition」エラーとして弾く
      （validator.py の _RELATIVE_NOTE 要件）。ブリーフのテンプレート文には
      この説明が欠けており、常にガードレール不通過→フォールバックへ
      落ちてしまい「LLM 生成が成功する経路」を検証できていなかったため、
      説明文を含む文章に変更し、実際に validate() を通過することを確認した。
    """

    def __init__(self, template: str = None) -> None:
        self.template = template
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.template is not None:
            return self.template
        time_s = next(l.split(": ")[1] for l in user.splitlines() if l.startswith("推奨就寝時刻"))
        pct = next(l.split(": ")[1] for l in user.splitlines() if l.startswith("明日の主要"))
        return (
            f"今夜は{time_s}までに寝るのがおすすめです。"
            f"あなたの平常時を100%とした推定の目安で、明日の主要時間帯は{pct}前後の見込みです。"
        )


def _memory(days: int = 14) -> MemoryClient:
    m = MemoryClient(InMemoryBackend())
    from datetime import date as d, timedelta

    base = d.fromisoformat(DATE)
    for i in range(1, days + 1):
        day = (base - timedelta(days=i)).isoformat()
        m.save_vital(DailyVital(date=day, total_sleep_min=450, bedtime_h=23.5, wake_h=31.0,
                                rem_sleep_min=90, deep_sleep_min=60))
    return m


def _deps(memory=None, llm=None, push=None):
    return night.NightDeps(
        settings=load_settings(),
        memory=memory or _memory(),
        llm_client=llm or StubLlm(),
        push_client=push or FakePushClient(),
    )


def test_target_drop_pct_setting_is_passed_to_recommend_bedtime(monkeypatch):
    """I6: Settings.target_drop_pct（STC_TARGET_DROP_PCT）が recommend_bedtime に
    実際に渡ることを確認する（以前は誰も参照せず C.TARGET_DROP_PCT 直参照だった）。"""
    captured = {}
    original = night.tp.recommend_bedtime

    def spy(*args, **kwargs):
        captured["target_drop_pct"] = kwargs.get("target_drop_pct")
        return original(*args, **kwargs)

    monkeypatch.setattr(night.tp, "recommend_bedtime", spy)

    settings = replace(load_settings(), target_drop_pct=3.5)
    deps = night.NightDeps(
        settings=settings, memory=_memory(), llm_client=StubLlm(), push_client=FakePushClient()
    )
    night.run_night(DATE, NOW_H, deps)

    assert captured["target_drop_pct"] == 3.5


def test_sends_one_message_with_quick_reply():
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(push=push))
    assert len(push.sent) == 1
    assert "quickReply" in push.sent[0]


def test_quick_reply_answers_are_parseable():
    """送ったクイックリプライの回答が parse_feedback で読める。"""
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(push=push))
    texts = [i["action"]["text"] for i in push.sent[0]["quickReply"]["items"]]
    assert [parse_feedback(t) for t in texts] == [1, 2, 3, 4, 5]


def test_saves_prediction_and_marks_notified():
    memory = _memory()
    night.run_night(DATE, NOW_H, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "NIGHT")
    assert pred is not None
    assert pred.bedtime_h is not None
    assert memory.is_notified(DATE, "NIGHT") is True


def test_second_run_does_not_send_again():
    """冪等: 同じ日付で2回実行しても送信は1回。"""
    memory = _memory()
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    assert len(push.sent) == 1


def test_retry_key_is_stable_for_same_slot_and_differs_by_slot_or_date():
    first = FakePushClient()
    second = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(push=first))
    night.run_night("2026-08-27", NOW_H, _deps(push=second))

    assert first.retry_keys[0] == night._retry_key(DATE, "NIGHT")
    assert first.retry_keys[0] != second.retry_keys[0]
    assert first.retry_keys[0] == night._retry_key(DATE, "NIGHT")
    assert first.retry_keys[0] != night._retry_key(DATE, "MORNING")


def test_falls_back_to_template_when_llm_fails():
    """LLM 障害時もテンプレート文で送信し、クイックリプライも付く。"""
    push = FakePushClient()
    result = night.run_night(DATE, NOW_H, _deps(llm=NullClient(), push=push))
    assert len(push.sent) == 1
    assert "quickReply" in push.sent[0]
    assert result["used_fallback"] is True


def test_falls_back_when_llm_output_fails_guardrail():
    """ガードレールを通らない生成が続いたらテンプレートに落ちる。"""
    push = FakePushClient()
    bad = StubLlm(template="必ず-99%落ちます。手遅れです。")
    result = night.run_night(DATE, NOW_H, _deps(llm=bad, push=push))
    assert result["used_fallback"] is True
    assert len(bad.calls) == 3  # 初回 + 再生成2回


def test_message_contains_the_recommended_time():
    push = FakePushClient()
    memory = _memory()
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    pred = memory.get_prediction(DATE, "NIGHT")
    from sleeptech.timefmt import fmt_hm_plain

    assert fmt_hm_plain(pred.bedtime_h) in push.sent[0]["text"]


def test_works_with_no_history_using_defaults():
    """履歴ゼロでも予測を出し、精度が低い旨を含める。"""
    push = FakePushClient()
    empty = MemoryClient(InMemoryBackend())
    night.run_night(DATE, NOW_H, _deps(memory=empty, push=push))
    assert len(push.sent) == 1


def test_is_deterministic():
    """同じ入力からは同じ推奨就寝時刻が出る。"""
    a = night.run_night(DATE, NOW_H, _deps())
    b = night.run_night(DATE, NOW_H, _deps())
    assert a["bedtime_h"] == b["bedtime_h"]


class _MarkNotifiedFailsMemory(MemoryClient):
    """mark_notified 呼び出し時に必ず例外を投げる記憶クライアント。"""

    def mark_notified(self, date: str, slot: str) -> bool:
        raise RuntimeError("mark_notified boom")


class _SavePredictionFailsMemory(MemoryClient):
    """save_prediction 呼び出し時に必ず例外を投げる記憶クライアント。"""

    def save_prediction(self, pred) -> None:
        raise RuntimeError("save_prediction boom")


class _PushFailsClient:
    """push 呼び出し時に必ず例外を投げる送信クライアント。"""

    def push(self, message: dict) -> None:
        raise RuntimeError("push boom")


def test_mark_notified_failure_does_not_break_delivery():
    """push 成功後に mark_notified が失敗しても例外は伝播せず delivered=True のまま。"""
    memory = _MarkNotifiedFailsMemory(InMemoryBackend())
    for i in range(1, 15):
        from datetime import date as d, timedelta

        day = (d.fromisoformat(DATE) - timedelta(days=i)).isoformat()
        memory.save_vital(DailyVital(date=day, total_sleep_min=450, bedtime_h=23.5,
                                      wake_h=31.0, rem_sleep_min=90, deep_sleep_min=60))
    push = FakePushClient()
    result = night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    assert len(push.sent) == 1
    assert result["delivered"] is True


def test_save_prediction_failure_does_not_break_delivery():
    """push 成功後に save_prediction が失敗しても例外は伝播せず delivered=True のまま。"""
    memory = _SavePredictionFailsMemory(InMemoryBackend())
    for i in range(1, 15):
        from datetime import date as d, timedelta

        day = (d.fromisoformat(DATE) - timedelta(days=i)).isoformat()
        memory.save_vital(DailyVital(date=day, total_sleep_min=450, bedtime_h=23.5,
                                      wake_h=31.0, rem_sleep_min=90, deep_sleep_min=60))
    push = FakePushClient()
    result = night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    assert len(push.sent) == 1
    assert result["delivered"] is True


def test_push_failure_still_propagates_and_leaves_not_notified():
    """回帰: push の失敗は今まで通り伝播し、is_notified は False のままである。"""
    memory = _memory()
    with pytest.raises(RuntimeError):
        night.run_night(DATE, NOW_H, _deps(memory=memory, push=_PushFailsClient()))
    assert memory.is_notified(DATE, "NIGHT") is False
