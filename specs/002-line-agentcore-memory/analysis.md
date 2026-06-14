# Cross-Artifact Analysis — LINE 対話エージェント（002）

**Date**: 2026-06-14 | 対象: spec / plan / research / data-model / contracts / tasks（002）＋ constitution

## 1. 要件カバレッジ（FR → タスク）

| FR | 内容 | タスク | 状態 |
|----|------|--------|------|
| FR-101 | 自由文に対話応答 | T208, T209 | ✅ |
| FR-102 | 会話履歴を文脈反映(STM) | T204, T208 | ✅ |
| FR-103 | 長期記憶でユーザー学習(LTM) | T204, T208, T212 | ✅ |
| FR-104 | バイタル傾向を文脈活用 | T208（build_context）| ✅ |
| FR-105 | 会話からの自己申告抽出(併用) | T206, T207, T209 | ✅ |
| FR-106 | 返信のガードレール必須 | T209, T210 | ✅ |
| FR-107 | ユーザー単位の記憶分離 | T204（actorId=userId）| ✅ |
| FR-108 | 入口の署名検証 | T209（既存流用）| ✅ |
| FR-109 | PII/トークン非ログ | 既存 observability | ✅ |
| FR-110 | 既存ループが申告/LTM活用 | T212 | ✅ |
| FR-111 | 遅延時の簡潔応答 | T211, T213 | ✅ |
| FR-112 | 健康コーチ挙動 | T208 | ✅ |

**結果**: 全12 FR がタスクにマップ。孤立なし。

## 2. Constitution 整合

| 原則 | 担保 |
|------|------|
| I. 仕様駆動 | research に FR対応表、本 analysis でトレース ✅ |
| II. 安全・ガードレール (NON-NEGOTIABLE) | 会話返信に `generate_safe_message` 適用（T209）、危険応答非送信テスト（T210）✅ |
| III. プライバシー最小権限 | actorId 以外をキーにしない、最小IAM(T213)、マスキング継続 ✅ |
| IV. IaC | Memory作成スクリプト＋Layer/IAM/env を CDK 管理(T213) ✅ |
| V. 観測・コスト | 会話=Haiku、相関IDログ ✅ |

## 3. 整合性・リスク

| # | 重大度 | 指摘 | 対応 |
|---|--------|------|------|
| B1 | Med | agentcore Memory data plane の正確なAPI（CreateEvent/Retrieve のキー）が未確定 | 実装時に実機検証（research D8）。`memory_store` に集約し1箇所で調整 |
| B2 | Med | Webhook 同期実行＋Memory取得＋生成のレイテンシ | Haiku＋timeout60s/memory512、STM k=6 に抑制。超過時フォールバック(T211) |
| B3 | Low | LTM 抽出は結果整合（即時反映しない）| MVP は STM＋DDB文脈で成立。LTMは時間経過で寄与（spec Assumptions 記載済み）|
| B4 | Low | boto3 Layer と Lambda 同梱 boto3 の競合 | Layer が優先される（PYTHONPATH 順）。デプロイ後 `UnknownServiceError` が出ないことを確認(T214,215)|
| B5 | Info | DynamoDB 併存（Memory と二重）| research D2 で役割分担を正当化済み（置換不可）|

**Critical ブロッカー**: なし。

## 4. 用語・数値の一貫性

- 「actorId=LINE userId」「sessionId=line-<date>」「3戦略(semantic/userPreference/summary)」が spec/plan/research/data-model/contracts で一貫 ✅
- 会話モデル=Haiku、ガードレール=既存流用、DynamoDB併存が全成果物で一致 ✅

## 5. 総合判定

✅ **PASS** — 全12 FR マップ、Constitution 5原則担保、Critical ブロッカーなし。
B1（API実機確定）と B2（レイテンシ）は実装時に解消する既知事項。`/speckit-implement` 進行可。MVP=T201–T210→デプロイ。
