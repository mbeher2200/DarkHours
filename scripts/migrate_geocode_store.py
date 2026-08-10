#!/usr/bin/env python3
"""One-shot migration: the ``__geocode__`` blob → one DynamoDB item per location.

The aws-backend geocode store used to keep every saved location in a single item
under ``__geocode__``. That item reached DynamoDB's hard 400 KB limit, after which
every write failed and no new location was ever cached. ``DynamoGeocodeStore`` now
writes one item per location under ``geocode|<key>``; this copies the existing
entries across so nothing (in particular, explicitly saved named locations) is lost.

Run this BEFORE deploying the per-key store. The old code only reads the blob and
the new code only reads per-key items, so running it first means there is no window
where a lookup misses. The blob is left in place — delete it once the new store has
run cleanly for a while:

    aws dynamodb delete-item --table-name "$PYNIGHTSKY_CACHE_TABLE" \
        --key '{"cache_key":{"S":"__geocode__"}}'

Usage:
    PYNIGHTSKY_CACHE_TABLE=<table> python scripts/migrate_geocode_store.py [--apply]

Defaults to a dry run; pass --apply to write. Idempotent — safe to re-run.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BLOB_KEY = "__geocode__"
PREFIX = "geocode|"


def _existing_keys(table, table_name: str, keys: list) -> set:
    """Which of these location keys already exist as per-key items.

    BatchGetItem, 100 at a time. One GetItem per key is thousands of sequential
    round-trips — minutes of wall time for a check that should take seconds.
    """
    import boto3
    ddb = boto3.resource("dynamodb")
    found = set()
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        request = {table_name: {
            "Keys": [{"cache_key": PREFIX + k} for k in chunk],
            "ProjectionExpression": "cache_key",
        }}
        while request:
            resp = ddb.batch_get_item(RequestItems=request)
            for item in resp.get("Responses", {}).get(table_name, []):
                found.add(item["cache_key"][len(PREFIX):])
            request = resp.get("UnprocessedKeys") or None
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write items (default: dry run)")
    ap.add_argument("--table", default=os.environ.get("PYNIGHTSKY_CACHE_TABLE"))
    args = ap.parse_args()

    if not args.table:
        print("error: set PYNIGHTSKY_CACHE_TABLE or pass --table", file=sys.stderr)
        return 2

    import boto3
    table = boto3.resource("dynamodb").Table(args.table)

    item = table.get_item(Key={"cache_key": BLOB_KEY}).get("Item")
    if not item:
        print(f"no {BLOB_KEY!r} item found — nothing to migrate")
        return 0

    raw = item["value"]
    entries = json.loads(raw)
    print(f"blob: {len(raw.encode()):,} bytes, {len(entries):,} entries")

    # Skip keys already migrated so a re-run is cheap and non-destructive.
    # BatchGetItem in chunks of 100 — one GetItem per key is thousands of
    # sequential round-trips and takes minutes.
    todo = dict(entries)
    for present in _existing_keys(table, args.table, list(entries)):
        todo.pop(present, None)

    print(f"already migrated: {len(entries) - len(todo):,}")
    print(f"to write        : {len(todo):,}")

    if not args.apply:
        for key in list(todo)[:5]:
            print(f"  would write {PREFIX + key!r}")
        if len(todo) > 5:
            print(f"  ... and {len(todo) - 5:,} more")
        print("\ndry run — re-run with --apply to write")
        return 0

    written = 0
    with table.batch_writer() as batch:
        for key, entry in todo.items():
            batch.put_item(Item={"cache_key": PREFIX + key, "value": json.dumps(entry)})
            written += 1
            if written % 250 == 0:
                print(f"  {written:,}/{len(todo):,}")
    print(f"wrote {written:,} items")

    missing = [k for k in entries if table.get_item(
        Key={"cache_key": PREFIX + k}).get("Item") is None]
    if missing:
        print(f"WARNING: {len(missing)} entries still missing, e.g. {missing[:3]}",
              file=sys.stderr)
        return 1
    print(f"verified all {len(entries):,} entries present as per-key items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
