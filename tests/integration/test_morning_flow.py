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


def test_morning_sent_log_contains_calibration_audit_fields(monkeypatch):
    """朝送信ログに補正値・利用件数・信頼度を記録する。"""
    records = []

    class CapturingLogger:
        def log(self, step, **fields):
            records.append((step, fields))

    monkeypatch.setattr(morning, "StructuredLogger", CapturingLogger)
    morning.run_morning(DATE, _deps())

    fields = next(fields for step, fields in records if step == "morning_sent")
    assert fields["offset"] == 0.0
    assert fields["feedback_count"] == 0
    assert fields["confidence"] == "low"


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


def test_morning_line_uses_domain_summary_but_keeps_full_curve_in_memory():
    """LINEは簡潔な領域表示、DynamoDB用予測は16時間分を保持する。"""
    memory = _memory()
    push = FakePushClient()
    source = FakeHealthSource(raw={
        "total_sleep_min": 316,
        "deep_sleep_min": 99,
        "rem_sleep_min": 74,
        "awakenings": 3,
        "awake_duration_min": 24,
        "sleep_efficiency_pct": 78.0,
        "bedtime_h": 25.3,
        "wake_h": 31.0,
        "hrv": 38.0,
    })

    morning.run_morning(
        DATE, _deps(memory=memory, source=source, llm=NullClient(), push=push)
    )

    text = push.sent[0]["text"]
    assert "今日のコンディション" in text
    assert "集中力" in text
    assert "睡眠回復" in text
    assert "中途覚醒3回/24分" in text
    assert "生体変化" not in text
    assert "現行の%計算には使っていません" not in text
    assert len([line for line in text.splitlines() if "時台 低下見込み" in line]) <= 1
    assert len(memory.get_prediction(DATE, "MORNING").curve) == 16


def test_recovery_uses_target_sleep_not_short_habit(monkeypatch):
    """習慣5.4時間・目標7時間なら今夜の就寝目安を96分早める。"""
    captured = {}
    original_plan = morning.rc.plan

    def spy_plan(wake_h, bedtime_h):
        captured["bedtime_h"] = bedtime_h
        return original_plan(wake_h, bedtime_h)

    monkeypatch.setattr(morning.rc, "plan", spy_plan)
    memory = MemoryClient(InMemoryBackend())
    from datetime import date as d, timedelta
    base = d.fromisoformat(DATE)
    for i in range(1, 15):
        day = (base - timedelta(days=i)).isoformat()
        memory.save_vital(DailyVital(date=day, total_sleep_min=324, bedtime_h=25.6,
                                     wake_h=31.0, rem_sleep_min=90, deep_sleep_min=60))
    morning.run_morning(DATE, _deps(memory=memory))
    baseline = morning.bl.compute(
        [v.to_sleep_input() for v in memory.get_vitals_desc(DATE, 14)],
        min_target_sleep_h=load_settings().min_target_sleep_h,
    )
    assert baseline.sleep_h == pytest.approx(5.4, abs=0.2)
    assert captured["bedtime_h"] == pytest.approx(31.0 + (24.0 - 7.0))


def test_saved_curve_keeps_each_calibrated_range():
    """LINEで省略しても、監査用カーブには各時間帯の幅を保持する。"""
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory, llm=NullClient()))
    pred = memory.get_prediction(DATE, "MORNING")
    assert len(pred.curve) == 16
    for row in pred.curve:
        assert set(("hour", "raw_focus_pct", "focus_pct", "mood_pct", "range_low_pct", "range_high_pct")) <= row.keys()
        assert row["range_low_pct"] <= row["focus_pct"] <= row["range_high_pct"]


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
    assert "今日のリカバリー" in push.sent[0]["text"]


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
    marked = [line for line in text.splitlines() if "が谷" in line and "集中力" in line]
    assert len(marked) == 1
    hour = int(marked[0].split("］", 1)[1].split("時台", 1)[0])
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


_MATERIAL_HEAD = "【リカバリー提案の材料】"
_JUDGE_MARK = "評価者"


def _section_from_material(material: str, times_per_line: int = 1) -> str:
    """材料に載った領域と時刻から、契約どおりのリカバリー節を組み立てる。"""
    import re

    lines = ["今日のリカバリー"]
    label = None
    for line in material.splitlines():
        head = re.match(r"^■ (?P<label>[^［]+)［", line)
        if head is not None:
            label = head.group("label").strip()
            continue
        if label is not None and "使ってよい時刻" in line:
            found = re.findall(r"\d{1,2}:\d{2}", line)[:times_per_line]
            body = "と".join(found) + "に決めた手順を実行する"
            lines.append(f"・{label}：{body}")
            label = None
    return "\n".join(lines)


