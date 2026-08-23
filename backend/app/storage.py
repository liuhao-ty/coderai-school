from __future__ import annotations

from contextlib import contextmanager
import hashlib
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Iterator
from urllib.parse import quote
import uuid

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from backend.app.db import CLOUD_MODE, DATA_DIR
from backend.app.tenancy import current_organization_id


OBJECT_PREFIX = "object://"
STORAGE_BACKEND = os.environ.get(
    "CODERAI_STORAGE_BACKEND",
    "s3" if CLOUD_MODE else "local",
).strip().lower()
S3_BUCKET = os.environ.get("CODERAI_S3_BUCKET", "coderai-pilot").strip()
S3_ENDPOINT = os.environ.get("CODERAI_S3_ENDPOINT", "").strip()
S3_REGION = os.environ.get("CODERAI_S3_REGION", "us-east-1").strip() or "us-east-1"
S3_ACCESS_KEY = os.environ.get("CODERAI_S3_ACCESS_KEY", "").strip()
S3_SECRET_KEY = os.environ.get("CODERAI_S3_SECRET_KEY", "").strip()
S3_SECURE = os.environ.get("CODERAI_S3_SECURE", "true").lower() in {"1", "true", "yes"}
S3_SSE = os.environ.get("CODERAI_S3_SSE", "").strip()

_SAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")
_s3_client_instance = None


def is_object_reference(value: str | Path | None) -> bool:
    return str(value or "").startswith(OBJECT_PREFIX)


def _safe_name(filename: str) -> str:
    name = Path(filename or "file").name
    cleaned = _SAFE_NAME.sub("-", name).strip(".-")
    return (cleaned or "file")[:180]


def build_object_key(category: str, filename: str, *, stable_name: str = "") -> str:
    category_path = PurePosixPath(category.strip("/"))
    if ".." in category_path.parts:
        raise ValueError("Object storage category is invalid.")
    leaf = _safe_name(stable_name or f"{uuid.uuid4().hex}-{filename}")
    return str(PurePosixPath("organizations", str(current_organization_id()), category_path, leaf))


def _reference(bucket: str, key: str) -> str:
    return f"{OBJECT_PREFIX}{bucket}/{key.lstrip('/')}"


def parse_object_reference(reference: str) -> tuple[str, str]:
    if not is_object_reference(reference):
        raise ValueError("Not an object storage reference.")
    value = reference[len(OBJECT_PREFIX):]
    bucket, separator, key = value.partition("/")
    if not separator or not bucket or not key or ".." in PurePosixPath(key).parts:
        raise ValueError("Object storage reference is invalid.")
    return bucket, key


def owned_object_parts(reference: str) -> tuple[str, str]:
    bucket, key = parse_object_reference(reference)
    prefix = PurePosixPath("organizations", str(current_organization_id()))
    key_path = PurePosixPath(key)
    if bucket != S3_BUCKET or key_path.parts[:len(prefix.parts)] != prefix.parts:
        raise ValueError("Object storage reference belongs to another organization.")
    return bucket, key


def _s3_client():
    global _s3_client_instance
    if _s3_client_instance is not None:
        return _s3_client_instance
    if STORAGE_BACKEND != "s3":
        raise RuntimeError("S3 storage is not enabled.")
    if not S3_BUCKET or not S3_ENDPOINT or not S3_ACCESS_KEY or not S3_SECRET_KEY:
        raise RuntimeError("S3 storage configuration is incomplete.")
    import boto3

    endpoint = S3_ENDPOINT
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"{'https' if S3_SECURE else 'http'}://{endpoint}"
    _s3_client_instance = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    return _s3_client_instance


def ensure_storage_ready(create_bucket: bool = False) -> dict:
    if STORAGE_BACKEND == "local":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return {"backend": "local", "ready": True, "bucket": ""}
    client = _s3_client()
    try:
        client.head_bucket(Bucket=S3_BUCKET)
    except Exception:
        if not create_bucket:
            raise
        client.create_bucket(Bucket=S3_BUCKET)
    return {"backend": "s3", "ready": True, "bucket": S3_BUCKET}


