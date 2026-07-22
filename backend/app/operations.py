from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.auth import require_admin
from backend.app.db import CLOUD_MODE, DATA_DIR, DB_PATH, get_db
from backend.app.models import Asset, Course, CourseMaterial, CoursePackage, ModerationLog, Project, SubmissionVersion, VideoTask
from backend.app.storage import is_object_reference, list_organization_objects, object_exists, organization_storage_usage


LOG_DIR = DATA_DIR / "logs"
CACHE_DIR = DATA_DIR / "cache"
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
CONTENT_SCAN_DIRS = [DATA_DIR / "projects", DATA_DIR / "outputs", DATA_DIR / "assets" / "library"]
LOG_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(prefix="/api/system/operations", dependencies=[Depends(require_admin)])


class FileRepairRequest(BaseModel):
    project_ids: list[int] = Field(default_factory=list, max_length=500)
    asset_ids: list[int] = Field(default_factory=list, max_length=500)


def directory_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    count = 0
    size = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                count += 1
                size += item.stat().st_size
        except OSError:
            continue
    return count, size


def local_path(raw_path: str) -> Path | None:
    value = raw_path.strip()
    if not value or value.startswith(("http://", "https://")) or is_object_reference(value):
        return None
    return Path(value).resolve()


def is_managed(path: Path) -> bool:
    try:
        path.relative_to(DATA_DIR)
        return True
    except ValueError:
        return False


def safe_log_path(name: str) -> Path:
    if not name or Path(name).name != name:
        raise HTTPException(status_code=400, detail={"code": "LOG_NAME_INVALID", "message": "日志文件名不合法。"})
    candidate = (LOG_DIR / name).resolve()
    if candidate.parent != LOG_DIR.resolve() or not candidate.is_file():
        raise HTTPException(status_code=404, detail={"code": "LOG_NOT_FOUND", "message": "日志文件不存在。"})
    return candidate


def tail_text(path: Path, max_bytes: int = 256 * 1024) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        content = handle.read()
    return content.decode("utf-8", errors="replace")


def resolve_records(records: Iterable[Project | Asset]) -> set[Path]:
    result: set[Path] = set()
    for record in records:
        path = local_path(record.file_path)
        if path and is_managed(path):
            result.add(path)
    return result


@router.get("/storage")
def storage_statistics():
    if CLOUD_MODE:
        usage = organization_storage_usage()
        local_files, local_bytes = directory_stats(DATA_DIR)
        return {
            "data_dir": "cloud-object-storage",
            "storage_backend": usage["backend"],
            "total_files": usage["object_count"],
            "total_bytes": usage["total_bytes"],
            "categories": [
                {"key": "objects", "label": "机构对象文件", "file_count": usage["object_count"], "size_bytes": usage["total_bytes"]},
                {"key": "runtime", "label": "服务运行文件", "file_count": local_files, "size_bytes": local_bytes},
            ],
        }
    categories = []
    configured = [
        ("database", "数据库", DB_PATH),
        ("projects", "作品文件", DATA_DIR / "projects"),
        ("outputs", "AI 输出", DATA_DIR / "outputs"),
        ("assets", "课程素材", DATA_DIR / "assets"),
        ("ai_inputs", "AI 输入", DATA_DIR / "ai_inputs"),
        ("cache", "运行缓存", CACHE_DIR),
        ("logs", "运行日志", LOG_DIR),
        ("plugins", "插件数据", DATA_DIR / "plugins"),
        ("acceptance", "验收证据", DATA_DIR / "acceptance"),
    ]
    for key, label, path in configured:
        if path.is_file():
            file_count, size_bytes = 1, path.stat().st_size
        else:
            file_count, size_bytes = directory_stats(path)
        categories.append({"key": key, "label": label, "file_count": file_count, "size_bytes": size_bytes})
    total_files, total_bytes = directory_stats(DATA_DIR)
    return {"data_dir": str(DATA_DIR), "total_files": total_files, "total_bytes": total_bytes, "categories": categories}


@router.get("/logs")
def list_logs():
    logs = []
    for path in sorted(LOG_DIR.iterdir(), key=lambda item: item.stat().st_mtime if item.is_file() else 0, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        logs.append({
            "name": path.name,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, BEIJING_TZ).isoformat(),
        })
    return {"logs": logs[:100]}


@router.get("/logs/{name}")
def read_log(name: str):
    path = safe_log_path(name)
    return {"name": path.name, "size_bytes": path.stat().st_size, "content": tail_text(path)}


@router.post("/logs/{name}/clear")
def clear_log(name: str):
    path = safe_log_path(name)
    cleared_bytes = path.stat().st_size
    try:
        path.write_text("", encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=409, detail={"code": "LOG_CLEAR_FAILED", "message": f"日志正在使用，暂时无法清空：{exc}"})
    return {"name": path.name, "cleared_bytes": cleared_bytes}