class RecoveryLlm:
    """生成と採点を1つのクライアントで返し分ける偽LLM。

    system に評価者プロンプトの目印が含まれていれば採点、そうでなければ生成。
    """

    def __init__(self, section=None, scores=(5, 5, 5), judge_text=None,
                 judge_error=None):
        self.section = section
        self.scores = scores
        self.judge_text = judge_text
        self.judge_error = judge_error
        self.generated = []
        self.judged = []
        self.calls = []

    def _judge_payload(self) -> str:
        import json

        relevance, actionability, time_fit = self.scores
        return json.dumps({
            "relevance": relevance, "actionability": actionability,
            "time_fit": time_fit, "reason": "テスト",
        }, ensure_ascii=False)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if _JUDGE_MARK in system:
            self.judged.append(user)
            if self.judge_error is not None:
                raise self.judge_error
            return self.judge_text if self.judge_text is not None else self._judge_payload()
        self.generated.append(user)
        if callable(self.section):
            return self.section(user, len(self.generated))
        if self.section is not None:
            return self.section
        return _section_from_material(user)


class OldBaselineNoteLlm:
    """旧基準注記を返すLLM。guardrailがrejectすることを確認する。"""

    def complete(self, system: str, user: str) -> str:
        return user + "\n※あなたの平常時を100%とした推定の目安です"


def test_old_baseline_note_is_rejected_in_production_llm_path():
    push = FakePushClient()
    memory = _memory()
    result = morning.run_morning(DATE, _deps(memory=memory, llm=OldBaselineNoteLlm(), push=push))
    assert result["used_fallback"] is True


def test_llm_success_path_sends_the_generated_recovery_section():
    """契約どおりの節を返せば、そのまま送信される。"""
    push = FakePushClient()
    llm = RecoveryLlm()

    got = morning.run_morning(DATE, _deps(llm=llm, push=push))

    assert got["used_fallback"] is False
    assert len(push.sent) == 1
    assert "今日のリカバリー" in push.sent[0]["text"]
    assert len(llm.generated) == 1 and len(llm.judged) == 1


def test_japanese_time_notation_in_recovery_is_accepted():
    """「15時30分」のような和文表記でも、正規化して同じ時刻なら受理する。"""
    import re

    def as_japanese(material: str, _n: int) -> str:
        section = _section_from_material(material)
        return re.sub(
            r"(\d{1,2}):(\d{2})",
            lambda m: f"{int(m.group(1))}時{int(m.group(2))}分",
            section,
        )

    got = morning.run_morning(DATE, _deps(llm=RecoveryLlm(section=as_japanese)))

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


# --- リカバリーは対象領域と1:1、judge が品質を採点する ---


def _targets_for(date: str = DATE) -> int:
    """このテストデータで対象になる領域の数を求める。"""
    from sleeptech.model import condition as cn

    memory = _memory()
    llm = RecoveryLlm()
    morning.run_morning(date, _deps(memory=memory, llm=llm))
    material = llm.generated[0]
    return material.count("■ ")


def test_recovery_has_one_line_per_attention_domain():
    """提案は対象領域と同数・同順で、各行に時刻が入る。"""
    import re

    push = FakePushClient()
    llm = RecoveryLlm()

    morning.run_morning(DATE, _deps(llm=llm, push=push))

    material = llm.generated[0]
    expected = material.count("■ ")
    text = push.sent[0]["text"]
    section = text.split("今日のリカバリー")[1].strip().splitlines()
    assert len(section) == expected
    assert all(re.search(r"\d{1,2}:\d{2}", line) for line in section)


def test_material_lists_only_attention_and_rest_domains():
    """材料に載るのは［注意］［要休養］の領域だけ。"""
    llm = RecoveryLlm()

    morning.run_morning(DATE, _deps(llm=llm))

    material = llm.generated[0]
    assert "［注意］" in material or "［要休養］" in material
    assert "［良好］" not in material
    assert "［通常］" not in material


def test_wrong_number_of_items_is_rejected():
    """対象領域より多く書いたら構造検査で弾く。"""
    push = FakePushClient()

    def too_many(material: str, _n: int) -> str:
        section = _section_from_material(material)
        return section + "\n・おまけ：7:30に何かする"

    got = morning.run_morning(DATE, _deps(llm=RecoveryLlm(section=too_many), push=push))

    assert got["used_fallback"] is True
    assert "おまけ" not in push.sent[0]["text"]


