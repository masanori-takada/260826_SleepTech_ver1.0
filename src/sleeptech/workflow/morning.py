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
from sleeptech.model import recovery as rc
from sleeptech.model import two_process as tp
from sleeptech.notifier.line import build_message
from sleeptech.observability import StructuredLogger

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


def run_morning(date: str, deps: MorningDeps) -> dict:
    """朝フローを実行する。date は起点日（前夜の夜メッセージと同じ日付）。"""
    log = StructuredLogger()
    log.log("morning_start", date=date)

    # 1. 冪等（定時実行と「おはよう」前倒しが重なっても1回だけ送るためのガード）
    if deps.memory.is_notified(date, SLOT):
        log.log("morning_skipped", reason="already_notified")
        return {"delivered": False, "skipped": True}

    # 2-3. 収集と保存（Google Health は起床日で睡眠を返すため wake_date = date + 1日）
    wake_date = (date_cls.fromisoformat(date) + timedelta(days=1)).isoformat()
    try:
        raw = deps.health_source.fetch_raw(wake_date)
    except ConsentRevokedError as exc:
        log.log("consent_revoked", error=str(exc))
        deps.push_client.push(build_message(_CONSENT_TEXT, with_feedback=False))
        _mark_notified(deps, date, log)
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
        deps.push_client.push(build_message(_NO_DATA_TEXT, with_feedback=False))
        _mark_notified(deps, date, log)
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
    baseline = bl.compute(sleeps, override_wake_h=override)
    debt_h = tp.sleep_debt_h(sleeps, baseline)
    curve = tp.predict_curve(vital.to_sleep_input(), baseline, debt_h)

    # 5. リカバリー（今夜の就寝は「起床 + 平常時の覚醒時間」と見なす）
    next_bedtime_h = vital.wake_h + (24.0 - baseline.sleep_h)
    plan = rc.plan(vital.wake_h, next_bedtime_h)
    log.log("recovery", caffeine=len(plan.caffeine_times_h))

    # 6. 文章化・検証・送信
    # 必須は谷の低下率1つ。他の時間帯の値は許可リストに入れる（列挙しても捏造扱いしない）
    all_pcts = [d.focus_pct for d in curve] + [d.mood_pct for d in curve]
    # 谷（LLM に必須値として要求する低下率）は主要時間帯（起床+2h〜+10h）に限定する。
    # 全区間で探すと睡眠慣性の影響で起床直後の値が常に最大になってしまうため。
    worst = max(tp.main_window_slice(curve), key=lambda d: d.focus_pct)
    times = list(plan.caffeine_times_h) + [
        plan.caffeine_cutoff_h, plan.light_morning_h, plan.light_avoid_after_h
    ]
    facts = gv.Facts(
        times=times,
        percents=[worst.focus_pct] if worst.focus_pct > 0 else [],
        allowed_times=times,
        allowed_percents=all_pcts,
    )
    body, used_fallback = _compose(deps, curve, plan, vital, facts, log)

    # push の例外はここで捕捉せず伝播させる。送信していないのに mark_notified して
    # しまう事故（Lambda 再試行時の二重送信）を防ぐため（夜フローと同じ方針）。
    deps.push_client.push(build_message(body, with_feedback=False))
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
                mean_drop_pct=round(tp.mean_drop_in_main_window(curve, vital.wake_h)),
                curve=[asdict(d) for d in curve],
                recovery=asdict(plan),
            )
        )
    except Exception as exc:  # noqa: BLE001 — push 成功後の失敗は伝播させない
        log.log("save_prediction_failed", error=str(exc))

    return {
        "delivered": True,
        "skipped": False,
        "consent_revoked": False,
        "has_sleep": True,
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


def _compose(deps, curve, plan, vital, facts, log) -> tuple[str, bool]:
    """LLM で文章化し検証する。通らなければテンプレート文を返す。"""
    user = prompts.morning_user(curve, plan, vital)
    for attempt in range(deps.max_regen + 1):
        try:
            text = deps.llm_client.complete(prompts.MORNING_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001 — LLM 障害は必ずフォールバックへ
            log.log("llm_failed", error=str(exc))
            break
        result = gv.validate(text, facts)
        if result.passed:
            return text, False
        log.log("guardrail_rejected", attempt=attempt, reason=result.reason)
    return fallback.morning_text(curve, plan, vital), True
