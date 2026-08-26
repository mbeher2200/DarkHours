#!/usr/bin/env python3
"""Delete bortle| cache entries whose class or zone predates the unrounded classifier.

darksky.lookup() now derives bortle_class and lp_zone from the unrounded SQM. Entries
written before that carry a class derived from a 1-decimal SQM, and they have no TTL
(darksky.lookup writes them with ttl_seconds unset), so they never expire on their own.

SELECTIVITY
    A stored 1-decimal value S was rounded from some raw value in [S-0.05, S+0.05]. It
    can only disagree with the unrounded classifier if that interval spans a _BORTLE or
    _LORENZ_ZONES boundary. The candidate set is derived at runtime by evaluating
    sqm_to_bortle/sqm_to_zone across each interval -- not from a hardcoded list, and not
    from a threshold-membership test, which over-includes at interval endpoints.

    Everything outside that set is provably unaffected and is left in place, so most of
    the warm cache survives. Locally that is ~40% of bortle| entries preserved.

    Candidates are deleted outright rather than recomputed and compared. The key
    quantises to 0.01deg, which is coarser than either raster pixel (VIIRS 0.00417deg,
    Falchi 0.00833deg), so the key does not identify the pixel the entry was read from
    -- a recompute-and-compare would disagree with the original reading ~11% of the time
    and could leave a stale entry in place. Deleting costs one raster read on next
    access. lookup() now records the unrounded SQM (sqm_raw) in the payload, so future
    passes can reclassify exactly without touching a raster or a coordinate.

    Entries whose sqm is not a 1-decimal value were never rounded (the La <= 0 branch
    returns the full-precision natural-sky constant) and are never candidates. Neither
    are entries carrying sqm_raw, which only the current classifier writes -- so a
    re-run still reports zero once live traffic has repopulated the cache.

CAPACITY
    cache_key is the partition key and Query cannot do begins_with on a partition key,
    so a full scan is required. sqm lives inside a JSON string attribute, so it cannot
    be filtered server-side either. The scan is paced (bounded page size, inter-page
    sleep, adaptive retries, eventually-consistent reads) and reports consumed capacity.
    Check `aws dynamodb describe-table` for BillingMode and size before a first run, and
    run off-peak.

The table name comes from PYNIGHTSKY_CACHE_TABLE (never hardcoded -- public repo).
Dry-run is the default; pass --delete to actually remove items.

Usage:
  export PYNIGHTSKY_CACHE_TABLE=<table>   # discover per docs/ or cdk.out
  python scripts/purge_bortle_stale.py                 # count + consumed RCU, no writes
  python scripts/purge_bortle_stale.py --delete        # actually delete
  python scripts/purge_bortle_stale.py --local         # ~/.darkhours/cache instead
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from darkhours.darksky import sqm_to_bortle, sqm_to_zone  # noqa: E402

PREFIX = "bortle|"

# Scan pacing. Deliberately conservative: this competes with production reads.
PAGE_SIZE = 200
PAGE_SLEEP_S = 0.10
DELETE_SLEEP_EVERY = 25      # one batch_writer flush
DELETE_SLEEP_S = 0.10


def risky_values(lo: float = 10.0, hi: float = 22.4, step_points: int = 201) -> set[float]:
    """1-decimal SQM values whose rounding interval spans a classification boundary.

    Derived from the live threshold tables via the classifier functions themselves, so
    it stays correct if either table is edited.
    """
    out = set()
    for i in range(int(round(lo * 10)), int(round(hi * 10)) + 1):
        s = round(i / 10.0, 1)
        base_b, base_z = sqm_to_bortle(s)[0], sqm_to_zone(s)
        for j in range(step_points):
            x = (s - 0.05) + j * (0.10 / (step_points - 1))
            if sqm_to_bortle(x)[0] != base_b or sqm_to_zone(x) != base_z:
                out.add(s)
                break
    return out


def is_candidate(value, risky: set[float]) -> bool:
    """True if this cached payload could carry a class from the rounded classifier."""
    if not isinstance(value, dict):
        return False
    # Entries written by the current classifier carry the unrounded SQM they were
    # derived from. Their class is correct by construction, so the scan stays
    # convergent as traffic repopulates the cache.
    if "sqm_raw" in value:
        return False
    sqm = value.get("sqm")
    if sqm is None:
        return False
    try:
        sqm = float(sqm)
    except (TypeError, ValueError):
        return False
    # Values that are not 1-decimal were never rounded onto a threshold.
    if abs(sqm - round(sqm, 1)) > 1e-9:
        return False
    return round(sqm, 1) in risky


# ── DynamoDB ────────────────────────────────────────────────────────────────

def scan_candidates(table, risky: set[float]) -> tuple[list[str], int, float]:
    """Return (candidate keys, total bortle| items seen, consumed RCU)."""
    from boto3.dynamodb.conditions import Attr

    paginator = table.meta.client.get_paginator("scan")
    keys: list[str] = []
    seen = 0
    rcu = 0.0
    # begins_with is applied server-side. It does not reduce RCU (the scan still reads
    # every item) but it keeps non-bortle payloads off the wire and out of json.loads.
    pages = paginator.paginate(
        TableName=table.name,
        FilterExpression=Attr("cache_key").begins_with(PREFIX),
        ProjectionExpression="cache_key, #v",
        ExpressionAttributeNames={"#v": "value"},
        ReturnConsumedCapacity="TOTAL",
        PaginationConfig={"PageSize": PAGE_SIZE},
    )
    for page in pages:
        cap = page.get("ConsumedCapacity") or {}
        rcu += float(cap.get("CapacityUnits", 0.0))
        for item in page.get("Items", []):
            key = item.get("cache_key", "")
            if not key.startswith(PREFIX):
                continue
            seen += 1
            try:
                payload = json.loads(item.get("value", "null"))
            except (TypeError, ValueError):
                continue
            if is_candidate(payload, risky):
                keys.append(key)
        time.sleep(PAGE_SLEEP_S)
    return keys, seen, rcu


def delete_keys(table, keys: list[str]) -> None:
    """Batch-delete the given cache keys, paced so writes don't spike the table."""
    with table.batch_writer() as batch:
        for n, key in enumerate(keys, 1):
            batch.delete_item(Key={"cache_key": key})
            if n % DELETE_SLEEP_EVERY == 0:
                time.sleep(DELETE_SLEEP_S)


