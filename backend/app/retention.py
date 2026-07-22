from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.audit import record_teacher_audit
from backend.app.auth import require_admin
from backend.app.db import CLOUD_MODE, DATA_DIR, get_db
from backend.app.models import (
    Asset,
    ModerationLog,
    PrivacyPolicy,
    Project,
    RetentionException,
    RetentionRequest,
    SubmissionVersion,
    TaskSubmission,
    TeacherAuditLog,
    TeacherSession,
    UsageLog,
    User,
    VideoTask,
    WorkflowRun,
    now,
)
from backend.app.storage import (
    is_object_reference,
    purge_quarantined_objects,
    quarantine_objects,
    restore_quarantined_objects,
)


router = APIRouter(prefix="/api/privacy/retention", dependencies=[Depends(require_admin)], tags=["retention"])
FINAL_VIDEO_STATUSES = {"success", "failed", "timed_out", "download_failed", "expired", "canceled"}


class RetentionPreviewRequest(BaseModel):
    retention_days: int | None = Field(default=None, ge=30, le=3650)


class RetentionExceptionRequest(BaseModel):
    target_type: Literal["student", "record"]
    target_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=300)
    expires_at: datetime | None = None


class RetentionApprovalRequest(BaseModel):
    note: str = Field(default="", max_length=300)


class RetentionExecuteRequest(BaseModel):
    confirmation: str


def _current_retention_days(db: Session) -> int:
    policy = db.query(PrivacyPolicy).filter(PrivacyPolicy.status == "published").order_by(
        PrivacyPolicy.effective_at.desc(), PrivacyPolicy.id.desc()
    ).first()
    return max(30, min(3650, int(policy.retention_days if policy else 365)))


def _active_exception_keys(db: Session) -> set[str]:
    current = now()
    rows = db.query(RetentionException).filter(
        RetentionException.active.is_(True),
        (RetentionException.expires_at.is_(None)) | (RetentionException.expires_at > current),
    ).all()
    return {f"{item.target_type}:{item.target_id}" for item in rows}


def _excluded(exceptions: set[str], table: str, record_id: int, user_id: int | None = None) -> bool:
    return f"record:{table}:{record_id}" in exceptions or (user_id is not None and f"student:{user_id}" in exceptions)


