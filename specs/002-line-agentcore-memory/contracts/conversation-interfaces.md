# Contract: 会話機能の内部インターフェース（002）

**Date**: 2026-06-14 | **Branch**: `002-line-agentcore-memory`

boto3（agentcore Memory / Bedrock）は注入可能にし、テストはフェイクで検証する。

## C1. memory_store（agentcore Memory ラッパ）

```python
# conversation/memory_store.py
class AgentCoreMemory:
    def __init__(self, memory_id: str, region: str,
                 control_client=None, data_client=None): ...

    def write_turn(self, actor_id: str, session_id: str, role: str, text: str) -> None:
        """会話ターンを STM へ保存（CreateEvent, FR-102）。"""

    def get_recent_turns(self, actor_id: str, session_id: str, k: int = 6) -> list[dict]:
        """直近 k ターンを取得（短期文脈, FR-102）。"""

    def retrieve_ltm(self, actor_id: str, query: str, top_k: int = 5) -> list[str]:
        """facts/preferences/summaries を横断 semantic 検索し、文脈文字列群を返す（FR-103,104）。
        結果整合のため空でも許容。"""
```

- 例外時は空リスト/no-op（fail-soft）。会話自体は継続（記憶が欠けても応答はする）。
- userId 以外をキーにしない（FR-107 分離）。

## C2. extractor（自己申告抽出）

```python
# conversation/extractor.py
def extract_ramen_report(text: str, invoker: ModelInvoker, model_id: str) -> bool | None:
    """固定パース併用の補完。会話文から夜食ラーメン有無を yes/no/unknown 分類（FR-105）。
    まず parse_ramen_reply、None のとき LLM 分類。判別不能は None。"""
```

## C3. agent（健康コーチ会話）

```python
# conversation/agent.py
@dataclass
class ConversationContext:
    recent_turns: list[dict]      # STM
    ltm_snippets: list[str]       # LTM
    vitals_summary: str           # DDB 直近バイタル要約
    goal_summary: str             # DDB 目標

def build_context(actor_id, session_id, memory, dynamo_memory, goal) -> ConversationContext: ...

def generate_reply(user_text: str, ctx: ConversationContext,
                   invoker: ModelInvoker, model_id: str) -> str:
    """健康コーチ system prompt（睡眠/体調/夜食に焦点、逸脱は本題へ）で応答生成（FR-101,112）。
    返り値は未検証。送信前に guardrail 必須（FR-106）。"""

HEALTH_COACH_PROMPT: str
SAFE_CONVERSATION_FALLBACK: str   # ガードレール不合格時の安全応答
```

## C4. webhook 拡張フロー

```python
# handlers/webhook_handler.py（拡張）
# 1. 署名検証（既存 verify_line_signature, FR-108）
# 2. 各 message:
#    a. 固定パース → None なら extractor で補完 → 確定なら REPORT 保存
#    b. build_context → generate_reply
#    c. guardrail.generate_safe_message(lambda: generate_reply(...), ctx, 2) → 本文確定
#    d. reply_message で返信（既存・urllib）
#    e. memory.write_turn(USER) / write_turn(ASSISTANT)
# 返信・記憶はベストエフォート（失敗してもクラッシュしない）
```

## C5. テスト契約

- `test_conversation_extractor.py`: 固定パース優先、LLM補完で yes/no/unknown、判別不能=None。
- `test_memory_store.py`: write_turn/get_recent_turns/retrieve_ltm を boto3 フェイクで検証。例外時 fail-soft。
- `test_webhook_conversation.py`（統合）: 署名検証 → 会話応答生成 → **ガードレール不合格時に危険応答が返らない** → 返信＋申告保存。
