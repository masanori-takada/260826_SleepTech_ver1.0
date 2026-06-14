"""環境・モデル・しきい値の設定（T004 / FR-004,006,009,014,016,017 / 原則V）。

環境変数で上書き可能。既定値は spec の clarify 結果に従う:
- 通知は1晩1回
- ガードレール再生成は最大2回
- バイタル保持は1年
- 既定目標は成功率85% / 減量2kg
- LLM は初期開発で最安の Haiku に統一、本番は分析=Opus 4.8 / 生成=Sonnet 4.6
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    """Bedrock のモデル ID。環境(`NRB_ENV`)に応じて切替。"""

    analyzer_model: str
    persuader_model: str
    guardrail_model: str


# 開発既定: 全用途を最安 Haiku に統一（原則V コスト規律）
_DEV_MODELS = ModelConfig(
    analyzer_model="anthropic.claude-haiku-4-5-20251001-v1:0",
    persuader_model="anthropic.claude-haiku-4-5-20251001-v1:0",
    guardrail_model="anthropic.claude-haiku-4-5-20251001-v1:0",
)

# 本番: 分析=Opus 4.8 / 生成=Sonnet 4.6 / 検証=Haiku
_PROD_MODELS = ModelConfig(
    analyzer_model="anthropic.claude-opus-4-8",
    persuader_model="anthropic.claude-sonnet-4-6",
    guardrail_model="anthropic.claude-haiku-4-5-20251001-v1:0",
)


@dataclass(frozen=True)
class Settings:
    env: str
    region: str
    models: ModelConfig

    # しきい値（clarify 確定値）
    max_regenerations: int          # ガードレール再生成上限（FR-009）
    nightly_notification_limit: int  # 1晩あたり通知上限（FR-014）
    vital_retention_days: int        # バイタル保持日数（FR-016）
    default_success_rate_target: float  # 既定成功率（FR-017）
    default_weight_loss_target_kg: float  # 既定減量(kg)（FR-017）
    night_notification_hour_jst: int  # 夜通知の時刻(JST)

    # 永続先
    dynamodb_table: str
    secret_google: str
    secret_line: str


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None and raw.strip() else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw is not None and raw.strip() else default


def load_settings() -> Settings:
    """環境変数から設定を構築する。テストでは monkeypatch で上書き可能。"""
    env = os.getenv("NRB_ENV", "dev").lower()
    models = _PROD_MODELS if env in ("prod", "production") else _DEV_MODELS
    return Settings(
        env=env,
        region=os.getenv("AWS_REGION", "us-east-1"),
        models=models,
        max_regenerations=_env_int("NRB_MAX_REGEN", 2),
        nightly_notification_limit=_env_int("NRB_NIGHTLY_LIMIT", 1),
        vital_retention_days=_env_int("NRB_RETENTION_DAYS", 365),
        default_success_rate_target=_env_float("NRB_DEFAULT_SUCCESS_RATE", 0.85),
        default_weight_loss_target_kg=_env_float("NRB_DEFAULT_WEIGHT_LOSS_KG", 2.0),
        night_notification_hour_jst=_env_int("NRB_NIGHT_HOUR_JST", 23),
        dynamodb_table=os.getenv("NRB_DDB_TABLE", "NightRamenTable"),
        secret_google=os.getenv("NRB_SECRET_GOOGLE", "night-ramen/google-oauth"),
        secret_line=os.getenv("NRB_SECRET_LINE", "night-ramen/line"),
    )
