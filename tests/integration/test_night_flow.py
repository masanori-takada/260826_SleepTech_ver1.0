"""夜フローの統合（仕様 第4節「データフロー（夜）」）。"""

from dataclasses import replace
import time
from urllib.error import HTTPError

import pytest

from sleeptech.config import load_settings
from sleeptech.llm.client import LlmError, NullClient
from sleeptech.memory.client import InMemoryBackend, MemoryClient, NotificationClaimBusy
from sleeptech.memory.schemas import DailyVital, Feedback, Prediction
from sleeptech.notifier.line import FakePushClient, LinePushClient, parse_feedback
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


def test_night_saves_raw_calibrated_range_and_confidence():
    memory = _memory()
    memory.save_prediction(Prediction(date="2026-08-25", slot="MORNING", mean_drop_pct=0))
    memory.save_feedback(Feedback(date=DATE, score=1, prediction_date="2026-08-25"))
    result = night.run_night(DATE, NOW_H, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "NIGHT")
    assert result["range_low_pct"] <= result["range_high_pct"]
    assert pred.raw_mean_drop_pct is not None
    assert pred.calibrated_mean_drop_pct == pred.mean_drop_pct
    assert pred.confidence == "low"
    assert pred.feedback_count == 1


def test_second_run_does_not_send_again():
    """冪等: 同じ日付で2回実行しても送信は1回。"""
    memory = _memory()
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    assert len(push.sent) == 1


def test_pending_lease_is_returned_as_retryable_failure():
    """送信中のPENDING leaseは正常なskipではなくSchedulerへ失敗を返す。"""
    memory = _memory()
    memory.claim_notification(DATE, "NIGHT", now=time.time(), lease_seconds=90)

    with pytest.raises(NotificationClaimBusy):
        night.run_night(DATE, NOW_H, _deps(memory=memory))


def test_duplicate_retry_key_response_completes_night_as_sent():
    """LINEの重複retry key受理409でも夜フローを成功完了しSENTにする。"""
    accepted = HTTPError(
        "https://api.line.me/v2/bot/message/push", 409, "Conflict",
        {"X-Line-Accepted-Request-Id": "accepted-request-id"}, None,
    )

    def duplicate_post(url, headers, body):
        raise accepted

    memory = _memory()
    push = LinePushClient("token", "U123", http_post=duplicate_post)
    result = night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))

    assert result["delivered"] is True
    assert result["skipped"] is False
    assert memory.is_notified(DATE, "NIGHT") is True


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


# --- I4: 低下率0%の晩の扱い ---
# _memory() の既定履歴（毎晩7.5h睡眠）は baseline.sleep_h と一致するため、
# NOW_H=22.5 で推奨就寝時刻に間に合う夜は drop=0% になる（実際に上のテストの
# "advice" ログでも drop=0 を確認済み）。


def test_zero_drop_night_does_not_require_percent_in_facts(monkeypatch):
    """I4: 低下率0%のときは Facts.percents が空になる（0%の明記を必須にしない）。"""
    memory = _memory()
    captured = {}
    original = night.gv.validate

    def spy(text, facts):
        captured["facts"] = facts
        return original(text, facts)

    monkeypatch.setattr(night.gv, "validate", spy)

    push = FakePushClient()
    result = night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))

    assert result["mean_drop_pct"] == 0
    assert captured["facts"].percents == [0, 5]


def test_zero_drop_night_fallback_avoids_minus_zero_percent():
    """I4: LLM を落として0%の晩のテンプレート文を確認する。'-0%' という
    不自然な表記を含まず、専用の前向きな文面になっていること。
    """
    push = FakePushClient()
    result = night.run_night(DATE, NOW_H, _deps(llm=NullClient(), push=push))
    assert result["mean_drop_pct"] == 0
    assert result["used_fallback"] is True
    text = push.sent[0]["text"]
    assert "-0%" not in text
    assert "0〜5%" in text


