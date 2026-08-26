"""夜フロー（仕様 第4節「データフロー（夜）」）。

直近履歴 → ベースライン → 推奨就寝時刻 → 文章化 → 検証 → 送信。
数値はすべてモデル層が決め、LLM は文章化だけを担う。
LLM が落ちてもテンプレート文で数値は届く。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import uuid
import inspect

from sleeptech.guardrail import fallback
from sleeptech.guardrail import validator as gv
from sleeptech.llm import prompts
from sleeptech.memory.schemas import Prediction
from sleeptech.model import baseline as bl
from sleeptech.model import constants as C
from sleeptech.model import two_process as tp
from sleeptech.notifier.line import build_message
from sleeptech.observability import StructuredLogger

SLOT = "NIGHT"


def _retry_key(date: str, slot: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sleeptech:{date}:{slot}"))


def _push(deps, message: dict, retry_key: str) -> None:
    if "retry_key" in inspect.signature(deps.push_client.push).parameters:
        deps.push_client.push(message, retry_key=retry_key)
    else:
        deps.push_client.push(message)


@dataclass
class NightDeps:
    settings: object
    memory: object
    llm_client: object
    push_client: object
    max_regen: int = 2
    deadline: object | None = None


def run_night(date: str, now_h: float, deps: NightDeps) -> dict:
    """夜フローを実行する。now_h は起点日 0:00 からの経過時間（22:30 なら 22.5）。"""
    log = StructuredLogger()
    log.log("night_start", date=date, now_h=now_h)

    if deps.memory.is_notified(date, SLOT) or not deps.memory.claim_notification(date, SLOT):
        log.log("night_skipped", reason="already_notified")
        return {"delivered": False, "skipped": True}

    # 1-2. 履歴からベースラインと睡眠負債
    vitals = deps.memory.get_vitals_desc(date, C.BASELINE_LOOKBACK_DAYS)
    sleeps = [s for s in (v.to_sleep_input() for v in vitals) if s is not None]
    override = deps.memory.get_config().get("override_wake_h", deps.settings.override_wake_h)
    baseline = bl.compute(sleeps, override_wake_h=override)
    debt_h = tp.sleep_debt_h(sleeps, baseline)
    log.log("baseline", days=baseline.days_used, default=baseline.is_default, debt=debt_h)

    # 3. 推奨就寝時刻
    advice = tp.recommend_bedtime(now_h, baseline, debt_h)
    log.log("advice", bedtime_h=advice.bedtime_h, drop=advice.mean_drop_pct,
            status=advice.status)

    # 4-5. 文章化と検証
    # require_day_prefix=True: 就寝時刻が日跨ぎ（bedtime_h >= 24.0）のとき、
    # 本文中の時刻表記に「翌」が付いていることまで照合する（夜フロー限定）。
    facts = gv.Facts(
        times=[advice.bedtime_h],
        percents=[advice.mean_drop_pct],
        require_day_prefix=True,
    )
    body, used_fallback = _compose(deps, advice, baseline, debt_h, facts, log)

    # 6. 送信
    # LinePushClient.push は HTTPError/URLError を捕捉せず送出する。
    # ここで捕捉せず Lambda の再試行に委ねる: mark_notified 前に例外が
    # 伝播すれば「送信できていないのに通知済み扱いになる」事故を防げる
    # （申し送り通り、送信していないのに mark_notified することだけは避ける）。
    # Lambda 側は非同期呼び出し・EventBridge Scheduler いずれでも失敗時に
    # 自動リトライされる構成を前提とし、ここでは例外を握りつぶさず
    # そのまま上位へ伝播させることで「本当に届いた時だけ既読化する」
    # 冪等性を守る。
    _push(deps, build_message(body, with_feedback=True), _retry_key(date, SLOT))
    log.log("night_sent", fallback=used_fallback)

    # push は成功済みなので、ここで失敗扱いにすると再試行で二重送信になる。
    # mark_notified が失敗しても送信自体は完了しているため、ログに残して続行する
    # （翌日の定時実行は別日付のキーになるため、フラグが立たなくても再送は起きない）。
    try:
        marked = deps.memory.mark_notified(date, SLOT)
        if not marked:
            # 条件付き書き込みが false ＝ 既に他の実行が通知済みフラグを立てていた
            # （並行実行の検知）。送信は完了しているため挙動は変えず記録のみ行う。
            log.log("mark_notified_conflict", date=date, slot=SLOT)
    except Exception as exc:  # noqa: BLE001 — push 成功後の失敗は伝播させない
        log.log("mark_notified_failed", error=str(exc))

    # 7. 保存
    # push は成功済みなので、ここで失敗扱いにすると再試行で二重送信になる。
    # save_prediction が失敗しても実害は限定的（予測レコードが1日分欠けても
    # ベースライン算出は他の日で成立する）ため、ログに残して続行する。
    try:
        deps.memory.save_prediction(
            Prediction(
                date=date,
                slot=SLOT,
                bedtime_h=advice.bedtime_h,
                mean_drop_pct=advice.mean_drop_pct,
                status=advice.status,
                curve=[asdict(d) for d in advice.curve],
            )
        )
    except Exception as exc:  # noqa: BLE001 — push 成功後の失敗は伝播させない
        log.log("save_prediction_failed", error=str(exc))

    return {
        "delivered": True,
        "skipped": False,
        "bedtime_h": advice.bedtime_h,
        "mean_drop_pct": advice.mean_drop_pct,
        "used_fallback": used_fallback,
    }


def _compose(deps, advice, baseline, debt_h, facts, log) -> tuple[str, bool]:
    """LLM で文章化し検証する。通らなければテンプレート文を返す。"""
    user = prompts.night_user(advice, baseline, debt_h)
    for attempt in range(deps.max_regen + 1):
        if deps.deadline is not None and not deps.deadline.can_start():
            break
        try:
            text = deps.llm_client.complete(prompts.NIGHT_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001 — LLM 障害は必ずフォールバックへ
            log.log("llm_failed", error=str(exc))
            break
        result = gv.validate(text, facts)
        if result.passed:
            return text, False
        log.log("guardrail_rejected", attempt=attempt, reason=result.reason)
    return fallback.night_text(advice, baseline, debt_h), True
