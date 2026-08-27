"""朝フロー（仕様 第4節「データフロー（朝）」）。

冪等チェックを冒頭に、フラグ設定を末尾に置く。この関数は MorningFn（8:00 定時）と
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
from sleeptech.guardrail import fallback
from sleeptech.guardrail import validator as gv
from sleeptech.llm import prompts
from sleeptech.memory.schemas import Prediction
from sleeptech.model import baseline as bl
from sleeptech.model import constants as C
from sleeptech.model.calibration import apply_offset, display_range
from sleeptech.model import recovery as rc
from sleeptech.model import two_process as tp
from sleeptech.notifier.line import build_message
from sleeptech.observability import StructuredLogger
from sleeptech.workflow._common import push as _push
from sleeptech.workflow._common import retry_key as _retry_key
from sleeptech.workflow._common import save_message as _save_message
from sleeptech.workflow._calibration import load_calibration

SLOT = "MORNING"

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


def run_morning(date: str, deps: MorningDeps) -> dict:
    """朝フローを実行する。date は起点日（前夜の夜メッセージと同じ日付）。"""
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
    next_bedtime_h = vital.wake_h + (24.0 - baseline.sleep_h)
    plan = rc.plan(vital.wake_h, next_bedtime_h)
    log.log("recovery", caffeine=len(plan.caffeine_times_h))

    # 6. 文章化・検証・送信
    # 朝の16時間帯それぞれについて、時刻と低下見込みrangeを必須出力にする。
    all_pcts = [endpoint for low, high in ranges for endpoint in (low, high)]
    # 谷（LLM に必須値として要求する低下率）は主要時間帯（起床+2h〜+10h）に限定する。
    # 全区間で探すと睡眠慣性の影響で起床直後の値が常に最大になってしまうため。
    worst_index = max(range(len(curve)), key=lambda i: calibrated_focus[i]
                      if i >= C.MAIN_WINDOW_START_H and i <= C.MAIN_WINDOW_END_H else -1)
    times = list(plan.caffeine_times_h) + [
        plan.caffeine_cutoff_h, plan.light_morning_h, plan.light_avoid_after_h
    ]
    # I7対応: 許可リストにカーブの各時刻も加える。必須の times には含めない
    # （含めると全時刻を書かないと通らなくなり、LLMの言い換え余地が消える）。
    # allowed_times に加えないと「14時から16時にかけて」のような自然な言い換えで
    # normalize() が 14:00 / 16:00 を抽出し、許可外＝捏造として弾かれてしまう
    # （「14時台」表記だけは除外規則で通るため、自然な言い換えほど不安定だった）。
    allowed_times = times + [float(d.hour) for d in curve]
    facts = gv.Facts(
        times=times,
        percents=list(ranges[worst_index]),
        allowed_times=allowed_times,
        allowed_percents=all_pcts,
        confidence={"low": "低", "medium": "中", "high": "高"}[cal.confidence],
        feedback_count=cal.feedback_count,
        required_notes=gv.REQUIRED_NOTES,
        required_hourly_ranges=[(d.hour, ranges[i]) for i, d in enumerate(curve)],
    )
    body, used_fallback, regen_count = _compose(
        deps, curve, ranges, plan, vital, cal.confidence, cal.feedback_count, facts, log,
    )

    # push の例外はここで捕捉せず伝播させる。送信していないのに mark_notified して
    # しまう事故（Lambda 再試行時の二重送信）を防ぐため（夜フローと同じ方針）。
    _push(deps, build_message(body, with_feedback=False), _retry_key(date, SLOT))
    log.log("morning_sent", fallback=used_fallback)

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
    )

    return {
        "delivered": True,
        "skipped": False,
        "consent_revoked": False,
        "has_sleep": True,
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


def _compose(deps, curve, ranges, plan, vital, confidence, feedback_count, facts, log) -> tuple[str, bool, int]:
    """LLM で文章化し検証する。通らなければテンプレート文を返す。

    戻り値の3つめは regen_count（ガードレール不通過による再試行回数。
    最初の1回は含まない）。

    M2対応: 以前はフォールバック時に常に deps.max_regen を返していたため、
    LLM が初回で例外を投げてbreakした場合（実際には1回も再試行していない）
    でも regen_count=2（max_regen）という不正確な値が記録されていた。
    実際に試行した回数（attempts_made）から数える。
    """
    user = prompts.morning_user(curve, plan, vital, ranges, confidence, feedback_count)
    attempts_made = 0
    for attempt in range(deps.max_regen + 1):
        if deps.deadline is not None and not deps.deadline.can_start():
            break
        attempts_made = attempt + 1
        try:
            text = deps.llm_client.complete(prompts.MORNING_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001 — LLM 障害は必ずフォールバックへ
            log.log("llm_failed", error=str(exc))
            break
        result = gv.validate(text, facts)
        if result.passed:
            return text, False, attempt
        log.log("guardrail_rejected", attempt=attempt, reason=result.reason)
    return fallback.morning_text(curve, ranges, plan, vital, confidence, feedback_count), True, max(attempts_made - 1, 0)
