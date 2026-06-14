# Night Ramen Breakdown — 構成フロー図

深夜のラーメン断ち自律改善ループシステムの全体構成。
朝（①②）で収集、夜（③④⑤⑥）で説得通知、自己申告（⑦⑧）でループが閉じる。

```mermaid
flowchart TB
    user["👤 ユーザー（LINEアプリ）"]
    ghealth["🩺 Google Health API（Fitbit）<br/>睡眠 / 心拍 / HRV / 歩数 / カロリー"]

    subgraph AWS["AWS us-east-1 — NightRamenStack（CDK / IaC）"]
        ebM["⏰ EventBridge<br/>朝 08:00 JST"]
        ebN["⏰ EventBridge<br/>夜 23:00 JST"]
        lM["λ Morning Lambda<br/>バイタル収集 ＋ 自己申告紐づけ"]
        lN["λ Night Lambda<br/>分析→目標評価→生成→検証→通知→学習"]
        lW["λ Webhook Lambda<br/>署名検証 → bool解釈 → 保存"]
        url["🌐 Lambda Function URL（公開HTTPS）"]
        gr["🛡 ガードレール<br/>差別/逸脱/正論の暴力を遮断<br/>fail-closed・最大2回再生成"]
        ddb[("🗄 DynamoDB NightRamenTable<br/>バイタル / 目標 / 申告 / 冪等（1年TTL）")]
        bed["🤖 Amazon Bedrock<br/>分析=Opus 4.8 / 生成=Sonnet 4.6<br/>（初期=Haiku・推論プロファイル経由）"]
        sec["🔐 Secrets Manager<br/>Google OAuth / LINE（署名検証鍵込み）"]
    end

    %% 朝フロー（収集）
    ebM -->|起動| lM
    lM -->|① 前夜バイタル取得| ghealth
    lM -->|② 収集保存 ＋ 申告読込| ddb
    lM -.->|トークン参照| sec

    %% 夜フロー（説得通知）
    ebN -->|起動| lN
    lN -->|③ バイタル/目標を読込| ddb
    lN -->|④ 分析・説得生成| bed
    lN -->|⑤ 送信前検証| gr
    gr -->|⑥ 説得通知（push・23時1回）| user
    lN -.->|モデル/トークン| sec

    %% 自己申告フロー（ループを閉じる）
    user -->|⑦ 返信「食べた / 我慢した」| url
    url --> lW
    lW -->|⑧ 申告を保存（REPORT）| ddb
    lW -.->|署名検証鍵| sec

    classDef ext fill:#E8F0FE,stroke:#4285F4,color:#111;
    classDef lambda fill:#FFF4E5,stroke:#FF9900,color:#111;
    classDef data fill:#E8EAF6,stroke:#3F51B5,color:#111;
    classDef sched fill:#FCE8E6,stroke:#E8453C,color:#111;
    classDef ai fill:#E6F4EA,stroke:#34A853,color:#111;
    classDef guard fill:#FDECEA,stroke:#C0392B,color:#111;
    classDef secret fill:#FCE8E6,stroke:#D93025,color:#111;
    classDef line fill:#06C755,stroke:#04994A,color:#fff;

    class ghealth ext;
    class user line;
    class lM,lN,lW,url lambda;
    class ddb data;
    class ebM,ebN sched;
    class bed ai;
    class gr guard;
    class sec secret;
```

## 凡例

- **実線** … データ/処理のフロー（①〜⑧の順）
- **破線** … Secrets Manager からの認証情報・鍵の参照
- **朝 08:00 JST** … Google Health から前夜バイタルを収集し、自己申告と紐づけて DynamoDB へ
- **夜 23:00 JST** … 蓄積データを Bedrock で分析・説得生成 → ガードレール検証 → LINE へ1回通知
- **自己申告** … ユーザーの LINE 返信を Webhook（署名検証付き）で受け、DynamoDB に記録 → 翌朝の収集が紐づけ

## シーケンス（夜の説得 1サイクル）

```mermaid
sequenceDiagram
    autonumber
    participant EB as EventBridge(23:00)
    participant N as Night Lambda
    participant D as DynamoDB
    participant B as Bedrock
    participant G as ガードレール
    participant L as LINE
    participant U as ユーザー

    EB->>N: 起動
    N->>D: バイタル/目標/現行戦略を読込
    N->>B: 分析（ラーメンの悪影響を数値化）
    N->>B: 説得メッセージ生成（達成率→トーン）
    N->>G: 送信前検証（差別/逸脱/正論の暴力）
    alt 検証パス
        G-->>N: OK
        N->>L: push（未通知時のみ・1晩1回）
        L->>U: 説得通知 ＋「昨夜ラーメン食べた？」
    else 2回再生成しても不合格
        G-->>N: 固定の安全文面へフォールバック
        N->>L: push（安全文面）
    end
    N->>B: 説得戦略を自律更新（検証通過版のみ保存）
    N->>D: メッセージ履歴/戦略を保存
```
