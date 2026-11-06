"""Extract: incremental, idempotent copy of the S3 dataset into data/raw/.

S3 is the read-only *source*; all processing happens locally. Each run:
1. lists the bucket into a manifest (key, size, etag, last_modified): the lineage record
   of exactly which source files a run used;
2. downloads only objects that are new or whose etag changed, so re-running is cheap
   and never duplicates files.

Usage:
    uv run python -m pipelines.etl.extract --list             # inspect only, no download
    uv run python -m pipelines.etl.extract                    # sync the tables we need
"""

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from cardagent.config import Settings, get_settings

# Tables the card-support workflow needs (docs/ARCHITECTURE.md, step 1b).
REQUIRED_TABLES = (
    "customers",
    "products",
    "transactions",
    "call_center_interactions",
    "call_transcripts",
    "complaints",
    "satisfaction_surveys",
    "daily_exchange_rates",
)

MANIFEST_NAME = "_manifest.json"


@dataclass(frozen=True)
class SourceObject:
    key: str
    size: int
    etag: str
    last_modified: str

    def table(self, prefix: str) -> str:
        """Table name: first path segment after the prefix, without extension."""
        first = self.key.removeprefix(prefix).split("/", 1)[0]
        return first.split(".", 1)[0]


def s3_client(settings: Settings):
    import boto3

    if not (settings.aws_access_key_id and settings.aws_secret_access_key):
        raise SystemExit("Missing AWS credentials: copy .env.example to .env and fill them in.")
    return boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
        region_name=settings.aws_default_region,
    )


def list_objects(client, bucket: str, prefix: str) -> list[SourceObject]:
    objects = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            if o["Key"].endswith("/"):
                continue
            objects.append(
                SourceObject(
                    key=o["Key"],
                    size=o["Size"],
                    etag=o["ETag"].strip('"'),
                    last_modified=o["LastModified"].isoformat(),
                )
            )
    return objects


def summarize(objects: list[SourceObject], prefix: str) -> str:
    by_table: dict[str, list[SourceObject]] = defaultdict(list)
    for o in objects:
        by_table[o.table(prefix)].append(o)
    lines = [f"{'table':28} {'files':>6} {'MB':>9}  example key"]
    for table, objs in sorted(by_table.items()):
        mb = sum(o.size for o in objs) / 1e6
        mark = "*" if table in REQUIRED_TABLES else " "
        lines.append(f"{mark}{table:27} {len(objs):6} {mb:9.1f}  {objs[0].key}")
    lines.append("(* = needed for the card-support workflow)")
    return "\n".join(lines)


def local_path(raw_dir: Path, obj: SourceObject, prefix: str) -> Path:
    return raw_dir / obj.key.removeprefix(prefix)


def load_manifest(raw_dir: Path) -> dict[str, dict]:
    path = raw_dir / MANIFEST_NAME
    return json.loads(path.read_text()) if path.exists() else {}


def plan_downloads(
    objects: list[SourceObject], previous: dict[str, dict], raw_dir: Path, prefix: str
) -> list[SourceObject]:
    """Objects that are new, changed (different etag), or missing locally."""
    todo = []
    for o in objects:
        seen = previous.get(o.key)
        if seen is None or seen["etag"] != o.etag or not local_path(raw_dir, o, prefix).exists():
            todo.append(o)
    return todo


def sync(settings: Settings, tables: tuple[str, ...] = REQUIRED_TABLES) -> list[SourceObject]:
    client = s3_client(settings)
    prefix = settings.s3_prefix
    objects = [
        o for o in list_objects(client, settings.s3_bucket, prefix) if o.table(prefix) in tables
    ]
    raw_dir = settings.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    previous = load_manifest(raw_dir)

    todo = plan_downloads(objects, previous, raw_dir, prefix)

    def download(o: SourceObject) -> None:
        dest = local_path(raw_dir, o, prefix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp name, then rename: an interrupted run never leaves a
        # half-written file that a later run would mistake for a complete one.
        tmp = dest.with_suffix(dest.suffix + ".part")
        client.download_file(settings.s3_bucket, o.key, str(tmp))
        tmp.replace(dest)

    # ~5k small daily files: threads overlap network latency (boto3 clients are thread-safe).
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, _ in enumerate(pool.map(download, todo), 1):
            if i % 250 == 0 or i == len(todo):
                print(f"downloaded {i}/{len(todo)}", flush=True)

    manifest = {o.key: asdict(o) for o in objects}
    (raw_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    print(f"{len(todo)} downloaded, {len(objects) - len(todo)} already up to date.")
    return objects


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="only list the bucket")
    args = parser.parse_args()

    settings = get_settings()
    if args.list:
        objects = list_objects(s3_client(settings), settings.s3_bucket, settings.s3_prefix)
        print(summarize(objects, settings.s3_prefix))
    else:
        sync(settings)


if __name__ == "__main__":
    main()
