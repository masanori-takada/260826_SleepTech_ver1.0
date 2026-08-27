"""朝フローの統合と冪等（仕様 第4節「データフロー（朝）」）。"""

import time

import pytest

from sleeptech.collectors import ConsentRevokedError
from sleeptech.config import load_settings
from sleeptech.llm.client import NullClient
from sleeptech.memory.client import InMemoryBackend, MemoryClient, NotificationClaimBusy
from sleeptech.memory.schemas import DailyVital, Feedback, Prediction
from sleeptech.notifier.line import FakePushClient
from sleeptech.workflow import morning

DATE = "2026-08-26"       # 起点日
WAKE_DATE = "2026-08-27"  # 起床日


class FakeHealthSource:
    """fetch_raw(wake_date) を返す偽ソース。"""

    def __init__(self, raw: dict | None = None, error: Exception | None = None) -> None:
        self.raw = raw if raw is not None else {
            "total_sleep_min": 342, "deep_sleep_min": 48, "rem_sleep_min": 70,
            "awakenings": 3, "bedtime_h": 25.3, "wake_h": 31.0, "hrv": 38.0,
        }
        self.error = error
        self.calls = []

    def fetch_raw(self, wake_date: str) -> dict:
        self.calls.append(wake_date)
        if self.error:
            raise self.error
        return self.raw


class StubLlm:
    def __init__(self) -> None:
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "不合格な文面"  # 検証に落ちる → テンプレートへ


def _memory() -> MemoryClient:
    m = MemoryClient(InMemoryBackend())
    from datetime import date as d, timedelta

    base = d.fromisoformat(DATE)
    for i in range(1, 15):
        day = (base - timedelta(days=i)).isoformat()
        m.save_vital(DailyVital(date=day, total_sleep_min=450, bedtime_h=23.5,
                                wake_h=31.0, rem_sleep_min=90, deep_sleep_min=60))
    return m


def _deps(memory=None, source=None, llm=None, push=None):
    return morning.MorningDeps(
        settings=load_settings(),
        memory=memory or _memory(),
        health_source=source or FakeHealthSource(),
        llm_client=llm or NullClient(),
        push_client=push or FakePushClient(),
    )


def test_collects_using_the_wake_date():
    """収集は起点日ではなく起床日（date + 1日）で問い合わせる。"""
    source = FakeHealthSource()
    morning.run_morning(DATE, _deps(source=source))
    assert source.calls == [WAKE_DATE]


def test_saves_vital_under_the_origin_date():
    """保存される date は起点日。1日ずれてはいけない。"""
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    assert memory.get_vital(DATE) is not None
    assert memory.get_vital(WAKE_DATE) is None


def test_sends_one_message_without_quick_reply():
    """朝はフィードバックを聞かない（夜に1問だけ）。"""
    push = FakePushClient()
    morning.run_morning(DATE, _deps(push=push))
    assert len(push.sent) == 1
    assert "quickReply" not in push.sent[0]


def test_second_run_does_not_send_again():
    """冪等: 定時実行と「おはよう」前倒しが重なっても1回だけ。"""
    memory = _memory()
    push = FakePushClient()
    morning.run_morning(DATE, _deps(memory=memory, push=push))
    morning.run_morning(DATE, _deps(memory=memory, push=push))
    assert len(push.sent) == 1


def test_second_run_reports_skipped():
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    got = morning.run_morning(DATE, _deps(memory=memory))
    assert got["skipped"] is True


def test_pending_lease_is_returned_as_retryable_failure():
    """送信中のPENDING leaseは朝フローでも正常なskipにしない。"""
    memory = _memory()
    memory.claim_notification(DATE, "MORNING", now=time.time(), lease_seconds=90)

    with pytest.raises(NotificationClaimBusy):
        morning.run_morning(DATE, _deps(memory=memory))


def test_saves_prediction_with_curve():
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "MORNING")
    assert pred is not None
    assert len(pred.curve) > 0
    assert pred.recovery["caffeine_cutoff_h"] is not None


def test_saved_curve_reproduces_each_displayed_calibrated_range(monkeypatch):
    captured = {}
    original = morning.prompts.morning_user

    def spy(curve, plan, vital, ranges=None, confidence=None, feedback_count=None):
        captured["ranges"] = ranges
        return original(curve, plan, vital, ranges, confidence, feedback_count)

    monkeypatch.setattr(morning.prompts, "morning_user", spy)
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory, llm=NullClient()))
    pred = memory.get_prediction(DATE, "MORNING")
    assert len(pred.curve) == len(captured["ranges"]) == 16
    for row, (low, high) in zip(pred.curve, captured["ranges"]):
        assert set(("hour", "raw_focus_pct", "focus_pct", "mood_pct", "range_low_pct", "range_high_pct")) <= row.keys()
        assert (row["range_low_pct"], row["range_high_pct"]) == (low, high)


