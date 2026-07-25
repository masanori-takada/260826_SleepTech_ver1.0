"""分析エージェント（T014 / FR-004）。

バイタル時系列からラーメンの悪影響を推論・数値化する。

説得メッセージの話題が偏らないよう、VITAL_FIELDS の**全指標**について
決定的な数値根拠を作る:

  - impacts      : ラーメン有/無の平均比較（申告がある指標のみ）
  - metric_stats : 指標ごとの最新値・直近7日平均・それ以前との差・自己ベスト/ワースト
  - behavior     : 我慢の申告サマリ（成功率・連続日数・最後に食べた日）

申告が片側しか無い日が続いても metric_stats は必ず作れるため、
「比較できないから同じ話題しか出せない」状態を避けられる。
"""

from __future__ import annotations

from datetime import datetime, timezone

from night_ramen.agents.prompts import (
    ANALYSIS_PROMPT,
    ModelInvoker,
    vitals_to_prompt_payload,
)
from night_ramen.agents.topics import METRIC_META
from night_ramen.memory.schemas import VITAL_FIELDS, AnalysisResult, DailyVitalRecord

# 「直近」とみなす日数（トレンド比較の窓）
RECENT_WINDOW_DAYS = 7


def analyze(
    date: str,
    vitals: list[DailyVitalRecord],
    invoker: ModelInvoker,
    model_id: str,
) -> AnalysisResult:
    """ラーメンの悪影響を分析する（FR-004）。

    データが乏しい/全欠損のときは断定せず空に近い結果を返す（FR-015）。
    """
    usable = [v for v in vitals if v.has_any_vitals]
    if not usable:
        return AnalysisResult(
            date=date,
            impacts=[],
            summary="バイタルデータが不足しているため、個別の悪影響は推論できません。",
            model_id=model_id,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )

    payload = vitals_to_prompt_payload(usable)
    prompt = (
        f"以下はユーザーの直近のバイタルデータです。\n{payload}\n\n"
        "夜食ラーメンの有無(ramen_last_night)で各指標がどう変化したかを比較し、"
        "悪影響を簡潔に要約してください。"
        "睡眠・自律神経・体組成・翌日の活動量など、データのある指標は幅広く拾ってください。"
    )
    summary = invoker(model_id, prompt, ANALYSIS_PROMPT)
    return AnalysisResult(
        date=date,
        impacts=_derive_impacts(usable),
        summary=summary.strip(),
        model_id=model_id,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        metric_stats=derive_metric_stats(usable),
        behavior=derive_behavior(vitals),
    )


def _derive_impacts(vitals: list[DailyVitalRecord]) -> list[dict]:
    """ラーメン有/無で平均を比較した決定的な数値根拠（LLM 要約の裏付け）。

    VITAL_FIELDS 全指標を対象にする（以前は睡眠・HRV・深い睡眠・歩数の4指標のみで、
    説得メッセージの話題がその4つに固定される原因になっていた）。
    """
    ramen = [v for v in vitals if v.ramen_last_night is True]
    no_ramen = [v for v in vitals if v.ramen_last_night is False]
    if not ramen or not no_ramen:
        return []

    impacts: list[dict] = []
    for metric in VITAL_FIELDS:
        r_vals = _values(ramen, metric)
        n_vals = _values(no_ramen, metric)
        if not r_vals or not n_vals:
            continue
        r_avg = sum(r_vals) / len(r_vals)
        n_avg = sum(n_vals) / len(n_vals)
        impacts.append(
            {
                "metric": metric,
                "label": _label(metric),
                "unit": _unit(metric),
                "ramen_avg": round(r_avg, 2),
                "no_ramen_avg": round(n_avg, 2),
                "delta": round(r_avg - n_avg, 2),
                "ramen_days": len(r_vals),
                "no_ramen_days": len(n_vals),
            }
        )
    return impacts


def derive_metric_stats(vitals: list[DailyVitalRecord]) -> list[dict]:
    """指標ごとの記述統計。ラーメンの申告が無くても必ず作れる数値根拠。

    返す各要素: 最新値と日付・件数・直近7日平均・それ以前の平均と差・最良/最悪値。
    """
    ordered = sorted(vitals, key=lambda v: v.date)
    stats: list[dict] = []

    for metric in VITAL_FIELDS:
        points = [
            (v.date, getattr(v, metric))
            for v in ordered
            if getattr(v, metric) is not None
        ]
        if not points:
            continue

        values = [p[1] for p in points]
        recent = values[-RECENT_WINDOW_DAYS:]
        prior = values[:-RECENT_WINDOW_DAYS]
        recent_avg = sum(recent) / len(recent)
        prior_avg = sum(prior) / len(prior) if prior else None

        higher_is_better = _higher_is_better(metric)
        best_point = _extreme(points, higher_is_better, best=True)
        worst_point = _extreme(points, higher_is_better, best=False)

        stats.append(
            {
                "metric": metric,
                "label": _label(metric),
                "unit": _unit(metric),
                "higher_is_better": higher_is_better,
                "days": len(values),
                "latest": round(points[-1][1], 2),
                "latest_date": points[-1][0],
                "recent_avg": round(recent_avg, 2),
                "prior_avg": round(prior_avg, 2) if prior_avg is not None else None,
                "trend_delta": (
                    round(recent_avg - prior_avg, 2) if prior_avg is not None else None
                ),
                "best": round(best_point[1], 2) if best_point else None,
                "best_date": best_point[0] if best_point else None,
                "worst": round(worst_point[1], 2) if worst_point else None,
                "worst_date": worst_point[0] if worst_point else None,
            }
        )
    return stats


def derive_behavior(vitals: list[DailyVitalRecord]) -> dict:
    """ラーメン我慢の申告サマリ（連続記録・成功率・最後に食べた日）。"""
    ordered = sorted(vitals, key=lambda v: v.date)
    reported = [v for v in ordered if v.ramen_last_night is not None]
    if not reported:
        return {"reported_days": 0}

    abstained = [v for v in reported if v.ramen_last_night is False]
    last_ramen = next(
        (v.date for v in reversed(reported) if v.ramen_last_night is True), None
    )

    streak = 0
    for v in reversed(reported):
        if v.ramen_last_night is False:
            streak += 1
        else:
            break

    return {
        "reported_days": len(reported),
        "abstained_days": len(abstained),
        "ramen_days": len(reported) - len(abstained),
        "success_rate": round(len(abstained) / len(reported), 3),
        "current_abstinence_streak": streak,
        "last_ramen_date": last_ramen,
        "first_reported_date": reported[0].date,
        "last_reported_date": reported[-1].date,
    }


def _values(vitals: list[DailyVitalRecord], metric: str) -> list[float]:
    return [getattr(v, metric) for v in vitals if getattr(v, metric) is not None]


def _extreme(points: list[tuple], higher_is_better: bool | None, best: bool):
    """良い方向が定義されている指標のみ最良/最悪を返す（方向不明は None）。"""
    if higher_is_better is None:
        return None
    want_max = higher_is_better if best else not higher_is_better
    return max(points, key=lambda p: p[1]) if want_max else min(points, key=lambda p: p[1])


def _label(metric: str) -> str:
    meta = METRIC_META.get(metric)
    return meta.label if meta else metric


def _unit(metric: str) -> str:
    meta = METRIC_META.get(metric)
    return meta.unit if meta else ""


def _higher_is_better(metric: str) -> bool | None:
    meta = METRIC_META.get(metric)
    return meta.higher_is_better if meta else None
