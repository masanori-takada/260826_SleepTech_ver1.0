# Phase 0: Research & Decisions — LINE 対話エージェント（002）

**Date**: 2026-06-14 | **Branch**: `002-line-agentcore-memory`

AWS 公式（Bedrock AgentCore Memory）＋ Strands 統合ドキュメント、および実機検証に基づく決定。

## D1. agentcore Memory の能力（確認済み）

- **短期記憶(STM)**: 会話ターンを `actorId`/`sessionId` 単位で `CreateEvent` 保存、`ListEvents`/get_last_k_turns で取得。
- **長期記憶(LTM)**: 戦略で抽出。`semanticMemoryStrategy`(事実) / `userPreferenceMemoryStrategy`(嗜好) /
  `summaryMemoryStrategy`(要約)。名前空間 `/facts/{actorId}/` `/preferences/{actorId}/` `/summaries/{actorId}/{sessionId}/`。
  取得は**セマンティック検索**（top_k, relevance_score）。抽出は**非同期・結果整合**。
- Strands には `AgentCoreMemorySessionManager` 統合があるが、本プロジェクトは Lambda ベース（agentcore Runtime ではない）。

## D2. DynamoDB は不要か → **不要ではない（併存）**

| データ | ストア | 根拠 |
|--------|--------|------|
| 会話履歴・長期ユーザー理解 | **agentcore Memory** | 会話継続・意味検索はこれが本領 |
| デイリーバイタル時系列 | **DynamoDB** | 分析は「ラーメン有/無の正確な日付別数値比較」が必要。Memory のセマンティック検索は厳密な範囲取得に不向き |
| 通知冪等(1晩1回) | **DynamoDB** | Memory は追記型・結果整合で**原子的条件付き書込み不可** |
| 目標・自己申告(REPORT) | **DynamoDB** | 厳密なキー取得・決定的読み出しが必要 |

→ agentcore Memory は「置き換え」ではなく会話機能のための「追加」。役割分担で共存が正解。

## D3. Lambda への載せ方（実機検証で確定）

- boto3 **1.38.35 では `bedrock-agentcore` / `bedrock-agentcore-control` が UnknownServiceError**（サービスモデル未収録）。
- `bedrock-agentcore` Python SDK も内部で botocore のモデルに依存しており、古い botocore では同様に失敗。
  さらに SDK は **pydantic(ネイティブ pydantic-core)＋starlette/uvicorn** を引き込み、Windows ビルドの Lambda 同梱が重い。
- **boto3 1.43.29 に更新すると agentcore Memory が利用可能**（data plane + control 両方 OK を実機確認）。
- **決定**: 純Python の **boto3>=1.43 を Lambda Layer** で提供し、Lambda コードは **boto3 直叩き**で agentcore Memory を使う。
  strands/SDK は同梱しない。Layer は `pip install boto3 -t` で作れ、ネイティブ依存が無くクロスプラットフォーム安全（Docker不要）。
- **却下**: strands `AgentCoreMemorySessionManager` 同梱 → ネイティブ依存・重量でデプロイ困難。

## D4. 会話エージェントの実行場所

- **決定**: 既存 **Webhook Lambda 内で同期実行**。ユーザー発話 → 文脈構築（Memory取得＋DDBバイタル要約）→ Bedrock(Haiku)生成 →
  ガードレール → LINE 返信、を1リクエストで完結。会話ターンは Memory へ `CreateEvent`。
- timeout を 30s→60s、memory を 256→512MB に引き上げ（Memory取得＋生成の余裕）。
- LINE の replyToken 有効期間内（数十秒）に返信。Haiku なら数秒で収まる。

## D5. ガードレール（NON-NEGOTIABLE）

- 会話返信も既存 `guardrail.generate_safe_message(generator, ctx, max_retries=2)` を適用。
- 不合格時は安全なフォールバック応答（例「うまく言葉にできなかった、もう一度教えて」）。原則II・FR-106。

## D6. 自己申告の併用（FR-105）

- Webhook はまず**固定キーワードパース**（`parse_ramen_reply`）。`None`（判別不能）なら**会話抽出（LLM）**で補完。
- いずれかで真偽が確定したら DynamoDB `REPORT/<date>` に保存（既存 `DynamoSelfReportSource.put`）。
- 会話抽出は Bedrock に「ユーザー発話から夜食ラーメンの有無を yes/no/unknown で分類」させる小タスク。

## D7. ユーザー/セッションの識別

- `actorId` = LINE userId（ユーザー単位分離＝FR-107）。
- `sessionId` = 日付ベース（`line-<YYYY-MM-DD>`）。日内の連続会話は同一セッション、LTM が日跨ぎ理解を担う。
- 名前空間: `/facts/{actorId}/` `/preferences/{actorId}/` `/summaries/{actorId}/{sessionId}/`。

## D8. Memory リソースの provisioning

- **決定**: `scripts/create_memory.py` で **1回だけ**作成（3戦略）。出力 `MEMORY_ID` を CDK の環境変数 `NRB_MEMORY_ID` に設定。
- 作成は数分（非同期 ACTIVE 化）。`create_memory_and_wait` 相当（boto3 control + ポーリング）。
- 実 API のフィールド名（create_memory のstrategies構造・CreateEvent/Retrieve のキー）は**実機で検証して確定**（Google Health 同様）。

## FR → 実装トレーサビリティ

| FR | 実装 |
|----|------|
| FR-101,102 | `conversation/agent.py`（STM 取得→生成）, `webhook_handler` |
| FR-103,104 | `conversation/memory_store.py`（LTM retrieve）＋ DDBバイタル要約注入 |
| FR-105 | `conversation/extractor.py` ＋ 既存 `parse_ramen_reply` ＋ `DynamoSelfReportSource` |
| FR-106 | 既存 `guardrail`（会話返信に適用） |
| FR-107 | actorId=userId による名前空間分離（`memory_store.py`） |
| FR-108 | 既存 `verify_line_signature` |
| FR-109 | 既存 `observability`（マスキング）|
| FR-110 | 既存 morning/night が DDB(申告) と Memory(LTM) を参照 |
| FR-111 | timeout/memory 引き上げ＋簡潔フォールバック |
| FR-112 | `agent.py` の健康コーチ system prompt |

## 未解決（実装時に実機確定）

- agentcore Memory data plane の正確な API シグネチャ（CreateEvent / RetrieveMemoryRecords のパラメータ・レスポンス）。
- LTM 抽出の所要時間（結果整合の実遅延）。MVP は STM＋即時 DDB 文脈で成立し、LTM は時間経過で効いてくる設計とする。
