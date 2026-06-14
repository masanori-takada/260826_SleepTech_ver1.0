# Contract: 記憶・永続スキーマ

**Date**: 2026-06-14 | **Branch**: `001-night-ramen-breakdown`

data-model.md のエンティティを、agentcore Memory と DynamoDB 単一テーブルの物理キーへ写像する。

## agentcore Memory（名前空間）

| 名前空間 | エンティティ | キー |
|----------|-------------|------|
| `vitals/` | DailyVitalRecord | `date` |
| `analysis/` | AnalysisResult | `date` |
| `messages/` | PersuasionMessage | `date`（＋連番） |
| `strategy/` | PersuasionStrategy | `current` ＋ `v{version}` 履歴 |

- 書き込みは `memory/client.py` を必ず経由（直接アクセス禁止）。
- `strategy/current` は `guardrail_passed=True` のもののみ（FR-010 不変条件）。

## DynamoDB 単一テーブル `NightRamenTable`

| PK | SK | 用途 | 主属性 |
|----|----|------|--------|
| `GOAL` | `current` | 目標設定 | success_rate_target, weight_loss_target_kg, baseline_weight_kg |
| `NOTIFY` | `<date>` | 通知冪等フラグ | notified(bool), ttl |

- `ttl`: NOTIFY 項目に epoch 秒。7日後失効（冪等確認に十分）。
- 課金: オンデマンド（単一ユーザーで低負荷）。

## シークレット（AWS Secrets Manager）

| シークレット名 | 内容 |
|----------------|------|
| `night-ramen/google-oauth` | Google リフレッシュトークン, client_id, client_secret |
| `night-ramen/line` | LINE channel access token, user id |

- いずれも平文ログ・コード埋め込み禁止（FR-011 / 原則III）。

## バリデーション規約

- 日付は `YYYY-MM-DD`（JST）。
- バイタル数値は欠損許容（null）。異常値は保存前に除外し `missing_fields` へ。
- 文字列本文は最大長を設定（LINE 制約に準拠）。超過時は要約。
