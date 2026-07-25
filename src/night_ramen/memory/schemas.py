"""記憶エンティティのスキーマ（T005 / data-model.md）。

すべての日付は ISO 8601 `YYYY-MM-DD`（JST 基準）。バイタル数値は欠損許容（None）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Tone(str, Enum):
    """説得メッセージの強度トーン（FR-006）。"""

    PRAISE = "praise"            # 称賛・維持（達成率高）
    NEUTRAL = "neutral"          # 中立（目標未設定・標準）
    WARNING = "warning"          # 警告（やや未達）
    STRONG_WARNING = "strong_warning"  # 強い警告（大幅未達）


class GuardrailResult(str, Enum):
    """ガードレール検証の最終結果（FR-008）。"""

    PASS = "pass"
    FALLBACK = "fallback"


# バイタル指標のフィールド名一覧（FR-003）。欠損検出・言及検査に利用。
VITAL_FIELDS: tuple[str, ...] = (
    "sleep_score",
    "total_sleep_min",
    "awakenings",
    "deep_sleep_min",
    "night_avg_hr",
    "hrv",
    "skin_temp_delta",
    "respiration_rate",
    "weight_kg",
    "body_fat_pct",
    "next_day_steps",
    "next_day_calories",
)


@dataclass
class DailyVitalRecord:
    """E1: ある日付の1日分バイタル＋自己申告（FR-002,003）。"""

    date: str
    ramen_last_night: bool | None = None
    sleep_score: int | None = None
    total_sleep_min: int | None = None   # 総睡眠時間(分)。睡眠ステージから派生
    awakenings: int | None = None
    deep_sleep_min: int | None = None
    night_avg_hr: float | None = None
    hrv: float | None = None
    skin_temp_delta: float | None = None
    respiration_rate: float | None = None
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    next_day_steps: int | None = None
    next_day_calories: int | None = None
    missing_fields: list[str] = field(default_factory=list)
    ingested_at: str | None = None

    def recompute_missing(self) -> None:
        """値が None のバイタル指標を missing_fields に反映（FR-003,015）。"""
        self.missing_fields = [f for f in VITAL_FIELDS if getattr(self, f) is None]

    @property
    def has_any_vitals(self) -> bool:
        """少なくとも1つのバイタルが取得できているか（全欠損判定用）。"""
        return any(getattr(self, f) is not None for f in VITAL_FIELDS)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Goal:
    """E2: ユーザー定義の定量目標（FR-005,017）。"""

    success_rate_target: float = 0.85
    weight_loss_target_kg: float = 2.0
    baseline_weight_kg: float | None = None
    set_at: str | None = None


@dataclass
class AnalysisResult:
    """E5: ラーメンの悪影響の推論・数値化（FR-004）。

    impacts はラーメン有/無の平均比較、metric_stats は指標ごとの記述統計
    （最新値・直近平均・トレンド・自己ベスト）。behavior は我慢の申告サマリ。
    説得メッセージの話題を毎晩変えるため、全 VITAL_FIELDS 分を保持する。
    """

    date: str
    impacts: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    model_id: str = ""
    analyzed_at: str | None = None
    metric_stats: list[dict[str, Any]] = field(default_factory=list)
    behavior: dict[str, Any] = field(default_factory=dict)


@dataclass
class PersuasionMessage:
    """E3: 各夜の説得メッセージ履歴（FR-007,008,009 / SC-002,004）。"""

    date: str
    body: str
    tone: Tone = Tone.NEUTRAL
    guardrail_result: GuardrailResult = GuardrailResult.PASS
    regeneration_count: int = 0
    referenced_metrics: list[str] = field(default_factory=list)
    delivered: bool = False
    sent_at: str | None = None
    # 話題ローテーション用（FR-007）: その夜に主役として指示した指標と語りの切り口。
    # referenced_metrics（本文からの検出）と併せて「次に何を話すか」の判断材料になる。
    focus_metrics: list[str] = field(default_factory=list)
    angle: str = ""


@dataclass
class PersuasionStrategy:
    """E4: 現行の説得プロンプト戦略（FR-010）。

    不変条件: guardrail_passed=True のもののみ記憶へ保存される。
    """

    version: int = 1
    system_prompt: str = ""
    rationale: str = ""
    guardrail_passed: bool = False
    updated_at: str | None = None