def run_dynamo(args, risky: set[float]) -> int:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    table_name = os.environ.get("PYNIGHTSKY_CACHE_TABLE")
    if not table_name:
        print("PYNIGHTSKY_CACHE_TABLE is not set.", file=sys.stderr)
        return 1

    # Adaptive retries carry a client-side rate limiter that backs off on throttling.
    cfg = Config(retries={"mode": "adaptive", "max_attempts": 10})
    table = boto3.resource("dynamodb", config=cfg).Table(table_name)

    try:
        keys, seen, rcu = scan_candidates(table, risky)
    except ClientError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not seen:
        print("No bortle| items found -- nothing to do.")
        return 0

    pct = 100.0 * len(keys) / seen
    print(f"bortle| items scanned : {seen}")
    print(f"candidates            : {len(keys)}  ({pct:.1f}%)")
    print(f"preserved             : {seen - len(keys)}  ({100 - pct:.1f}%)")
    print(f"consumed read units   : {rcu:.1f}")

    if args.verbose:
        for key in keys:
            print(f"  {key}")

    if args.delete:
        try:
            delete_keys(table, keys)
        except ClientError as e:
            print(f"Error during delete: {e}", file=sys.stderr)
            return 1
        print(f"Deleted {len(keys)} bortle| items.")
    else:
        print(f"Dry run: {len(keys)} items would be deleted (re-run with --delete).")
    return 0


# ── Local file cache ────────────────────────────────────────────────────────

def run_local(args, risky: set[float]) -> int:
    from darkhours.cache import _CACHE_DIR

    cache_dir = Path(_CACHE_DIR)
    if not cache_dir.exists():
        print(f"No local cache at {cache_dir} -- nothing to do.")
        return 0

    # Filenames are sha256 hashes; the original key lives in the JSON body.
    candidates: list[Path] = []
    seen = 0
    for path in cache_dir.glob("*.json"):
        try:
            entry = json.loads(path.read_text())
        except Exception:
            continue
        if not str(entry.get("key", "")).startswith(PREFIX):
            continue
        seen += 1
        if is_candidate(entry.get("value"), risky):
            candidates.append(path)

    if not seen:
        print("No bortle| entries in the local cache -- nothing to do.")
        return 0

    pct = 100.0 * len(candidates) / seen
    print(f"bortle| entries       : {seen}")
    print(f"candidates            : {len(candidates)}  ({pct:.1f}%)")
    print(f"preserved             : {seen - len(candidates)}  ({100 - pct:.1f}%)")

    if args.delete:
        for path in candidates:
            path.unlink(missing_ok=True)
        print(f"Deleted {len(candidates)} local bortle| entries.")
    else:
        print(f"Dry run: {len(candidates)} entries would be deleted (re-run with --delete).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true",
                        help="actually delete the items (default: dry run)")
    parser.add_argument("--local", action="store_true",
                        help="operate on ~/.darkhours/cache instead of DynamoDB")
    parser.add_argument("--verbose", action="store_true",
                        help="list every candidate key")
    args = parser.parse_args()

    risky = risky_values()
    print(f"candidate SQM values ({len(risky)}): "
          f"{' '.join(f'{v:.1f}' for v in sorted(risky))}\n")

    return run_local(args, risky) if args.local else run_dynamo(args, risky)


if __name__ == "__main__":
    sys.exit(main())