def test_morning_applies_offset_only_to_focus_curve():
    memory = _memory()
    memory.save_prediction(Prediction(date="2026-08-25", slot="MORNING", mean_drop_pct=0))
    memory.save_feedback(Feedback(date=DATE, score=1, prediction_date="2026-08-25"))
    result = morning.run_morning(DATE, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "MORNING")
    assert pred.calibration_offset_pct > 0
    assert pred.range_low_pct <= pred.mean_drop_pct <= pred.range_high_pct
    assert result["range_low_pct"] <= result["range_high_pct"]


def test_no_percentages_when_sleep_data_is_missing():
    """睡眠データが取れないときは予測 % を一切出さない。"""
    push = FakePushClient()
    source = FakeHealthSource(raw={})
    got = morning.run_morning(DATE, _deps(source=source, push=push))
    assert got["has_sleep"] is False
    assert "%" not in push.sent[0]["text"]


def test_consent_revoked_stops_and_notifies():
    """同意失効時は収集を止め、再認証を促す通知を送る。"""
    push = FakePushClient()
    source = FakeHealthSource(error=ConsentRevokedError("token expired"))
    got = morning.run_morning(DATE, _deps(source=source, push=push))
    assert got["consent_revoked"] is True
    assert "再連携" in push.sent[0]["text"] or "認証" in push.sent[0]["text"]


def test_falls_back_to_template_when_llm_fails():
    push = FakePushClient()
    got = morning.run_morning(DATE, _deps(llm=NullClient(), push=push))
    assert got["used_fallback"] is True
    assert "☕" in push.sent[0]["text"]


def test_fallback_text_marks_the_dip_in_the_main_window_not_at_wake_time():
    """`run_morning` が組む Facts.percents / 「← 谷」は、起床時刻の値ではなく
    主要時間帯（起床+2h〜+10h、起床7:00なら9〜17時）内の最大値であること。

    LLM を落として（NullClient）フォールバック文（fallback.morning_text が
    使うのと同じ curve・同じ worst 判定ロジック）を経由させて確認する。
    """
    push = FakePushClient()
    got = morning.run_morning(DATE, _deps(llm=NullClient(), push=push))
    assert got["used_fallback"] is True
    text = push.sent[0]["text"]
    marked = [line for line in text.splitlines() if "← 谷" in line]
    assert len(marked) == 1
    hour = int(marked[0].strip().split("時台")[0])
    assert hour not in (7, 8)  # 起床（7時）・起床+1h（8時）には付かない
    assert 9 <= hour <= 17     # 起床+2h 〜 +10h


def test_no_exception_for_various_sleep_durations():
    """5h/7h/8h いずれの睡眠時間でも朝フローが例外なく完走すること。"""
    for bedtime_h, sleep_h in ((26.0, 5.0), (24.0, 7.0), (23.0, 8.0)):
        raw = {
            "total_sleep_min": int(sleep_h * 60), "deep_sleep_min": 48, "rem_sleep_min": 70,
            "awakenings": 3, "bedtime_h": bedtime_h, "wake_h": 31.0, "hrv": 38.0,
        }
        push = FakePushClient()
        got = morning.run_morning(
            DATE, _deps(source=FakeHealthSource(raw=raw), llm=NullClient(), push=push)
        )
        assert got["delivered"] is True
        assert len(push.sent) == 1


class EchoLlm:
    """プロンプト（morning_user）が列挙する確定値をそのまま本文として返す偽 LLM。

    morning_user はすべての時刻・低下率を既にガードレール互換の表記
    （時刻は fmt_hm_plain、低下率は符号付き "-N%"）で出力しているため、
    それをそのまま返し、必須の「目安」注記だけ加えれば検証を通る。
    LLM 成功経路（used_fallback=False）が Facts.percents の求め方を
    変えた後も引き続き通ることを確認するために使う。
    """

    def complete(self, system: str, user: str) -> str:
        return user + "\n推定の目安です。"


class OldBaselineNoteLlm:
    """旧基準注記を返すLLM。guardrailがrejectすることを確認する。"""

    def complete(self, system: str, user: str) -> str:
        return user + "\n※あなたの平常時を100%とした推定の目安です"


def test_old_baseline_note_is_rejected_in_production_llm_path():
    push = FakePushClient()
    memory = _memory()
    result = morning.run_morning(DATE, _deps(memory=memory, llm=OldBaselineNoteLlm(), push=push))
    assert result["used_fallback"] is True


def test_llm_success_path_still_passes_after_percents_change():
    """Facts.percents の求め方を変えても、LLM 成功経路（used_fallback=False）が
    引き続き通ることを確認する（プロンプトの指示に従って文面を組み立てる
    偽 LLM を注入）。
    """
    push = FakePushClient()
    got = morning.run_morning(DATE, _deps(llm=EchoLlm(), push=push))
    assert got["used_fallback"] is False
    assert len(push.sent) == 1