def put_bytes(
    category: str,
    filename: str,
    content: bytes,
    *,
    content_type: str = "",
    stable_name: str = "",
) -> str:
    if STORAGE_BACKEND == "local":
        directory = (DATA_DIR / category).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / _safe_name(stable_name or f"{uuid.uuid4().hex}-{filename}")
        target.write_bytes(content)
        return str(target)

    key = build_object_key(category, filename, stable_name=stable_name)
    kwargs = {
        "Bucket": S3_BUCKET,
        "Key": key,
        "Body": content,
        "ContentType": content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "Metadata": {"sha256": hashlib.sha256(content).hexdigest()},
    }
    if S3_SSE:
        kwargs["ServerSideEncryption"] = S3_SSE
    _s3_client().put_object(**kwargs)
    return _reference(S3_BUCKET, key)


def put_file(category: str, source: Path, *, filename: str = "", content_type: str = "", stable_name: str = "") -> str:
    stored_name = filename or source.name
    if STORAGE_BACKEND == "local":
        directory = (DATA_DIR / category).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / _safe_name(stable_name or f"{uuid.uuid4().hex}-{stored_name}")
        shutil.copyfile(source, target)
        return str(target)

    key = build_object_key(category, stored_name, stable_name=stable_name)
    checksum = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            checksum.update(chunk)
    extra_args = {
        "ContentType": content_type or mimetypes.guess_type(stored_name)[0] or "application/octet-stream",
        "Metadata": {"sha256": checksum.hexdigest()},
    }
    if S3_SSE:
        extra_args["ServerSideEncryption"] = S3_SSE
    _s3_client().upload_file(str(source), S3_BUCKET, key, ExtraArgs=extra_args)
    return _reference(S3_BUCKET, key)


def read_bytes(reference: str, *, maximum_bytes: int | None = None) -> bytes:
    if is_object_reference(reference):
        bucket, key = owned_object_parts(reference)
        response = _s3_client().get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        if maximum_bytes is None:
            return body.read()
        content = body.read(maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise ValueError("Stored object exceeds the permitted size.")
        return content
    path = managed_local_path(reference)
    if maximum_bytes is not None and path.stat().st_size > maximum_bytes:
        raise ValueError("Stored file exceeds the permitted size.")
    return path.read_bytes()


def object_exists(reference: str) -> bool:
    if not reference:
        return False
    if is_object_reference(reference):
        try:
            bucket, key = owned_object_parts(reference)
            _s3_client().head_object(Bucket=bucket, Key=key)
            return True
        except ValueError:
            return False
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if str(error.get("Code") or "") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
    try:
        return Path(reference).resolve().is_file()
    except OSError:
        return False


def delete_object(reference: str) -> bool:
    if not reference:
        return False
    if is_object_reference(reference):
        bucket, key = owned_object_parts(reference)
        _s3_client().delete_object(Bucket=bucket, Key=key)
        return True
    try:
        path = managed_local_path(reference)
        if path.is_file():
            path.unlink()
            return True
    except (ValueError, OSError):
        return False
    return False


def managed_local_path(reference: str) -> Path:
    path = Path(reference)
    resolved = path.resolve()
    resolved.relative_to(DATA_DIR.resolve())
    if not resolved.is_file():
        raise FileNotFoundError(reference)
    if resolved.is_symlink():
        raise ValueError("Symbolic links are not accepted.")
    return resolved


@contextmanager
def materialize(reference: str, *, suffix: str = "") -> Iterator[Path]:
    if not is_object_reference(reference):
        yield managed_local_path(reference)
        return
    content = read_bytes(reference)
    with tempfile.TemporaryDirectory(prefix="coderai-object-") as temporary_dir:
        target = Path(temporary_dir) / f"object{suffix or Path(parse_object_reference(reference)[1]).suffix}"
        target.write_bytes(content)
        yield target


def storage_response(
    reference: str,
    *,
    media_type: str = "application/octet-stream",
    filename: str = "",
    inline: bool = True,
):
    if is_object_reference(reference):
        bucket, key = owned_object_parts(reference)
        try:
            response = _s3_client().get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STORED_FILE_MISSING", "message": "云端文件不存在或暂时不可用。"},
            ) from exc
        headers = {"X-Content-Type-Options": "nosniff"}
        if filename:
            disposition = "inline" if inline else "attachment"
            headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(filename)}"
        return StreamingResponse(
            response["Body"].iter_chunks(chunk_size=1024 * 1024),
            media_type=media_type or response.get("ContentType") or "application/octet-stream",
            headers=headers,
        )
    path = managed_local_path(reference)
    return FileResponse(
        path,
        media_type=media_type,
        filename=None if inline else (filename or path.name),
        headers={"X-Content-Type-Options": "nosniff"},
    )


