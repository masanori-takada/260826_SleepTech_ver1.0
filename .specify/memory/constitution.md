<!--
Sync Impact Report
===================
Version change: TEMPLATE (unversioned) → 1.0.0
Bump rationale: 初回批准（MAJOR）。全プレースホルダを具体原則へ置換。

Modified principles:
- [PRINCIPLE_1_NAME] → I. 仕様駆動開発 (Spec-Driven First)
- [PRINCIPLE_2_NAME] → II. 安全・ガードレール最優先 (Safety & Guardrails / NON-NEGOTIABLE)
- [PRINCIPLE_3_NAME] → III. プライバシーと最小権限 (Privacy & Least Privilege)
- [PRINCIPLE_4_NAME] → IV. Infrastructure as Code
- [PRINCIPLE_5_NAME] → V. 観測可能性とコスト規律 (Observability & Cost Discipline)

Added sections:
- 追加制約 (技術スタック・コンプライアンス)
- 開発ワークフローと品質ゲート

Removed sections: なし

Templates requiring updates:
- ✅ .specify/templates/plan-template.md (Constitution Check と整合)
- ✅ .specify/templates/spec-template.md (要求/スコープと整合)
- ✅ .specify/templates/tasks-template.md (原則由来タスク分類と整合)

Follow-up TODOs: なし
-->

# Night Ramen Breakdown Constitution
<!-- 深夜のラーメン断ち自律改善ループシステム -->

## Core Principles

### I. 仕様駆動開発 (Spec-Driven First)

すべての実装は仕様（`spec-v4.md` および `specs/**/spec.md`）に由来しなければならない（MUST）。
コードを書く前に spec → plan → tasks の各成果物が存在し、相互に整合していること。
仕様にない機能を「念のため」実装してはならない（MUST NOT）。仕様の変更はコードの変更に先行する。

**根拠:** SDD を採用する本プロジェクトでは、仕様こそが唯一の信頼できる情報源であり、
エージェントの自律ループが暴走しないための最初の防壁となる。

### II. 安全・ガードレール最優先 (Safety & Guardrails / NON-NEGOTIABLE)

ユーザーへ送信される説得メッセージは、送信前に必ずガードレール検証を通過しなければならない（MUST）。
検証は最低限、次の3点を機械的にチェックする:
1. 差別的・不適切な言語を含まない。
2. 仕様のルール・目標から逸脱しない。
3. 人格否定・過激な罵倒・モチベーションを完全に喪失させる「正論の暴力」に当たらない。

検証に失敗したメッセージは送信せず（MUST NOT send）、安全なフォールバック文面に差し替えるか送信を見送る。
ガードレールロジックには自動テストを必須とする（MUST）。

**根拠:** 本システムは人間の心理に直接介入する。安全側に倒す設計がなければ、
自律最適化はユーザーを傷つける方向へ容易に逸脱しうる。これは交渉不可の原則である。

### III. プライバシーと最小権限 (Privacy & Least Privilege)

Google Health のバイタルデータは Restricted スコープで扱う機微情報である。
- OAuth スコープ・IAM ロール・Bedrock アクセスはすべて最小権限で付与する（MUST）。
- 認証情報・トークン・PII をソースコードやログに平文で残してはならない（MUST NOT）。シークレットは Secrets Manager 等で管理する。
- 健康データの保持は目的に必要な範囲・期間に限定する。

**根拠:** 機微な健康データの漏洩は回復不能な損害を生む。権限とデータ露出を既定で絞る。

### IV. Infrastructure as Code

すべての AWS リソース（Lambda, EventBridge, IAM, agentcore 関連）は AWS CDK で定義する（MUST）。
本番・開発環境への手動コンソール変更を恒久的な構成として残してはならない（MUST NOT）。
構成変更は `cdk diff` でレビュー可能であること。

**根拠:** 再現性・レビュー可能性・ロールバック可能性を担保し、ドリフトを防ぐ。

### V. 観測可能性とコスト規律 (Observability & Cost Discipline)

- すべての Lambda・エージェント実行は構造化ログ（CloudWatch）を出力する（MUST）。
- LLM 呼び出しは初期開発では最安の Claude Haiku に統一し、モデル選択を設定で切替可能にする（MUST）。
  本番の分析は Claude Opus 4.8、メッセージ生成は Claude Sonnet 4.6 を既定とする。
- 自律ループの各ステップ（収集・分析・目標評価・プロンプト更新・通知）は追跡可能であること。

**根拠:** 自律ループとLLMはコストとデバッグ難度が増大しやすい。観測とコスト抑制を既定にする。

## 追加制約 (技術スタックとコンプライアンス)

- **言語/ランタイム:** Python (Lambda, エージェント)。IaC は AWS CDK (Python)。
- **エージェント基盤:** agentcore（長期記憶 Memory・ガードレール）、strands（ループ制御）。
- **LLM:** Amazon Bedrock 経由のみ。直接の外部LLM API 呼び出しは行わない。
- **通知:** LINE Messaging API。
- **スケジューラ:** Amazon EventBridge（朝の収集・夜間の通知発火）。
- **データソース:** Google Health API（OAuth 2.0 Restricted スコープ、テストユーザー同意を明示管理）。
- コメント・ドキュメントは日本語で記述する。

## 開発ワークフローと品質ゲート

- 各機能は `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement` の順で進める。
  曖昧点があれば `/speckit-clarify` を、実装前整合性確認に `/speckit-analyze` を用いる。
- **品質ゲート（実装をマージする前に必須）:**
  1. ガードレール検証ロジックのテストが緑であること（原則 II）。
  2. シークレット・PII がコード/ログに混入していないこと（原則 III）。
  3. インフラ変更が CDK 経由で `cdk diff` 可能であること（原則 IV）。
- 仕様逸脱・原則違反を含む変更は、正当化を `plan.md` の Complexity Tracking に明記しない限り却下する。

## Governance

本 Constitution は他のすべての開発慣行に優先する（supersedes）。
- 改正には変更内容の文書化・根拠・影響範囲（依存テンプレート同期）の明記を必須とする。
- バージョニングは semantic versioning に従う:
  MAJOR = 原則の削除・後方非互換な再定義 / MINOR = 原則・節の追加または実質的拡張 / PATCH = 字句・明確化。
- すべての設計レビューは本 Constitution への適合を確認すること。
  特に原則 II（安全・ガードレール）への違反は、いかなる理由でもマージを差し止める。

**Version**: 1.0.0 | **Ratified**: 2026-06-14 | **Last Amended**: 2026-06-14
