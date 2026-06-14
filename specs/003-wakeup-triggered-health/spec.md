# Feature Specification: 起床トリガー型データ連携

**Feature Branch**: `003-wakeup-triggered-health`

**Created**: 2026-06-15

**Status**: Implemented

**Input**: User description: "早めに起きたとしても朝８時を待たずにデータ連携を取れるようにしたい。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - LINE一言で当日データを即収集 (Priority: P1)

ユーザーが朝8時の定時収集を待たずに起床した際、LINE で「おはよう」などの一言を送ると、
システムがその場で当日分の Google Health バイタルを収集して保存し、結果を短く返信する。

**Why this priority**: 起床時刻は日によって変動するため固定スケジュールの前倒しでは対応
できない。本人のLINE操作を起点にすることで、いつ起きても最新データに基づく対話・通知の
前提を整えられる。これが本機能の唯一かつ中核の価値。

**Independent Test**: フェイクの HealthDataSource を注入し、Webhook イベント（text=「おはよう」）
で handler を呼ぶと当日 DailyVitalRecord が保存され、返信文に主要指標が含まれることを確認
（`tests/integration/test_wakeup_flow.py`）。

**Acceptance Scenarios**:

1. **Given** 当日バイタル未収集の朝6時、**When** ユーザーが「おはよう」を送る、
   **Then** 当日のバイタルが収集・保存され、睡眠スコア・深い睡眠・心拍/HRV・昨夜のラーメン
   有無を含む確認メッセージが返信される。
2. **Given** デバイスが未同期で全指標が欠損、**When** トリガー語を送る、
   **Then** 「まだ同期されてないみたい。少し経ってからもう一度試してね」と返信される。
3. **Given** Google Health の同意が失効、**When** トリガー語を送る、
   **Then** 収集は停止し、連携切れを知らせる返信が返る（収集は保存されない）。
4. **Given** ユーザーが「食べた」「我慢した」を送る、**When** Webhook が受信、
   **Then** 従来通り自己申告として保存・確認返信され、収集分岐には入らない。

### Edge Cases

- トリガー語を1日に複数回送った場合: 都度収集が走るが `save_vital` は上書き（冪等）のため
  実害はない（MVP では回数制限なし）。
- 朝8時の定時収集とトリガー収集が同日に二重で走った場合: 上書きで安全（冪等）。
- 収集が長引いた場合: replyToken が短命のため取りこぼす恐れ。収集を高速に保つことで回避し、
  問題化したら push 方式へ切替（plan の留意点参照）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST 朝8時の EventBridge 定時収集（`cron(0 8 * * ? *)` Asia/Tokyo）を
  フォールバックとして維持する。
- **FR-002**: System MUST LINE メッセージから起床トリガー語（「おはよう」「データ更新」
  「同期」等）を判定する（`is_wakeup_trigger`）。自己申告語とは重複させない。
- **FR-003**: System MUST トリガー語受信時に当日分の収集（`run_morning`）を同期実行し、
  結果を DynamoDB に保存する。朝ループと同じ依存・処理を再利用する。
- **FR-004**: System MUST 収集結果を短い日本語サマリ（睡眠スコア・深い睡眠・中途覚醒・
  平均心拍・HRV・昨夜のラーメン有無、欠損は「—」）で LINE 返信する。
- **FR-005**: System MUST 同意失効時（`ConsentRevokedError`）は収集を停止し、保存せず、
  連携切れを知らせる返信を行う（FR-012/002 の挙動を継承）。
- **FR-006**: System MUST 自己申告（食べた/我慢した）の従来動作を維持し、収集分岐より
  優先する。

### Key Entities *(include if feature involves data)*

- **DailyVitalRecord**: 既存。当日1日分のバイタル＋自己申告。本機能で新規エンティティは
  追加しない（総睡眠時間フィールドは現状なし）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 早起きした朝、LINE で一言送るだけで8時を待たずに当日データが収集・保存される。
- **SC-002**: トリガーから数秒以内に結果サマリが返信される（replyToken 有効時間内）。
- **SC-003**: 自己申告の既存フローに回帰がない（既存テストが全て通過）。
- **SC-004**: 同日にトリガー収集と8時定時収集が二重に走ってもデータが壊れない（冪等）。

## Assumptions

- ユーザーは単一利用者（本人）で、LINE Webhook 経由で操作する。
- Google Health の OAuth リフレッシュトークンは `night-ramen/google-oauth` に有効な状態で
  保管されている。
- 「総睡眠時間（◯時間）」表示は現スキーマに無いため範囲外。必要時は `DailyVitalRecord` への
  `total_sleep_min` 追加＋収集ロジック拡張（002 ブランチに実装あり）を別スコープで行う。
- LINE 応答は同期＋`reply_message`（無料）を採用。`channel_secret` 設定による署名検証は
  本番で有効化推奨（公開エンドポイント濫用対策）。