@router.post("/cache/clear")
def clear_cache():
    files = [path for path in CACHE_DIR.rglob("*") if path.is_file()]
    cleared_bytes = 0
    cleared_files = 0
    for path in files:
        try:
            cleared_bytes += path.stat().st_size
            path.unlink()
            cleared_files += 1
        except OSError as exc:
            raise HTTPException(status_code=409, detail={"code": "CACHE_CLEAR_FAILED", "message": f"缓存清理失败：{exc}"})
    for path in sorted((item for item in CACHE_DIR.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    return {"cleared_files": cleared_files, "cleared_bytes": cleared_bytes}


def consistency_result(db: Session) -> dict:
    projects = db.query(Project).order_by(Project.id.asc()).all()
    assets = db.query(Asset).order_by(Asset.id.asc()).all()
    missing_projects = []
    missing_assets = []
    for project in projects:
        path = local_path(project.file_path)
        missing = bool(project.file_path and not project.file_path.startswith(("http://", "https://")) and not object_exists(project.file_path))
        if missing:
            missing_projects.append({
                "id": project.id, "title": project.title, "file_path": project.file_path,
                "managed": is_object_reference(project.file_path) or bool(path and is_managed(path)),
            })
    for asset in assets:
        path = local_path(asset.file_path)
        missing = bool(asset.file_path and not asset.file_path.startswith(("http://", "https://")) and not object_exists(asset.file_path))
        if missing:
            missing_assets.append({
                "id": asset.id, "title": asset.original_name or Path(asset.file_path).name,
                "file_path": asset.file_path,
                "managed": is_object_reference(asset.file_path) or bool(path and is_managed(path)),
            })

    referenced = resolve_records([*projects, *assets])
    referenced_objects = {
        record.file_path for record in [*projects, *assets] if is_object_reference(record.file_path)
    }
    for model, columns in (
        (CoursePackage, ("cover_path",)),
        (Course, ("cover_path",)),
        (CourseMaterial, ("source_path", "preview_path")),
        (SubmissionVersion, ("project_file_path",)),
        (VideoTask, ("source_image_path", "file_path")),
        (ModerationLog, ("resource_path",)),
    ):
        for record in db.query(model).all():
            referenced_objects.update(
                value for value in (str(getattr(record, column, "") or "") for column in columns) if is_object_reference(value)
            )
    orphan_files = []
    orphan_count = 0
    if CLOUD_MODE:
        for item in list_organization_objects():
            if item["reference"] in referenced_objects:
                continue
            orphan_count += 1
            if len(orphan_files) < 500:
                orphan_files.append({"file_path": item["reference"], "size_bytes": item["size_bytes"]})
    else:
        for root in CONTENT_SCAN_DIRS:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.resolve() in referenced:
                    continue
                orphan_count += 1
                if len(orphan_files) < 500:
                    orphan_files.append({"file_path": str(path.resolve()), "size_bytes": path.stat().st_size})
    return {
        "scanned_projects": len(projects),
        "scanned_assets": len(assets),
        "missing_projects": missing_projects,
        "missing_assets": missing_assets,
        "orphan_count": orphan_count,
        "orphan_files": orphan_files,
    }


@router.get("/files/scan")
def scan_file_consistency(db: Session = Depends(get_db)):
    return consistency_result(db)


@router.post("/files/repair")
def repair_missing_files(payload: FileRepairRequest, db: Session = Depends(get_db)):
    project_ids = list(dict.fromkeys(payload.project_ids))
    asset_ids = list(dict.fromkeys(payload.asset_ids))
    projects = db.query(Project).filter(Project.id.in_(project_ids)).all() if project_ids else []
    assets = db.query(Asset).filter(Asset.id.in_(asset_ids)).all() if asset_ids else []
    if {item.id for item in projects} != set(project_ids) or {item.id for item in assets} != set(asset_ids):
        raise HTTPException(status_code=404, detail={"code": "FILE_RECORD_NOT_FOUND", "message": "部分待修复记录不存在，未执行任何修改。"})
    for record in [*projects, *assets]:
        if not record.file_path or record.file_path.startswith(("http://", "https://")) or object_exists(record.file_path):
            raise HTTPException(status_code=409, detail={"code": "FILE_RECORD_NOT_MISSING", "message": "部分记录的文件并未缺失，未执行任何修改。"})
    for project in projects:
        project.file_path = ""
    for asset in assets:
        db.delete(asset)
    db.commit()
    return {
        "repaired_projects": len(projects),
        "removed_asset_records": len(assets),
        "scan": consistency_result(db),
    }
