"""環境変数からアプリケーション設定を読み込む。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """アプリケーション全体で共有する設定。"""

    env: str
    region: str
    table: str
    secret_google: str
    secret_line: str
    secret_openai: str
    llm_model: str
    target_drop_pct: float
    override_wake_h: float | None


def _env_float_or_none(name: str) -> float | None:
    raw = os.getenv(name)
    return float(raw) if raw and raw.strip() else None


def load_settings() -> Settings:
    """環境変数を読み、未指定項目には仕様の既定値を使う。"""
    return Settings(
        env=os.getenv("STC_ENV", "dev").lower(),
        region=os.getenv("AWS_REGION", "us-east-1"),
        table=os.getenv("STC_DDB_TABLE", "SleepTechTable"),
        secret_google=os.getenv("STC_SECRET_GOOGLE", "sleeptech/google-oauth"),
        secret_line=os.getenv("STC_SECRET_LINE", "sleeptech/line"),
        secret_openai=os.getenv("STC_SECRET_OPENAI", "sleeptech/openai"),
        # 実機で変更が必要な場合は環境変数だけを差し替える。
        llm_model=os.getenv("LLM_MODEL", "gpt-5.6-luna"),
        target_drop_pct=float(os.getenv("STC_TARGET_DROP_PCT", "10")),
        override_wake_h=_env_float_or_none("STC_OVERRIDE_WAKE_H"),
    )
