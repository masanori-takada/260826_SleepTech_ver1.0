# Tasks: Night Ramen Breakdown 自律改善ループシステム

**Input**: Design documents from `specs/001-night-ramen-breakdown/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: ガードレール検証のテストは Constitution 原則 II により**必須**。その他の主要ロジックにもユニットテストを含める。

**Organization**: タスクはユーザーストーリー単位でグループ化し、各ストーリーを独立して実装・テスト可能にする。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可（異なるファイル・依存なし）
- **[Story]**: 対応ユーザーストーリー
- ファイルパスを明記

---

## Phase 1: Setup (共有基盤)

- [ ] T001 リポジトリ直下に `src/night_ramen/`, `src/handlers/`, `infra/`, `tests/unit/`, `tests/integration/` のディレクトリ構成と各 `__init__.py` を作成（plan.md の構成に準拠）
- [ ] T002 `requirements.txt` / `pyproject.toml` を作成し依存（aws-cdk-lib, strands-agents, bedrock-agentcore, boto3, line-bot-sdk, google-auth-oauthlib, pytest, moto）を固定
- [ ] T003 [P] lint/format 設定（ruff + black）と `pytest.ini` を追加
- [ ] T004 [P] `src/night_ramen/config.py` に環境・モデル設定（既定 Haiku、本番 Opus 4.8/Sonnet 4.6 を切替可能）としきい値（通知1回/再生成2回/保持1年/既定目標85%・2kg）を実装

**Checkpoint**: 構成と設定の土台が完成

---

## Phase 2: Foundational (全ストーリーの前提・ブロッキング)

**⚠️ CRITICAL**: このフェーズ完了までユーザーストーリー実装は開始不可

- [ ] T005 `src/night_ramen/memory/schemas.py` に data-model.md の全エンティティ（DailyVitalRecord, Goal, PersuasionMessage, PersuasionStrategy, AnalysisResult, Tone enum）を dataclass で実装
- [ ] T006 [P] `src/night_ramen/observability.py` に構造化JSONログ・相関ID・PII/トークンマスキングを実装（FR-011,013 / 原則III,V）
- [ ] T007 `src/night_ramen/memory/client.py` に agentcore Memory ラッパ（save/get vital・analysis・strategy・message）を実装。`save_strategy` は `guardrail_passed=False` で ValueError（FR-010 不変条件）
- [ ] T008 [P] `tests/unit/test_memory_schemas.py` でスキーマの欠損許容・不変条件を検証
- [ ] T009 **[ガードレール/原則II]** `tests/unit/test_guardrail.py` を**先に**作成（差別/目標逸脱/正論の暴力=不合格、健全=合格、例外=fail-closed、3回不合格=fallback）。実装前に FAIL することを確認
- [ ] T010 **[ガードレール/原則II]** `src/night_ramen/guardrail/fallback.py`（固定安全文面）と `src/night_ramen/guardrail/validator.py`（3観点検査・fail-closed・`generate_safe_message` 最大2回再生成）を実装し T009 を緑にする（FR-008,009,015）

**Checkpoint**: 記憶・ログ・ガードレールの基盤が完成（送信経路の単一安全ゲート確立）

---

## Phase 3: User Story 1 - 夜の説得通知を受け取る (Priority: P1) 🎯 MVP

**Goal**: バイタルに基づく説得メッセージを 23:00 に LINE へ1回、ガードレール検証の上で送信する

**Independent Test**: ダミーのバイタル・目標を与え夜ループを発火し、検証通過した説得メッセージが LINE（dry-run可）へ届く

### Tests for User Story 1 ⚠️（先に書いて FAIL を確認）

- [ ] T011 [P] [US1] `tests/integration/test_night_flow.py` で夜ループの統合テスト（生成→検証→送信、未通知冪等、データ言及）
- [ ] T012 [P] [US1] `tests/unit/test_notifier.py` で LINE 送信の1晩1回冪等（`send_if_not_notified`）を検証

### Implementation for User Story 1

- [ ] T013 [P] [US1] `src/night_ramen/agents/prompts.py` にモデル選択・システムプロンプトの基盤を実装
- [ ] T014 [P] [US1] `src/night_ramen/agents/analyzer.py` に分析エージェント（バイタル→悪影響の数値化, FR-004）を Bedrock 経由で実装
- [ ] T015 [US1] `src/night_ramen/agents/persuader.py` に説得メッセージ生成（`generate_message`, FR-007）を実装（戦略+分析+トーンを入力）
- [ ] T016 [US1] `src/night_ramen/notifier/line.py` に LINE push と DynamoDB 冪等（NOTIFY フラグ, FR-014）を実装
- [ ] T017 [US1] `src/night_ramen/workflow/night.py` に strands 夜ループ（分析→生成→guardrail→送信）を実装。送信は検証通過＋未通知時のみ（原則II/FR-007,009,014）
- [ ] T018 [US1] `src/handlers/night_handler.py` に Lambda エントリ（EventBridge起動, dry_run対応）を実装
- [ ] T019 [US1] データ欠損時のフォールバック（個別化せず安全文面 or 見送り, FR-015）を `night.py` に組み込み T011 を緑にする

**Checkpoint**: US1 が単独で機能（MVP）。ダミーデータで LINE 通知を確認可能

---

## Phase 4: User Story 2 - 朝のバイタル収集と記憶蓄積 (Priority: P1)

**Goal**: 毎朝 Google Health から網羅的バイタルを取得し自己申告と紐づけ記憶へ保存

**Independent Test**: 同意済みテストアカウントで朝ルーティンを実行し、当日分レコードが記憶に保存される

### Tests for User Story 2 ⚠️

- [ ] T020 [P] [US2] `tests/unit/test_collectors.py` で欠損許容・異常値除外・同意失効時の停止（FR-003,012,015）を検証
- [ ] T021 [P] [US2] `tests/integration/test_morning_flow.py` で収集→記憶保存を検証

### Implementation for User Story 2

- [ ] T022 [P] [US2] `src/night_ramen/collectors/google_health.py` に OAuth（Secrets Manager トークン）と全指標取得（FR-001,003）、同意失効 `ConsentRevokedError`（FR-012）を実装
- [ ] T023 [P] [US2] `src/night_ramen/collectors/self_report.py` に夜食ラーメン自己申告取り込み（未申告=None, FR-002）を実装
- [ ] T024 [US2] `src/night_ramen/workflow/morning.py` に strands 朝ループ（収集→紐づけ→記憶保存, FR-002,013）を実装
- [ ] T025 [US2] `src/handlers/morning_handler.py` に Lambda エントリ（EventBridge起動）を実装し T021 を緑にする

**Checkpoint**: US1・US2 が独立して機能。個別化説得の根拠データが蓄積される

---

## Phase 5: User Story 3 - 目標設定と強度自動調整 (Priority: P2)

**Goal**: 達成率を評価し、説得メッセージの強度トーンを自動調整

**Independent Test**: 高/低達成率のダミー実績で、選択トーンが変化することを確認

### Tests for User Story 3 ⚠️

- [ ] T026 [P] [US3] `tests/unit/test_goals.py` で達成率算定・トーン決定・既定目標適用（FR-005,006,017）を検証

### Implementation for User Story 3

- [ ] T027 [P] [US3] `src/night_ramen/goals/evaluator.py` に `evaluate`（達成率）と `choose_tone`（強度決定、既定85%/2kg, FR-005,006,017）を実装
- [ ] T028 [US3] 目標設定の入出力（DynamoDB `GOAL/current`）と `/goal` 相当のCLI/設定インターフェースを実装
- [ ] T029 [US3] `workflow/night.py` にトーン決定を統合（evaluator→persuader へトーン受け渡し）し T026 を緑にする

**Checkpoint**: US1〜US3 が独立機能。説得がパーソナライズされる

---

## Phase 6: User Story 4 - 説得アプローチの自律学習 (Priority: P3)

**Goal**: 説得履歴と結果から戦略プロンプトを自律更新（検証通過版のみ保存）

**Independent Test**: 異なる履歴/結果を与えると次回戦略の方針が変化する

### Tests for User Story 4 ⚠️

- [ ] T030 [P] [US4] `tests/unit/test_strategy_update.py` で戦略更新が履歴を反映し、未検証戦略が保存されない（FR-010 不変条件）ことを検証

### Implementation for User Story 4

- [ ] T031 [US4] `src/night_ramen/agents/persuader.py` に `update_strategy`（履歴+結果→新戦略, FR-010）を実装
- [ ] T032 [US4] `workflow/night.py` 末尾に「戦略更新→guardrail検証→通過時のみ `save_strategy`」を統合し T030 を緑にする

**Checkpoint**: 全ユーザーストーリーが独立機能。自律改善ループが閉じる

---

## Phase 7: Infrastructure & Polish (横断)

- [ ] T033 `infra/app.py` と `infra/stacks/night_ramen_stack.py` に CDK スタック（Lambda×2, EventBridge Scheduler×2[08:00/23:00 JST], DynamoDB[TTL], Secrets参照, **関数ごと最小IAM**）を実装（原則IV）
- [ ] T034 [P] DynamoDB の TTL とバイタル1年保持ポリシ（FR-016）を実装・設定
- [ ] T035 [P] `cdk diff` / `cdk synth` が通ることを確認し、手動コンソール変更ゼロを担保（原則IV）
- [ ] T036 [P] 全ユニットテスト緑・カバレッジ確認（特に `test_guardrail.py`／品質ゲート）
- [ ] T037 quickstart.md の検証チェックリストを実行（SC-002,003,004,006 を確認）
- [ ] T038 [P] README に運用手順（Bedrockモデル有効化・Secrets登録・デプロイ）を記載

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 即時開始可
- **Phase 2 (Foundational)**: Setup 後。**全ストーリーをブロック**。特に T009/T010（ガードレール）は US1 送信の前提
- **Phase 3-6 (User Stories)**: Foundational 後。優先度順（US1→US2→US3→US4）。US1/US2 は P1
- **Phase 7 (Infra/Polish)**: 主要ストーリー完了後

### Within Each Story
- テストを先に書いて FAIL を確認 → 実装 → 緑化
- schemas/models → service → workflow → handler の順

### Parallel Opportunities
- T003,T004 / T006,T008 / 各ストーリーの [P] テスト・モデルは並列可
- Foundational 完了後、US1〜US4 は人員があれば並列着手可

---

## Implementation Strategy

### MVP First
1. Phase 1 Setup → 2. Phase 2 Foundational（ガードレール含む）→ 3. Phase 3 US1 → **検証**（LINE通知）→ デモ可

### Incremental Delivery
US1（MVP）→ US2（収集基盤）→ US3（目標調整）→ US4（自律学習）→ Infra/Polish の順に価値を積み増す

---

## Notes
- [P]=異なるファイル・依存なし。[Story]=トレーサビリティ
- ガードレール（T009/T010）と各テストは Constitution 品質ゲート。実装前に FAIL を確認
- 各タスク/論理単位ごとにコミット
- 機微データ・トークンを平文でログ/コードに残さない（FR-011 / 原則III）
