"""目標設定インターフェース（T028 / `/goal` 相当 / FR-005,017）。

spec-v4.md の `/goal --success-rate 85% --weight-loss 2kg` に対応する CLI。
DynamoDB の GOAL/current に保存する。

使用例:
    python -m night_ramen.goals.cli --success-rate 85% --weight-loss 2kg
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from night_ramen.config import load_settings
from night_ramen.memory.schemas import Goal


def parse_rate(text: str) -> float:
    """'85%' or '0.85' → 0.85。"""
    t = text.strip().rstrip("%")
    v = float(t)
    return v / 100.0 if "%" in text or v > 1 else v


def parse_kg(text: str) -> float:
    """'2kg' or '2' → 2.0。"""
    return float(text.strip().lower().rstrip("kg"))


def build_goal(success_rate: str | None, weight_loss: str | None, settings) -> Goal:
    return Goal(
        success_rate_target=(parse_rate(success_rate) if success_rate
                             else settings.default_success_rate_target),
        weight_loss_target_kg=(parse_kg(weight_loss) if weight_loss
                               else settings.default_weight_loss_target_kg),
        set_at=datetime.now(timezone.utc).isoformat(),
    )


def save_goal(goal: Goal, settings) -> None:
    import boto3

    ddb = boto3.resource("dynamodb", region_name=settings.region)
    table = ddb.Table(settings.dynamodb_table)
    item = {
        "PK": "GOAL",
        "SK": "current",
        "success_rate_target": str(goal.success_rate_target),
        "weight_loss_target_kg": str(goal.weight_loss_target_kg),
        "set_at": goal.set_at,
    }
    if goal.baseline_weight_kg is not None:
        item["baseline_weight_kg"] = str(goal.baseline_weight_kg)
    table.put_item(Item=item)


def main(argv: list[str] | None = None) -> Goal:
    parser = argparse.ArgumentParser(description="目標設定（/goal 相当）")
    parser.add_argument("--success-rate", help="ラーメン我慢成功率の目標（例: 85%%）")
    parser.add_argument("--weight-loss", help="減量目標（例: 2kg）")
    parser.add_argument("--save", action="store_true", help="DynamoDB に保存する")
    args = parser.parse_args(argv)

    settings = load_settings()
    goal = build_goal(args.success_rate, args.weight_loss, settings)
    if args.save:
        save_goal(goal, settings)
    print(f"目標: 成功率 {goal.success_rate_target:.0%} / 減量 {goal.weight_loss_target_kg}kg")
    return goal


if __name__ == "__main__":
    main()
