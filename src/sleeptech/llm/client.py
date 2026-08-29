"""OpenAI Chat Completions の呼び出し（仕様 第7節）。

OpenAI SDK は使わない。Lambda の実行時依存を boto3 + 標準ライブラリのみに
保つため、urllib.request で叩く。

モデルIDは config.Settings.llm_model（環境変数 LLM_MODEL）から渡される。
既定値は暫定であり、400/404 が返る場合は環境変数を差し替えればよい。
"""

from __future__ import annotations

from typing import Callable
import time

API_URL = "https://api.openai.com/v1/chat/completions"
MAX_ATTEMPTS = 3       # 初回 + リトライ2回
# 推論モデルでは reasoning のトークンも max_completion_tokens に含まれる。
# 700 のままだとプロンプトを厚くした朝フローで推論に食い尽くされ、
# 本文が空で返って毎回フォールバックしていた。
MAX_TOKENS = 2000
# 推論モデルは1回の応答に十数秒かかる。8秒では読み取りタイムアウトが続き、
# 朝フローが毎回テンプレートへ落ちていた。
ATTEMPT_TIMEOUT_SEC = 20.0
LINE_RESERVE_SEC = 12.0
TIMEOUT_SEC = ATTEMPT_TIMEOUT_SEC


class ExecutionDeadline:
    """Lambdaの残り時間から、外部APIに使える時間を制限する。"""

    def __init__(self, absolute: float, clock: Callable[[], float] | None = None,
                 reserve_sec: float = LINE_RESERVE_SEC) -> None:
        self.absolute = absolute
        self._clock = clock or time.monotonic
        self.reserve_sec = reserve_sec

    def remaining_sec(self) -> float:
        return self.absolute - self._clock()

    def can_start(self, expected_sec: float = 0.0) -> bool:
        return self.remaining_sec() > self.reserve_sec + expected_sec


class LlmError(Exception):
    """LLM 呼び出しが最終的に失敗したことを表す。呼び出し側はテンプレート文に切り替える。"""


class OpenAiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        http_post: Callable[[str, dict, dict], dict] | None = None,
        sleep: Callable[[float], None] | None = None,
        deadline: ExecutionDeadline | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._post = http_post or _default_http_post
        self._sleep = sleep or _default_sleep
        self._deadline = deadline

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
            "max_completion_tokens": MAX_TOKENS,
        }

        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            if self._deadline is not None and not self._deadline.can_start(ATTEMPT_TIMEOUT_SEC):
                raise LlmError("Lambda残り時間が少ないためLLMを中断しました")
            try:
                resp = self._post(API_URL, headers, body)
                text = resp["choices"][0]["message"]["content"]
                if not text or not text.strip():
                    raise ValueError("空の生成結果")
                return text.strip()
            except Exception as exc:  # noqa: BLE001 — 全失敗を LlmError に統一する
                last = exc
                if attempt < MAX_ATTEMPTS - 1:
                    backoff = 2.0**attempt
                    if self._deadline is not None and not self._deadline.can_start(
                        ATTEMPT_TIMEOUT_SEC + backoff
                    ):
                        break
                    self._sleep(backoff)
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