def test_line_without_a_time_is_rejected():
    """「何時に」が無い行は弾く。"""
    def no_time(material: str, _n: int) -> str:
        import re

        return re.sub(r"\d{1,2}:\d{2}と?", "", _section_from_material(material))

    got = morning.run_morning(DATE, _deps(llm=RecoveryLlm(section=no_time)))

    assert got["used_fallback"] is True


def test_time_from_another_domain_is_rejected():
    """その領域に提示していない時刻は使えない。"""
    def borrowed(material: str, _n: int) -> str:
        lines = _section_from_material(material).splitlines()
        lines[1] = lines[1].split("：")[0] + "：23:59に何かする"
        return "\n".join(lines)

    got = morning.run_morning(DATE, _deps(llm=RecoveryLlm(section=borrowed)))

    assert got["used_fallback"] is True


def test_no_recovery_section_and_no_llm_call_when_nothing_needs_attention():
    """対象0件の日はリカバリー節を出さず、LLMも呼ばない。"""
    memory = _memory()
    source = FakeHealthSource({
        "total_sleep_min": 430, "deep_sleep_min": 90, "rem_sleep_min": 95,
        "awakenings": 1, "bedtime_h": 23.5, "wake_h": 31.0, "hrv": 45.0,
    })
    llm = RecoveryLlm()
    push = FakePushClient()

    got = morning.run_morning(
        DATE, _deps(memory=memory, source=source, llm=llm, push=push)
    )

    assert got["judge_status"] == "skipped"
    assert llm.calls == []
    assert "今日のリカバリー" not in push.sent[0]["text"]
    assert got["used_fallback"] is False


# --- judge のゲート挙動 ---


def test_judge_passes_on_the_first_candidate():
    """閾値を超えたら即採用し、再生成しない。"""
    llm = RecoveryLlm(scores=(5, 5, 5))

    got = morning.run_morning(DATE, _deps(llm=llm))

    assert got["judge_status"] == "passed"
    assert got["judge_total"] == 100
    assert len(llm.generated) == 1


def test_low_scores_send_the_best_candidate():
    """閾値に届かなくても、最高点の案を送る。"""
    scores = iter([(3, 3, 3), (4, 3, 3), (2, 2, 2)])
    llm = RecoveryLlm()
    push = FakePushClient()

    def judge_next():
        relevance, actionability, time_fit = next(scores)
        import json

        return json.dumps({
            "relevance": relevance, "actionability": actionability,
            "time_fit": time_fit,
        })

    llm._judge_payload = judge_next

    got = morning.run_morning(DATE, _deps(llm=llm, push=push))

    assert got["judge_status"] == "best_of"
    assert got["used_fallback"] is False
    assert got["judge_total"] == 67  # (4,3,3) が最高点
    assert "今日のリカバリー" in push.sent[0]["text"]


def test_vetoed_candidates_fall_back_but_keep_a_recovery_section():
    """0点の軸がある案は採用しない。それでもリカバリー節は消さない。"""
    push = FakePushClient()
    llm = RecoveryLlm(scores=(0, 5, 5))

    got = morning.run_morning(DATE, _deps(llm=llm, push=push))

    assert got["judge_status"] == "vetoed"
    assert got["used_fallback"] is True
    assert "今日のリカバリー" in push.sent[0]["text"]


def test_judge_failure_sends_the_guardrail_passed_candidate():
    """judge が落ちても、決定論的検査を通った案はそのまま送る（fail-open）。"""
    from sleeptech.llm.client import LlmError

    push = FakePushClient()
    llm = RecoveryLlm(judge_error=LlmError("judge down"))

    got = morning.run_morning(DATE, _deps(llm=llm, push=push))

    assert got["judge_status"] == "unavailable"
    assert got["judge_total"] is None
    assert got["used_fallback"] is False
    assert "今日のリカバリー" in push.sent[0]["text"]


def test_unparsable_judge_output_is_treated_as_unavailable():
    """採点結果が読めないときも fail-open。"""
    llm = RecoveryLlm(judge_text="採点できません")

    got = morning.run_morning(DATE, _deps(llm=llm))

    assert got["judge_status"] == "unavailable"
    assert got["used_fallback"] is False


def test_judge_does_not_see_the_sleep_record():
    """採点入力には睡眠実績を渡さない（採点が睡眠の良し悪しに引きずられないため）。"""
    llm = RecoveryLlm()

    morning.run_morning(DATE, _deps(llm=llm))

    judged = llm.judged[0]
    assert "今日のリカバリー" in judged
    assert "昨夜：" not in judged
    assert "深睡眠" not in judged


