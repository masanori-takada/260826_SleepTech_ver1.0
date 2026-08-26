"""構造化ログ・相関ID・PII/トークンマスキング（T006 / FR-011,013 / 原則III,V）。

機微情報（トークン・健康データ値・OAuth コード等）は平文でログ出力しない。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from typing import Any

# マスキング対象のキー（部分一致、小文字比較）
_SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "client_secret",
    "refresh_token",
    "access_token",
    "code",
)

# 値中のトークンらしき文字列（長い英数字列）をマスク
_TOKENish = re.compile(r"[A-Za-z0-9_\-]{24,}")


def new_correlation_id() -> str:
    """実行ごとの相関ID（FR-013：各ステップ追跡）。"""
    return uuid.uuid4().hex


def mask_value(key: str, value: Any) -> Any:
    """キー名が機微なら値をマスク。文字列値中のトークン様文字列も伏せる。"""
    lk = key.lower()
    if any(part in lk for part in _SENSITIVE_KEY_PARTS):
        return "***REDACTED***"
    if isinstance(value, str):
        return _TOKENish.sub("***", value)
    return value


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sanitize(mask_value(k, v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


class StructuredLogger:
    """相関IDを付与した構造化JSONログを stdout に出力する。"""

    def __init__(self, name: str = "sleeptech", correlation_id: str | None = None):
        self._cid = correlation_id or new_correlation_id()
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

    @property
    def correlation_id(self) -> str:
        return self._cid

    def log(self, step: str, **fields: Any) -> None:
        """1ステップを記録。fields はマスキングされる（FR-011）。"""
        record = {
            "correlation_id": self._cid,
            "step": step,
            **_sanitize(fields),
        }
        self._logger.info(json.dumps(record, ensure_ascii=False))
