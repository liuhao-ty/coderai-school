from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import secrets
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import Base, DATA_DIR
from backend.app.models import now
from backend.app.storage import is_object_reference, read_bytes, reference_name
from backend.app.tenancy import current_organization_code, current_organization_id


FILE_COLUMNS: dict[str, tuple[str, ...]] = {
    "course_packages": ("cover_path",),
    "courses": ("cover_path",),
    "course_materials": ("source_path", "preview_path"),
    "projects": ("file_path",),
    "submission_versions": ("project_file_path",),
    "submission_attachments": ("file_path",),
    "agent_artifacts": ("file_path",),
    "assets": ("file_path",),
    "video_tasks": ("source_image_path", "file_path"),
    "moderation_logs": ("resource_path",),
}

EXCLUDED_TABLES = {"auth_login_attempts", "teacher_sessions", "student_sessions"}


def _value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def build_organization_export(db: Session) -> Path:
    export_dir = DATA_DIR / "exports" / "organizations"
    export_dir.mkdir(parents=True, exist_ok=True)
    organization_id = current_organization_id()
    organization_code = current_organization_code()
    path = export_dir / f"coderai-{organization_code}-{now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.zip"
    datasets: dict[str, list[dict]] = {}
    object_references: dict[str, str] = {}

    for table in Base.metadata.sorted_tables:
        if table.name in EXCLUDED_TABLES or "organization_id" not in table.c:
            continue
        rows = db.execute(select(table).where(table.c.organization_id == organization_id)).mappings().all()
        payload_rows: list[dict] = []
        for source in rows:
            row = {key: _value(value) for key, value in dict(source).items()}
            if table.name == "users":
                row["password_hash"] = ""
            elif table.name == "ai_providers":
                row["api_key"] = ""
            elif table.name == "app_settings" and row.get("key") in {
                "teacher_password_hash",
                "app_auth_secret",
                "commercial_license",
            }:
                row["value"] = ""
            elif table.name == "guardian_consents":
                row["guardian_contact_encrypted"] = ""
            for column in FILE_COLUMNS.get(table.name, ()):
                reference = str(row.get(column) or "")
                if is_object_reference(reference):
                    archive_name = object_references.setdefault(
                        reference,
                        f"files/{hashlib.sha256(reference.encode('utf-8')).hexdigest()[:20]}-{reference_name(reference)}",
                    )
                    row[column] = archive_name
            payload_rows.append(row)
        datasets[table.name] = payload_rows

    manifest = {
        "schema_version": "cloud-export-1.0",
        "organization": {"id": organization_id, "code": organization_code},
        "exported_at": now().isoformat(),
        "tables": {name: len(rows) for name, rows in datasets.items()},
        "file_count": len(object_references),
        "redactions": [
            "用户密码哈希",
            "登录会话和失败记录",
            "AI API Key",
            "服务器签名密钥和许可证",
            "监护人联系方式密文",
        ],
        "restore_supported": False,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(
            "README.md",
            "# CoderAI 机构数据导出\n\n此文件用于机构数据查阅与迁移核验，不是平台数据库恢复包。全平台恢复仅由运维执行。\n",
        )
        for name, rows in datasets.items():
            archive.writestr(f"data/{name}.json", json.dumps(rows, ensure_ascii=False, indent=2))
        for reference, archive_name in object_references.items():
            archive.writestr(archive_name, read_bytes(reference))
    return path