def reference_name(reference: str) -> str:
    if is_object_reference(reference):
        return Path(parse_object_reference(reference)[1]).name
    return Path(reference).name


def organization_storage_usage() -> dict[str, int | str]:
    if STORAGE_BACKEND == "local":
        files = [item for item in DATA_DIR.rglob("*") if item.is_file() and not item.is_symlink()]
        return {
            "backend": "local",
            "object_count": len(files),
            "total_bytes": sum(item.stat().st_size for item in files),
        }
    prefix = f"organizations/{current_organization_id()}/"
    object_count = 0
    total_bytes = 0
    continuation = None
    while True:
        kwargs: dict[str, object] = {"Bucket": S3_BUCKET, "Prefix": prefix, "MaxKeys": 1000}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = _s3_client().list_objects_v2(**kwargs)
        contents = response.get("Contents") or []
        object_count += len(contents)
        total_bytes += sum(int(item.get("Size") or 0) for item in contents)
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
    return {"backend": "s3", "object_count": object_count, "total_bytes": total_bytes}


def list_organization_objects(*, maximum: int = 20_000) -> list[dict[str, int | str]]:
    if STORAGE_BACKEND != "s3":
        return []
    prefix = f"organizations/{current_organization_id()}/"
    items: list[dict[str, int | str]] = []
    continuation = None
    while len(items) < maximum:
        kwargs: dict[str, object] = {"Bucket": S3_BUCKET, "Prefix": prefix, "MaxKeys": min(1000, maximum - len(items))}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = _s3_client().list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            items.append({
                "reference": _reference(S3_BUCKET, str(item["Key"])),
                "size_bytes": int(item.get("Size") or 0),
                "last_modified": item.get("LastModified").isoformat() if item.get("LastModified") else "",
            })
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
    return items


def quarantine_objects(references: list[str], request_id: str) -> list[tuple[str, str]]:
    if STORAGE_BACKEND != "s3":
        raise RuntimeError("Object quarantine is only available for S3 storage.")
    moved: list[tuple[str, str]] = []
    try:
        for reference in references:
            bucket, source_key = owned_object_parts(reference)
            target_key = str(PurePosixPath(
                "organizations",
                str(current_organization_id()),
                ".privacy-trash",
                _safe_name(request_id),
                hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:16] + "-" + Path(source_key).name,
            ))
            _s3_client().copy_object(
                Bucket=bucket,
                Key=target_key,
                CopySource={"Bucket": bucket, "Key": source_key},
                MetadataDirective="COPY",
            )
            _s3_client().delete_object(Bucket=bucket, Key=source_key)
            moved.append((reference, _reference(bucket, target_key)))
        return moved
    except Exception:
        restore_quarantined_objects(moved)
        raise


def restore_quarantined_objects(moved: list[tuple[str, str]]) -> None:
    for original, quarantine in reversed(moved):
        bucket, original_key = owned_object_parts(original)
        _, quarantine_key = owned_object_parts(quarantine)
        _s3_client().copy_object(
            Bucket=bucket,
            Key=original_key,
            CopySource={"Bucket": bucket, "Key": quarantine_key},
            MetadataDirective="COPY",
        )
        _s3_client().delete_object(Bucket=bucket, Key=quarantine_key)


def purge_quarantined_objects(moved: list[tuple[str, str]]) -> None:
    for _, quarantine in moved:
        try:
            bucket, key = owned_object_parts(quarantine)
            _s3_client().delete_object(Bucket=bucket, Key=key)
        except Exception:
            continue


def reference_in_category(reference: str, category: str) -> bool:
    expected = PurePosixPath(category.strip("/"))
    if ".." in expected.parts:
        return False
    if is_object_reference(reference):
        try:
            _, key = owned_object_parts(reference)
        except ValueError:
            return False
        prefix = PurePosixPath("organizations", str(current_organization_id()), expected)
        key_path = PurePosixPath(key)
        return key_path.parts[:len(prefix.parts)] == prefix.parts
    try:
        resolved = Path(reference).resolve()
        resolved.relative_to((DATA_DIR / Path(*expected.parts)).resolve())
        return resolved.is_file()
    except (ValueError, OSError):
        return False
