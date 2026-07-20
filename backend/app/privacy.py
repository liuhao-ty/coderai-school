from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.audit import record_teacher_audit
from backend.app.auth import SECRET_SETTING_KEY, ensure_auth_settings, get_setting, require_admin, require_student_or_teacher, require_teacher
from backend.app.db import DATA_DIR, get_db
from backend.app.models import (
    Asset,
    GuardianConsent,
    ModerationLog,
    PrivacyPolicy,
    Project,
    SubmissionVersion,
    StudentSession,
    TaskSubmission,
    TeacherAuditLog,
    TeacherSession,
    UsageLog,
    User,
    VideoTask,
    Workflow,
    WorkflowRun,
    now,
)
from backend.app.schemas import (
    GuardianConsentCreateRequest,
    GuardianConsentRevokeRequest,
    PrivacyPolicyPublishRequest,
    StudentDataDeleteRequest,
)
from backend.app.secrets import SecretProtectionError, decrypt_secret, encrypt_secret


BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
EXPORT_DIR = DATA_DIR / "exports" / "privacy"
TRASH_DIR = DATA_DIR / ".privacy-trash"
WORKFLOW_CACHE_DIR = DATA_DIR / "cache" / "workflows"
PREFLIGHT_TTL = timedelta(minutes=10)
DEFAULT_POLICY_VERSION = "1.0"
DEFAULT_POLICY_MARKDOWN = """# CoderAI 学堂隐私与未成年人数据保护政策

## 我们处理哪些数据

软件仅为课堂学习处理学生姓名、用户名、密码强哈希、班级、学龄分类、课程任务、AI 输入、生成结果、作品、提交与批改记录。学生账号由管理员创建，登录会话只用于本机身份隔离。

## 为什么处理

上述数据用于完成课程教学、作品保存、课堂评价、内容安全审核和必要的系统运维，不用于广告画像或向第三方出售。

## AI 服务与数据传输

启用云端 AI 服务时，学生主动提交的提示词、参考图片或必要上下文会发送给教师配置的 AI 服务商。机构应在启用前确认服务商条款，并按本政策记录监护人授权。

## 数据最小化与安全

学生只能访问自己的作品、提交和运行记录。教师高风险操作会记录脱敏审计日志；API 密钥使用 Windows DPAPI 加密；内容经过学龄分类策略和安全审核。

## 保存、导出与删除

课堂数据默认保留 365 天，机构可发布新政策调整期限。教师或学生可导出学生个人数据；删除必须由教师完成预检和二次确认，并同时处理数据库记录、受管文件、旧导出与可能包含该学生数据的本地备份。

## 监护人权利

监护人可向机构申请查阅、更正、导出、撤回授权或删除学生数据。撤回授权后，在要求授权的政策下，学生将不能继续使用云端 AI 生成功能。
"""

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


def _beijing_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING_TZ)
    else:
        value = value.astimezone(BEIJING_TZ)
    return value.isoformat()


def _local_naive(value: datetime | None) -> datetime:
    if value is None:
        return now()
    if value.tzinfo is not None:
        return value.astimezone(BEIJING_TZ).replace(tzinfo=None)
    return value


