"""記憶エンティティと予測入出力のスキーマ（仕様 第9節）。

時刻はすべて「起点日 0:00 からの経過時間（float, 単位=時）」で表す。
翌日 7:00 は 31.0、翌日 0:30 は 24.5。この単位のフィールドは _h で終わる。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class SleepInput:
    """予測モデルに渡す1晩分の睡眠。

    前提条件（呼び出し側で保証すること）:
    - 0 < duration_h <= 24 であること。
    - この範囲外の値を渡した場合の model 層の計算結果は保証されない
      （例外にはならないが、無意味な値が返る可能性がある）。
    - model 層は純粋関数の責務のため、ここでは妥当性検証を行わない。
      検証は上位層（DynamoDB からの取得層・ワークフロー層）の責務とする。
    """

    bedtime_h: float
    wake_h: float
    rem_min: float | None = None
    deep_min: float | None = None

    @property
    def duration_h(self) -> float:
        """睡眠時間（時間）。"""
        return self.wake_h - self.bedtime_h


@dataclass(frozen=True)
class Baseline:
    """直近履歴から算出した個人の平常時。

    is_default=True は履歴が不足しており既定値を使ったことを表す。
    このときメッセージに「まだデータが少ない」旨を含める必要がある。
    """

    wake_h: float
    sleep_h: float
    rem_min: float | None = None
    deep_min: float | None = None
    days_used: int = 0
    is_default: bool = False
    target_sleep_h: float | None = None

    @property
    def effective_target_sleep_h(self) -> float:
        """目標睡眠時間。旧データでは習慣睡眠時間へ後方互換する。"""
        return self.target_sleep_h if self.target_sleep_h is not None else self.sleep_h


@dataclass(frozen=True)
class HourlyDrop:
    """ある時刻におけるパフォーマンス低下率（%、0以上の整数）。"""

    hour: int        # 起床日の時刻（0-23）
    focus_pct: int   # 集中力
    mood_pct: int    # 気分


@dataclass(frozen=True)
class BedtimeAdvice:
    """夜フローが算出する推奨就寝時刻とその根拠。

    status:
      "ok"          — 閾値を満たす就寝時刻が見つかった
      "unreachable" — どんなに早く寝ても閾値に届かない（最善時刻を返す）
      "too_late"    — 既に探索区間の終端を過ぎている（今すぐ寝るべき）
    """

    bedtime_h: float
    mean_drop_pct: int
    curve: list[HourlyDrop]
    status: str


@dataclass(frozen=True)
class RecoveryPlan:
    """朝フローが提示するリカバリー行動の時刻。

    caffeine_times_h が空リストのときは「カフェインは勧めない」を意味する。
    """

    caffeine_times_h: list[float]
    caffeine_cutoff_h: float
    light_morning_h: float
    light_avoid_after_h: float


@dataclass
class DailyVital:
    """ある夜の睡眠とバイタル。date は起点日（＝夜メッセージの送信日）。"""

    date: str
    total_sleep_min: int | None = None
    deep_sleep_min: int | None = None
    rem_sleep_min: int | None = None
    awakenings: int | None = None
    bedtime_h: float | None = None
    wake_h: float | None = None
    hrv: float | None = None
    skin_temp_delta: float | None = None
    respiration_rate: float | None = None
    night_avg_hr: float | None = None
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    ingested_at: str | None = None

    @property
    def has_sleep(self) -> bool:
        """予測に必要な最低限（睡眠時間・起床時刻）が揃っているか。"""
        return self.total_sleep_min is not None and self.wake_h is not None

    def to_sleep_input(self) -> SleepInput | None:
        """予測モデルへの入力に変換する。データ不足なら None。

        C3対応: 予測には「床上時間（TIB, wake_h - bedtime_h）」ではなく
        「実睡眠時間」を使う。total_sleep_min はAWAKEステージを除いた実睡眠の
        分数なので、これがあれば bedtime_h = wake_h - total_sleep_min/60.0 で
        逆算した実睡眠ベースの就寝時刻を優先する。実績の bedtime_h（記録上の
        入床時刻）は本文表示専用とし、予測には使わない。total_sleep_min が
        欠損している日だけ、従来どおり実績の bedtime_h（=床上時間）を使う。
        """
        if not self.has_sleep:
            return None
        bedtime_h = (
            self.wake_h - self.total_sleep_min / 60.0
            if self.total_sleep_min is not None
            else self.bedtime_h
        )
        return SleepInput(
            bedtime_h=bedtime_h,
            wake_h=self.wake_h,
            rem_min=float(self.rem_sleep_min) if self.rem_sleep_min is not None else None,
            deep_min=float(self.deep_sleep_min) if self.deep_sleep_min is not None else None,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> DailyVital:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Prediction:
    """夜または朝に算出した予測。slot は "NIGHT" / "MORNING"。"""

    date: str
    slot: str
    bedtime_h: float | None = None
    mean_drop_pct: int = 0
    status: str = "ok"
    curve: list = field(default_factory=list)
    recovery: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Prediction:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Message:
    """送信した本文の記録（仕様 第9節 MSG#NIGHT / MSG#MORNING）。

    障害時に「何を送ったか」を後から追えるようにするための監査用エンティティ。
    guardrail_passed=False は LLM 文章化が通らずテンプレート文（フォールバック）
    を送ったことを表す。regen_count は LLM への再生成試行回数
    （最初の1回は含まず、ガードレール不通過による再試行の回数）。
    """

    date: str
    slot: str
    body: str = ""
    guardrail_passed: bool = False
    regen_count: int = 0
    delivered: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Feedback:
    """夜のクイックリプライで得た5段階評価。"""

    date: str
    score: int
    answered_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Feedback:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
