from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


FILE_COLUMNS: dict[str, tuple[str, ...]] = {
    "course_packages": ("cover_path",),
    "courses": ("cover_path",),
    "course_materials": ("source_path", "preview_path"),
    "projects": ("file_path",),
    "submission_versions": ("project_file_path",),
    "assets": ("file_path",),
    "video_tasks": ("source_image_path", "file_path"),
    "moderation_logs": ("resource_path",),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _configure_target(target_url: str) -> None:
    if target_url:
        os.environ["CODERAI_DATABASE_URL"] = target_url
    os.environ["CODERAI_CLOUD_MODE"] = "true"
    os.environ.setdefault("CODERAI_STORAGE_BACKEND", "s3")
    os.environ.setdefault("CODERAI_AUTO_CREATE_SCHEMA", "false")


def _upgrade_schema() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")


def bootstrap_admin(args: argparse.Namespace) -> int:
    _configure_target(args.target_url)
    _upgrade_schema()

    from fastapi import HTTPException
    from backend.app.auth import hash_password, validate_admin_password, validate_username
    from backend.app.db import SessionLocal
    from backend.app.models import Organization, User, now
    from backend.app.tenancy import organization_context, without_tenant_filter

    password = args.password or os.environ.get("CODERAI_BOOTSTRAP_PASSWORD", "")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("管理员初始密码: ")
    try:
        validate_admin_password(password)
        username = validate_username(args.username)
    except HTTPException as exc:
        message = exc.detail.get("message", str(exc.detail)) if isinstance(exc.detail, dict) else str(exc.detail)
        raise SystemExit(message) from exc

    code = args.organization_code.strip().lower()
    db = SessionLocal()
    try:
        with without_tenant_filter():
            organization = db.query(Organization).filter(Organization.code == code).first()
            if not organization:
                organization = Organization(
                    code=code,
                    name=args.organization_name.strip(),
                    active=True,
                    seat_limit=args.seat_limit,
                )
                db.add(organization)
                db.commit()
                db.refresh(organization)
        with organization_context(organization.id, organization.code):
            existing = db.query(User).filter(User.username.ilike(username)).first()
            if existing and not args.replace_password:
                raise SystemExit("管理员账号已存在；如需轮换密码，请增加 --replace-password。")
            if existing:
                if existing.role != "admin":
                    raise SystemExit("同名账号不是管理员，不能由引导命令覆盖。")
                existing.password_hash = hash_password(password)
                existing.password_change_required = False
                existing.credential_version = max(1, existing.credential_version or 1) + 1
                existing.active = True
                account = existing
            else:
                account = User(
                    name=args.name.strip(),
                    role="admin",
                    username=username,
                    password_hash=hash_password(password),
                    password_change_required=False,
                    credential_version=1,
                    registered_at=now(),
                    age_level="",
                    active=True,
                )
                db.add(account)
            db.commit()
            db.refresh(account)
        print(json.dumps({
            "organization": {"id": organization.id, "code": organization.code, "seat_limit": organization.seat_limit},
            "admin": {"id": account.id, "username": account.username, "name": account.name},
        }, ensure_ascii=False))
        return 0
    finally:
        db.close()


def _source_backup(sqlite_path: Path, data_dir: Path, backup_dir: Path) -> dict[str, str]:
    import sqlite3

    backup_dir.mkdir(parents=True, exist_ok=True)
    database_backup = backup_dir / "coderai.db"
    source = sqlite3.connect(str(sqlite_path))
    target = sqlite3.connect(str(database_backup))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    inventory = backup_dir / "file-inventory.json"
    files = {
        path.relative_to(data_dir).as_posix(): {"size": path.stat().st_size, "sha256": _sha256(path)}
        for path in data_dir.rglob("*")
        if path.is_file() and path.resolve() != sqlite_path.resolve()
    }
    inventory.write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"database": str(database_backup), "database_sha256": _sha256(database_backup), "inventory": str(inventory)}


