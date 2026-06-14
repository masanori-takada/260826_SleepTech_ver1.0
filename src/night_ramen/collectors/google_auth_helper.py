"""Google OAuth 2.0 アクセストークン取得（FR-001,011 / 原則III）。

サーバーサイド(S2S)フロー: Secrets Manager に保存したリフレッシュトークンから
アクセストークンを更新する。トークンは平文ログに出さない（observability でマスク済）。

参考: https://developers.google.com/identity/protocols/oauth2/web-server#offline
トークン端点: https://oauth2.googleapis.com/token
"""

from __future__ import annotations

from typing import Callable

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Google Health API のスコープ（読み取り専用、最小権限 / 原則III）
HEALTH_SCOPES = (
    "https://www.googleapis.com/auth/health.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/health.health_metrics_and_measurements.readonly",
)


def get_access_token(
    secret: dict,
    token_poster: Callable[[str, dict], dict] | None = None,
) -> str:
    """リフレッシュトークンからアクセストークンを取得する。

    secret は {client_id, client_secret, refresh_token} を含む dict。
    token_poster はテスト用に注入可能（(url, form) -> token_response_dict）。
    """
    required = ("client_id", "client_secret", "refresh_token")
    missing = [k for k in required if not secret.get(k)]
    if missing:
        raise ValueError(f"OAuth シークレットに不足項目があります: {missing}")

    poster = token_poster or _default_token_poster
    form = {
        "client_id": secret["client_id"],
        "client_secret": secret["client_secret"],
        "refresh_token": secret["refresh_token"],
        "grant_type": "refresh_token",
    }
    resp = poster(TOKEN_ENDPOINT, form)
    token = resp.get("access_token")
    if not token:
        # invalid_grant 等（リフレッシュトークン失効＝同意失効の可能性）
        raise PermissionError(f"アクセストークン取得に失敗: {resp.get('error', 'unknown')}")
    return token


def _default_token_poster(url: str, form: dict) -> dict:
    """本番用: urllib で token 端点へ POST（外部依存を最小化）。"""
    import json
    import urllib.parse
    import urllib.request

    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — 固定の Google 端点
        return json.loads(r.read().decode("utf-8"))
