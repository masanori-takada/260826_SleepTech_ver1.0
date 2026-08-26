# ブランチ全体レビュー修正フェーズA 実施報告

## 対応範囲

デプロイ阻害項目A1〜A4を、サブエージェントなし・テスト先行で修正した。

### A1 Google Health

- HRVは対象JST日について `sample_time.civil_time >= date AND < next_date` を付けて取得。
- Sleepは `sleep.interval.civil_end_time >= date AND < next_date` を付けて取得。
- Google HealthのSleep `metadata.nap` がtrueの候補を除外。
- 候補選択は入力順に依存せず、主睡眠フラグ（既存データ互換の `main` / `isMainSleep`）を優先し、次に物理区間の長さ、最後に起床時刻で決定。
- list取得は最大4ページ・8秒の時間予算で打ち切る。超過時は取得済みデータだけを集計し、取得済みデータが無ければ欠損として安全に扱う。
- Google公式仕様にあるSleepの最大pageSize 25に合わせた。

### A2 LLM時間予算

- Lambda contextの`get_remaining_time_in_millis`を単調時計ベースの`ExecutionDeadline`へ変換。
- OpenAI試行の上限を8秒、LINE送信用の予約時間を12秒とし、期限内に開始できない試行・再試行・backoffを打ち切る。
- 夜・朝のcomposeループもdeadlineを確認し、残り時間が少なければ即テンプレートへ切り替える。
- LINE送信の固定HTTP timeoutも8秒へ短縮。

### A3 LINE二重送信

- `date`と`slot`からUUIDv5を生成し、初回送信から`X-Line-Retry-Key`を付与。
- DynamoDB/InMemoryともに`PENDING`とleaseを原子的にclaimし、同一slotの並行実行を1つに制限。
- lease失効後は再claim可能。送信成功後は`SENT`へ遷移し、既存の`mark_notified`呼び出し契約も維持。
- 送信失敗時は`SENT`へ遷移しないため、lease失効後の再試行が可能。LINE側には同じRetry-Keyを再利用する。

### A4 Webhook送信者認可

- `source.type == "user"` かつ `source.userId == Secret user_id` のイベントだけ処理。
- group/room、欠落、第三者ユーザーはbatch内で個別にskipし、副作用を発生させない。
- LINE Secretは署名検証と認可で1回だけ取得。
- userIdはログへ出力しない。

## 仕様上の判断・矛盾

- Google公式のSleepMetadataには`nap`、`processed`、`manuallyEdited`等はあるが、`main`という標準フィールドは確認できない。そのため標準のnap除外を必須とし、`main` / `isMainSleep`は既存または供給元固有データに対する互換的な優先ヒントとしてのみ扱った。標準フィールドがない場合は最長非昼寝レコードを採用する。
- APIの実データ源を変更する`reconcile`や`dataSourceFamily`は、既存のlist取得契約を壊さないため今回導入していない。
- 既存の直接`mark_notified`利用は、claimなしでも従来どおり初回登録できる後方互換を維持した。通常フローはclaim経由である。

## 外部操作

AWS deploy、Secrets Manager登録、外部API実行、既存Scheduler変更、LINE実機確認は実施していない。

## 検証

- `python -m pytest -q`: 335 passed
- `git diff --check`: 実施予定（コミット前に再実行）
- 未追跡の`.agents/`と`AGENTS.md`: 変更なし
