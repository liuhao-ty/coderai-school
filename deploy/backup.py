from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit

import boto3


def _client(prefix: str):
    endpoint = os.environ[f"{prefix}_ENDPOINT"]
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get(f"{prefix}_REGION", "us-east-1"),
        aws_access_key_id=os.environ[f"{prefix}_ACCESS_KEY"],
        aws_secret_access_key=os.environ[f"{prefix}_SECRET_KEY"],
    )


def _pg_url() -> str:
    value = os.environ["CODERAI_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_metrics(success: bool, object_count: int = 0, total_bytes: int = 0) -> None:
    target = Path(os.environ.get("CODERAI_BACKUP_METRICS_FILE", "/tmp/coderai_backup.prom"))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    current = int(time.time())
    previous_success = 0
    if target.is_file():
        for line in target.read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("coderai_backup_last_success_timestamp_seconds "):
                try:
                    previous_success = int(float(line.rsplit(" ", 1)[-1]))
                except ValueError:
                    pass
    lines = [
        f"coderai_backup_last_success_timestamp_seconds {current if success else previous_success}",
        f"coderai_backup_last_failure_timestamp_seconds {0 if success else current}",
        f"coderai_backup_objects {object_count}",
        f"coderai_backup_bytes {total_bytes}",
    ]
    temporary.write_text("\n".join(lines) + "\n", encoding="ascii")
    temporary.replace(target)


def _list_prefixes(client, bucket: str, root: str) -> set[str]:
    prefixes: set[str] = set()
    continuation = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": root, "Delimiter": "/"}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = client.list_objects_v2(**kwargs)
        prefixes.update(item["Prefix"] for item in response.get("CommonPrefixes") or [])
        if not response.get("IsTruncated"):
            return prefixes
        continuation = response.get("NextContinuationToken")


def _delete_prefix(client, bucket: str, prefix: str) -> None:
    continuation = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = client.list_objects_v2(**kwargs)
        keys = [{"Key": item["Key"]} for item in response.get("Contents") or []]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
        if not response.get("IsTruncated"):
            return
        continuation = response.get("NextContinuationToken")


def _apply_retention(client, bucket: str, current: datetime) -> None:
    daily_cutoff = (current - timedelta(days=30)).date()
    for prefix in _list_prefixes(client, bucket, "daily/"):
        try:
            snapshot_date = datetime.strptime(prefix.rstrip("/").split("/")[-1], "%Y-%m-%d").date()
        except ValueError:
            continue
        if snapshot_date < daily_cutoff:
            _delete_prefix(client, bucket, prefix)

    month_cutoff = (current.year * 12 + current.month) - 11
    for prefix in _list_prefixes(client, bucket, "monthly/"):
        try:
            value = datetime.strptime(prefix.rstrip("/").split("/")[-1], "%Y-%m")
        except ValueError:
            continue
        if value.year * 12 + value.month < month_cutoff:
            _delete_prefix(client, bucket, prefix)


def _snapshot_prefixes(client, bucket: str, current: datetime) -> list[str]:
    prefixes = [f"daily/{current:%Y-%m-%d}"]
    monthly_prefix = f"monthly/{current:%Y-%m}"
    monthly_manifest_key = f"{monthly_prefix}/manifest.json"
    existing_month = client.list_objects_v2(Bucket=bucket, Prefix=monthly_manifest_key, MaxKeys=1)
    monthly_complete = any(
        item.get("Key") == monthly_manifest_key
        for item in existing_month.get("Contents") or []
    )
    if not monthly_complete:
        _delete_prefix(client, bucket, f"{monthly_prefix}/")
        prefixes.append(monthly_prefix)
    return prefixes


def run_backup() -> dict:
    source_endpoint = os.environ["CODERAI_S3_ENDPOINT"].rstrip("/")
    source_bucket = os.environ["CODERAI_S3_BUCKET"]
    backup_endpoint = os.environ["CODERAI_BACKUP_S3_ENDPOINT"].rstrip("/")
    backup_bucket = os.environ["CODERAI_BACKUP_S3_BUCKET"]
    if source_endpoint == backup_endpoint and source_bucket == backup_bucket:
        raise RuntimeError("Backup storage must be independent from production object storage.")

    source = _client("CODERAI_S3")
    backup = _client("CODERAI_BACKUP_S3")
    try:
        backup.head_bucket(Bucket=backup_bucket)
    except Exception:
        backup.create_bucket(Bucket=backup_bucket)

    current = datetime.now(timezone.utc)
    prefixes = _snapshot_prefixes(backup, backup_bucket, current)

    with tempfile.TemporaryDirectory(prefix="coderai-backup-") as temporary_dir:
        dump_path = Path(temporary_dir) / "coderai.dump"
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", f"--file={dump_path}", _pg_url()],
            check=True,
            timeout=60 * 60,
        )
        database_sha256 = _sha256(dump_path)
        database_size = dump_path.stat().st_size

        objects: list[dict] = []
        continuation = None
        while True:
            kwargs = {"Bucket": source_bucket}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            response = source.list_objects_v2(**kwargs)
            for item in response.get("Contents") or []:
                key = item["Key"]
                head = source.head_object(Bucket=source_bucket, Key=key)
                metadata = dict(head.get("Metadata") or {})
                with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as stream:
                    source.download_fileobj(source_bucket, key, stream)
                    stream.seek(0)
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                    object_sha256 = digest.hexdigest()
                    if metadata.get("sha256") and metadata["sha256"] != object_sha256:
                        raise RuntimeError(f"Source object checksum mismatch: {key}")
                    metadata["sha256"] = object_sha256
                    objects.append({
                        "key": key,
                        "size": int(item.get("Size") or 0),
                        "etag": str(item.get("ETag") or "").strip('"'),
                        "sha256": object_sha256,
                    })
                    for prefix in prefixes:
                        stream.seek(0)
                        backup.upload_fileobj(
                            stream,
                            backup_bucket,
                            f"{prefix}/objects/{key}",
                            ExtraArgs={"Metadata": metadata, "ContentType": head.get("ContentType") or "application/octet-stream"},
                        )
            if not response.get("IsTruncated"):
                break
            continuation = response.get("NextContinuationToken")

        manifest = {
            "schema_version": "coderai-backup-1.0",
            "created_at": current.isoformat(),
            "database": {"path": "database/coderai.dump", "size": database_size, "sha256": database_sha256},
            "objects": objects,
            "source_bucket": source_bucket,
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        for prefix in prefixes:
            backup.upload_file(str(dump_path), backup_bucket, f"{prefix}/database/coderai.dump", ExtraArgs={"Metadata": {"sha256": database_sha256}})
            backup.put_object(Bucket=backup_bucket, Key=f"{prefix}/manifest.json", Body=manifest_bytes, ContentType="application/json")

    _apply_retention(backup, backup_bucket, current)
    object_bytes = sum(item["size"] for item in objects)
    _write_metrics(True, len(objects), database_size + object_bytes)
    return {"prefixes": prefixes, "database_sha256": database_sha256, "object_count": len(objects), "bytes": database_size + object_bytes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            print(json.dumps(run_backup(), ensure_ascii=False), flush=True)
        except Exception as exc:
            _write_metrics(False)
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), flush=True)
            if not args.loop:
                raise
        if not args.loop:
            return 0
        time.sleep(max(3600, int(os.environ.get("CODERAI_BACKUP_INTERVAL_SECONDS", "86400"))))


if __name__ == "__main__":
    raise SystemExit(main())
