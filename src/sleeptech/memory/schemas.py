"""記憶エンティティと予測入出力のスキーマ（仕様 第9節）。

時刻はすべて「起点日 0:00 からの経過時間（float, 単位=時）」で表す。
翌日 7:00 は 31.0、翌日 0:30 は 24.5。この単位のフィールドは _h で終わる。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SleepInput:
    """予測モデルに渡す1晩分の睡眠。"""

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


@dataclass(frozen=True)
class HourlyDrop:
    """ある時刻におけるパフォーマンス低下率（%、0以上の整数）。"""

    hour: int        # 起床日の時刻（0-23）
    focus_pct: int   # 集中力
    mood_pct: int    # 気分
