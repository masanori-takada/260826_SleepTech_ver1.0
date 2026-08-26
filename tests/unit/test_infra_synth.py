"""CDK スタックの構成検査。cdk synth 相当をテストで行う。"""

import json
import importlib
from pathlib import Path

import pytest

aws_cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App, AssetStaging  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402

from infra.stacks.sleeptech_stack import (  # noqa: E402
    ASSET_EXCLUDES,
    REPO_ROOT,
    SleepTechStack,
)


@pytest.fixture(scope="module")
def template() -> Template:
    return Template.from_stack(SleepTechStack(App(), "TestStack"))


def test_creates_three_lambdas(template):
    template.resource_count_is("AWS::Lambda::Function", 3)


def test_each_lambda_has_the_expected_handler(template):
    resources = template.find_resources("AWS::Lambda::Function")
    handlers = {resource["Properties"]["Handler"] for resource in resources.values()}
    assert handlers == {
        "src.handlers.night_handler.handler",
        "src.handlers.morning_handler.handler",
        "src.handlers.webhook_handler.handler",
    }


def test_creates_two_schedules(template):
    template.resource_count_is("AWS::Scheduler::Schedule", 2)


def test_each_schedule_has_the_expected_expression(template):
    resources = template.find_resources("AWS::Scheduler::Schedule")
    expressions = {
        resource["Properties"]["ScheduleExpression"] for resource in resources.values()
    }
    assert expressions == {"cron(30 22 * * ? *)", "cron(0 8 * * ? *)"}


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


def test_table_is_retained_on_delete_or_replacement(template):
    tables = template.find_resources("AWS::DynamoDB::Table")
    assert len(tables) == 1
    table = next(iter(tables.values()))
    assert table["DeletionPolicy"] == "Retain"
    assert table["UpdateReplacePolicy"] == "Retain"


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
    policy_text = json.dumps(template.find_resources("AWS::IAM::Policy"))
    for secret_name in (
        "sleeptech/google-oauth",
        "sleeptech/line",
        "sleeptech/openai",
    ):
        assert secret_name in policy_text


def _secret_policy_text(template, handler: str) -> str:
    """指定Lambdaのロールに付与されたSecrets Managerポリシーを返す。"""
    functions = template.find_resources("AWS::Lambda::Function")
    function = next(
        resource
        for resource in functions.values()
        if resource["Properties"]["Handler"] == handler
    )
    role_id = function["Properties"]["Role"]["Fn::GetAtt"][0]
    role_ref = {"Ref": role_id}
    policies = template.find_resources("AWS::IAM::Policy")
    role_policies = [
        policy
        for policy in policies.values()
        if role_ref in policy["Properties"].get("Roles", [])
    ]
    return json.dumps(role_policies)


@pytest.mark.parametrize(
    ("handler", "allowed", "forbidden"),
    [
        (
            "src.handlers.night_handler.handler",
            ("sleeptech/line", "sleeptech/openai"),
            ("sleeptech/google-oauth",),
        ),
        (
            "src.handlers.morning_handler.handler",
            ("sleeptech/google-oauth", "sleeptech/line", "sleeptech/openai"),
            (),
        ),
        (
            "src.handlers.webhook_handler.handler",
            ("sleeptech/google-oauth", "sleeptech/line", "sleeptech/openai"),
            (),
        ),
    ],
)
def test_each_lambda_has_only_its_required_secret_permissions(
    template, handler, allowed, forbidden
):
    policy_text = _secret_policy_text(template, handler)
    for secret_name in allowed:
        assert secret_name in policy_text
    for secret_name in forbidden:
        assert secret_name not in policy_text


def test_asset_root_contains_src_and_all_handlers_are_importable():
    """synthアセット直下のsrc/から3つのLambda handlerを解決できる。"""
    repo_root = Path(REPO_ROOT)
    assert repo_root == Path(__file__).resolve().parents[2]
    assert (repo_root / "src").is_dir()
    for module_name in (
        "src.handlers.night_handler",
        "src.handlers.morning_handler",
        "src.handlers.webhook_handler",
    ):
        module = importlib.import_module(module_name)
        assert callable(getattr(module, "handler"))


def test_synth_asset_root_contains_src_and_all_handler_files():
    """CDKが実際にstageしたアセット直下にsrc/と3 handlerが存在する。"""
    staging = AssetStaging(
        App(),
        "HandlerAsset",
        source_path=REPO_ROOT,
        exclude=ASSET_EXCLUDES,
    )
    asset_root = Path(staging.absolute_staged_path)
    assert (asset_root / "src").is_dir()
    for handler_name in (
        "night_handler.py",
        "morning_handler.py",
        "webhook_handler.py",
    ):
        assert (asset_root / "src" / "handlers" / handler_name).is_file()
    for development_item in (
        ".agents",
        ".claude",
        ".specify",
        ".superpowers",
        "AGENTS.md",
        "CLAUDE.md",
        "infra",
    ):
        assert not (asset_root / development_item).exists()