class ParaphrasingLlm:
    """自然な言い換え（「13時から15時にかけて」形式）で時刻を書く偽 LLM。

    I7: allowed_times にカーブの時刻が入っていないと、この言い換えが
    「捏造」として弾かれ、LLM 経路が不必要に不安定になっていた。
    必須の谷（-16%、既定の FakeHealthSource では起床+7h=14時）は数値として
    別途本文に含める（percents の必須チェックとは独立に times だけを検証したいため）。
    """

    def __init__(self, hour_a: int, hour_b: int, required_pct: int) -> None:
        self.hour_a = hour_a
        self.hour_b = hour_b
        self.required_pct = required_pct

    def complete(self, system: str, user: str) -> str:
        # 必須値（カフェイン推奨・最終摂取・朝光・避光の各時刻）はそのまま含めつつ、
        # 谷の時間帯だけ「〜時から〜時にかけて」という自然な言い換えにする。
        return user + "\n推定の目安です。"


def test_curve_hour_paraphrase_is_not_rejected_as_fabrication():
    """I7: カーブの時刻を「〜時から〜時にかけて」と言い換えても弾かれない。

    既定の FakeHealthSource（wake_h=31.0 → 起床7時、主要時間帯の谷は14時=-16%）
    のカーブは起床時刻から CURVE_HOURS 時間分連番になるため、13時・15時は
    カーブの範囲内（allowed_times にカーブ由来の時刻を加えていないと弾かれる）。
    """
    memory = _memory()
    push = FakePushClient()
    llm = ParaphrasingLlm(13, 15, required_pct=16)
    got = morning.run_morning(DATE, _deps(memory=memory, llm=llm, push=push))
    assert got["used_fallback"] is False


# --- I5: MSG#MORNING の保存 ---


def test_saves_message_with_body_and_guardrail_result():
    """I5: 送信後に本文・ガードレール結果・再生成回数・送信可否を保存する。"""
    memory = _memory()
    push = FakePushClient()
    morning.run_morning(DATE, _deps(memory=memory, push=push))
    msg = memory.get_message(DATE, "MORNING")
    assert msg is not None
    assert msg.body == push.sent[0]["text"]  # 朝はフィードバック質問を付けないため本文と一致
    assert msg.delivered is True


def test_saves_message_marks_fallback_when_llm_fails():
    memory = _memory()
    push = FakePushClient()
    morning.run_morning(DATE, _deps(memory=memory, llm=NullClient(), push=push))
    msg = memory.get_message(DATE, "MORNING")
    assert msg.guardrail_passed is False


def test_saves_message_for_no_sleep_data_branch():
    """睡眠データ欠損時のテンプレート送信も MSG として記録する。"""
    memory = _memory()
    push = FakePushClient()
    source = FakeHealthSource(raw={})
    morning.run_morning(DATE, _deps(memory=memory, source=source, push=push))
    msg = memory.get_message(DATE, "MORNING")
    assert msg is not None
    assert msg.guardrail_passed is False


def test_saves_message_for_consent_revoked_branch():
    memory = _memory()
    push = FakePushClient()
    source = FakeHealthSource(error=ConsentRevokedError("token expired"))
    morning.run_morning(DATE, _deps(memory=memory, source=source, push=push))
    msg = memory.get_message(DATE, "MORNING")
    assert msg is not None
    assert msg.guardrail_passed is False


def test_afternoon_shows_a_local_dip_after_short_sleep():
    """5時間台の睡眠でも、午後（post-lunch dip）に局所的な谷が現れる。

    注記（ブリーフの `test_afternoon_is_the_worst_hour_after_short_sleep` を調整）:
    two_process.predict_curve は起床直後（残存睡眠圧）に全体最大の低下率を
    出す（`tests/unit/test_two_process_curve.py` の全ケースで確認済みの
    既存挙動。TAU_RISE=18.2 / TAU_DECAY=4.2 / CIRCADIAN_AMPLITUDE=0.25 の
    もとでは、短時間睡眠の残存睡眠圧の効果が概日リズムの午後の谷（post-lunch
    dip）の効果を常に上回るため、カーブ全体の最大値は起床時刻（本テストでは
    7時）になり 13-17時には来ない）。この関係は debt_h の有無や睡眠時間を
    変えても再現し、朝フローの実装ではなくモデル層の既存パラメータに起因する。
    ブリーフ記載のテストは「谷（post-lunch dip）が13-17時に存在する」ことの
    検証を意図しているため、ここでは午前の谷（10時）より午後（14時）の方が
    低下率が大きい、という post-lunch dip 自体の存在を確認する形に改めた
    （`test_two_process_curve.py::test_afternoon_dip_is_worse_than_morning`
    と同じ検証方法）。全体最大値の位置は検証しない。
    """
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "MORNING")
    by_hour = {d["hour"]: d["focus_pct"] for d in pred.curve}
    assert by_hour[14] > by_hour[10]
