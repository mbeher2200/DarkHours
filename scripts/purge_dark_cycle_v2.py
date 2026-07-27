#!/usr/bin/env python3
"""Delete orphaned dark_cycle_v2 cache items from the DynamoDB cache table.

The v2 -> v3 key bump in sky_events._dark_cycle_db_key (see that function's
comment) orphaned every v2 window: some were poisoned by the fixed
twilight-offset broadcast (a shoulder-season target_date zeroed all 30 nights),
and none carry a TTL, so they never expire on their own. This script physically
removes them to reclaim storage. It is safe to run at any time after the v3
code is deployed -- nothing reads v2 keys anymore.

The table name comes from PYNIGHTSKY_CACHE_TABLE (never hardcoded -- public
repo). Dry-run is the default; pass --delete to actually remove items.

Usage:
  export PYNIGHTSKY_CACHE_TABLE=<table>   # discover per docs/ or cdk.out
  python scripts/purge_dark_cycle_v2.py            # count + list, no writes
  python scripts/purge_dark_cycle_v2.py --delete   # actually delete
"""

import argparse
import os
import sys

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

PREFIX = "dark_cycle_v2|"


def find_v2_keys(table) -> list[str]:
    """Scan for cache keys beginning with the orphaned v2 prefix."""
    paginator = table.meta.client.get_paginator("scan")
    keys = []
    for page in paginator.paginate(
        TableName=table.name,
        FilterExpression=Attr("cache_key").begins_with(PREFIX),
        ProjectionExpression="cache_key",
    ):
        keys.extend(item["cache_key"] for item in page["Items"])
    return keys


def delete_keys(table, keys: list[str]) -> None:
    """Batch-delete the given cache keys (auto-chunked, unprocessed retried)."""
    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key={"cache_key": key})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true",
                        help="actually delete the items (default: dry run)")
    args = parser.parse_args()

    table_name = os.environ.get("PYNIGHTSKY_CACHE_TABLE")
    if not table_name:
        print("PYNIGHTSKY_CACHE_TABLE is not set.", file=sys.stderr)
        return 1

    table = boto3.resource("dynamodb").Table(table_name)
    try:
        keys = find_v2_keys(table)
        if not keys:
            print("No dark_cycle_v2 items found -- nothing to do.")
            return 0
        for key in keys:
            print(key)
        if args.delete:
            delete_keys(table, keys)
            print(f"Deleted {len(keys)} dark_cycle_v2 items.")
        else:
            print(f"Dry run: {len(keys)} dark_cycle_v2 items would be deleted "
                  f"(re-run with --delete).")
        return 0
    except ClientError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
