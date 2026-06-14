# Quickstart — LINE 対話エージェント（002）

**Date**: 2026-06-14 | **Branch**: `002-line-agentcore-memory`

## 前提

- 既存 NightRamenStack がデプロイ済み（feature 001）。
- boto3 >= 1.43（ローカル更新済み）。

## 1. agentcore Memory リソース作成（1回限り）

```bash
python scripts/create_memory.py --name NightRamenMemory   # 3戦略(semantic/userPreference/summary)付きで作成
# → 出力された MEMORY_ID を控える（数分でACTIVE）
```

## 2. boto3 Layer 素材を用意（純Python・Docker不要）

```bash
python -m pip install boto3>=1.43 -t infra/layers/boto3/python
```

## 3. 設定

- CDK の Webhook Lambda 環境変数に `NRB_MEMORY_ID=<上記ID>` を設定。
- boto3 Layer を Webhook Lambda にアタッチ。
- Webhook Lambda の IAM に `bedrock-agentcore:CreateEvent/ListEvents/RetrieveMemoryRecords` 等と `bedrock:InvokeModel/Converse` を付与。

## 4. テスト

```bash
python -m pytest tests/unit/test_conversation_extractor.py tests/unit/test_memory_store.py -v
python -m pytest tests/integration/test_webhook_conversation.py -v
```

## 5. デプロイ

```bash
cd infra && cdk diff && cdk deploy --require-approval never
```

## 6. 実機確認

1. LINE で Bot に「最近よく眠れてない気がする」などと話しかける。
2. データ傾向に触れた健康コーチ応答が返る（数秒以内）。
3. 「昨夜ラーメン我慢した」と送る → 申告が REPORT に false 記録される。
4. 後日また話しかけ、以前の文脈に触れた応答が返る（LTM 有効）。

## 検証チェックリスト（Done）

- [ ] 会話応答が文脈に沿う（SC-101,102）
- [ ] ガードレール不合格応答が送信されない（SC-103）
- [ ] 会話からの申告が正しく記録（SC-104）
- [ ] 後日セッションで過去の学習に言及（SC-105）
- [ ] トークン/PII の平文露出なし（SC-106）
- [ ] 通常応答が10秒以内（SC-107）

## トラブルシュート

- `UnknownServiceError: bedrock-agentcore` → Lambda の boto3 が古い。Layer の boto3>=1.43 が効いているか確認。
- 応答が遅い/タイムアウト → Webhook Lambda の timeout/memory を引き上げ。STM の k を縮小。
- LTM が空 → 抽出は非同期。会話を重ねると蓄積される（即時反映前提にしない）。
