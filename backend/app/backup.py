from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.app.auth import PASSWORD_CHANGE_REQUIRED_KEY, PASSWORD_SETTING_KEY, SECRET_SETTING_KEY, require_admin
from backend.app.audit import record_teacher_audit_to_sqlite
from backend.app.db import CLOUD_MODE, DATA_DIR, DB_PATH, get_db, init_db
from backend.app.licensing import LICENSE_SETTING_KEY
from backend.app.models import TeacherSession
from backend.app.audit import record_teacher_audit
from backend.app.cloud_export import build_organization_export
from sqlalchemy.orm import Session


PACKAGE_VERSION = "1.0"
SUPPORTED_VERSIONS = {"0.9", PACKAGE_VERSION}
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 20_000
MANAGED_DIRECTORIES = ("projects", "outputs", "assets", "curriculum", "ai_inputs", "plugins", "acceptance")
EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
BACKUP_LOCK = threading.Lock()

router = APIRouter(prefix="/api/system/backups", dependencies=[Depends(require_admin)])


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_zip_member(archive: zipfile.ZipFile, member: str) -> str:
    digest = hashlib.sha256()
    with archive.open(member) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise HTTPException(status_code=400, detail={"code": "BACKUP_PATH_INVALID", "message": f"备份包含不安全路径：{name}"})
    return str(path)


