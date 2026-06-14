# Phase 1: Data Model — LINE 対話エージェント（002）

**Date**: 2026-06-14 | **Branch**: `002-line-agentcore-memory`

## agentcore Memory（新規）

### 会話ターン（STM / Event）
| 概念 | 値 |
|------|----|
| memoryId | Memory リソースID（`NRB_MEMORY_ID`）|
| actorId | LINE userId（ユーザー単位分離）|
| sessionId | `line-<YYYY-MM-DD>`（日付セッション）|
| role | `USER` / `ASSISTANT` |
| text | 発話本文 |
| timestamp | 自動 |

### 長期記憶レコード（LTM / 戦略抽出）
| 戦略 | 名前空間 | 内容 |
|------|----------|------|
| semantic（事実） | `/facts/{actorId}/` | ユーザーに関する事実（例「夜勤がある」）|
| userPreference（嗜好） | `/preferences/{actorId}/` | 嗜好・効く伝え方（例「データで示すと響く」）|
| summary（要約） | `/summaries/{actorId}/{sessionId}/` | セッション要約 |

- 取得: 名前空間ごとに semantic 検索（top_k, relevance_score）。会話生成時に文脈へ注入。
- 抽出は非同期・結果整合（即時反映前提にしない）。

## DynamoDB（継続・本機能で参照/書込）

- **REPORT/<date>**: 会話/固定パースから抽出した夜食ラーメン有無（既存スキーマ）。本機能が書込む。
- **MEM#vitals/<date>**, **GOAL/current**: 会話の文脈構築のため読込（直近バイタル要約・目標）。

## 会話処理の状態遷移（1メッセージ）

```
LINE受信(署名検証OK)
  → 固定パース parse_ramen_reply
       ├ true/false → REPORT保存
       └ None → 会話抽出(LLM)で yes/no/unknown → 確定なら REPORT保存
  → 文脈構築: LTM取得(facts/preferences/summaries) + 直近バイタル要約(DDB) + 目標(DDB) + STM直近ターン
  → Bedrock(Haiku)で健康コーチ応答を生成
  → ガードレール検証(最大2回再生成) → 不合格なら安全フォールバック
  → LINE返信(reply_message)
  → 会話ターン(USER/ASSISTANT)を Memory へ CreateEvent
```

## エンティティ関係（概念）

```
LINE userId = actorId ──┬─▶ agentcore Memory（STM会話 / LTM facts・preferences・summaries）
                        └─▶ DynamoDB（REPORT 申告 / vitals 読込 / goal 読込）
会話応答 ← LTM + 直近バイタル要約 + 目標 + STM
夜の説得(既存) ← LTM(嗜好) + 申告(REPORT) を活用（FR-110）
```
