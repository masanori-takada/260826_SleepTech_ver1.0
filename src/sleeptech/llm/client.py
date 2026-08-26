"""OpenAI Chat Completions の呼び出し（仕様 第7節）。

OpenAI SDK は使わない。Lambda の実行時依存を boto3 + 標準ライブラリのみに
保つため、urllib.request で叩く。

モデルIDは config.Settings.llm_model（環境変数 LLM_MODEL）から渡される。
既定値は暫定であり、400/404 が返る場合は環境変数を差し替えればよい。
"""

from __future__ import annotations

from typing import Callable

API_URL = "https://api.openai.com/v1/chat/completions"
MAX_ATTEMPTS = 3       # 初回 + リトライ2回
TIMEOUT_SEC = 25
TEMPERATURE = 0.7
MAX_TOKENS = 700


class LlmError(Exception):
    """LLM 呼び出しが最終的に失敗したことを表す。呼び出し側はテンプレート文に切り替える。"""


class OpenAiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        http_post: Callable[[str, dict, dict], dict] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._post = http_post or _default_http_post
        self._sleep = sleep or _default_sleep

    def complete(self, system: str, user: str) -> str:
        """system / user プロンプトを渡し、生成テキストを返す。

        失敗時は指数バックオフで最大 MAX_ATTEMPTS 回まで試み、それでも駄目なら
        LlmError を送出する。
        """
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        }

        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = self._post(API_URL, headers, body)
                text = resp["choices"][0]["message"]["content"]
                if not text or not text.strip():
                    raise ValueError("空の生成結果")
                return text.strip()
            except Exception as exc:  # noqa: BLE001 — 全失敗を LlmError に統一する
                last = exc
                if attempt < MAX_ATTEMPTS - 1:
                    self._sleep(2.0**attempt)
        raise LlmError(f"LLM 呼び出しに失敗しました: {last}") from last


class NullClient:
    """常に失敗するクライアント。フォールバック経路の検証に使う。"""

    def complete(self, system: str, user: str) -> str:
        raise LlmError("LLM は無効化されています")


def _default_http_post(url: str, headers: dict, body: dict) -> dict:
    import json
    import urllib.request

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:  # noqa: S310 — 固定端点
        return json.loads(r.read().decode("utf-8"))


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
