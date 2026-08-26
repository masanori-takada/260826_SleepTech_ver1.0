"""CDK スタックの構成検査。cdk synth 相当をテストで行う。"""

import json

import pytest

aws_cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402

from infra.stacks.sleeptech_stack import SleepTechStack  # noqa: E402


@pytest.fixture(scope="module")
def template() -> Template:
    return Template.from_stack(SleepTechStack(App(), "TestStack"))


def test_creates_three_lambdas(template):
    template.resource_count_is("AWS::Lambda::Function", 3)


def test_creates_two_schedules(template):
    template.resource_count_is("AWS::Scheduler::Schedule", 2)


def test_table_uses_lowercase_pk_sk(template):
    """転用元はPK/SK（大文字）だった。本プロジェクトはpk/sk。"""
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ]
        },
    )


def test_table_has_ttl_enabled(template):
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True},
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        },
    )


def test_table_uses_on_demand_billing(template):
    template.has_resource_properties(
        "AWS::DynamoDB::Table", {"BillingMode": "PAY_PER_REQUEST"}
    )


def test_schedules_use_jst_and_flexible_window_off(template):
    template.has_resource_properties(
        "AWS::Scheduler::Schedule",
        {
            "ScheduleExpressionTimezone": "Asia/Tokyo",
            "FlexibleTimeWindow": {"Mode": "OFF"},
        },
    )


def test_night_schedule_has_scheduled_time_input(template):
    template.has_resource_properties(
        "AWS::Scheduler::Schedule",
        {
            "ScheduleExpression": "cron(30 22 * * ? *)",
            "Target": {
                "Input": '{"scheduled_time":"<aws.scheduler.scheduled-time>"}'
            },
        },
    )


def test_morning_schedule_has_no_input(template):
    resources = template.find_resources("AWS::Scheduler::Schedule")
    morning = next(
        resource
        for resource in resources.values()
        if resource["Properties"]["ScheduleExpression"] == "cron(0 8 * * ? *)"
    )
    assert "Input" not in morning["Properties"]["Target"]


def test_lambdas_are_python312_with_expected_handlers(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "src.handlers.night_handler.handler"},
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "src.handlers.morning_handler.handler"},
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "src.handlers.webhook_handler.handler"},
    )


def test_lambdas_receive_model_and_pythonpath(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {"LLM_MODEL": "gpt-5.6-luna", "PYTHONPATH": "/var/task/src"}
                )
            }
        },
    )


def test_webhook_has_a_public_function_url(template):
    template.resource_count_is("AWS::Lambda::Url", 1)
    template.has_resource_properties(
        "AWS::Lambda::Url", {"AuthType": "NONE"}
    )


def test_three_secret_references_are_present(template):
    template.resource_count_is("AWS::SecretsManager::Secret", 0)
    policies = template.find_resources("AWS::IAM::Policy")
    policy_text = json.dumps(policies)
    for secret_name in (
        "sleeptech/google-oauth",
        "sleeptech/line",
        "sleeptech/openai",
    ):
        assert secret_name in policy_text