def ensure_default_privacy_policy(db: Session) -> PrivacyPolicy:
    policy = db.query(PrivacyPolicy).filter(PrivacyPolicy.version == DEFAULT_POLICY_VERSION).first()
    if policy:
        return policy
    current = now()
    policy = PrivacyPolicy(
        version=DEFAULT_POLICY_VERSION,
        title="CoderAI 学堂隐私与未成年人数据保护政策",
        content_markdown=DEFAULT_POLICY_MARKDOWN,
        status="published",
        require_guardian_consent=False,
        allow_external_ai_processing=True,
        retention_days=365,
        effective_at=current,
        published_at=current,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def current_privacy_policy(db: Session) -> PrivacyPolicy:
    policy = (
        db.query(PrivacyPolicy)
        .filter(PrivacyPolicy.status == "published", PrivacyPolicy.effective_at <= now())
        .order_by(PrivacyPolicy.effective_at.desc(), PrivacyPolicy.id.desc())
        .first()
    )
    return policy or ensure_default_privacy_policy(db)


def _policy_payload(policy: PrivacyPolicy, current_id: int | None = None) -> dict[str, Any]:
    return {
        "id": policy.id,
        "version": policy.version,
        "title": policy.title,
        "content_markdown": policy.content_markdown,
        "status": policy.status,
        "active": policy.id == current_id,
        "require_guardian_consent": policy.require_guardian_consent,
        "allow_external_ai_processing": policy.allow_external_ai_processing,
        "retention_days": policy.retention_days,
        "effective_at": _beijing_iso(policy.effective_at),
        "published_at": _beijing_iso(policy.published_at),
        "created_at": _beijing_iso(policy.created_at),
        "updated_at": _beijing_iso(policy.updated_at),
    }


def _safe_scopes(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _masked_contact(stored: str) -> str:
    if not stored:
        return ""
    try:
        contact = decrypt_secret(stored).strip()
    except SecretProtectionError:
        return "无法解密"
    if len(contact) <= 4:
        return "*" * len(contact)
    return f"{contact[:2]}{'*' * min(8, len(contact) - 4)}{contact[-2:]}"


def _consent_payload(consent: GuardianConsent) -> dict[str, Any]:
    return {
        "id": consent.id,
        "user_id": consent.user_id,
        "policy_id": consent.policy_id,
        "policy_version": consent.policy.version if consent.policy else "",
        "guardian_name": consent.guardian_name,
        "relationship": consent.guardian_relationship,
        "guardian_contact_masked": _masked_contact(consent.guardian_contact_encrypted),
        "consent_method": consent.consent_method,
        "evidence_reference": consent.evidence_reference,
        "scopes": _safe_scopes(consent.scope_json),
        "status": "revoked" if consent.revoked_at else "granted",
        "consented_at": _beijing_iso(consent.consented_at),
        "revoked_at": _beijing_iso(consent.revoked_at),
        "revocation_reason": consent.revocation_reason,
        "created_at": _beijing_iso(consent.created_at),
    }


def _active_consent(db: Session, student_id: int, policy: PrivacyPolicy) -> GuardianConsent | None:
    candidates = (
        db.query(GuardianConsent)
        .filter(
            GuardianConsent.user_id == student_id,
            GuardianConsent.policy_id == policy.id,
            GuardianConsent.revoked_at.is_(None),
        )
        .order_by(GuardianConsent.consented_at.desc(), GuardianConsent.id.desc())
        .all()
    )
    required_scopes = {"ai_generation"}
    if policy.allow_external_ai_processing:
        required_scopes.add("cloud_provider_transfer")
    return next((item for item in candidates if required_scopes.issubset(set(_safe_scopes(item.scope_json)))), None)


def _student_privacy_payload(db: Session, student: User, include_history: bool = False) -> dict[str, Any]:
    policy = current_privacy_policy(db)
    consent = _active_consent(db, student.id, policy)
    history = []
    if include_history:
        history = [
            _consent_payload(item)
            for item in (
                db.query(GuardianConsent)
                .filter(GuardianConsent.user_id == student.id)
                .order_by(GuardianConsent.created_at.desc(), GuardianConsent.id.desc())
                .all()
            )
        ]
    return {
        "student": {
            "id": student.id,
            "name": student.name,
            "classroom_id": student.classroom_id,
            "classroom_name": student.classroom.name if student.classroom else "",
            "account_status": "archived" if student.archived_at else ("active" if student.active else "disabled"),
        },
        "policy": _policy_payload(policy, policy.id),
        "consent_required": policy.require_guardian_consent,
        "external_ai_allowed": policy.allow_external_ai_processing,
        "ai_access_allowed": policy.allow_external_ai_processing and (not policy.require_guardian_consent or consent is not None),
        "active_consent": _consent_payload(consent) if consent else None,
        "consent_history": history,
    }


def ensure_student_ai_consent(db: Session, student: User) -> None:
    policy = current_privacy_policy(db)
    if not policy.allow_external_ai_processing:
        raise HTTPException(
            status_code=403,
            detail={"code": "PRIVACY_EXTERNAL_AI_DISABLED", "message": "当前隐私政策未允许向云端 AI 服务发送学生数据。"},
        )
    if policy.require_guardian_consent and _active_consent(db, student.id, policy) is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "PRIVACY_CONSENT_REQUIRED", "message": "当前政策要求监护人授权，请联系教师完成授权记录后再使用 AI 生成功能。"},
        )


def _require_student(db: Session, student_id: int) -> User:
    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail={"code": "STUDENT_NOT_FOUND", "message": "学生不存在或已被删除。"})
    return student


def _privacy_teacher_actor(db: Session, teacher: TeacherSession) -> User:
    actor = teacher.user or db.get(User, teacher.user_id)
    if not actor or actor.role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail={"code": "TEACHER_AUTH_REQUIRED", "message": "教师账号不可用。"})
    return actor