def sqlite_backup(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def table_counts(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(str(db_path))
    try:
        tables = [
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}
    finally:
        connection.close()


def sanitize_database(source_path: Path, target_path: Path) -> dict[str, int]:
    sqlite_backup(source_path, target_path)
    connection = sqlite3.connect(str(target_path))
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "ai_providers" in tables:
            connection.execute("UPDATE ai_providers SET api_key = ''")
        if "teacher_sessions" in tables:
            connection.execute("DELETE FROM teacher_sessions")
        if "student_sessions" in tables:
            connection.execute("DELETE FROM student_sessions")
        if "guardian_consents" in tables:
            connection.execute("UPDATE guardian_consents SET guardian_contact_encrypted = ''")
        connection.commit()
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("备份数据库完整性检查失败")
    finally:
        connection.close()
    return table_counts(target_path)


def managed_files() -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    data_root = DATA_DIR.resolve()
    for directory_name in MANAGED_DIRECTORIES:
        root = data_root / directory_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(data_root).as_posix()
            except ValueError:
                continue
            result.append((resolved, f"files/{relative}"))
    return sorted(result, key=lambda item: item[1])


def build_backup_package() -> Path:
    timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d-%H%M%S")
    package_path = EXPORT_DIR / f"coderai-backup-{timestamp}.zip"
    with tempfile.TemporaryDirectory(dir=EXPORT_DIR) as temp_name:
        temp_dir = Path(temp_name)
        sanitized_db = temp_dir / "coderai.db"
        counts = sanitize_database(DB_PATH, sanitized_db)
        file_entries = []
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(sanitized_db, "database/coderai.db")
            for path, member in managed_files():
                stat = path.stat()
                archive.write(path, member)
                file_entries.append({"path": member, "size_bytes": stat.st_size, "sha256": sha256_path(path)})
            manifest = {
                "package_version": PACKAGE_VERSION,
                "app_version": "0.1.0",
                "created_at": datetime.now(BEIJING_TZ).isoformat(),
                "database": {
                    "path": "database/coderai.db",
                    "size_bytes": sanitized_db.stat().st_size,
                    "sha256": sha256_path(sanitized_db),
                    "tables": counts,
                },
                "files": file_entries,
                "redactions": ["ai_providers.api_key", "teacher_sessions", "student_sessions", "guardian_consents.guardian_contact_encrypted"],
                "managed_directories": list(MANAGED_DIRECTORIES),
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return package_path


async def save_upload(upload: UploadFile) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="coderai-restore-", suffix=".zip", dir=EXPORT_DIR, delete=False)
    path = Path(handle.name)
    total = 0
    try:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_PACKAGE_BYTES:
                raise HTTPException(status_code=413, detail={"code": "BACKUP_TOO_LARGE", "message": "备份包不能超过 2 GB。"})
            handle.write(chunk)
        handle.close()
        if total == 0:
            raise HTTPException(status_code=400, detail={"code": "BACKUP_EMPTY", "message": "备份包为空。"})
        return path
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise


def migrate_manifest(raw: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    version = str(raw.get("package_version") or "0.9")
    if version not in SUPPORTED_VERSIONS:
        raise HTTPException(
            status_code=400,
            detail={"code": "BACKUP_VERSION_UNSUPPORTED", "message": f"不支持备份版本 {version}，当前支持 {sorted(SUPPORTED_VERSIONS)}。"},
        )
    manifest = dict(raw)
    migrated_from = None
    if version == "0.9":
        migrated_from = version
        manifest["package_version"] = PACKAGE_VERSION
        manifest["redactions"] = sorted(
            set(manifest.get("redactions") or [])
            | {"ai_providers.api_key", "teacher_sessions", "student_sessions", "guardian_consents.guardian_contact_encrypted"}
        )
        manifest.setdefault("managed_directories", list(MANAGED_DIRECTORIES))
    return manifest, migrated_from


def inspect_package(package_path: Path, extract_database_to: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(package_path, "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail={"code": "BACKUP_ZIP_INVALID", "message": "文件不是有效的 CoderAI 压缩备份包。"})
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_MEMBERS:
            raise HTTPException(status_code=400, detail={"code": "BACKUP_MEMBER_LIMIT", "message": "备份包文件数量超过限制。"})
        total_expanded = 0
        names = set()
        for info in infos:
            name = safe_member_name(info.filename)
            names.add(name)
            total_expanded += info.file_size
            if total_expanded > MAX_EXPANDED_BYTES:
                raise HTTPException(status_code=400, detail={"code": "BACKUP_EXPANDED_TOO_LARGE", "message": "备份包解压后超过 20 GB。"})
            if info.file_size > 1024 * 1024 * 1024 and info.compress_size and info.file_size / info.compress_size > 200:
                raise HTTPException(status_code=400, detail={"code": "BACKUP_COMPRESSION_SUSPICIOUS", "message": "备份包压缩比例异常。"})
        if "manifest.json" not in names:
            raise HTTPException(status_code=400, detail={"code": "BACKUP_MANIFEST_MISSING", "message": "备份包缺少 manifest.json。"})
        try:
            raw_manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail={"code": "BACKUP_MANIFEST_INVALID", "message": "备份清单格式不正确。"})
        manifest, migrated_from = migrate_manifest(raw_manifest)
        database = manifest.get("database") or {}
        database_member = safe_member_name(str(database.get("path") or "database/coderai.db"))
        if database_member not in names:
            raise HTTPException(status_code=400, detail={"code": "BACKUP_DATABASE_MISSING", "message": "备份包缺少 SQLite 数据库。"})
        if database.get("sha256") and sha256_zip_member(archive, database_member) != database["sha256"]:
            raise HTTPException(status_code=400, detail={"code": "BACKUP_DATABASE_HASH_INVALID", "message": "备份数据库校验失败。"})
        files = manifest.get("files") or []
        expected_members = {database_member, "manifest.json"}
        for item in files:
            member = safe_member_name(str(item.get("path") or ""))
            if not member.startswith("files/") or member not in names:
                raise HTTPException(status_code=400, detail={"code": "BACKUP_FILE_MISSING", "message": f"备份文件缺失：{member}"})
            if item.get("sha256") and sha256_zip_member(archive, member) != item["sha256"]:
                raise HTTPException(status_code=400, detail={"code": "BACKUP_FILE_HASH_INVALID", "message": f"备份文件校验失败：{member}"})
            expected_members.add(member)
        unexpected = [name for name in names if name not in expected_members and not name.endswith("/")]
        if unexpected:
            raise HTTPException(status_code=400, detail={"code": "BACKUP_UNLISTED_FILE", "message": f"备份包含未登记文件：{unexpected[0]}"})

        database_target = extract_database_to
        temporary_database = None
        if database_target is None:
            temporary_database = tempfile.NamedTemporaryFile(prefix="coderai-check-", suffix=".db", dir=EXPORT_DIR, delete=False)
            temporary_database.close()
            database_target = Path(temporary_database.name)
        with archive.open(database_member) as source, database_target.open("wb") as target:
            shutil.copyfileobj(source, target)
        connection = sqlite3.connect(str(database_target))
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()
        if temporary_database:
            database_target.unlink(missing_ok=True)
        if integrity != "ok":
            raise HTTPException(status_code=400, detail={"code": "BACKUP_DATABASE_INVALID", "message": "备份数据库完整性检查未通过。"})

        conflicts = 0
        file_bytes = 0
        for item in files:
            member = str(item["path"])
            relative = PurePosixPath(member).relative_to("files")
            target = DATA_DIR.joinpath(*relative.parts)
            if target.exists():
                conflicts += 1
            file_bytes += int(item.get("size_bytes") or 0)
        preview = {
            "valid": True,
            "package_version": manifest["package_version"],
            "migrated_from": migrated_from,
            "created_at": manifest.get("created_at") or "",
            "database_tables": database.get("tables") or {},
            "file_count": len(files),
            "file_bytes": file_bytes,
            "existing_file_conflicts": conflicts,
            "redactions": manifest.get("redactions") or [],
            "warnings": [
                "API Key 不包含在备份中；恢复时保留本机已有密钥，没有本机密钥时需重新配置。",
                "恢复会替换数据库；本机管理员密码、当前教师登录令牌和当前设备许可证保留，学生需重新登录。",
            ],
        }
        return manifest, preview


def preserve_local_secrets() -> tuple[dict[str, str], dict[str, str], list[tuple]]:
    connection = sqlite3.connect(str(DB_PATH))
    try:
        provider_keys = {}
        settings = {}
        teacher_sessions = []
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "ai_providers" in tables:
            provider_keys = {str(row[0]): str(row[1] or "") for row in connection.execute("SELECT provider_type, api_key FROM ai_providers")}
        if "app_settings" in tables:
            settings = {
                str(row[0]): str(row[1]) for row in connection.execute(
                    "SELECT key, value FROM app_settings WHERE key IN (?, ?, ?, ?)",
                    (PASSWORD_SETTING_KEY, PASSWORD_CHANGE_REQUIRED_KEY, SECRET_SETTING_KEY, LICENSE_SETTING_KEY),
                )
            }
            settings.setdefault(
                LICENSE_SETTING_KEY,
                json.dumps({"license_key": "", "saved_at": datetime.now(BEIJING_TZ).isoformat()}, separators=(",", ":")),
            )
        if "teacher_sessions" in tables:
            session_columns = {row[1] for row in connection.execute("PRAGMA table_info(teacher_sessions)").fetchall()}
            if "user_id" in session_columns:
                organization_sql = "organization_id" if "organization_id" in session_columns else "1"
                teacher_sessions = connection.execute(
                    f"SELECT id, {organization_sql}, user_id, token_hash, refresh_token_hash, device_name, created_at, last_seen_at, access_expires_at, expires_at, revoked_at FROM teacher_sessions"
                ).fetchall()
            else:
                teacher_sessions = [
                    (row[0], 1, None, *row[1:])
                    for row in connection.execute(
                        "SELECT id, token_hash, refresh_token_hash, device_name, created_at, last_seen_at, access_expires_at, expires_at, revoked_at FROM teacher_sessions"
                    ).fetchall()
                ]
        return provider_keys, settings, teacher_sessions
    finally:
        connection.close()


def restore_local_secrets(provider_keys: dict[str, str], settings: dict[str, str], teacher_sessions: list[tuple]) -> None:
    connection = sqlite3.connect(str(DB_PATH))
    try:
        for provider_type, api_key in provider_keys.items():
            if api_key:
                connection.execute("UPDATE ai_providers SET api_key = ? WHERE provider_type = ?", (api_key, provider_type))
        for key, value in settings.items():
            updated = connection.execute("UPDATE app_settings SET value = ? WHERE key = ?", (value, key))
            if updated.rowcount == 0:
                connection.execute(
                    "INSERT INTO app_settings(key, value, updated_at) VALUES(?, ?, ?)",
                    (key, value, datetime.now(BEIJING_TZ).replace(tzinfo=None).isoformat()),
                )
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()} if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone() else set()
        if {"username", "password_hash", "password_change_required"}.issubset(user_columns):
            admin_hash = settings.get(PASSWORD_SETTING_KEY)
            if admin_hash:
                connection.execute(
                    "UPDATE users SET password_hash = ?, password_change_required = ? WHERE lower(username) = 'admin'",
                    (admin_hash, 1 if settings.get(PASSWORD_CHANGE_REQUIRED_KEY) == "true" else 0),
                )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS teacher_sessions (
                id INTEGER PRIMARY KEY,
                organization_id INTEGER NOT NULL DEFAULT 1,
                user_id INTEGER,
                token_hash VARCHAR(64) UNIQUE NOT NULL,
                refresh_token_hash VARCHAR(64) UNIQUE NOT NULL,
                device_name VARCHAR(160) DEFAULT '此设备',
                created_at DATETIME,
                last_seen_at DATETIME,
                access_expires_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL,
                revoked_at DATETIME
            )
            """
        )
        teacher_session_columns = {row[1] for row in connection.execute("PRAGMA table_info(teacher_sessions)").fetchall()}
        if "organization_id" not in teacher_session_columns:
            connection.execute("ALTER TABLE teacher_sessions ADD COLUMN organization_id INTEGER NOT NULL DEFAULT 1")
        if "user_id" not in teacher_session_columns:
            connection.execute("ALTER TABLE teacher_sessions ADD COLUMN user_id INTEGER")
        connection.execute("DELETE FROM teacher_sessions")
        connection.executemany(
            "INSERT INTO teacher_sessions(id, organization_id, user_id, token_hash, refresh_token_hash, device_name, created_at, last_seen_at, access_expires_at, expires_at, revoked_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            teacher_sessions,
        )
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='student_sessions'").fetchone():
            connection.execute("DELETE FROM student_sessions")
        connection.commit()
    finally:
        connection.close()


def restore_files(archive: zipfile.ZipFile, manifest: dict[str, Any], conflict_strategy: str) -> int:
    restored = 0
    if conflict_strategy == "replace":
        for directory_name in MANAGED_DIRECTORIES:
            shutil.rmtree(DATA_DIR / directory_name, ignore_errors=True)
    for item in manifest.get("files") or []:
        member = safe_member_name(str(item["path"]))
        relative = PurePosixPath(member).relative_to("files")
        if not relative.parts or relative.parts[0] not in MANAGED_DIRECTORIES:
            raise RuntimeError(f"备份文件不在受管目录：{member}")
        target = DATA_DIR.joinpath(*relative.parts)
        if conflict_strategy == "keep_existing" and target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        restored += 1
    return restored


def restore_package(package_path: Path, conflict_strategy: str) -> dict[str, Any]:
    if conflict_strategy not in {"replace", "keep_existing"}:
        raise HTTPException(status_code=400, detail={"code": "BACKUP_CONFLICT_STRATEGY_INVALID", "message": "冲突策略必须是 replace 或 keep_existing。"})
    with tempfile.TemporaryDirectory(dir=EXPORT_DIR) as temp_name:
        temp_dir = Path(temp_name)
        package_db = temp_dir / "package.db"
        manifest, preview = inspect_package(package_path, package_db)
        rollback_db = temp_dir / "rollback.db"
        rollback_files = temp_dir / "files"
        sqlite_backup(DB_PATH, rollback_db)
        for directory_name in MANAGED_DIRECTORIES:
            source = DATA_DIR / directory_name
            if source.exists():
                shutil.copytree(source, rollback_files / directory_name)
        provider_keys, security_settings, teacher_sessions = preserve_local_secrets()
        try:
            sqlite_backup(package_db, DB_PATH)
            restore_local_secrets(provider_keys, security_settings, teacher_sessions)
            with zipfile.ZipFile(package_path, "r") as archive:
                restored_files = restore_files(archive, manifest, conflict_strategy)
            init_db()
            return {"restored": True, "restored_files": restored_files, "conflict_strategy": conflict_strategy, "preview": preview}
        except Exception as exc:
            sqlite_backup(rollback_db, DB_PATH)
            for directory_name in MANAGED_DIRECTORIES:
                shutil.rmtree(DATA_DIR / directory_name, ignore_errors=True)
                source = rollback_files / directory_name
                if source.exists():
                    shutil.copytree(source, DATA_DIR / directory_name)
            init_db()
            raise HTTPException(
                status_code=500,
                detail={"code": "BACKUP_RESTORE_ROLLED_BACK", "message": f"恢复失败，已自动回滚：{exc}"},
            )


@router.get("/export")
def export_backup(
    background_tasks: BackgroundTasks,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not BACKUP_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={"code": "BACKUP_BUSY", "message": "另一个备份或恢复任务正在执行。"})
    try:
        package_path = build_organization_export(db) if CLOUD_MODE else build_backup_package()
    finally:
        BACKUP_LOCK.release()
    if CLOUD_MODE:
        record_teacher_audit(
            db,
            teacher,
            "organization.data_exported",
            target_type="organization_export",
            summary="导出本机构数据（不含凭据）",
            details={"restore_supported": False},
        )
    background_tasks.add_task(package_path.unlink, missing_ok=True)
    return FileResponse(package_path, media_type="application/zip", filename=package_path.name)


@router.post("/preflight")
async def preflight_backup(file: UploadFile = File(...)):
    if CLOUD_MODE:
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_RESTORE_REQUIRED", "message": "云端完整恢复仅允许平台运维执行。"})
    package_path = await save_upload(file)
    try:
        _, preview = inspect_package(package_path)
        return {"preview": preview}
    finally:
        package_path.unlink(missing_ok=True)


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    conflict_strategy: str = Form("replace"),
    teacher: TeacherSession = Depends(require_admin),
):
    if CLOUD_MODE:
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_RESTORE_REQUIRED", "message": "机构管理员不能覆盖云端数据库，请联系平台运维执行恢复。"})
    if not BACKUP_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={"code": "BACKUP_BUSY", "message": "另一个备份或恢复任务正在执行。"})
    package_path = await save_upload(file)
    try:
        result = restore_package(package_path, conflict_strategy)
        record_teacher_audit_to_sqlite(
            DB_PATH,
            teacher.id,
            "backup.package_restored",
            target_type="backup",
            summary="恢复完整压缩备份",
            details={
                "conflict_strategy": conflict_strategy,
                "restored_files": result.get("restored_files", 0),
            },
        )
        return result
    finally:
        package_path.unlink(missing_ok=True)
        BACKUP_LOCK.release()