def scan_retention(db: Session, retention_days: int | None = None) -> dict[str, Any]:
    days = retention_days or _current_retention_days(db)
    cutoff = now() - timedelta(days=days)
    exceptions = _active_exception_keys(db)
    submitted_project_ids = {int(item[0]) for item in db.query(TaskSubmission.project_id).distinct().all()}

    projects = [
        item for item in db.query(Project).filter(Project.updated_at <= cutoff).order_by(Project.id).all()
        if item.id not in submitted_project_ids and not _excluded(exceptions, "projects", item.id, item.user_id)
    ]
    project_ids = {item.id for item in projects}
    workflow_runs = [
        item for item in db.query(WorkflowRun).filter(WorkflowRun.created_at <= cutoff).order_by(WorkflowRun.id).all()
        if not _excluded(exceptions, "workflow_runs", item.id, item.user_id)
    ]
    video_tasks = [
        item for item in db.query(VideoTask).filter(VideoTask.updated_at <= cutoff, VideoTask.status.in_(FINAL_VIDEO_STATUSES)).order_by(VideoTask.id).all()
        if not _excluded(exceptions, "video_tasks", item.id, item.user_id)
    ]
    usage_logs = [
        item for item in db.query(UsageLog).filter(UsageLog.created_at <= cutoff).order_by(UsageLog.id).all()
        if not _excluded(exceptions, "usage_logs", item.id, item.user_id)
    ]
    moderation_logs = [
        item for item in db.query(ModerationLog).filter(ModerationLog.created_at <= cutoff).order_by(ModerationLog.id).all()
        if (item.project_id is None or item.project_id in project_ids)
        and not _excluded(exceptions, "moderation_logs", item.id, item.user_id)
    ]
    audit_logs = [
        item for item in db.query(TeacherAuditLog).filter(TeacherAuditLog.created_at <= cutoff).order_by(TeacherAuditLog.id).all()
        if not _excluded(exceptions, "teacher_audit_logs", item.id)
    ]

    preview = {
        "retention_days": days,
        "cutoff_at": cutoff.isoformat(),
        "generated_at": now().isoformat(),
        "records": {
            "projects": [item.id for item in projects],
            "workflow_runs": [item.id for item in workflow_runs],
            "video_tasks": [item.id for item in video_tasks],
            "usage_logs": [item.id for item in usage_logs],
            "moderation_logs": [item.id for item in moderation_logs],
            "teacher_audit_logs_anonymize": [item.id for item in audit_logs],
        },
        "counts": {
            "projects": len(projects),
            "workflow_runs": len(workflow_runs),
            "video_tasks": len(video_tasks),
            "usage_logs": len(usage_logs),
            "moderation_logs": len(moderation_logs),
            "teacher_audit_logs_anonymize": len(audit_logs),
        },
        "exception_count": len(exceptions),
        "requires_approval": True,
        "automatic_deletion": False,
    }
    preview["preview_hash"] = hashlib.sha256(
        json.dumps(preview["records"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return preview


def _actor(teacher: TeacherSession) -> User:
    if not teacher.user or teacher.user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "仅管理员可管理数据保留。"})
    return teacher.user


def _request_payload(item: RetentionRequest) -> dict[str, Any]:
    return {
        "id": item.id,
        "status": item.status,
        "cutoff_at": item.cutoff_at.isoformat(),
        "preview": json.loads(item.preview_json or "{}"),
        "preview_hash": item.preview_hash,
        "requested_by_user_id": item.requested_by_user_id,
        "approved_by_user_id": item.approved_by_user_id,
        "executed_by_user_id": item.executed_by_user_id,
        "approval_note": item.approval_note,
        "requested_at": item.requested_at.isoformat(),
        "approved_at": item.approved_at.isoformat() if item.approved_at else None,
        "executed_at": item.executed_at.isoformat() if item.executed_at else None,
    }


def create_retention_request(db: Session, requested_by_user_id: int | None = None, retention_days: int | None = None) -> RetentionRequest:
    preview = scan_retention(db, retention_days)
    existing = db.query(RetentionRequest).filter(
        RetentionRequest.status.in_(("pending", "approved")),
        RetentionRequest.preview_hash == preview["preview_hash"],
    ).first()
    if existing:
        return existing
    item = RetentionRequest(
        status="pending",
        cutoff_at=datetime.fromisoformat(preview["cutoff_at"]),
        preview_json=json.dumps(preview, ensure_ascii=False, separators=(",", ":")),
        preview_hash=preview["preview_hash"],
        requested_by_user_id=requested_by_user_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.post("/preview")
def preview_retention(payload: RetentionPreviewRequest, db: Session = Depends(get_db)):
    return {"preview": scan_retention(db, payload.retention_days)}


@router.post("/requests")
def request_retention(
    payload: RetentionPreviewRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = _actor(teacher)
    item = create_retention_request(db, actor.id, payload.retention_days)
    record_teacher_audit(db, teacher, "privacy.retention.requested", target_type="retention_request", target_id=item.id, summary="创建到期数据删除审批")
    return {"request": _request_payload(item)}


@router.get("/requests")
def list_retention_requests(db: Session = Depends(get_db)):
    items = db.query(RetentionRequest).order_by(RetentionRequest.requested_at.desc()).limit(100).all()
    return {"requests": [_request_payload(item) for item in items]}


@router.post("/requests/{request_id}/approve")
def approve_retention(
    request_id: int,
    payload: RetentionApprovalRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = _actor(teacher)
    item = db.get(RetentionRequest, request_id)
    if not item or item.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "RETENTION_REQUEST_NOT_PENDING", "message": "该删除审批不处于待审批状态。"})
    current = scan_retention(db, json.loads(item.preview_json)["retention_days"])
    if current["preview_hash"] != item.preview_hash:
        raise HTTPException(status_code=409, detail={"code": "RETENTION_PREVIEW_CHANGED", "message": "到期数据已经变化，请重新生成删除预览。"})
    item.status = "approved"
    item.approved_by_user_id = actor.id
    item.approved_at = now()
    item.approval_note = payload.note.strip()
    db.commit()
    db.refresh(item)
    record_teacher_audit(db, teacher, "privacy.retention.approved", target_type="retention_request", target_id=item.id, summary="批准到期数据删除")
    return {"request": _request_payload(item)}


def _candidate_file_references(db: Session, records: dict[str, list[int]]) -> list[str]:
    project_ids = records.get("projects", [])
    video_ids = records.get("video_tasks", [])
    candidates: set[str] = set()
    if project_ids:
        candidates.update(str(item[0]) for item in db.query(Project.file_path).filter(Project.id.in_(project_ids)).all() if item[0])
    if video_ids:
        for task in db.query(VideoTask).filter(VideoTask.id.in_(video_ids)).all():
            candidates.update(value for value in (task.source_image_path, task.file_path) if value)
    other_references = {
        str(item[0]) for item in db.query(Project.file_path).filter(~Project.id.in_(project_ids or [-1])).all() if item[0]
    }
    other_references.update(
        str(item[0]) for item in db.query(Asset.file_path).filter(
            or_(Asset.project_id.is_(None), ~Asset.project_id.in_(project_ids or [-1]))
        ).all() if item[0]
    )
    other_references.update(
        str(item[0]) for item in db.query(SubmissionVersion.project_file_path).all() if item[0]
    )
    other_references.update(
        value
        for task in db.query(VideoTask).filter(~VideoTask.id.in_(video_ids or [-1])).all()
        for value in (task.source_image_path, task.file_path)
        if value
    )
    return sorted(candidates - other_references)


def _quarantine_local(references: list[str], request_id: int) -> tuple[Path, list[tuple[Path, Path]]]:
    root = DATA_DIR / ".retention-trash" / str(request_id)
    moved: list[tuple[Path, Path]] = []
    try:
        for raw in references:
            if not raw or raw.startswith(("http://", "https://", "data:")):
                continue
            source = Path(raw).resolve()
            source.relative_to(DATA_DIR.resolve())
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(DATA_DIR.resolve())
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            moved.append((source, target))
        return root, moved
    except Exception:
        for source, target in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            target.replace(source)
        raise


@router.post("/requests/{request_id}/execute")
def execute_retention(
    request_id: int,
    payload: RetentionExecuteRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = _actor(teacher)
    if payload.confirmation != "确认执行到期数据删除":
        raise HTTPException(status_code=400, detail={"code": "RETENTION_CONFIRMATION_REQUIRED", "message": "请输入完整确认文字。"})
    item = db.get(RetentionRequest, request_id)
    if not item or item.status != "approved":
        raise HTTPException(status_code=409, detail={"code": "RETENTION_REQUEST_NOT_APPROVED", "message": "该删除请求尚未批准。"})
    preview = json.loads(item.preview_json or "{}")
    current = scan_retention(db, preview.get("retention_days"))
    if current["preview_hash"] != item.preview_hash:
        raise HTTPException(status_code=409, detail={"code": "RETENTION_PREVIEW_CHANGED", "message": "到期数据已经变化，请重新生成并审批删除预览。"})
    records: dict[str, list[int]] = preview["records"]
    file_references = _candidate_file_references(db, records)
    object_moved: list[tuple[str, str]] = []
    local_root: Path | None = None
    local_moved: list[tuple[Path, Path]] = []
    try:
        if CLOUD_MODE:
            object_moved = quarantine_objects([item for item in file_references if is_object_reference(item)], f"retention-{request_id}")
        else:
            local_root, local_moved = _quarantine_local(file_references, request_id)
        project_ids = records.get("projects", [])
        if project_ids:
            db.query(Asset).filter(Asset.project_id.in_(project_ids)).delete(synchronize_session=False)
            db.query(Project).filter(Project.id.in_(project_ids)).delete(synchronize_session=False)
        for model, key in (
            (WorkflowRun, "workflow_runs"),
            (VideoTask, "video_tasks"),
            (UsageLog, "usage_logs"),
            (ModerationLog, "moderation_logs"),
        ):
            ids = records.get(key, [])
            if ids:
                db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        audit_ids = records.get("teacher_audit_logs_anonymize", [])
        if audit_ids:
            db.query(TeacherAuditLog).filter(TeacherAuditLog.id.in_(audit_ids)).update({
                TeacherAuditLog.actor_user_id: None,
                TeacherAuditLog.actor_username: "",
                TeacherAuditLog.actor_name: "",
                TeacherAuditLog.target_id: "retention-redacted",
                TeacherAuditLog.summary: "历史操作记录（到期去标识）",
                TeacherAuditLog.details_json: "{}",
            }, synchronize_session=False)
        item.status = "executed"
        item.executed_by_user_id = actor.id
        item.executed_at = now()
        record_teacher_audit(
            db,
            teacher,
            "privacy.retention.executed",
            target_type="retention_request",
            target_id=item.id,
            summary="执行经批准的到期数据删除",
            details={"counts": preview.get("counts", {}), "file_count": len(file_references)},
            commit=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        if object_moved:
            restore_quarantined_objects(object_moved)
        for source, target in reversed(local_moved):
            if target.is_file():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
        raise HTTPException(status_code=500, detail={"code": "RETENTION_EXECUTION_ROLLED_BACK", "message": "到期删除失败，数据库和文件已回滚。"}) from exc
    purge_quarantined_objects(object_moved)
    if local_root:
        shutil.rmtree(local_root, ignore_errors=True)
    db.refresh(item)
    return {"request": _request_payload(item), "deleted_counts": preview.get("counts", {})}


@router.post("/exceptions")
def create_exception(
    payload: RetentionExceptionRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = _actor(teacher)
    item = RetentionException(
        target_type=payload.target_type,
        target_id=payload.target_id.strip(),
        reason=payload.reason.strip(),
        expires_at=payload.expires_at,
        created_by_user_id=actor.id,
        active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    record_teacher_audit(db, teacher, "privacy.retention.exception_created", target_type="retention_exception", target_id=item.id, summary="添加数据保留例外")
    return {"exception": {"id": item.id, "target_type": item.target_type, "target_id": item.target_id, "reason": item.reason, "expires_at": item.expires_at.isoformat() if item.expires_at else None}}


@router.get("/exceptions")
def list_exceptions(db: Session = Depends(get_db)):
    items = db.query(RetentionException).filter(RetentionException.active.is_(True)).order_by(RetentionException.created_at.desc()).all()
    return {"exceptions": [{
        "id": item.id,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "reason": item.reason,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "created_at": item.created_at.isoformat(),
    } for item in items]}


@router.delete("/exceptions/{exception_id}")
def deactivate_exception(
    exception_id: int,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(RetentionException, exception_id)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "RETENTION_EXCEPTION_NOT_FOUND", "message": "保留例外不存在。"})
    item.active = False
    db.commit()
    record_teacher_audit(db, teacher, "privacy.retention.exception_removed", target_type="retention_exception", target_id=item.id, summary="移除数据保留例外")
    return {"removed": True}