def _ensure_privacy_student_access(db: Session, teacher: TeacherSession, student: User) -> None:
    _privacy_teacher_actor(db, teacher)


def _student_records(db: Session, student_id: int) -> dict[str, list[Any]]:
    projects = db.query(Project).filter(Project.user_id == student_id).order_by(Project.id).all()
    project_ids = [item.id for item in projects]
    submission_filter = TaskSubmission.user_id == student_id
    if project_ids:
        submission_filter = or_(submission_filter, TaskSubmission.project_id.in_(project_ids))
    submissions = db.query(TaskSubmission).filter(submission_filter).order_by(TaskSubmission.id).all()
    submission_ids = [item.id for item in submissions]

    versions = []
    if submission_ids or project_ids:
        filters = []
        if submission_ids:
            filters.append(SubmissionVersion.submission_id.in_(submission_ids))
        if project_ids:
            filters.append(SubmissionVersion.project_id.in_(project_ids))
        versions = db.query(SubmissionVersion).filter(or_(*filters)).order_by(SubmissionVersion.id).all()

    assets = db.query(Asset).filter(Asset.project_id.in_(project_ids)).order_by(Asset.id).all() if project_ids else []
    workflows = db.query(Workflow).filter(Workflow.owner_user_id == student_id).order_by(Workflow.id).all()
    workflow_ids = [item.id for item in workflows]
    run_filter = WorkflowRun.user_id == student_id
    if workflow_ids:
        run_filter = or_(run_filter, WorkflowRun.workflow_id.in_(workflow_ids))
    workflow_runs = db.query(WorkflowRun).filter(run_filter).order_by(WorkflowRun.id).all()
    video_filter = VideoTask.user_id == student_id
    moderation_filter = ModerationLog.user_id == student_id
    if project_ids:
        video_filter = or_(video_filter, VideoTask.project_id.in_(project_ids))
        moderation_filter = or_(moderation_filter, ModerationLog.project_id.in_(project_ids))

    return {
        "projects": projects,
        "submissions": submissions,
        "submission_versions": versions,
        "assets": assets,
        "workflows": workflows,
        "workflow_runs": workflow_runs,
        "video_tasks": db.query(VideoTask).filter(video_filter).order_by(VideoTask.id).all(),
        "moderation_logs": db.query(ModerationLog).filter(moderation_filter).order_by(ModerationLog.id).all(),
        "usage_logs": db.query(UsageLog).filter(UsageLog.user_id == student_id).order_by(UsageLog.id).all(),
        "guardian_consents": db.query(GuardianConsent).filter(GuardianConsent.user_id == student_id).order_by(GuardianConsent.id).all(),
    }


def _raw_record_paths(records: dict[str, list[Any]]) -> list[str]:
    paths: list[str] = []
    paths.extend(item.file_path for item in records["projects"])
    paths.extend(item.file_path for item in records["assets"])
    paths.extend(item.project_file_path for item in records["submission_versions"])
    for item in records["video_tasks"]:
        paths.extend([item.file_path, item.source_image_path])
    paths.extend(item.resource_path for item in records["moderation_logs"])
    return [str(item).strip() for item in paths if str(item).strip()]


