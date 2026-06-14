"""分析エージェント（T014 / FR-004）。

バイタル時系列からラーメンの悪影響を推論・数値化する。
"""

from __future__ import annotations

from datetime import datetime, timezone

from night_ramen.agents.prompts import (
    ANALYSIS_PROMPT,
    ModelInvoker,
    vitals_to_prompt_payload,
)
from night_ramen.memory.schemas import AnalysisResult, DailyVitalRecord


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
    )
    summary = invoker(model_id, prompt, ANALYSIS_PROMPT)
    return AnalysisResult(
        date=date,
        impacts=_derive_impacts(usable),
        summary=summary.strip(),
        model_id=model_id,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
    )


def _derive_impacts(vitals: list[DailyVitalRecord]) -> list[dict]:
    """ラーメン有/無で平均を比較した決定的な数値根拠（LLM 要約の裏付け）。"""
    ramen = [v for v in vitals if v.ramen_last_night is True]
    no_ramen = [v for v in vitals if v.ramen_last_night is False]
    if not ramen or not no_ramen:
        return []

    impacts: list[dict] = []
    for metric in ("sleep_score", "hrv", "deep_sleep_min", "next_day_steps"):
        r_vals = [getattr(v, metric) for v in ramen if getattr(v, metric) is not None]
        n_vals = [getattr(v, metric) for v in no_ramen if getattr(v, metric) is not None]
        if not r_vals or not n_vals:
            continue
        r_avg = sum(r_vals) / len(r_vals)
        n_avg = sum(n_vals) / len(n_vals)
        impacts.append(
            {
                "metric": metric,
                "ramen_avg": round(r_avg, 2),
                "no_ramen_avg": round(n_avg, 2),
                "delta": round(r_avg - n_avg, 2),
            }
        )
    return impacts
