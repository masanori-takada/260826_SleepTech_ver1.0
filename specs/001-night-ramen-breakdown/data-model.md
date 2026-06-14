# Phase 1: Data Model — Night Ramen Breakdown

**Date**: 2026-06-14 | **Branch**: `001-night-ramen-breakdown`

spec.md「Key Entities」を実装可能なスキーマへ具体化する。永続先は agentcore Memory（記憶系）と
DynamoDB（制御メタ系）に分かれる。日付は ISO 8601（`YYYY-MM-DD`, JST 基準）。

## E1. デイリーバイタルレコード (DailyVitalRecord)  ── Memory

| フィールド | 型 | 説明 | 出典FR |
|-----------|----|------|--------|
| `date` | string(date) | 対象日（記録キー） | FR-002 |
| `ramen_last_night` | bool \| null | 夜食ラーメン有無の自己申告。未申告は null | FR-002 |
| `sleep_score` | int \| null | 睡眠スコア | FR-003 |
| `awakenings` | int \| null | 中途覚醒回数 | FR-003 |
| `deep_sleep_min` | int \| null | 深い睡眠の時間(分) | FR-003 |
| `night_avg_hr` | float \| null | 夜間平均心拍数 | FR-003 |
| `hrv` | float \| null | 心拍変動(HRV, ms) | FR-003 |
| `skin_temp_delta` | float \| null | 睡眠中の皮膚温変化(℃) | FR-003 |
| `respiration_rate` | float \| null | 呼吸数(回/分) | FR-003 |
| `weight_kg` | float \| null | 翌朝の体重 | FR-003 |
| `body_fat_pct` | float \| null | 体脂肪率 | FR-003 |
| `next_day_steps` | int \| null | 翌日の総歩数 | FR-003 |
| `next_day_calories` | int \| null | 翌日の消費カロリー | FR-003 |
| `missing_fields` | string[] | 取得できなかった指標名（欠損記録） | FR-003,015 |
| `ingested_at` | string(datetime) | 取得時刻 | FR-013 |

- **検証**: 全バイタルは null 許容（欠損許容, FR-003）。異常値は取り込み時に除外し `missing_fields` へ。
- **保持**: 1年（FR-016）。

## E2. 目標 (Goal)  ── DynamoDB (`PK=GOAL`, `SK=current`)

| フィールド | 型 | 既定値 | 説明 |
|-----------|----|--------|------|
| `success_rate_target` | float | 0.85 | ラーメン我慢成功率の目標（FR-017） |
| `weight_loss_target_kg` | float | 2.0 | 減量目標(kg)（FR-017） |
| `baseline_weight_kg` | float \| null | null | 開始時体重（達成率算定の基準） |
| `set_at` | string(datetime) | — | 設定時刻 |

- **未設定時**: 既定値（85% / 2kg）を用いる（FR-017）。

## E3. 説得メッセージ履歴 (PersuasionMessage)  ── Memory

| フィールド | 型 | 説明 | 出典FR |
|-----------|----|------|--------|
| `date` | string(date) | 送信対象夜の日付 | — |
| `body` | string | 生成された本文（送信版 or フォールバック） | FR-007 |
| `tone` | enum(`praise`,`neutral`,`warning`,`strong_warning`) | 選択された強度トーン | FR-006 |
| `guardrail_result` | enum(`pass`,`fallback`) | 検証結果 | FR-008 |
| `regeneration_count` | int | 再生成回数(0-2) | FR-009 |
| `referenced_metrics` | string[] | 本文が言及したバイタル指標 | SC-004 |
| `delivered` | bool | 実送信されたか | SC-002 |
| `sent_at` | string(datetime) | 送信/見送り時刻 | FR-013 |

## E4. 説得プロンプト戦略 (PersuasionStrategy)  ── Memory (`current` を1件保持＋履歴)

| フィールド | 型 | 説明 | 出典FR |
|-----------|----|------|--------|
| `version` | int | 戦略バージョン（更新ごと +1） | FR-010 |
| `system_prompt` | string | 説得生成に用いる現行システムプロンプト | FR-010 |
| `rationale` | string | 直近更新の根拠（どの結果を反映したか） | FR-010,013 |
| `guardrail_passed` | bool | 検証通過版のみ保存（true 固定） | FR-010 |
| `updated_at` | string(datetime) | 更新時刻 | FR-013 |

- **不変条件**: `guardrail_passed=false` の戦略は保存しない（FR-010）。

## E5. 分析結果 (AnalysisResult)  ── Memory

| フィールド | 型 | 説明 | 出典FR |
|-----------|----|------|--------|
| `date` | string(date) | 分析対象日 | — |
| `impacts` | object[] | `{metric, ramen_effect, magnitude, confidence}` の配列 | FR-004 |
| `summary` | string | ラーメンの悪影響の要約（説得の根拠） | FR-004 |
| `model_id` | string | 使用 Bedrock モデル | FR-013,V |
| `analyzed_at` | string(datetime) | 分析時刻 | FR-013 |

## E6. 通知冪等フラグ  ── DynamoDB (`PK=NOTIFY`, `SK=<date>`, TTL)

| フィールド | 型 | 説明 |
|-----------|----|------|
| `date` | string(date) | 通知対象夜 |
| `notified` | bool | 当夜の送信済みフラグ（1晩1回, FR-014） |
| `ttl` | number | 自動失効（epoch、例: 7日後） |

## 状態遷移（夜の説得メッセージ）

```
生成(tone決定) → ガードレール検証
  ├─ pass            → 送信 → PersuasionMessage(delivered=true, result=pass)
  └─ fail → 再生成(<=2)
            ├─ pass  → 送信
            └─ 2回超 → 固定安全文面 → 送信 → result=fallback
（送信は当夜未通知のときのみ：NOTIFY フラグで冪等）
```

## 関係図（概念）

```
Goal ──基準──▶ goals/evaluator ──達成率──▶ persuader
DailyVitalRecord ──▶ analyzer ──▶ AnalysisResult ──根拠──▶ persuader
PersuasionStrategy(current) ──方針──▶ persuader ──本文──▶ guardrail ──▶ notifier(LINE)
送信結果 + 翌日の DailyVitalRecord ──学習──▶ PersuasionStrategy(version+1)
```