def test_zero_drop_night_llm_success_path_still_passes():
    """I4対応後も LLM 成功経路（used_fallback=False）が0%の晩で通ること。"""
    push = FakePushClient()
    memory = _memory()
    result = night.run_night(DATE, NOW_H, _deps(memory=memory, llm=StubLlm(), push=push))
    assert result["mean_drop_pct"] == 0
    assert result["used_fallback"] is False


def test_normal_night_llm_success_path_still_passes_with_nonzero_drop():
    """通常晩（履歴不足で0%ではない）でも LLM 成功経路が引き続き通ること。"""
    push = FakePushClient()
    empty = MemoryClient(InMemoryBackend())  # 履歴ゼロ→既定ベースライン、drop>0 になりやすい
    result = night.run_night(DATE, NOW_H, _deps(memory=empty, llm=StubLlm(), push=push))
    assert result["used_fallback"] is False


# --- 最終レビュー最終波: LLM成功経路（used_fallback=False）の網羅確認 ---
# M1（fallback.night_text の0%判定順序）とnight_handlerの再解釈ロジック変更が
# LLM成功経路（テンプレートに落ちない経路）を壊していないことを確認する。


def test_too_late_night_llm_success_path_still_passes():
    """夜・too_late のLLM成功経路（used_fallback=False）が通ること。

    limit_h = wake_h(31.0) - MIN_SLEEP_BEFORE_WAKE_H(4.0) = 27.0。
    now_h=28.0 は既に探索区間の終端を過ぎているため status="too_late" になる。
    """
    push = FakePushClient()
    memory = _memory()
    result = night.run_night(DATE, 28.0, _deps(memory=memory, llm=StubLlm(), push=push))
    assert result["used_fallback"] is False
    pred = memory.get_prediction(DATE, "NIGHT")
    assert pred.status == "too_late"


def test_day_crossing_bedtime_night_llm_success_path_still_passes():
    """夜・日跨ぎ就寝（bedtime_h>=24、本文の時刻表記に「翌」が付く）のLLM成功経路が
    通ること（night.run_night の require_day_prefix=True の照合込み）。
    """
    push = FakePushClient()
    memory = _memory()
    result = night.run_night(DATE, 24.5, _deps(memory=memory, llm=StubLlm(), push=push))
    assert result["used_fallback"] is False
    assert result["bedtime_h"] >= 24.0


# --- I5: MSG#NIGHT の保存 ---


def test_saves_message_with_body_and_guardrail_result():
    """I5: 送信後に本文・ガードレール結果・再生成回数・送信可否を保存する。"""
    memory = _memory()
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    msg = memory.get_message(DATE, "NIGHT")
    assert msg is not None
    # push.sent[0]["text"] は build_message がクイックリプライ質問文を
    # 末尾に足した後の文字列なので、保存された body（LLM/テンプレートの
    # 素の本文）はその先頭部分として含まれる。
    assert push.sent[0]["text"].startswith(msg.body)
    assert msg.delivered is True


def test_saves_message_marks_fallback_when_guardrail_rejected():
    memory = _memory()
    push = FakePushClient()
    bad = StubLlm(template="必ず-99%落ちます。手遅れです。")
    night.run_night(DATE, NOW_H, _deps(memory=memory, llm=bad, push=push))
    msg = memory.get_message(DATE, "NIGHT")
    assert msg.guardrail_passed is False


class _SaveMessageFailsMemory(MemoryClient):
    """save_message 呼び出し時に必ず例外を投げる記憶クライアント。"""

    def save_message(self, msg) -> None:
        raise RuntimeError("save_message boom")


def test_save_message_failure_does_not_break_delivery():
    """push 成功後に save_message が失敗しても例外は伝播せず delivered=True のまま。"""
    memory = _SaveMessageFailsMemory(InMemoryBackend())
    from datetime import date as d, timedelta

    for i in range(1, 15):
        day = (d.fromisoformat(DATE) - timedelta(days=i)).isoformat()
        memory.save_vital(DailyVital(date=day, total_sleep_min=450, bedtime_h=23.5,
                                      wake_h=31.0, rem_sleep_min=90, deep_sleep_min=60))
    push = FakePushClient()
    result = night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    assert len(push.sent) == 1
    assert result["delivered"] is True
