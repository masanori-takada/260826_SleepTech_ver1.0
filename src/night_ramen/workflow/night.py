"""夜の説得ループ（T017,T019,T029,T032 / US1,3,4）。

パイプライン: 分析 → 目標評価 → トーン決定 → 生成 → ガードレール検証 → 送信 → 戦略更新。
送信はガードレール通過かつ未通知時のみ（原則II / FR-007,009,014）。
データ全欠損時は個別化せず安全文面へフォールバック（FR-015）。

依存はすべて注入可能（テスト容易性）。本番依存は build_night_dependencies で組み立てる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, date as date_cls

from night_ramen.agents import analyzer, persuader, topics
from night_ramen.agents.prompts import ModelInvoker
from night_ramen.config import Settings
from night_ramen.goals import evaluator
from night_ramen.guardrail.validator import GuardrailContext, generate_safe_message, validate
from night_ramen.memory.client import MemoryClient
from night_ramen.memory.schemas import (
    GuardrailResult,
    Goal,
    PersuasionMessage,
    Tone,
)
from night_ramen.notifier.line import (
    IdempotencyStore,
    PushClient,
    now_iso,
    send_if_not_notified,
)
from night_ramen.observability import StructuredLogger


@dataclass
class NightDependencies:
    settings: Settings
    memory: MemoryClient
    invoker: ModelInvoker
    goal: Goal
    idempotency: IdempotencyStore
    push_client: PushClient
    lookback_days: int = 30
    # 話題ローテーション（FR-007）: 何晩分の履歴を見て、毎晩いくつの指標を主役にするか
    topic_history_limit: int = 14
    focus_metric_count: int = 2


def _referenced_metrics(text: str) -> list[str]:
    """本文が言及したバイタル指標名を抽出（SC-004 計測用）。

    キーワード定義は agents.topics に集約（話題ローテーションと同じ定義を使う）。
    """
    return topics.detect_metrics(text)


def run_night(date: str, deps: NightDependencies) -> PersuasionMessage:
    """夜ループを実行し、生成/送信した PersuasionMessage を返す。"""
    log = StructuredLogger()
    log.log("night_start", date=date)

    until = date
    since = (date_cls.fromisoformat(date) - timedelta(days=deps.lookback_days)).isoformat()
    vitals = deps.memory.get_vitals(since, until)
    log.log("vitals_loaded", count=len(vitals))

    has_data = any(v.has_any_vitals for v in vitals)
    ctx = GuardrailContext(
        goal_summary=f"成功率{deps.goal.success_rate_target:.0%} / "
        f"減量{deps.goal.weight_loss_target_kg}kg"
    )

    focus_metrics: list[str] = []
    angle_id = ""

    if not has_data:
        # データ全欠損 → 個別化せず安全文面（FR-015）
        log.log("no_data_fallback")
        from night_ramen.guardrail.fallback import SAFE_FALLBACK_MESSAGE

        body, result, tone, regen = SAFE_FALLBACK_MESSAGE, GuardrailResult.FALLBACK, Tone.NEUTRAL, 0
    else:
        # 分析（FR-004）
        analysis = analyzer.analyze(date, vitals, deps.invoker, deps.settings.models.analyzer_model)
        deps.memory.save_analysis(analysis)
        log.log("analyzed", impacts=len(analysis.impacts))

        # 目標評価 → トーン決定（FR-005,006）
        progress = evaluator.evaluate(deps.goal, vitals)
        tone = evaluator.choose_tone(progress)
        log.log("goal_evaluated", success_rate=progress.success_rate, tone=tone.value)

        # 今夜の話題を選ぶ（FR-007）。直近の説得履歴を見て、前夜と違う指標・切り口にする。
        history = deps.memory.get_recent_messages(limit=deps.topic_history_limit)
        available = topics.available_metrics(vitals)
        has_reports = any(v.ramen_last_night is not None for v in vitals)
        focus_metrics = topics.select_focus_metrics(
            date, available, history, count=deps.focus_metric_count
        )
        angle = topics.select_angle(date, focus_metrics, has_reports, history)
        angle_id = angle.id
        avoid_metrics = topics.recently_used_metrics(date, history)
        log.log(
            "topic_selected",
            focus=",".join(focus_metrics),
            angle=angle_id,
            available=len(available),
        )

        # 生成 → ガードレール（最大2回再生成, FR-009）
        def _gen() -> str:
            current = deps.memory.get_current_strategy()
            return persuader.generate_message(
                current,
                analysis,
                tone,
                deps.invoker,
                deps.settings.models.persuader_model,
                focus_metrics=focus_metrics,
                angle=angle,
                avoid_metrics=avoid_metrics,
            )

        body, result = generate_safe_message(_gen, ctx, deps.settings.max_regenerations)
        regen = 0 if result == GuardrailResult.PASS else deps.settings.max_regenerations
        log.log("message_generated", guardrail=result.value)

    # 送信（通過かつ未通知時のみ, FR-007,014）
    # 自己申告を促す固定の質問文を付加（翌朝の収集が REPORT を読み取り紐づける / US2）。
    sent_body = body + "\n\n―――\n（翌朝でOK）昨夜ラーメンは「食べた」？「我慢した」？返信で教えてね🍜"
    delivered = send_if_not_notified(date, sent_body, deps.idempotency, deps.push_client)
    log.log("notify", delivered=delivered)

    msg = PersuasionMessage(
        date=date,
        body=body,
        tone=tone,
        guardrail_result=result,
        regeneration_count=regen,
        referenced_metrics=_referenced_metrics(body),
        delivered=delivered,
        sent_at=now_iso(),
        focus_metrics=focus_metrics,
        angle=angle_id,
    )
    deps.memory.save_message(msg)

    # 戦略の自律更新（FR-010）— 検証通過版のみ保存
    if has_data:
        _maybe_update_strategy(date, deps, ctx, log)

    log.log(
        "night_done",
        delivered=delivered,
        guardrail=result.value,
        angle=angle_id,
        referenced=",".join(msg.referenced_metrics),
    )
    return msg


def _maybe_update_strategy(
    date: str, deps: NightDependencies, ctx: GuardrailContext, log: StructuredLogger
) -> None:
    """説得履歴と結果から戦略を更新し、ガードレール通過時のみ保存（FR-010 不変条件）。"""
    current = deps.memory.get_current_strategy()
    recent_msgs = deps.memory.get_recent_messages(limit=10)
    recent_vitals = deps.memory.get_vitals(
        (date_cls.fromisoformat(date) - timedelta(days=deps.lookback_days)).isoformat(), date
    )
    proposed = persuader.update_strategy(
        current, recent_msgs, recent_vitals, deps.invoker, deps.settings.models.persuader_model
    )
    # 戦略プロンプト自体もガードレールで検証（暴走防止）
    if validate(proposed.system_prompt, ctx).passed:
        proposed.guardrail_passed = True
        deps.memory.save_strategy(proposed)
        log.log("strategy_updated", version=proposed.version)
    else:
        log.log("strategy_rejected", reason="guardrail")
