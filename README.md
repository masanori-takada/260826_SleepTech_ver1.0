# SleepTech

睡眠データから翌日のパフォーマンス低下を予測し、LINE で1日2回アドバイスする。

- **夜 22:30**: 推奨就寝時刻を1つ提示
- **朝 8:00**（「おはよう」で前倒し可）: 時間帯別の低下カーブとリカバリー行動

## アーキテクチャ

| 層 | 技術 |
|----|------|
| インフラ | AWS CDK (Python) — Lambda×3, EventBridge Scheduler×2, DynamoDB, Secrets Manager |
| 予測 | Two-Process Model（純粋関数・LLM 不使用） |
| 文章化 | OpenAI Chat Completions API |
| 通知 | LINE Messaging API |
| データ源 | Google Health API v4（OAuth 2.0） |

## テスト

```bash
python -m pytest
```

## ドキュメント

- 構成図: `docs/architecture.md`
- デプロイ手順: `docs/deploy.md`
- 仕様: `docs/superpowers/specs/2026-08-26-sleep-performance-line-advisor-design.md`
- 計画: `docs/superpowers/plans/2026-08-26-sleep-performance-line-advisor.md`