def _managed_file(raw_path: str) -> Path | None:
    if not raw_path or raw_path.startswith(("http://", "https://", "data:")):
        return None
    candidate = Path(raw_path)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(DATA_DIR.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or resolved.is_symlink():
        return None
    return resolved


def _other_referenced_paths(db: Session, records: dict[str, list[Any]]) -> set[Path]:
    excluded = {key: {item.id for item in values} for key, values in records.items()}
    raw_paths: list[str] = []
    raw_paths.extend(item.file_path for item in db.query(Project).filter(~Project.id.in_(excluded["projects"] or [-1])).all())
    raw_paths.extend(item.file_path for item in db.query(Asset).filter(~Asset.id.in_(excluded["assets"] or [-1])).all())
    raw_paths.extend(item.project_file_path for item in db.query(SubmissionVersion).filter(~SubmissionVersion.id.in_(excluded["submission_versions"] or [-1])).all())
    for item in db.query(VideoTask).filter(~VideoTask.id.in_(excluded["video_tasks"] or [-1])).all():
        raw_paths.extend([item.file_path, item.source_image_path])
    raw_paths.extend(item.resource_path for item in db.query(ModerationLog).filter(~ModerationLog.id.in_(excluded["moderation_logs"] or [-1])).all())
    return {path for raw in raw_paths if (path := _managed_file(raw)) is not None}


def _files_under(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    result = set()
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                path.resolve().relative_to(DATA_DIR.resolve())
            except (OSError, ValueError):
                continue
            result.add(path.resolve())
    return result


def _student_file_scope(
    db: Session,
    student_id: int,
    records: dict[str, list[Any]],
    include_cleanup_artifacts: bool = True,
) -> dict[str, Any]:
    referenced = {_managed_file(item) for item in _raw_record_paths(records)}
    referenced.discard(None)
    other_references = _other_referenced_paths(db, records)
    shared = {path for path in referenced if path in other_references}
    managed = {path for path in referenced if path not in shared}
    managed.update(_files_under(DATA_DIR / "ai_inputs" / f"student-{student_id}"))

    cache_files: set[Path] = set()
    backup_files: set[Path] = set()
    if include_cleanup_artifacts:
        cache_files = _files_under(WORKFLOW_CACHE_DIR)
        backup_files = {
            path.resolve()
            for pattern in ("coderai-backup-*.zip", "coderai-privacy-*.zip")
            for path in (DATA_DIR / "exports").rglob(pattern)
            if path.is_file() and not path.is_symlink()
        }
        managed.update(cache_files)
        managed.update(backup_files)

    external_references = sum(1 for item in _raw_record_paths(records) if _managed_file(item) is None)
    details = [
        {
            "path": path,
            "relative": path.relative_to(DATA_DIR.resolve()).as_posix(),
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in sorted(managed)
        if path.is_file()
    ]
    return {
        "files": details,
        "shared_files": len(shared),
        "external_references": external_references,
        "workflow_cache_files": len(cache_files),
        "backup_files": len(backup_files),
    }


def _preflight_summary(db: Session, student: User) -> tuple[dict[str, Any], dict[str, list[Any]], dict[str, Any]]:
    records = _student_records(db, student.id)
    file_scope = _student_file_scope(db, student.id, records)
    counts = {name: len(items) for name, items in records.items()}
    counts["student_sessions"] = db.query(StudentSession).filter(StudentSession.user_id == student.id).count()
    counts["legacy_unattributed_moderation_logs"] = db.query(ModerationLog).filter(ModerationLog.user_id.is_(None), ModerationLog.project_id.is_(None)).count()
    counts["legacy_unattributed_usage_logs"] = db.query(UsageLog).filter(UsageLog.user_id.is_(None)).count()
    summary = {
        "student_id": student.id,
        "student_name": student.name,
        "counts": counts,
        "managed_file_count": len(file_scope["files"]),
        "managed_file_bytes": sum(item["size"] for item in file_scope["files"]),
        "shared_file_count": file_scope["shared_files"],
        "external_reference_count": file_scope["external_references"],
        "workflow_cache_file_count": file_scope["workflow_cache_files"],
        "backup_file_count": file_scope["backup_files"],
        "consequences": [
            "学生账号、用户名、密码强哈希、注册邀请码、登录会话、作品、提交与批改记录将永久删除。",
            "学生工作流、视频任务、内容审核、用量记录和监护人授权记录将永久删除。",
            "受管文件、该学生 AI 输入、工作流缓存和可能包含该学生数据的本地旧备份将删除。",
            "外部路径或远程链接只从数据库移除，不会删除机构在软件之外保存的文件。",
        ],
    }
    return summary, records, file_scope


def _snapshot_digest(summary: dict[str, Any], file_scope: dict[str, Any]) -> str:
    payload = {
        "student_id": summary["student_id"],
        "student_name": summary["student_name"],
        "counts": summary["counts"],
        "files": [(item["relative"], item["size"], item["mtime_ns"]) for item in file_scope["files"]],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _signing_secret(db: Session) -> bytes:
    ensure_auth_settings(db)
    secret = get_setting(db, SECRET_SETTING_KEY) or ""
    return secret.encode("utf-8")


def _preflight_token(db: Session, student_id: int, digest: str) -> str:
    payload = {
        "student_id": student_id,
        "snapshot": digest,
        "expires_at": int((datetime.now(timezone.utc) + PREFLIGHT_TTL).timestamp()),
        "nonce": secrets.token_urlsafe(12),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_signing_secret(db), raw, hashlib.sha256).digest()
    return f"{base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"


def _decode_preflight_token(db: Session, token: str) -> dict[str, Any]:
    try:
        raw_part, signature_part = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_part + "=" * (-len(raw_part) % 4))
        signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        expected = hmac.new(_signing_secret(db), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(raw)
        if int(payload["expires_at"]) < int(datetime.now(timezone.utc).timestamp()):
            raise HTTPException(status_code=409, detail={"code": "PRIVACY_PREFLIGHT_EXPIRED", "message": "删除预检已过期，请重新预检。"})
        return payload
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail={"code": "PRIVACY_PREFLIGHT_INVALID", "message": "删除预检凭证无效，请重新预检。"})


def _row_payload(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            result[column.name] = _beijing_iso(value)
        else:
            result[column.name] = value
    return result


def _export_consent_payload(consent: GuardianConsent, include_guardian_contact: bool) -> dict[str, Any]:
    result = _row_payload(consent)
    stored = result.pop("guardian_contact_encrypted", "")
    if include_guardian_contact:
        try:
            result["guardian_contact"] = decrypt_secret(stored) if stored else ""
        except SecretProtectionError:
            result["guardian_contact"] = ""
            result["guardian_contact_error"] = "stored_contact_could_not_be_decrypted"
    else:
        result["guardian_contact_masked"] = _masked_contact(stored)
    result["scopes"] = _safe_scopes(consent.scope_json)
    return result


def _cleanup_export(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_stale_privacy_artifacts() -> None:
    cutoff = datetime.now().timestamp() - 24 * 60 * 60
    for root in (EXPORT_DIR, TRASH_DIR):
        if not root.exists():
            continue
        for path in root.iterdir():
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.is_file():
                    path.unlink()
            except OSError:
                continue


def build_student_export(db: Session, student: User, include_guardian_contact: bool = False) -> Path:
    cleanup_stale_privacy_artifacts()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    records = _student_records(db, student.id)
    file_scope = _student_file_scope(db, student.id, records, include_cleanup_artifacts=False)
    current_policy = current_privacy_policy(db)
    package_path = EXPORT_DIR / f"coderai-privacy-student-{student.id}-{datetime.now(BEIJING_TZ).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.zip"
    student_payload = _row_payload(student)
    student_payload.pop("password_hash", None)
    student_payload["classroom_name"] = student.classroom.name if student.classroom else ""
    datasets: dict[str, Any] = {
        "student": student_payload,
        "privacy_policy": _policy_payload(current_policy, current_policy.id),
        "submission_context": [
            {
                "submission_id": item.id,
                "task_title": item.task.title if item.task else "",
                "project_title": item.project.title if item.project else "",
                "classroom_name": item.classroom.name if item.classroom else "",
            }
            for item in records["submissions"]
        ],
    }
    for name, rows in records.items():
        datasets[name] = [
            _export_consent_payload(item, include_guardian_contact) if isinstance(item, GuardianConsent) else _row_payload(item)
            for item in rows
        ]
    manifest = {
        "schema_version": "1.0",
        "exported_at": datetime.now(BEIJING_TZ).isoformat(),
        "student_id": student.id,
        "datasets": {name: len(value) if isinstance(value, list) else 1 for name, value in datasets.items()},
        "file_count": len(file_scope["files"]),
        "file_bytes": sum(item["size"] for item in file_scope["files"]),
        "excluded_shared_files": file_scope["shared_files"],
        "external_references_not_copied": file_scope["external_references"],
        "notes": [
            "未关联到具体学生的旧版审核和用量日志无法可靠归属，因此不包含在个人导出中。",
            "监护人联系方式仅在教师授权导出中提供明文，学生自助导出只包含掩码。",
        ],
    }
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, value in datasets.items():
            archive.writestr(f"data/{name}.json", json.dumps(value, ensure_ascii=False, indent=2))
        archive.writestr(
            "README.md",
            "# CoderAI 学生个人数据导出\n\n本压缩包只包含该学生可归属的数据和受管文件，不包含教师密码、API Key、其他学生数据或共享素材。\n",
        )
        for item in file_scope["files"]:
            archive.write(item["path"], f"files/{item['relative']}")
    return package_path


def _move_to_quarantine(files: list[dict[str, Any]], request_id: str) -> tuple[Path, list[tuple[Path, Path]]]:
    quarantine = TRASH_DIR / request_id
    moved: list[tuple[Path, Path]] = []
    try:
        for item in files:
            source: Path = item["path"]
            if not source.is_file():
                continue
            target = quarantine / item["relative"]
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            moved.append((source, target))
        return quarantine, moved
    except Exception:
        _restore_quarantine(moved)
        shutil.rmtree(quarantine, ignore_errors=True)
        raise


def _restore_quarantine(moved: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(moved):
        if not target.is_file():
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        target.replace(source)


def _delete_database_records(
    db: Session,
    student: User,
    records: dict[str, list[Any]],
    teacher: TeacherSession,
    request_id: str,
    summary: dict[str, Any],
    reason: str,
) -> None:
    ids = {name: [item.id for item in rows] for name, rows in records.items()}
    if ids["submission_versions"]:
        db.query(SubmissionVersion).filter(SubmissionVersion.id.in_(ids["submission_versions"])).delete(synchronize_session=False)
    if ids["submissions"]:
        db.query(TaskSubmission).filter(TaskSubmission.id.in_(ids["submissions"])).delete(synchronize_session=False)
    if ids["assets"]:
        db.query(Asset).filter(Asset.id.in_(ids["assets"])).delete(synchronize_session=False)
    if ids["workflow_runs"]:
        db.query(WorkflowRun).filter(WorkflowRun.id.in_(ids["workflow_runs"])).delete(synchronize_session=False)
    if ids["workflows"]:
        db.query(Workflow).filter(Workflow.id.in_(ids["workflows"])).delete(synchronize_session=False)
    if ids["video_tasks"]:
        db.query(VideoTask).filter(VideoTask.id.in_(ids["video_tasks"])).delete(synchronize_session=False)
    if ids["moderation_logs"]:
        db.query(ModerationLog).filter(ModerationLog.id.in_(ids["moderation_logs"])).delete(synchronize_session=False)
    if ids["usage_logs"]:
        db.query(UsageLog).filter(UsageLog.id.in_(ids["usage_logs"])).delete(synchronize_session=False)
    if ids["guardian_consents"]:
        db.query(GuardianConsent).filter(GuardianConsent.id.in_(ids["guardian_consents"])).delete(synchronize_session=False)
    if ids["projects"]:
        db.query(Project).filter(Project.id.in_(ids["projects"])).delete(synchronize_session=False)
    db.query(StudentSession).filter(StudentSession.user_id == student.id).delete(synchronize_session=False)

    # Older versions did not link these logs to a student. Purging them is safer than retaining potentially personal prompts.
    db.query(ModerationLog).filter(ModerationLog.user_id.is_(None), ModerationLog.project_id.is_(None)).delete(synchronize_session=False)
    db.query(UsageLog).filter(UsageLog.user_id.is_(None)).delete(synchronize_session=False)

    subject_fingerprint = hmac.new(_signing_secret(db), f"student:{student.id}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    db.query(TeacherAuditLog).filter(
        TeacherAuditLog.target_type == "student",
        TeacherAuditLog.target_id == str(student.id),
    ).update(
        {
            TeacherAuditLog.target_id: f"deleted:{subject_fingerprint}",
            TeacherAuditLog.summary: "与已删除学生相关的历史操作（已去标识）",
            TeacherAuditLog.details_json: "{}",
        },
        synchronize_session=False,
    )
    db.delete(student)
    record_teacher_audit(
        db,
        teacher,
        "privacy.student.deleted",
        target_type="privacy_request",
        target_id=request_id,
        summary="完成学生个人数据删除",
        details={
            "subject_fingerprint": subject_fingerprint,
            "record_counts": summary["counts"],
            "managed_file_count": summary["managed_file_count"],
            "reason_recorded": bool(reason.strip()),
        },
        commit=False,
    )


def delete_student_data(
    db: Session,
    student: User,
    records: dict[str, list[Any]],
    file_scope: dict[str, Any],
    teacher: TeacherSession,
    summary: dict[str, Any],
    reason: str,
) -> tuple[str, list[str]]:
    request_id = "pdr_" + secrets.token_urlsafe(12)
    quarantine: Path | None = None
    moved: list[tuple[Path, Path]] = []
    try:
        quarantine, moved = _move_to_quarantine(file_scope["files"], request_id)
        _delete_database_records(db, student, records, teacher, request_id, summary, reason)
        db.commit()
    except Exception as exc:
        db.rollback()
        _restore_quarantine(moved)
        if quarantine:
            shutil.rmtree(quarantine, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "PRIVACY_DELETE_ROLLED_BACK", "message": "删除未完成，数据库和受管文件已回滚，请检查日志后重试。"},
        ) from exc

    warnings: list[str] = []
    if quarantine:
        try:
            shutil.rmtree(quarantine)
        except OSError:
            warnings.append("隔离区清理失败，系统会在下次启动时重试。")
    return request_id, warnings


@router.get("/policy")
def get_public_policy(db: Session = Depends(get_db)):
    policy = current_privacy_policy(db)
    return {"policy": _policy_payload(policy, policy.id)}


@router.get("/me")
def get_my_privacy(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    if identity["role"] != "student":
        raise HTTPException(status_code=403, detail={"code": "STUDENT_AUTH_REQUIRED", "message": "请以学生身份查看个人隐私状态。"})
    payload = _student_privacy_payload(db, identity["student"], include_history=False)
    if payload["active_consent"]:
        payload["active_consent"].pop("guardian_name", None)
        payload["active_consent"].pop("guardian_contact_masked", None)
        payload["active_consent"].pop("evidence_reference", None)
    return payload


@router.get("/me/export")
def export_my_data(
    background_tasks: BackgroundTasks,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    if identity["role"] != "student":
        raise HTTPException(status_code=403, detail={"code": "STUDENT_AUTH_REQUIRED", "message": "请以学生身份导出个人数据。"})
    package_path = build_student_export(db, identity["student"])
    background_tasks.add_task(_cleanup_export, package_path)
    return FileResponse(package_path, media_type="application/zip", filename=f"coderai-student-{identity['student'].id}-data.zip")


@router.get("/settings")
def get_privacy_settings(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    current = current_privacy_policy(db)
    policies = db.query(PrivacyPolicy).order_by(PrivacyPolicy.effective_at.desc(), PrivacyPolicy.id.desc()).all()
    return {
        "current_policy": _policy_payload(current, current.id),
        "policies": [_policy_payload(item, current.id) for item in policies],
        "student_count": db.query(User).filter(User.role == "student").count(),
        "active_consent_count": db.query(GuardianConsent).filter(GuardianConsent.policy_id == current.id, GuardianConsent.revoked_at.is_(None)).count(),
    }


@router.post("/policies")
def publish_privacy_policy(
    payload: PrivacyPolicyPublishRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(PrivacyPolicy).filter(PrivacyPolicy.version == payload.version.strip()).first():
        raise HTTPException(status_code=409, detail={"code": "PRIVACY_POLICY_VERSION_EXISTS", "message": "该隐私政策版本已存在，请使用新的版本号。"})
    policy = PrivacyPolicy(
        version=payload.version.strip(),
        title=payload.title.strip(),
        content_markdown=payload.content_markdown.strip(),
        status="published",
        require_guardian_consent=payload.require_guardian_consent,
        allow_external_ai_processing=payload.allow_external_ai_processing,
        retention_days=payload.retention_days,
        effective_at=_local_naive(payload.effective_at),
        published_at=now(),
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    active = current_privacy_policy(db)
    record_teacher_audit(
        db,
        teacher,
        "privacy.policy.published",
        target_type="privacy_policy",
        target_id=policy.id,
        summary="发布新的隐私与未成年人数据政策",
        details={
            "version": policy.version,
            "require_guardian_consent": policy.require_guardian_consent,
            "allow_external_ai_processing": policy.allow_external_ai_processing,
            "retention_days": policy.retention_days,
        },
    )
    return {"policy": _policy_payload(policy, active.id)}


@router.get("/students")
def list_student_privacy(teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = _privacy_teacher_actor(db, teacher)
    query = db.query(User).filter(User.role == "student")
    students = query.order_by(User.name, User.id).all()
    return {"students": [_student_privacy_payload(db, student, include_history=False) for student in students]}


@router.get("/students/{student_id}")
def get_student_privacy(student_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    student = _require_student(db, student_id)
    _ensure_privacy_student_access(db, teacher, student)
    return _student_privacy_payload(db, student, include_history=True)


@router.post("/students/{student_id}/consents")
def grant_guardian_consent(
    student_id: int,
    payload: GuardianConsentCreateRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    student = _require_student(db, student_id)
    _ensure_privacy_student_access(db, teacher, student)
    policy = current_privacy_policy(db)
    if _active_consent(db, student.id, policy):
        raise HTTPException(status_code=409, detail={"code": "GUARDIAN_CONSENT_EXISTS", "message": "当前政策已经有有效监护人授权；如需更换，请先撤回原授权。"})
    required_scopes = {"ai_generation"}
    if policy.allow_external_ai_processing:
        required_scopes.add("cloud_provider_transfer")
    if not required_scopes.issubset(set(payload.scopes)):
        raise HTTPException(
            status_code=400,
            detail={"code": "GUARDIAN_CONSENT_SCOPE_INCOMPLETE", "message": "授权范围必须包含 AI 生成和当前政策要求的云端服务商处理。"},
        )
    consent = GuardianConsent(
        user_id=student.id,
        policy_id=policy.id,
        guardian_name=payload.guardian_name.strip(),
        guardian_relationship=payload.relationship.strip(),
        guardian_contact_encrypted=encrypt_secret(payload.guardian_contact.strip()),
        consent_method=payload.consent_method,
        evidence_reference=payload.evidence_reference.strip(),
        scope_json=json.dumps(list(dict.fromkeys(payload.scopes)), ensure_ascii=False, separators=(",", ":")),
        recorded_by_session_id=teacher.id,
        consented_at=_local_naive(payload.consented_at),
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    record_teacher_audit(
        db,
        teacher,
        "privacy.consent.granted",
        target_type="student",
        target_id=student.id,
        summary="记录监护人授权",
        details={"policy_version": policy.version, "consent_method": consent.consent_method, "scopes": _safe_scopes(consent.scope_json)},
    )
    return {"consent": _consent_payload(consent), "privacy": _student_privacy_payload(db, student, include_history=True)}


@router.post("/students/{student_id}/consents/{consent_id}/revoke")
def revoke_guardian_consent(
    student_id: int,
    consent_id: int,
    payload: GuardianConsentRevokeRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    student = _require_student(db, student_id)
    _ensure_privacy_student_access(db, teacher, student)
    consent = db.query(GuardianConsent).filter(GuardianConsent.id == consent_id, GuardianConsent.user_id == student.id).first()
    if not consent:
        raise HTTPException(status_code=404, detail={"code": "GUARDIAN_CONSENT_NOT_FOUND", "message": "监护人授权记录不存在。"})
    if consent.revoked_at:
        raise HTTPException(status_code=409, detail={"code": "GUARDIAN_CONSENT_REVOKED", "message": "该授权已经撤回。"})
    consent.revoked_at = now()
    consent.revocation_reason = payload.reason.strip()
    db.commit()
    db.refresh(consent)
    record_teacher_audit(
        db,
        teacher,
        "privacy.consent.revoked",
        target_type="student",
        target_id=student.id,
        summary="撤回监护人授权",
        details={"policy_version": consent.policy.version if consent.policy else ""},
    )
    return {"consent": _consent_payload(consent), "privacy": _student_privacy_payload(db, student, include_history=True)}


@router.get("/students/{student_id}/export")
def export_student_data(
    student_id: int,
    background_tasks: BackgroundTasks,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    student = _require_student(db, student_id)
    _ensure_privacy_student_access(db, teacher, student)
    package_path = build_student_export(db, student, include_guardian_contact=True)
    record_teacher_audit(
        db,
        teacher,
        "privacy.student.exported",
        target_type="student",
        target_id=student.id,
        summary="导出学生个人数据",
        details={"package_scope": "student_only"},
    )
    background_tasks.add_task(_cleanup_export, package_path)
    return FileResponse(package_path, media_type="application/zip", filename=f"coderai-student-{student.id}-data.zip")


@router.get("/students/{student_id}/deletion-preflight")
def preflight_student_deletion(student_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    student = _require_student(db, student_id)
    _ensure_privacy_student_access(db, teacher, student)
    summary, _, file_scope = _preflight_summary(db, student)
    return {
        "preflight": summary,
        "preflight_token": _preflight_token(db, student.id, _snapshot_digest(summary, file_scope)),
        "expires_in_seconds": int(PREFLIGHT_TTL.total_seconds()),
    }


@router.post("/students/{student_id}/delete")
def permanently_delete_student_data(
    student_id: int,
    payload: StudentDataDeleteRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    student = _require_student(db, student_id)
    _ensure_privacy_student_access(db, teacher, student)
    token = _decode_preflight_token(db, payload.preflight_token)
    if int(token.get("student_id", -1)) != student.id:
        raise HTTPException(status_code=400, detail={"code": "PRIVACY_PREFLIGHT_SUBJECT_MISMATCH", "message": "删除预检与当前学生不匹配。"})
    if payload.confirm_student_name.strip() != student.name:
        raise HTTPException(status_code=400, detail={"code": "PRIVACY_DELETE_CONFIRMATION_INVALID", "message": "确认姓名不匹配，未执行删除。"})
    summary, records, file_scope = _preflight_summary(db, student)
    if token.get("snapshot") != _snapshot_digest(summary, file_scope):
        raise HTTPException(status_code=409, detail={"code": "PRIVACY_PREFLIGHT_CHANGED", "message": "学生数据在预检后发生变化，请重新预检。"})
    request_id, warnings = delete_student_data(db, student, records, file_scope, teacher, summary, payload.reason)
    return {"deleted": True, "student_id": student_id, "request_id": request_id, "summary": summary, "warnings": warnings}
