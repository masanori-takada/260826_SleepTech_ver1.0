"""SleepTech の AWS 構成。

Lambda の実行時依存は boto3 と標準ライブラリだけなので、依存パッケージの
Layer は作らず、リポジトリルートをアセットとして配布する。
"""

from __future__ import annotations

import json
import os

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_scheduler as scheduler
from constructs import Construct


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SECRET_NAMES = {
    "google": "sleeptech/google-oauth",
    "line": "sleeptech/line",
    "openai": "sleeptech/openai",
}
ASSET_EXCLUDES = [
    ".git",
    ".venv",
    ".pytest_cache",
    "cdk.out",
    "tests",
    "docs",
    ".agents",
    ".claude",
    ".specify",
    ".superpowers",
    "AGENTS.md",
    "CLAUDE.md",
    "infra",
]


class SleepTechStack(Stack):
    """SleepTech のLambda、Scheduler、DynamoDBをまとめたスタック。"""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = ddb.Table(
            self,
            "SleepTechTable",
            table_name="SleepTechTable",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.RETAIN,
        )

        secret_refs = {
            key: secretsmanager.Secret.from_secret_name_v2(
                self, f"{key.title()}Secret", name
            )
            for key, name in SECRET_NAMES.items()
        }
        environment = {
            "STC_ENV": "prod",
            "STC_DDB_TABLE": table.table_name,
            "STC_SECRET_GOOGLE": SECRET_NAMES["google"],
            "STC_SECRET_LINE": SECRET_NAMES["line"],
            "STC_SECRET_OPENAI": SECRET_NAMES["openai"],
            # 暫定値。API実機値はTask 17で確認する。
            "LLM_MODEL": "gpt-5.6-luna",
            "PYTHONPATH": "/var/task/src",
        }

        night_fn = self._function(
            "NightFn", "src.handlers.night_handler.handler", environment
        )
        morning_fn = self._function(
            "MorningFn", "src.handlers.morning_handler.handler", environment
        )
        webhook_fn = self._function(
            "WebhookFn", "src.handlers.webhook_handler.handler", environment
        )

        secret_access = {
            night_fn: ("line", "openai"),
            morning_fn: ("google", "line", "openai"),
            webhook_fn: ("google", "line", "openai"),
        }
        for function, secret_keys in secret_access.items():
            table.grant_read_write_data(function)
            for secret_key in secret_keys:
                secret_refs[secret_key].grant_read(function)

        webhook_url = webhook_fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        # Minor対応: LINE Developers コンソールに貼るWebhook URLをコンソールで
        # 探し回らずに済むよう、デプロイ出力に出す。
        CfnOutput(self, "WebhookFunctionUrl", value=webhook_url.url)

        # 最終レビュー対応（優先2）: 両Scheduleに scheduled_time を渡し、朝ハンドラも
        # 夜と同じ安全な起点日導出を使えるようにする（morning_handler.py参照）。
        scheduled_time_input = json.dumps(
            {"scheduled_time": "<aws.scheduler.scheduled-time>"},
            separators=(",", ":"),
        )
        self._schedule(
            "NightSchedule",
            "cron(30 22 * * ? *)",
            night_fn,
            input_value=scheduled_time_input,
        )
        self._schedule(
            "MorningSchedule",
            "cron(0 8 * * ? *)",
            morning_fn,
            input_value=scheduled_time_input,
        )

    def _function(
        self, name: str, handler: str, environment: dict[str, str]
    ) -> lambda_.Function:
        return lambda_.Function(
            self,
            name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler=handler,
            code=lambda_.Code.from_asset(
                REPO_ROOT,
                exclude=ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(60),
            memory_size=512,
            environment=environment,
        )

    def _schedule(
        self,
        name: str,
        expression: str,
        function: lambda_.Function,
        input_value: str | None = None,
    ) -> None:
        role = iam.Role(
            self,
            f"{name}Role",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        function.grant_invoke(role)
        target = scheduler.CfnSchedule.TargetProperty(
            arn=function.function_arn,
            role_arn=role.role_arn,
            input=input_value,
            # 最終レビュー対応（優先2）: RetryPolicy未指定だと既定で最大24時間・185回
            # リトライされ、日跨ぎリトライ（起点日のずれ・IDEM先食い）を招きうる。
            # 明示的に短く絞る。
            retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                maximum_event_age_in_seconds=3600,
                maximum_retry_attempts=5,
            ),
        )
        scheduler.CfnSchedule(
            self,
            name,
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"
            ),
            schedule_expression=expression,
            schedule_expression_timezone="Asia/Tokyo",
            target=target,
        )
