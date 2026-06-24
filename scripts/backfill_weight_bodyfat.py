"""過去バイタルデータのバックフィルスクリプト。

DynamoDB の既存バイタルレコードのうち VITAL_FIELDS に欠損がある日付について
Google Health API から全フィールドを再取得し、None のフィールドだけ補完して保存する。
既存の値（None でない）は上書きしない。

使い方:
    python scripts/backfill_weight_bodyfat.py [--dry-run] [--since YYYY-MM-DD]

--dry-run : DynamoDB への書き込みを行わず結果だけ表示する
--since   : この日付以降のレコードのみ対象（省略時=全件）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_access_token_provider(region: str, secret_name: str):
    import boto3
    from night_ramen.collectors.google_auth_helper import get_access_token

    def provider() -> str:
        sm = boto3.client("secretsmanager", region_name=region)
        secret = json.loads(sm.get_secret_value(SecretId=secret_name)["SecretString"])
        return get_access_token(secret)

    return provider


def main():
    parser = argparse.ArgumentParser(description="過去バイタルデータのバックフィル（全フィールド）")
    parser.add_argument("--dry-run", action="store_true", help="書き込みを行わず確認のみ")
    parser.add_argument("--since", default=None, help="対象開始日 YYYY-MM-DD（省略=全件）")
    args = parser.parse_args()

    region = os.getenv("AWS_REGION", "us-east-1")
    table_name = os.getenv("NRB_DDB_TABLE", "NightRamenTable")
    secret_name = os.getenv("NRB_SECRET_GOOGLE", "night-ramen/google-oauth")

    import boto3
    from boto3.dynamodb.conditions import Key

    from night_ramen.collectors.google_health_api import GoogleHealthApiSource
    from night_ramen.memory.schemas import VITAL_FIELDS, DailyVitalRecord

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    # 既存バイタルレコードを全件取得
    print("DynamoDB から既存バイタルレコードを取得中...")
    raw_items: list[dict] = []
    kwargs: dict = {"KeyConditionExpression": Key("PK").eq("MEM#vitals")}
    while True:
        resp = table.query(**kwargs)
        raw_items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    print(f"  合計 {len(raw_items)} 件のレコードを発見")

    # 欠損フィールドがある日付を対象に絞り込む
    targets: list[tuple[dict, list[str]]] = []  # (data, missing_fields)
    for it in raw_items:
        if "json" not in it:
            continue
        data = json.loads(it["json"])
        date = data.get("date", "")
        if args.since and date < args.since:
            continue
        missing = [f for f in VITAL_FIELDS if data.get(f) is None]
        if missing:
            targets.append((data, missing))

    targets.sort(key=lambda x: x[0]["date"])
    print(f"  欠損フィールドがあるレコード: {len(targets)} 件\n")

    if not targets:
        print("バックフィル対象なし。終了します。")
        return

    # 対象日付と欠損フィールドの一覧を表示
    for data, missing in targets:
        print(f"  {data['date']}: 欠損={missing}")

    if args.dry_run:
        print("\n[dry-run] 対象一覧の表示のみ。--dry-run を外すと実際に取得・保存します。")
        return

    print()
    api_source = GoogleHealthApiSource(_make_access_token_provider(region, secret_name))

    updated = 0
    skipped = 0
    failed = 0

    for data, missing_before in targets:
        date = data["date"]
        print(f"[{date}] 取得中（欠損: {missing_before}）...", end=" ", flush=True)

        try:
            fetched = api_source.fetch_raw(date)
        except Exception as exc:
            print(f"失敗 ({type(exc).__name__}: {exc})")
            failed += 1
            continue

        # None フィールドだけ補完（既存値は保持）
        filled = []
        for field in VITAL_FIELDS:
            if data.get(field) is None and fetched.get(field) is not None:
                data[field] = fetched[field]
                filled.append(f"{field}={fetched[field]}")

        if not filled:
            print("→ APIもデータなし（スキップ）")
            skipped += 1
            continue

        # missing_fields を再計算
        record = DailyVitalRecord(**{k: v for k, v in data.items()
                                     if k in DailyVitalRecord.__dataclass_fields__})
        record.recompute_missing()
        data["missing_fields"] = record.missing_fields

        # DynamoDB へ保存（TTL は既存値を引き継ぐ）
        existing = table.get_item(Key={"PK": "MEM#vitals", "SK": date}).get("Item", {})
        item = {
            "PK": "MEM#vitals",
            "SK": date,
            "json": json.dumps(data, ensure_ascii=False, default=str),
        }
        if "ttl" in existing:
            item["ttl"] = existing["ttl"]
        table.put_item(Item=item)

        print(f"→ 補完: {filled}")
        updated += 1

    print(f"\n--- 完了 ---")
    print(f"  補完保存: {updated} 件 / APIもデータなし: {skipped} 件 / 失敗: {failed} 件")


if __name__ == "__main__":
    main()
