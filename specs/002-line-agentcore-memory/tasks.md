# Tasks: LINE 対話エージェント（長期記憶活用 / 002）

**Input**: design docs from `specs/002-line-agentcore-memory/`
**Tests**: ガードレール適用と自己申告抽出は必須テスト（原則II / 品質ゲート）。

## Phase 1: Setup & 基盤

- [ ] T201 `scripts/create_memory.py` を実装（boto3 control で3戦略付き Memory 作成、ACTIVE 待ち、MEMORY_ID 出力）。実機で1回実行し ID 取得
- [ ] T202 `src/night_ramen/config.py` に `memory_id`（`NRB_MEMORY_ID`）と会話モデル（既定Haiku）設定を追加
- [ ] T203 [P] `infra/layers/boto3/python` に boto3>=1.43 を展開（純Python Layer 素材）

## Phase 2: 会話コア（ガードレール再利用）

- [ ] T204 [P] `conversation/memory_store.py` 実装（AgentCoreMemory: write_turn/get_recent_turns/retrieve_ltm、boto3注入可、例外fail-soft）
- [ ] T205 [P] `tests/unit/test_memory_store.py`（boト3フェイクで write/retrieve・fail-soft 検証）
- [ ] T206 `conversation/extractor.py` 実装（固定パース併用→LLM補完で yes/no/unknown, FR-105）
- [ ] T207 [P] `tests/unit/test_conversation_extractor.py`（固定優先・LLM補完・判別不能=None）
- [ ] T208 `conversation/agent.py` 実装（健康コーチ prompt・build_context・generate_reply・SAFE_CONVERSATION_FALLBACK, FR-101,104,112）

## Phase 3: Webhook 統合（US1/US2）

- [ ] T209 `handlers/webhook_handler.py` を拡張: 署名検証→固定パース/会話抽出→申告保存→build_context→generate_reply→**guardrail.generate_safe_message**→reply→Memory書込（FR-106 必須）
- [ ] T210 `tests/integration/test_webhook_conversation.py`（署名→会話→**危険応答が送信されない**→返信＋申告保存）を作成し緑化
- [ ] T211 データ欠損/記憶空/抽出不能の各フォールバック（FR-111, Edge Cases）を組み込み

## Phase 4: 既存ループへの接続（US3 / FR-110）

- [ ] T212 夜の説得（`workflow/night.py`）が LTM(嗜好) を文脈に取り込めるよう接続（任意strに要約注入）。記憶空でも従来動作を維持

## Phase 5: インフラ & デプロイ

- [ ] T213 `infra/stacks/night_ramen_stack.py`: Webhook Lambda に boto3 Layer・`NRB_MEMORY_ID` env・bedrock-agentcore/bedrock IAM を付与、timeout 60s/memory 512MB へ
- [ ] T214 `cdk diff`/`synth` 通過確認 → `cdk deploy`
- [ ] T215 全ユニット緑（特に extractor/guardrail）＋ quickstart 検証（会話・申告・後日記憶）

## Dependencies

- T201→T202→T213（MEMORY_ID 必要）。T203→T213（Layer）。
- T204/T206/T208（会話コア）→ T209（統合）→ T210。
- Phase5 は会話コア＋統合完了後。

## Implementation Strategy

MVP: T201-T210（会話応答＋ガードレール＋申告抽出）→ デプロイ（T213-T214）→ 実機確認。
US3(T212) と微調整は MVP 後。各タスク/論理単位でコミット。