def migrate_cloud(args: argparse.Namespace) -> int:
    sqlite_path = Path(args.sqlite).resolve()
    data_dir = Path(args.data_dir).resolve()
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite 数据库不存在：{sqlite_path}")
    sqlite_path.relative_to(data_dir)

    lock_path = data_dir / ".cloud-migration.lock"
    readonly_path = data_dir / ".cloud-migrated-readonly"
    if readonly_path.exists() and not args.force:
        raise SystemExit("本地数据已经标记为完成云迁移；增加 --force 才能重新执行。")
    lock_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")

    uploaded_references: list[str] = []
    manifest: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(sqlite_path),
        "source_database_sha256": _sha256(sqlite_path),
        "organization_code": args.organization_code.strip().lower(),
        "tables": {},
        "files": [],
        "ai_keys_migrated": False,
        "rollback_objects": uploaded_references,
    }
    manifest_path = Path(args.manifest).resolve()
    backup_dir = manifest_path.parent / f"pre-migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    manifest["source_backup"] = _source_backup(sqlite_path, data_dir, backup_dir)
    source_engine = None

    try:
        _configure_target(args.target_url)
        _upgrade_schema()
        from sqlalchemy import Integer, MetaData, create_engine, func, select, text
        from backend.app.db import Base, engine as target_engine
        from backend.app.models import Organization
        from backend.app.storage import delete_object, put_file, read_bytes
        from backend.app.tenancy import organization_context, without_tenant_filter

        source_engine = create_engine(f"sqlite:///{sqlite_path}")
        source_meta = MetaData()
        source_meta.reflect(bind=source_engine)
        source_tables = source_meta.tables

        with target_engine.begin() as connection:
            with without_tenant_filter():
                organization = connection.execute(
                    select(Organization.__table__).where(Organization.code == manifest["organization_code"])
                ).mappings().first()
                if organization:
                    organization_id = int(organization["id"])
                    connection.execute(
                        Organization.__table__.update().where(Organization.id == organization_id).values(
                            name=args.organization_name,
                            active=True,
                            seat_limit=args.seat_limit,
                        )
                    )
                else:
                    result = connection.execute(Organization.__table__.insert().values(
                        code=manifest["organization_code"],
                        name=args.organization_name,
                        active=True,
                        seat_limit=args.seat_limit,
                    ).returning(Organization.id))
                    organization_id = int(result.scalar_one())
            manifest["organization_id"] = organization_id

        target_tenant_tables = [
            table for table in Base.metadata.sorted_tables if "organization_id" in table.c
        ]
        with target_engine.begin() as connection:
            foreign_rows = sum(
                int(connection.execute(
                    select(func.count()).select_from(table).where(table.c.organization_id != organization_id)
                ).scalar_one())
                for table in target_tenant_tables
            )
            if foreign_rows:
                raise RuntimeError(
                    "首轮 SQLite 迁移要求空的云端业务数据库；检测到其他机构数据，已停止以避免主键冲突。"
                )
            existing = sum(
                int(connection.execute(select(table.c.organization_id).where(table.c.organization_id == organization_id).limit(1)).first() is not None)
                for table in target_tenant_tables
            )
            if existing and not args.replace:
                raise RuntimeError("目标机构已有业务数据；请使用空机构或增加 --replace。")
            if args.replace:
                for table in reversed(target_tenant_tables):
                    connection.execute(table.delete().where(table.c.organization_id == organization_id))

        source_rows: dict[str, list[dict[str, Any]]] = {}
        with source_engine.connect() as source_connection:
            for table in target_tenant_tables:
                source_table = source_tables.get(table.name)
                if source_table is None:
                    continue
                rows = [dict(row) for row in source_connection.execute(select(source_table)).mappings()]
                source_organizations = {
                    int(row.get("organization_id") or 1)
                    for row in rows
                }
                if len(source_organizations) > 1:
                    raise RuntimeError(
                        f"本地表 {table.name} 包含多个机构，不能合并到单个试点机构。"
                    )
                source_rows[table.name] = rows

        file_map: dict[str, str] = {}
        with organization_context(organization_id, manifest["organization_code"]):
            for table_name, rows in source_rows.items():
                for row in rows:
                    for column_name in FILE_COLUMNS.get(table_name, ()):
                        raw = str(row.get(column_name) or "").strip()
                        if not raw or raw.startswith(("http://", "https://", "object://")):
                            continue
                        if raw in file_map:
                            row[column_name] = file_map[raw]
                            continue
                        source_path = Path(raw).resolve()
                        source_path.relative_to(data_dir)
                        if not source_path.is_file() or source_path.is_symlink():
                            raise RuntimeError(f"记录引用的文件缺失：{table_name}.{column_name} -> {raw}")
                        relative = source_path.relative_to(data_dir)
                        category = f"migration/{table_name}/{row.get('id', 'record')}/{column_name}"
                        reference = put_file(
                            category,
                            source_path,
                            filename=source_path.name,
                            stable_name=source_path.name,
                        ) if not args.dry_run else f"dry-run://{relative.as_posix()}"
                        source_hash = _sha256(source_path)
                        if not args.dry_run:
                            target_hash = hashlib.sha256(read_bytes(reference)).hexdigest()
                            if target_hash != source_hash:
                                delete_object(reference)
                                raise RuntimeError(f"对象上传校验失败：{relative.as_posix()}")
                            uploaded_references.append(reference)
                        file_map[raw] = reference
                        row[column_name] = reference
                        manifest["files"].append({
                            "source": relative.as_posix(),
                            "reference": reference,
                            "size": source_path.stat().st_size,
                            "sha256": source_hash,
                        })

        if not args.dry_run:
            with target_engine.begin() as connection:
                for table in target_tenant_tables:
                    rows = source_rows.get(table.name, [])
                    prepared: list[dict[str, Any]] = []
                    allowed = set(table.c.keys())
                    for index, source_row in enumerate(rows, start=1):
                        row = {key: value for key, value in source_row.items() if key in allowed}
                        row["organization_id"] = organization_id
                        if table.name == "app_settings":
                            key = str(row.get("key") or "")
                            row["id"] = key if organization_id == 1 else f"{organization_id}:{key}"
                        elif "id" in table.c and "id" not in row:
                            row["id"] = index
                        if table.name == "ai_providers":
                            row["api_key"] = ""
                            row["enabled"] = False
                            row["last_test_status"] = "reentry_required"
                            row["last_test_message"] = "云迁移后请重新录入机构 API Key。"
                        prepared.append(row)
                    if prepared:
                        connection.execute(table.insert(), prepared)
                    manifest["tables"][table.name] = {"source": len(rows), "target": len(prepared)}

                if target_engine.dialect.name == "postgresql":
                    for table in target_tenant_tables:
                        if "id" not in table.c or not isinstance(table.c.id.type, Integer):
                            continue
                        connection.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {table.name}), 1), true)"
                        ))

                for table in target_tenant_tables:
                    expected = len(source_rows.get(table.name, []))
                    actual = int(connection.execute(
                        select(func.count()).select_from(table).where(table.c.organization_id == organization_id)
                    ).scalar_one())
                    if actual != expected:
                        raise RuntimeError(f"迁移数量校验失败：{table.name}，源 {expected}，目标 {actual}")
                    manifest["tables"].setdefault(table.name, {})["verified"] = actual

        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["status"] = "dry_run" if args.dry_run else "complete"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        if not args.dry_run:
            readonly_path.write_text(json.dumps({
                "migrated_at": manifest["completed_at"],
                "manifest": str(manifest_path),
                "retain_until": args.retain_until,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": manifest["status"], "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0
    except Exception:
        manifest["status"] = "failed"
        manifest["failed_at"] = datetime.now(timezone.utc).isoformat()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        try:
            from backend.app.storage import delete_object
            from backend.app.tenancy import organization_context

            with organization_context(
                int(manifest.get("organization_id") or 1),
                str(manifest.get("organization_code") or "coderai-pilot"),
            ):
                for reference in reversed(uploaded_references):
                    try:
                        delete_object(reference)
                    except Exception as cleanup_error:
                        manifest.setdefault("rollback_errors", []).append(str(cleanup_error))
        finally:
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
        raise
    finally:
        if source_engine is not None:
            source_engine.dispose()
        lock_path.unlink(missing_ok=True)


def generate_secret_key(_: argparse.Namespace) -> int:
    print(base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"))
    return 0


def rotate_secrets(args: argparse.Namespace) -> int:
    _configure_target(args.target_url)
    from backend.app.db import SessionLocal
    from backend.app.secrets import master_key_fingerprint, rotate_provider_secrets

    db = SessionLocal()
    try:
        count = rotate_provider_secrets(db)
        print(json.dumps({"rotated": count, "active_key_fingerprint": master_key_fingerprint()}))
        return 0
    finally:
        db.close()


def unfreeze_local(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).resolve()
    for name in (".cloud-migration.lock", ".cloud-migrated-readonly"):
        (data_dir / name).unlink(missing_ok=True)
    print(json.dumps({"writable": True, "data_dir": str(data_dir)}, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="coderai-admin")
    commands = root.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap-admin", help="创建或轮换首个机构管理员")
    bootstrap.add_argument("--target-url", default=os.environ.get("CODERAI_DATABASE_URL", ""))
    bootstrap.add_argument("--organization-code", default="coderai-pilot")
    bootstrap.add_argument("--organization-name", default="CoderAI 试点机构")
    bootstrap.add_argument("--seat-limit", type=int, default=50)
    bootstrap.add_argument("--username", default="admin")
    bootstrap.add_argument("--name", default="机构管理员")
    bootstrap.add_argument("--password", default="")
    bootstrap.add_argument("--replace-password", action="store_true")
    bootstrap.set_defaults(handler=bootstrap_admin)

    migrate = commands.add_parser("migrate-cloud", help="一次性迁移 SQLite 和本地文件到云端")
    migrate.add_argument("--sqlite", required=True)
    migrate.add_argument("--data-dir", required=True)
    migrate.add_argument("--target-url", default=os.environ.get("CODERAI_DATABASE_URL", ""))
    migrate.add_argument("--organization-code", default="coderai-pilot")
    migrate.add_argument("--organization-name", default="CoderAI 试点机构")
    migrate.add_argument("--seat-limit", type=int, default=50)
    migrate.add_argument("--manifest", default="migration-output/cloud-migration-manifest.json")
    migrate.add_argument("--retain-until", default="迁移完成后30天")
    migrate.add_argument("--replace", action="store_true")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--force", action="store_true")
    migrate.set_defaults(handler=migrate_cloud)

    key = commands.add_parser("generate-secret-key", help="生成 AES-GCM 服务器主密钥")
    key.set_defaults(handler=generate_secret_key)

    rotate = commands.add_parser("rotate-secrets", help="使用当前主密钥重加密机构 API Key")
    rotate.add_argument("--target-url", default=os.environ.get("CODERAI_DATABASE_URL", ""))
    rotate.set_defaults(handler=rotate_secrets)

    unfreeze = commands.add_parser("unfreeze-local", help="回滚云端时解除本地只读标记")
    unfreeze.add_argument("--data-dir", required=True)
    unfreeze.set_defaults(handler=unfreeze_local)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
