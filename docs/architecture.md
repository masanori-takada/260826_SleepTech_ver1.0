# SleepTech ver1.0 — 構成フロー図

睡眠データから翌日のパフォーマンス低下を予測し、LINE で1日2回アドバイスするシステムの全体構成。

> 最終更新: 2026-08-26

## フロー概要

- **夜フロー（22:30 JST）** … 直近の睡眠負債・目標達成率から推奨就寝時刻を算出し、LLMで文章化してLINE push
- **朝フロー（8:00 JST、または「おはよう」等の発話で前倒し）** … 前夜の睡眠データをGoogle Health APIから収集し、時間帯別パフォーマンス低下カーブとリカバリー行動をLLMで文章化してLINE push
- **Webhook（常時）** … LINE署名検証 → 送信者認可（本人のみ）→ 起床トリガー検出時は朝フローを前倒し実行、クイックリプライ評価は直近の未回答予測にフィードバックとして保存

```mermaid
flowchart TB
    user["👤 ユーザー（LINEアプリ）"]
    ghealth["🩺 Google Health API v4<br/>睡眠時間 / 就寝・起床時刻"]
    openai["🤖 OpenAI Chat Completions API<br/>文章生成"]

    subgraph AWS["AWS — SleepTechStack（CDK）"]
        ebN["⏰ EventBridge Scheduler<br/>夜 22:30 JST"]
        ebM["⏰ EventBridge Scheduler<br/>朝 8:00 JST"]
        lN["λ NightFn<br/>睡眠負債算出→推奨就寝時刻→文章生成→push"]
        lM["λ MorningFn<br/>バイタル収集→低下カーブ算出→文章生成→push"]
        lW["λ WebhookFn<br/>署名検証→送信者認可→起床前倒し／評価保存"]
        url["🌐 Lambda Function URL（LINE Webhook HTTPS）"]
        ddb[("🗄 DynamoDB SleepTechTable<br/>バイタル / 予測 / メッセージ / フィードバック / 冪等フラグ")]
        sec["🔐 Secrets Manager<br/>sleeptech/google-oauth<br/>sleeptech/line<br/>sleeptech/openai"]
    end

    %% 夜フロー
    ebN -->|scheduled_time| lN
    lN -->|① 睡眠負債・目標達成率を読込| ddb
    lN -->|② 推奨就寝時刻の文章化| openai
    lN -->|③ push（Retry-Key付・1晩1回）| user
    lN -.->|LINEトークン/OpenAIキー参照| sec
    lN -->|④ 予測/メッセージ/冪等を保存| ddb

    %% 朝フロー
    ebM -->|scheduled_time| lM
    lM -->|⑤ 前夜の睡眠データ取得| ghealth
    lM -->|⑥ 低下カーブ・リカバリー行動の文章化| openai
    lM -->|⑦ push（Retry-Key付・1日1回）| user
    lM -.->|Googleトークン/LINEトークン/OpenAIキー参照| sec
    lM -->|⑧ バイタル/予測/メッセージ/冪等を保存| ddb

    %% Webhookフロー
    user -->|⑨ 発話（起床トリガー／評価回答）| url
    url --> lW
    lW -.->|署名検証鍵・送信者ID参照| sec
    lW -->|⑩ 起床トリガー時: 朝フローを前倒し実行| lM
    lW -->|⑪ 評価回答時: 直近未回答予測にフィードバック保存| ddb

    classDef ext fill:#E8F0FE,stroke:#4285F4,color:#111;
    classDef lambda fill:#FFF4E5,stroke:#FF9900,color:#111;
    classDef data fill:#E8EAF6,stroke:#3F51B5,color:#111;
    classDef sched fill:#FCE8E6,stroke:#E8453C,color:#111;
    classDef ai fill:#E6F4EA,stroke:#34A853,color:#111;
    classDef secret fill:#FCE8E6,stroke:#D93025,color:#111;
    classDef line fill:#06C755,stroke:#04994A,color:#fff;

    class ghealth ext;
    class user line;
    class lM,lN,lW,url lambda;
    class ddb data;
    class ebM,ebN sched;
    class openai ai;
    class sec secret;
```

## 凡例

- **実線** … データ/処理のフロー（①〜⑪）
- **破線** … Secrets Manager 参照
- **冪等制御** … `retry_key`（UUID v4形状。LINE `X-Line-Retry-Key`）＋ DynamoDB `IDEM#NIGHT` / `IDEM#MORNING`（`claim`→`complete`の2段階ロック）で二重送信を防ぐ
- **起点日の扱い** … 夜フローは当日、朝フローは前日を起点日とする（前夜の夜メッセージと同じ日付に揃えるため）。`scheduled_time`（EventBridge Schedulerが渡すISO時刻）を優先し、壁時計時刻はフォールバック

## シーケンス（夜フロー 1サイクル）

```mermaid
sequenceDiagram
    autonumber
    participant EB as EventBridge(22:30)
    participant N as NightFn
    participant D as DynamoDB
    participant O as OpenAI
    participant L as LINE
    participant U as ユーザー

    EB->>N: 起動（scheduled_time）
    N->>D: 睡眠負債・目標達成率・過去バイタルを読込
    N->>O: 推奨就寝時刻の文章化
    alt ガードレール検証パス
        N->>D: 冪等フラグをclaim（未通知時のみ）
        N->>L: push（Retry-Key=UUIDv4、クイックリプライ付き）
        L->>U: 推奨就寝時刻の通知
        N->>D: 冪等フラグをcomplete、予測/メッセージを保存
    else 検証不合格（LLM失敗・再生成上限到達）
        N->>L: push（固定の安全文面）
    end
```

## シーケンス（朝フロー／Webhook起床前倒しを含む）

```mermaid
sequenceDiagram
    autonumber
    participant U as ユーザー
    participant L as LINE
    participant W as WebhookFn
    participant M as MorningFn
    participant G as Google Health API
    participant D as DynamoDB
    participant O as OpenAI

    alt 定刻起動（8:00）
        Note over M: EventBridge Schedulerが起動
    else 起床トリガー発話で前倒し
        U->>L: 「おはよう」等
        L->>W: Webhook（署名付き）
        W->>W: 署名検証→送信者認可（本人のみ）
        W->>M: 朝フローを前倒し実行
    end
    M->>G: 前夜の睡眠時間・就寝/起床時刻を取得
    M->>D: 冪等フラグをclaim（未通知時のみ）
    M->>O: 低下カーブ・リカバリー行動の文章化
    M->>L: push（Retry-Key=UUIDv4、クイックリプライ付き）
    L->>U: 朝の通知
    M->>D: 冪等フラグをcomplete、バイタル/予測/メッセージを保存

    U->>L: クイックリプライで評価回答
    L->>W: Webhook（署名付き）
    W->>W: 署名検証→送信者認可（本人のみ）
    W->>D: 直近の未回答予測（当日/前日の2日分）にフィードバック保存
```