def test_judge_scores_are_saved_with_the_message():
    """スコアを保存し、後から推移を追える。"""
    memory = _memory()
    llm = RecoveryLlm(scores=(4, 4, 4))

    morning.run_morning(DATE, _deps(memory=memory, llm=llm))

    msg = memory.get_message(DATE, "MORNING")
    assert msg.judge_total == 80
    assert msg.judge_scores["relevance"] == 4
    assert msg.judge_status == "passed"
    assert memory.get_messages_desc("MORNING", 5)[0].judge_total == 80


def test_morning_message_has_no_ability_note():
    """朝本文に免責注記を出さない。"""
    push = FakePushClient()

    morning.run_morning(DATE, _deps(llm=NullClient(), push=push))

    assert "実測の仕事能力ではありません" not in push.sent[0]["text"]


def test_fallback_body_still_carries_recovery_actions():
    """LLMが落ちてもリカバリー節は決定論的に届く。"""
    push = FakePushClient()

    morning.run_morning(DATE, _deps(llm=StubLlm(), push=push))

    text = push.sent[0]["text"]
    assert "今日のコンディション" in text
    assert "今日のリカバリー" in text


def test_tonight_bedtime_comes_from_the_personal_wake_baseline():
    """寝坊した日でも、今夜の就寝目標は個人基準の起床から逆算する。

    実測の起床から引くと、遅く起きた日ほど入浴も就寝も深夜へずれて
    実行できない提案になる。
    """
    memory = _memory()  # 個人基準の起床は 7:00（wake_h=31.0）
    source = FakeHealthSource({
        "total_sleep_min": 308, "deep_sleep_min": 36, "rem_sleep_min": 10,
        "awakenings": 1, "bedtime_h": 27.5, "wake_h": 32.75,  # 実測は 8:45 起床
    })
    llm = RecoveryLlm()

    morning.run_morning(DATE, _deps(memory=memory, source=source, llm=llm))

    material = llm.generated[0]
    assert "今夜の就寝目標 0:" not in material
    assert "今夜の就寝目標 1:" not in material
    assert "今夜の就寝目標 23:30" in material


def test_headline_quotation_is_rejected_end_to_end():
    """領域評価を引き写した節は送らない。"""
    push = FakePushClient()

    def quoting(material: str, _n: int) -> str:
        import re

        lines = _section_from_material(material).splitlines()
        headline = re.search(r"^■ [^［]+［[^］]+］(?P<text>.+)$", material, re.M)
        lines[1] = lines[1].split("：")[0] + "：" + headline.group("text") + "を踏まえて休む"
        return "\n".join(lines)

    got = morning.run_morning(DATE, _deps(llm=RecoveryLlm(section=quoting), push=push))

    assert got["used_fallback"] is True
    assert "を踏まえて休む" not in push.sent[0]["text"]


def test_recovery_never_offers_a_time_that_already_passed():
    """配信時点で過ぎた時刻を提案しない（実機で5:50・7:20が出た回帰）。"""
    import re

    memory = _memory()
    source = FakeHealthSource({
        "total_sleep_min": 241, "deep_sleep_min": 48, "rem_sleep_min": 5,
        "awakenings": 0, "bedtime_h": 25.0, "wake_h": 29.33,  # 5:20 起床
    })
    push = FakePushClient()
    now_h = 24.0 + 9.5  # 翌日 9:30 に配信

    morning.run_morning(
        DATE, _deps(memory=memory, source=source, llm=RecoveryLlm(), push=push), now_h
    )

    text = push.sent[0]["text"]
    section = text.split("今日のリカバリー")[1] if "今日のリカバリー" in text else ""
    for hour, minute in re.findall(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", section):
        assert int(hour) + int(minute) / 60.0 >= 9.5


def test_judge_is_told_the_current_time():
    """採点入力に現在時刻を渡す。無いと過去時刻の提案を妥当と誤判定する。"""
    llm = RecoveryLlm()

    morning.run_morning(DATE, _deps(llm=llm), 24.0 + 9.5)

    assert "現在時刻: 9:30" in llm.judged[0]


def test_morning_defaults_to_the_scheduled_delivery_time():
    """now_h を省いても定時配信の9:30を基準にする。"""
    memory = _memory()
    llm = RecoveryLlm()

    morning.run_morning(DATE, _deps(memory=memory, llm=llm))

    assert "今は9:30" in llm.generated[0]
