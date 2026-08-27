"""CDK スタックの構成検査。cdk synth 相当をテストで行う。"""

import json
import importlib
import shutil
import subprocess
import sys
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


def test_creates_four_lambdas(template):
    template.resource_count_is("AWS::Lambda::Function", 4)


def test_each_lambda_has_the_expected_handler(template):
    resources = template.find_resources("AWS::Lambda::Function")
    handlers = {resource["Properties"]["Handler"] for resource in resources.values()}
    assert handlers == {
        "src.handlers.night_handler.handler",
        "src.handlers.morning_handler.handler",
        "src.handlers.webhook_handler.handler",
        "src.handlers.backfill_handler.handler",
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


def test_morning_schedule_also_has_scheduled_time_input(template):
    """最終レビュー対応（優先2）: 朝ハンドラも scheduled_time から安全に起点日を
    導けるよう、MorningScheduleにも夜と同じ input を渡す。"""
    resources = template.find_resources("AWS::Scheduler::Schedule")
    morning = next(
        resource
        for resource in resources.values()
        if resource["Properties"]["ScheduleExpression"] == "cron(0 8 * * ? *)"
    )
    assert morning["Properties"]["Target"]["Input"] == (
        '{"scheduled_time":"<aws.scheduler.scheduled-time>"}'
    )


def test_both_schedules_have_a_retry_policy(template):
    """最終レビュー対応（優先2）: RetryPolicy未指定だと既定で最大24時間・185回
    リトライされ、日跨ぎリトライ（起点日のずれ・IDEM先食い）を招きうる。"""
    resources = template.find_resources("AWS::Scheduler::Schedule")
    assert len(resources) == 2
    for resource in resources.values():
        retry_policy = resource["Properties"]["Target"]["RetryPolicy"]
        assert retry_policy["MaximumEventAgeInSeconds"] == 3600
        assert 1 <= retry_policy["MaximumRetryAttempts"] <= 5


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
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "src.handlers.backfill_handler.handler"},
    )


def test_backfill_has_900_second_timeout(template):
    functions = template.find_resources("AWS::Lambda::Function")
    backfill = next(
        resource for resource in functions.values()
        if resource["Properties"]["Handler"] == "src.handlers.backfill_handler.handler"
    )
    assert backfill["Properties"]["Timeout"] == 900


def test_backfill_is_not_a_schedule_or_function_url_target(template):
    functions = template.find_resources("AWS::Lambda::Function")
    backfill = next(
        resource for resource in functions.values()
        if resource["Properties"]["Handler"] == "src.handlers.backfill_handler.handler"
    )
    backfill_ref = backfill["Properties"]["Role"]
    schedules = json.dumps(template.find_resources("AWS::Scheduler::Schedule"))
    urls = json.dumps(template.find_resources("AWS::Lambda::Url"))
    assert "BackfillFn" not in schedules
    assert "BackfillFn" not in urls
    assert backfill_ref


def test_backfill_role_has_dynamodb_read_write_but_no_external_secret_access(template):
    policy_text = _secret_policy_text(template, "src.handlers.backfill_handler.handler")
    assert "dynamodb:GetItem" in policy_text
    assert "dynamodb:PutItem" in policy_text
    assert "sleeptech/google-oauth" in policy_text
    assert "sleeptech/line" not in policy_text
    assert "sleeptech/openai" not in policy_text


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


def test_all_runtime_functions_receive_min_target_sleep_hours(template):
    """バックフィル以外の実行時Lambdaへ最低目標睡眠時間を渡す。"""
    functions = template.find_resources("AWS::Lambda::Function")
    runtime_functions = [
        resource
        for resource in functions.values()
        if resource["Properties"]["Handler"]
        != "src.handlers.backfill_handler.handler"
    ]

    assert runtime_functions
    assert all(
        resource["Properties"]["Environment"]["Variables"][
            "STC_MIN_TARGET_SLEEP_H"
        ]
        == "7.0"
        for resource in runtime_functions
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
        (
            "src.handlers.backfill_handler.handler",
            ("sleeptech/google-oauth",),
            ("sleeptech/line", "sleeptech/openai"),
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
        "src.handlers.backfill_handler",
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
        "backfill_handler.py",
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
        "LINE_Developersの３つの値.txt",
        "OpenAI_API_Key.txt",
    ):
        assert not (asset_root / development_item).exists()


def test_asset_excludes_all_compiled_python_artifacts():
    assert "**/__pycache__/**" in ASSET_EXCLUDES
    assert "**/*.pyc" in ASSET_EXCLUDES
    assert "**/*.pyo" in ASSET_EXCLUDES
    assert "**/*.pyd" in ASSET_EXCLUDES


def test_synth_asset_contains_no_compiled_python_artifacts():
    staging = AssetStaging(
        App(),
        "CompiledArtifactExclusionAsset",
        source_path=REPO_ROOT,
        exclude=ASSET_EXCLUDES,
    )
    asset_root = Path(staging.absolute_staged_path)

    assert not list(asset_root.rglob("__pycache__"))
    assert not list(asset_root.rglob("*.pyc"))
    assert not list(asset_root.rglob("*.pyo"))
    assert not list(asset_root.rglob("*.pyd"))


def test_webhook_role_explicitly_allows_dynamodb_transactions(template):
    policy_text = _secret_policy_text(template, "src.handlers.webhook_handler.handler")

    assert "dynamodb:TransactWriteItems" in policy_text


def test_cdk_output_is_outside_infra_tree():
    config = json.loads((Path(REPO_ROOT) / "infra" / "cdk.json").read_text(encoding="utf-8"))

    assert config["output"] == "../cdk.out"


def test_synth_then_root_compileall_does_not_scan_cdk_output():
    repo_root = Path(REPO_ROOT)
    infra_root = repo_root / "infra"
    infra_output = infra_root / "cdk.out"
    repo_output = repo_root / "cdk.out"

    try:
        cdk_command = shutil.which("cdk") or shutil.which("cdk.cmd")
        assert cdk_command, "cdkコマンドがPATHにありません"
        synth = subprocess.run(
            [cdk_command, "synth", "--strict"],
            cwd=infra_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert synth.returncode == 0, synth.stderr
        assert not infra_output.exists()
        assert repo_output.is_dir()

        compileall = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", "src", "infra"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert compileall.returncode == 0, compileall.stderr
        assert not list(repo_output.rglob("__pycache__"))
        assert not list(repo_output.rglob("*.pyc"))
        assert not list(repo_output.rglob("*.pyo"))
        assert not list(repo_output.rglob("*.pyd"))
    finally:
        if repo_output.exists():
            shutil.rmtree(repo_output)
        if infra_output.exists():
            shutil.rmtree(infra_output)
