"""Night Ramen Breakdown インフラ（T033,T034 / 原則III,IV / FR-014,016）。

構成:
- DynamoDB 単一テーブル（GOAL/NOTIFY、TTL=FR-016/冪等=FR-014）
- Lambda×2（朝の収集 / 夜の通知ループ）
- EventBridge Scheduler×2（08:00 / 23:00 JST）
- Secrets（Google/LINE）参照と関数ごと最小 IAM（原則III）

注: Lambda の依存（strands/bedrock-agentcore/line-bot-sdk/google-* 等）は
    Lambda Layer もしくは bundling で同梱する。本スタックはコードを `../src` から取り込む。
"""

from __future__ import annotations

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

# 既定モデル（dev=Haiku）。本番は環境変数 NRB_ENV=prod で切替。
_DEFAULT_ENV = {
    "NRB_ENV": "dev",
    "NRB_DDB_TABLE": "NightRamenTable",
    "NRB_SECRET_GOOGLE": "night-ramen/google-oauth",
    "NRB_SECRET_LINE": "night-ramen/line",
    "NRB_NIGHT_HOUR_JST": "23",
    # agentcore Memory リソースID（002 / scripts/create_memory.py の出力）
    "NRB_MEMORY_ID": "NightRamenMemory-t8edYf6l1T",
}


class NightRamenStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- DynamoDB（GOAL/NOTIFY、TTL で NOTIFY を自動失効）---
        table = dynamodb.Table(
            self,
            "NightRamenTable",
            table_name="NightRamenTable",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,  # 単一ユーザーで低負荷
            time_to_live_attribute="ttl",                       # FR-014 冪等の自動失効
            removal_policy=RemovalPolicy.RETAIN,                # 機微データ保護
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )

        # --- Secrets（既存を参照）---
        google_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "GoogleSecret", "night-ramen/google-oauth"
        )
        line_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "LineSecret", "night-ramen/line"
        )

        # Lambda の共通コード資産（src/ をパッケージ）
        code = lambda_.Code.from_asset("../src")
        runtime = lambda_.Runtime.PYTHON_3_13

        # --- 朝の収集 Lambda（最小権限: Health/Memory/Secrets read, Table write）---
        morning_fn = lambda_.Function(
            self,
            "MorningFn",
            function_name="NightRamen-Morning",
            runtime=runtime,
            handler="handlers.morning_handler.handler",
            code=code,
            timeout=Duration.minutes(5),
            memory_size=512,
            environment=dict(_DEFAULT_ENV),
        )
        table.grant_read_write_data(morning_fn)
        google_secret.grant_read(morning_fn)

        # --- 夜の通知ループ Lambda（最小権限: Bedrock/Memory/LINE/Table）---
        night_fn = lambda_.Function(
            self,
            "NightFn",
            function_name="NightRamen-Night",
            runtime=runtime,
            handler="handlers.night_handler.handler",
            code=code,
            timeout=Duration.minutes(5),
            memory_size=512,
            environment=dict(_DEFAULT_ENV),
        )
        table.grant_read_write_data(night_fn)
        line_secret.grant_read(night_fn)
        # Bedrock の最小権限（モデル呼び出しのみ）
        night_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:Converse"],
                resources=["*"],  # モデルARNを絞る場合はここを限定
            )
        )

        # --- boto3 Layer（Lambda 同梱 boto3 は agentcore Memory 非対応のため上書き / 002）---
        boto3_layer = lambda_.LayerVersion(
            self,
            "Boto3Layer",
            code=lambda_.Code.from_asset("layers/boto3"),
            compatible_runtimes=[runtime],
            description="boto3>=1.43 (bedrock-agentcore Memory 対応)",
        )

        # --- Webhook Lambda（LINE 自己申告受信 ＋ 対話エージェント / US2 + 002）---
        webhook_fn = lambda_.Function(
            self,
            "WebhookFn",
            function_name="NightRamen-Webhook",
            runtime=runtime,
            handler="handlers.webhook_handler.handler",
            code=code,
            timeout=Duration.seconds(60),   # 会話: Memory取得＋生成の余裕（FR-111）
            memory_size=512,
            environment=dict(_DEFAULT_ENV),
            layers=[boto3_layer],
        )
        table.grant_read_write_data(webhook_fn)  # REPORT 読み書き / バイタル・目標 読込
        line_secret.grant_read(webhook_fn)        # 署名検証・返信トークン
        # Bedrock（会話生成・自己申告分類）
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:Converse"], resources=["*"]
            )
        )
        # agentcore Memory（会話の STM/LTM 読み書き / 002）
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                ],
                resources=["*"],  # 単一 Memory 利用。ARN で絞る場合はここを限定
            )
        )
        # 公開 HTTPS エンドポイント（LINE Webhook URL に登録する）
        webhook_url = webhook_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE
        )

        # --- EventBridge Scheduler（JST 指定）---
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        morning_fn.grant_invoke(scheduler_role)
        night_fn.grant_invoke(scheduler_role)

        scheduler.CfnSchedule(
            self,
            "MorningSchedule",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="cron(0 8 * * ? *)",
            schedule_expression_timezone="Asia/Tokyo",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=morning_fn.function_arn, role_arn=scheduler_role.role_arn
            ),
        )
        scheduler.CfnSchedule(
            self,
            "NightSchedule",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="cron(0 23 * * ? *)",  # 23:00 JST（1晩1回, FR-014）
            schedule_expression_timezone="Asia/Tokyo",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=night_fn.function_arn, role_arn=scheduler_role.role_arn
            ),
        )

        # LINE Developers Console の Webhook URL に登録する公開エンドポイント
        CfnOutput(self, "WebhookUrl", value=webhook_url.url, description="LINE Webhook URL")
