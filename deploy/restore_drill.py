from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

import boto3
import psycopg


def _backup_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["CODERAI_BACKUP_S3_ENDPOINT"],
        region_name=os.environ.get("CODERAI_BACKUP_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["CODERAI_BACKUP_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["CODERAI_BACKUP_S3_SECRET_KEY"],
    )


def _postgres_url(database: str | None = None) -> str:
    value = os.environ["CODERAI_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}" if database else parts.path, parts.query, ""))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a backup into an isolated temporary database and verify object inventory.")
    parser.add_argument("snapshot", help="For example daily/2026-07-20")
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != "RUN-ISOLATED-RESTORE-DRILL":
        raise SystemExit("Invalid confirmation text.")

    client = _backup_client()
    bucket = os.environ["CODERAI_BACKUP_S3_BUCKET"]
    manifest = json.loads(client.get_object(Bucket=bucket, Key=f"{args.snapshot}/manifest.json")["Body"].read())
    drill_database = "coderai_restore_drill"
    with psycopg.connect(_postgres_url("postgres"), autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{drill_database}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{drill_database}"')
    try:
        with tempfile.TemporaryDirectory(prefix="coderai-restore-drill-") as temporary_dir:
            dump_path = Path(temporary_dir) / "coderai.dump"
            client.download_file(bucket, f"{args.snapshot}/database/coderai.dump", str(dump_path))
            if _sha256(dump_path) != manifest["database"]["sha256"]:
                raise RuntimeError("Database dump checksum mismatch.")
            subprocess.run(["pg_restore", "--no-owner", "--no-acl", f"--dbname={_postgres_url(drill_database)}", str(dump_path)], check=True, timeout=60 * 60)
            with psycopg.connect(_postgres_url(drill_database)) as connection:
                table_count = connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
                if table_count < 10:
                    raise RuntimeError("Restored database contains too few application tables.")
            for item in manifest.get("objects") or []:
                head = client.head_object(Bucket=bucket, Key=f"{args.snapshot}/objects/{item['key']}")
                if int(head["ContentLength"]) != int(item["size"]):
                    raise RuntimeError(f"Object size mismatch: {item['key']}")
                with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as stream:
                    client.download_fileobj(bucket, f"{args.snapshot}/objects/{item['key']}", stream)
                    stream.seek(0)
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                    if digest.hexdigest() != item["sha256"]:
                        raise RuntimeError(f"Object checksum mismatch: {item['key']}")
        print(json.dumps({"status": "passed", "snapshot": args.snapshot, "tables": table_count, "objects": len(manifest.get("objects") or [])}))
        return 0
    finally:
        with psycopg.connect(_postgres_url("postgres"), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{drill_database}" WITH (FORCE)')


if __name__ == "__main__":
    raise SystemExit(main())
