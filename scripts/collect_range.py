"""指定期間の全日付バイタルを収集する（新規日付を含む一括収集）。

backfill_weight_bodyfat.py は「DynamoDB に既存レコードがある日付の欠損補完」専用だが、
本スクリプトは指定期間の**全日付**について run_morning を実行し、レコードが無い日も新規収集する。
save_vital は上書き保存（冪等）なので既存日付に再実行しても安全。

⚠️  --protect-existing なしで再実行すると、API に存在しないフィールド（睡眠・HRV 等）が
    DynamoDB の既存 non-null 値を None で上書きする。バグ修正後の再収集では必ず
    --protect-existing を付けること。

使い方:
    python scripts/collect_range.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--dry-run]
    python scripts/collect_range.py --protect-existing  # 既存 non-null 値を保護して再収集

既定: --since 2025-12-22, --until 今日(JST)
--dry-run: 収集は実行するが DynamoDB へ保存しない（run_morning は保存するため、
           dry-run では収集件数の見積もりのみ＝実際には日付一覧の表示に留める）
--protect-existing: 既存レコードの non-null フィールドを API から取れなくなった null で
                    上書きしない（睡眠・HRV 等 API 保持期間が短い指標の保護に使う）
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date as _date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _today_jst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _daterange(since: str, until: str):
    d = _date.fromisoformat(since)
    end = _date.fromisoformat(until)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="指定期間の全日付バイタル収集")
    parser.add_argument("--since", default="2025-12-22", help="収集開始日 YYYY-MM-DD")
    parser.add_argument("--until", default=_today_jst(), help="収集終了日 YYYY-MM-DD（既定=今日JST）")
    parser.add_argument("--dry-run", action="store_true", help="対象日一覧のみ表示し収集しない")
    parser.add_argument("--protect-existing", action="store_true",
                        help="既存 non-null 値を null で上書きしない（睡眠・HRV 保護用）")
    args = parser.parse_args()

    dates = list(_daterange(args.since, args.until))
    print(f"対象期間: {args.since} 〜 {args.until}（{len(dates)} 日）")

    if args.dry_run:
        print("[dry-run] 収集は実行しません。対象日数のみ表示しました。")
        return

    import json
    import boto3
    from handlers.morning_handler import build_morning_dependencies
    from night_ramen.collectors import ConsentRevokedError
    from night_ramen.config import load_settings
    from night_ramen.workflow.morning import run_morning

    settings = load_settings()
    memory, health_source, report_source = build_morning_dependencies(settings)

    ddb_table = None
    if args.protect_existing:
        ddb_table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.dynamodb_table)
        print("[protect-existing] 既存 non-null フィールドを保護して収集します")

    collected = 0
    consent_stopped = 0
    failed = 0
    no_data = 0

    for date in dates:
        try:
            record = run_morning(date, memory, health_source, report_source)
            # --protect-existing: API から取れなくなった null が既存 non-null を上書きするのを防ぐ
            if args.protect_existing and record is not None and ddb_table is not None:
                item = ddb_table.get_item(Key={"PK": "MEM#vitals", "SK": date}).get("Item")
                if item:
                    existing = json.loads(item.get("json", "{}"))
                    patched = False
                    for field, val in existing.items():
                        if val is not None and record.to_dict().get(field) is None:
                            setattr(record, field, val)
                            patched = True
                    if patched:
                        memory.save_vital(record)
        except ConsentRevokedError:
            print(f"[{date}] 同意失効 → 収集停止")
            consent_stopped += 1
            continue
        except Exception as exc:  # noqa: BLE001 — 個別日失敗は継続
            print(f"[{date}] 失敗 ({type(exc).__name__}: {exc})")
            failed += 1
            continue

        if record is None:
            print(f"[{date}] None（同意失効など）")
            consent_stopped += 1
            continue

        present = [f for f in record.to_dict() if f not in ("date", "ingested_at",
                   "missing_fields") and record.to_dict()[f] is not None]
        if not present:
            print(f"[{date}] 全欠損（データなし）")
            no_data += 1
        else:
            print(f"[{date}] 保存（欠損 {len(record.missing_fields)} 件 / "
                  f"weight={getattr(record, 'weight_kg', None)} "
                  f"body_fat={getattr(record, 'body_fat_pct', None)}）")
            collected += 1

    print(f"\n--- 完了 ---")
    print(f"  保存: {collected} 日 / 全欠損: {no_data} 日 / "
          f"同意失効: {consent_stopped} 日 / 失敗: {failed} 日")


if __name__ == "__main__":
    main()
