"""夜フロー（仕様 第4節「データフロー（夜）」）。

直近履歴 → ベースライン → 推奨就寝時刻 → 文章化 → 検証 → 送信。
数値はすべてモデル層が決め、LLM は文章化だけを担う。
LLM が落ちてもテンプレート文で数値は届く。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

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


@dataclass
class NightDeps:
    settings: object
    memory: object
    llm_client: object
    push_client: object
    max_regen: int = 2


def run_night(date: str, now_h: float, deps: NightDeps) -> dict:
    """夜フローを実行する。now_h は起点日 0:00 からの経過時間（22:30 なら 22.5）。"""
    log = StructuredLogger()
    log.log("night_start", date=date, now_h=now_h)

    if deps.memory.is_notified(date, SLOT):
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
    deps.push_client.push(build_message(body, with_feedback=True))
    deps.memory.mark_notified(date, SLOT)
    log.log("night_sent", fallback=used_fallback)

    # 7. 保存
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
