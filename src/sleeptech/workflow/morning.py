"""朝フロー（仕様 第4節「データフロー（朝）」）。

冪等チェックを冒頭に、フラグ設定を末尾に置く。この関数は MorningFn（9:30 定時）と
WebhookFn（「おはよう」検出）の両方から呼ばれるため、ここに閉じ込めないと二重送信する。
転用元プロジェクトは webhook 側で収集のみを先行実行し送信は無条件だったため、
その設計をそのまま持ち込むと二重送信になる。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date as date_cls
from datetime import timedelta

from sleeptech.collectors import ConsentRevokedError
from sleeptech.collectors import vitals as vt
from sleeptech.guardrail import validator as gv
from sleeptech.llm.client import ATTEMPT_TIMEOUT_SEC
from sleeptech.llm import judge as jg
from sleeptech.llm import prompts
from sleeptech.memory.schemas import Prediction
from sleeptech.model import baseline as bl
from sleeptech.model import constants as C
from sleeptech.model.calibration import apply_offset, display_range
from sleeptech.model import recovery as rc
from sleeptech.model import condition as cn
from sleeptech.model import two_process as tp
from sleeptech.notifier.line import build_message
from sleeptech.observability import StructuredLogger
from sleeptech.workflow._common import push as _push
from sleeptech.workflow._common import retry_key as _retry_key
from sleeptech.workflow._common import save_message as _save_message
from sleeptech.workflow._calibration import load_calibration

SLOT = "MORNING"

# 1ラウンド = リカバリー生成 + 採点。両方ぶんの実行時間を先に確保する。
_ROUND_BUDGET_SEC = ATTEMPT_TIMEOUT_SEC * 2

_NO_DATA_TEXT = (
    "おはようございます。今朝は睡眠データを取得できませんでした。\n"
    "予測は出せませんが、起床後30分以内に屋外光を浴びておくと今日の立ち上がりが変わります。"
)
_CONSENT_TEXT = (
    "睡眠データにアクセスできなくなりました。Google Health の再連携（再認証）をお願いします。"
)


@dataclass
class MorningDeps:
    settings: object
    memory: object
    health_source: object
    llm_client: object
    push_client: object
    max_regen: int = 2
    deadline: object | None = None


# 朝の定時配信時刻。起点日 0:00 からの経過時間（翌日 9:30）。
DEFAULT_MORNING_NOW_H = 24.0 + 9.5


def run_morning(date: str, deps: MorningDeps, now_h: float | None = None) -> dict:
    """朝フローを実行する。date は起点日（前夜の夜メッセージと同じ日付）。

    now_h は起点日 0:00 からの経過時間で表した配信時刻。既に過ぎた時刻を
    リカバリーに出さないために使う。省略時は定時配信の 9:30 とみなす。
    """
    now_h = DEFAULT_MORNING_NOW_H if now_h is None else now_h
    log = StructuredLogger()
    log.log("morning_start", date=date)

    # 1. 冪等（定時実行と「おはよう」前倒しが重なっても1回だけ送るためのガード）
    if deps.memory.is_notified(date, SLOT) or not deps.memory.claim_notification(date, SLOT):
        log.log("morning_skipped", reason="already_notified")
        return {"delivered": False, "skipped": True}

    # 2-3. 収集と保存（Google Health は起床日で睡眠を返すため wake_date = date + 1日）
    wake_date = (date_cls.fromisoformat(date) + timedelta(days=1)).isoformat()
    try:
        raw = deps.health_source.fetch_raw(wake_date)
    except ConsentRevokedError as exc:
        log.log("consent_revoked", error=str(exc))
        # M2対応: _push は push_client.push の例外を捕捉せず送出するため、
        # ここに到達している時点で送信は成功済み。delivered=True は実態と一致する。
        _push(deps, build_message(_CONSENT_TEXT, with_feedback=False), _retry_key(date, SLOT))
        _mark_notified(deps, date, log)
        _save_message(
            deps, date, SLOT, _CONSENT_TEXT,
            guardrail_passed=False, regen_count=0, delivered=True, log=log,
        )
        return {
            "delivered": True,
            "skipped": False,
            "consent_revoked": True,
            "has_sleep": False,
            "used_fallback": True,
        }

    vital = vt.to_daily_vital(raw, wake_date)
    deps.memory.save_vital(vital)
    log.log("vital_saved", date=vital.date, has_sleep=vital.has_sleep)

    # 睡眠データが取れていなければ予測 % を一切出さない（推測で数字を出さない）
    if not vital.has_sleep:
        # M2対応: _push は push_client.push の例外を捕捉せず送出するため、
        # ここに到達している時点で送信は成功済み。delivered=True は実態と一致する。
        _push(deps, build_message(_NO_DATA_TEXT, with_feedback=False), _retry_key(date, SLOT))
        _mark_notified(deps, date, log)
        _save_message(
            deps, date, SLOT, _NO_DATA_TEXT,
            guardrail_passed=False, regen_count=0, delivered=True, log=log,
        )
        return {
            "delivered": True,
            "skipped": False,
            "consent_revoked": False,
            "has_sleep": False,
            "used_fallback": True,
        }

    # 4. 予測カーブ
    history = deps.memory.get_vitals_desc(date, C.BASELINE_LOOKBACK_DAYS)
    sleeps = [s for s in (v.to_sleep_input() for v in history) if s is not None]
    override = deps.memory.get_config().get("override_wake_h", deps.settings.override_wake_h)
    baseline = bl.compute(
        sleeps,
        override_wake_h=override,
        min_target_sleep_h=deps.settings.min_target_sleep_h,
    )
    debt_h = tp.sleep_debt_h(sleeps, baseline)
    curve = tp.predict_curve(vital.to_sleep_input(), baseline, debt_h)
    cal = load_calibration(deps.memory, baseline.days_used)
    calibrated_focus = [apply_offset(d.focus_pct, cal.offset_pct) for d in curve]
    ranges = [display_range(value, cal.margin_pct) for value in calibrated_focus]
    raw_mean = tp.mean_drop_in_main_window(curve, vital.wake_h)
    calibrated_mean = round(sum(calibrated_focus[i] for i, d in enumerate(curve)
                                if d in tp.main_window_slice(curve)) /
                            len(tp.main_window_slice(curve)))
    mean_low, mean_high = display_range(calibrated_mean, cal.margin_pct)

    # 5. リカバリー（今夜の就寝は「起床 + 平常時の覚醒時間」と見なす）
    # 今夜の就寝目標は「明日の起床目安 − 目標睡眠」。実測の起床から引くと、
    # 寝坊した日ほど入浴も就寝も深夜へずれて実行不能な提案になる。
    next_bedtime_h = baseline.wake_h + (24.0 - baseline.effective_target_sleep_h)
    plan = rc.plan(vital.wake_h, next_bedtime_h)
    log.log("recovery", caffeine=len(plan.caffeine_times_h))

    # 6. 計算可能な領域だけを短く表示する。16時間分のカーブは保存専用とし、
    # LINE本文には主要時間帯の谷だけを残す。
    condition_report = cn.assess_conditions(
        vital, history, curve, ranges, baseline.effective_target_sleep_h
    )
    # リカバリーは［注意］［要休養］の領域だけに1:1で出す。時刻メニューは
    # 予測カーブが示した実際の谷から組み立て、LLMにはそこから選ばせる。
    condition_body = cn.render_condition_section(condition_report, vital)
    times = rc.times(
        vital.wake_h, next_bedtime_h, _dip_h(condition_report, vital.wake_h), plan
    )
    # 配信時点で過ぎている時刻は提案に使えない。使える時刻が残らない領域は外す。
    targets = cn.actionable_targets(cn.recovery_targets(condition_report), times, now_h)
    required_lines = tuple(
        line.rstrip("。．") for line in condition_body.splitlines() if line.strip()
    )
    facts = gv.Facts(
        required_phrases=required_lines,
        allowed_times=list(cn.recovery_time_materials(targets, times, now_h)),
    )
    body, used_fallback, regen_count, judge_record = _compose(
        deps, condition_body, targets, times, facts, log, now_h,
    )

    # push の例外はここで捕捉せず伝播させる。送信していないのに mark_notified して
    # しまう事故（Lambda 再試行時の二重送信）を防ぐため（夜フローと同じ方針）。
    _push(deps, build_message(body, with_feedback=False), _retry_key(date, SLOT))
    log.log(
        "morning_sent",
        fallback=used_fallback,
        offset=cal.offset_pct,
        feedback_count=cal.feedback_count,
        confidence=cal.confidence,
    )

    # push は成功済みなので、ここで失敗扱いにすると再試行で二重送信になる。
    # mark_notified が失敗してもログに残して続行する（夜フローと同じ方針）。
    _mark_notified(deps, date, log)

    # 7. 保存（push は成功済みのため、失敗してもログに残して続行する）
    try:
        deps.memory.save_prediction(
            Prediction(
                date=date,
                slot=SLOT,
                mean_drop_pct=calibrated_mean,
                curve=[
                    {
                        "hour": d.hour,
                        "raw_focus_pct": d.focus_pct,
                        "focus_pct": calibrated_focus[i],
                        "mood_pct": d.mood_pct,
                        "range_low_pct": ranges[i][0],
                        "range_high_pct": ranges[i][1],
                    }
                    for i, d in enumerate(curve)
                ],
                recovery=asdict(plan),
                raw_mean_drop_pct=raw_mean,
                calibrated_mean_drop_pct=calibrated_mean,
                range_low_pct=mean_low,
                range_high_pct=mean_high,
                confidence=cal.confidence,
                feedback_count=cal.feedback_count,
                calibration_offset_pct=cal.offset_pct,
            )
        )
    except Exception as exc:  # noqa: BLE001 — push 成功後の失敗は伝播させない
        log.log("save_prediction_failed", error=str(exc))

    # I5対応: 仕様 第9節 MSG#MORNING。本文・ガードレール結果・再生成回数・送信可否を記録する。
    # M2対応: delivered=True は push() が例外を投げずに戻ってきたことをもって
    # 「届いた」とみなしている。_push は push_client.push の例外を捕捉せず
    # そのまま送出するため、ここに到達している時点で送信は成功済みであり、
    # True は実態と一致する。
    _save_message(
        deps, date, SLOT, body,
        guardrail_passed=not used_fallback,
        regen_count=regen_count,
        delivered=True,
        log=log,
        judge=judge_record,
    )

    return {
        "delivered": True,
        "skipped": False,
        "consent_revoked": False,
        "has_sleep": True,
        "judge_status": judge_record["judge_status"],
        "judge_total": judge_record["judge_total"],
        "mean_drop_pct": calibrated_mean,
        "range_low_pct": mean_low,
        "range_high_pct": mean_high,
        "confidence": cal.confidence,
        "feedback_count": cal.feedback_count,
        "used_fallback": used_fallback,
    }


def _mark_notified(deps: MorningDeps, date: str, log: StructuredLogger) -> None:
    """通知済みフラグを立てる。push 成功後の失敗は握りつぶしてログに残す。"""
    try:
        marked = deps.memory.mark_notified(date, SLOT)
        if not marked:
            # 条件付き書き込みが false ＝ 既に他の実行が通知済みフラグを立てていた
            # （定時実行と「おはよう」前倒しの並行実行を検知）。送信は完了しているため
            # 挙動は変えず記録のみ行う。
            log.log("mark_notified_conflict", date=date, slot=SLOT)
    except Exception as exc:  # noqa: BLE001 — push 成功後の失敗は伝播させない
        log.log("mark_notified_failed", error=str(exc))


def _dip_h(report, wake_h: float) -> float:
    """集中力の谷の時刻。カーブのインデックスは起床からの経過時間に対応する。"""
    for assessment in report.displayed_assessments:
        if assessment.domain == "focus" and assessment.dip_index is not None:
            return wake_h + float(assessment.dip_index)
    return wake_h + C.PHASE_DIP_OFFSET_H


def _judge_record(status: str, score, attempts: int) -> dict:
    """Message へ保存する judge の記録。"""
    return {
        "judge_status": status,
        "judge_total": None if score is None else score.total,
        "judge_scores": {} if score is None else score.as_dict(),
        "judge_attempts": attempts,
        "judge_reason": "" if score is None else score.reason,
    }


def _compose(deps, condition_body, targets, times, facts, log, now_h=None):
    """リカバリー節を生成し、構造・ガードレール・judge の順に検査する。

    戻り値は (本文, フォールバックか, 再生成回数, judgeの記録)。

    judge は品質の判定であって安全の判定ではない。構造検査とガードレールを
    通った案は既に送っても害がないため、judge が使えないときは fail-open で
    そのまま送る。判定できたうえで0点の軸があったときだけ拒否する。
    """
    if not targets:
        # 出す対象が無い日はLLMを呼ばない。書くことが無いうえ、
        # 8秒とガードレール不合格のリスクを払うだけになる。
        log.log("recovery_skipped", reason="no_target_domain")
        return condition_body, False, 0, _judge_record("skipped", None, 0)

    material = cn.build_recovery_material(targets, times, now_h)
    expected = cn.recovery_domain_times(targets, times, now_h)
    domain_lines = tuple(
        f"{label}［{assessment.level}］{assessment.headline}"
        for label, assessment in zip(
            (pair[0] for pair in expected), targets
        )
    )
    user = prompts.morning_recovery_user(material)

    best_body = None
    best_score = None
    attempts_made = 0
    judged = 0
    for attempt in range(deps.max_regen + 1):
        # 生成と採点で2回LLMを呼ぶ。採点できない案を作っても捨てるだけなので、
        # 2回ぶんの予算が残っているときだけ次のラウンドを始める。
        if deps.deadline is not None and not deps.deadline.can_start(_ROUND_BUDGET_SEC):
            break
        attempts_made = attempt + 1
        try:
            section = deps.llm_client.complete(prompts.MORNING_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001 — LLM 障害は必ずフォールバックへ
            log.log("llm_failed", error=str(exc))
            break

        structure = gv.validate_recovery_structure(section, expected)
        if not structure.passed:
            log.log("guardrail_rejected", attempt=attempt, reason=structure.reason)
            continue
        candidate = "\n\n".join([condition_body, section.strip()])
        result = gv.validate(candidate, facts)
        if not result.passed:
            log.log("guardrail_rejected", attempt=attempt, reason=result.reason)
            continue

        score = jg.score_recovery(
            deps.llm_client,
            prompts.JUDGE_SYSTEM,
            prompts.judge_user(domain_lines, section.strip(), now_h),
            log,
        )
        if score is None:
            # 判定できなかった。決定論的な検査は通っているのでそのまま送る。
            return candidate, False, attempt, _judge_record("unavailable", None, judged)
        judged += 1
        log.log(
            "judge_scored", attempt=attempt, total=score.total,
            eligible=score.eligible, **score.as_dict(),
        )
        if not score.eligible:
            continue
        if score.total >= jg.PASS_SCORE:
            return candidate, False, attempt, _judge_record("passed", score, judged)
        if best_score is None or score.total > best_score.total:
            best_body, best_score = candidate, score

    if best_body is not None:
        # 閾値に届かなくても、リカバリー節が消えるより最高点を送るほうがよい。
        return best_body, False, max(attempts_made - 1, 0), _judge_record(
            "best_of", best_score, judged
        )

    fallback = "\n\n".join([
        condition_body, cn.render_recovery_section(targets, times, now_h),
    ])
    status = "vetoed" if judged else "unavailable"
    return fallback, True, max(attempts_made - 1, 0), _judge_record(status, None, judged)
