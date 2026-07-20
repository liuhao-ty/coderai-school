import json
import hashlib
import csv
import io
import mimetypes
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from pydantic import ValidationError

from backend.app.auth import (
    DEFAULT_STUDENT_PASSWORD,
    account_payload,
    check_login_allowed,
    change_student_password,
    change_teacher_password,
    clear_login_failures,
    find_account_by_username,
    generate_temporary_password,
    hash_password,
    issue_student_session,
    issue_teacher_session,
    login_student_by_credentials,
    login_teacher_by_credentials,
    optional_teacher_or_student,
    record_login_failure,
    require_admin,
    require_student_account,
    require_student_or_teacher,
    require_teacher,
    require_teacher_session,
    refresh_teacher_session,
    revoke_all_student_sessions,
    revoke_all_teacher_sessions,
    revoke_student_session_by_token,
    revoke_teacher_session,
    revoke_teacher_session_by_refresh_token,
    teacher_session_payload,
    teacher_password_change_required,
    teacher_password_is_weak,
    validate_admin_password,
    validate_teacher_password,
    validate_username,
)
from backend.app.db import DATA_DIR, SessionLocal, get_db, init_db
from backend.app.audit import record_teacher_audit, teacher_audit_payload
from backend.app.models import AIProvider, Asset, Classroom, ClassroomTeacher, Course, CourseMaterial, CoursePackage, CoursePackageTeacher, CourseSchedule, CurriculumCourse, FeedbackTemplate, Lesson, ModerationLog, Project, SubmissionVersion, Task, TaskSubmission, TeacherAuditLog, TeacherSession, UsageLog, User, VideoTask, Workflow, WorkflowRun, now
from backend.app.curriculum import CURRICULUM_DIR, MATERIAL_KINDS, convert_slides_material, curriculum_course_dir, libreoffice_status, validate_course_material
from backend.app.operations import router as operations_router
from backend.app.backup import router as backup_router
from backend.app.privacy import ensure_student_ai_consent, router as privacy_router
from backend.app.provider_presets import list_provider_presets
from backend.app.school_stages import (
    infer_school_stages,
    normalize_school_stage,
    normalize_school_stages,
    parse_school_stage,
    school_stage_label,
    school_stages_from_json,
    school_stages_label,
)
from backend.app.package_security import MAX_COURSE_PACKAGE_BYTES, validate_course_package
from backend.app.secrets import encrypt_secret
from backend.app.licensing import ensure_student_seat_capacity, get_license_status, save_license_status
from backend.app.plugins import router as plugins_router
from backend.app.readiness import router as readiness_router
from backend.app.schemas import (
    ClassTaskRequest,
    ClassroomCreateRequest,
    ClassroomTeachersUpdateRequest,
    ClassroomUpdateRequest,
    CourseImportRequest,
    CoursePackageRequest,
    CoursePackageTeachersUpdateRequest,
    CourseScheduleBatchRequest,
    CourseScheduleCancelRequest,
    CourseScheduleUpdateRequest,
    CourseUpdateRequest,
    CurriculumCourseOrderRequest,
    CurriculumCourseRequest,
    FeedbackTemplateRequest,
    AssetRegisterRequest,
    ImageGenerateRequest,
    ImageModerationReviewRequest,
    LicenseSettingsRequest,
    LessonRequest,
    ModerationRequest,
    ModerationSettingsRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ProviderSettingsRequest,
    ProviderConnectionTestRequest,
    ProviderEnabledRequest,
    ProviderRoutesRequest,
    StudentCreateRequest,
    StudentBatchRequest,
    StudentBatchRestoreRequest,
    StudentBatchTransferRequest,
    StudentClassroomUpdateRequest,
    StudentUpdateRequest,
    StudentLoginRequest,
    StudentPasswordChangeRequest,
    StudentRegisterRequest,
    SubmissionCreateRequest,
    SubmissionReviewRequest,
    SystemRestoreRequest,
    TeacherLoginRequest,
    TeacherAccountCreateRequest,
    TeacherAccountUpdateRequest,
    TeacherLogoutRequest,
    TeacherPasswordChangeRequest,
    TeacherRefreshRequest,
    TextGenerateRequest,
    VideoGenerateRequest,
    WorkflowRunRequest,
    WorkflowNodeRetryRequest,
    WorkflowSaveRequest,
)
from backend.app.services import (
    generate_image,
    generate_text,
    generate_video_task,
    active_provider,
    ensure_default_provider_routes,
    get_blocked_words,
    json_dumps,
    provider_payload,
    provider_management_payload,
    provider_model_catalog_payload,
    provider_status_payload,
    remove_provider_from_routes,
    run_moderation,
    save_project,
    save_blocked_words,
    save_provider_routes,
    test_provider_connection,
    refresh_video_task,
    retry_video_task,
    to_classroom_dict,
    to_course_dict,
    to_asset_dict,
    to_moderation_log_dict,
    to_project_dict,
    to_submission_dict,
    to_submission_version_dict,
    to_lesson_dict,
    to_task_dict,
    to_user_dict,
    to_video_task_dict,
    workflow_templates,
)


app = FastAPI(title="CoderAI 学堂 API", version="0.1.0")
app.include_router(operations_router)
app.include_router(backup_router)
app.include_router(privacy_router)
app.include_router(plugins_router)
app.include_router(readiness_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "tauri://localhost"],
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_untrusted_package_bodies(request: Request, call_next):
    limits = {
        "/api/courses/import": MAX_COURSE_PACKAGE_BYTES,
    }
    limit = limits.get(request.url.path) if request.method == "POST" else None
    content_length = request.headers.get("content-length", "")
    if limit and content_length.isdigit() and int(content_length) > limit:
        return JSONResponse(status_code=413, content={"detail": {"code": "COURSE_PACKAGE_TOO_LARGE", "message": "课程包请求体过大。"}})
    return await call_next(request)

ASSET_LIBRARY_DIR = DATA_DIR / "assets" / "library"
ASSET_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
AI_INPUT_DIR = DATA_DIR / "ai_inputs"
AI_INPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKFLOW_CACHE_DIR = DATA_DIR / "cache" / "workflows"
WORKFLOW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
MAX_AI_INPUT_BYTES = 10 * 1024 * 1024
MAX_ASSET_BYTES = 50 * 1024 * 1024
ASSET_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "video": {".mp4", ".webm", ".mov"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg"},
    "document": {".pdf", ".txt", ".md", ".docx", ".pptx", ".xlsx", ".csv", ".json"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".json", ".sb3"},
}


def local_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(BEIJING_TZ).replace(tzinfo=None)
    return value


def attach_image_moderation(db: Session, project: Project, moderation_log_id: int | None) -> None:
    if not moderation_log_id:
        return
    log = db.query(ModerationLog).filter(ModerationLog.id == moderation_log_id).first()
    if log:
        log.project_id = project.id
        log.owner_teacher_id = project.owner_teacher_id
        log.classroom_id = project.classroom_id
        db.commit()


def safe_image_output(value: Any, identity: dict) -> Any:
    if isinstance(value, list):
        return [safe_image_output(item, identity) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized = {key: safe_image_output(item, identity) for key, item in value.items()}
    status = str(value.get("moderation_status") or "approved")
    if status != "approved" and identity.get("role") != "teacher":
        sanitized["url"] = ""
        sanitized["file_path"] = ""
        sanitized["moderation_message"] = "图片正在等待教师复核，审核通过后会出现在作品库。" if status == "pending" else "图片未通过自动审核，已交由教师复核。"
    return sanitized


def project_payload_for_identity(project: Project, identity: dict) -> dict:
    payload = to_project_dict(project)
    if identity.get("role") == "student" and project.moderation_status != "approved":
        payload.update({"file_path": "", "file_exists": False, "file_status": "moderation_hidden"})
    return payload


def ensure_student_can_access_moderated_project(project: Project, identity: dict) -> None:
    if identity.get("role") == "student" and project.moderation_status != "approved":
        raise HTTPException(
            status_code=403,
            detail={"code": "PROJECT_MODERATION_PENDING", "message": "图片作品正在等待教师复核，暂时不能访问。"},
        )


def ensure_project_access(db: Session, project: Project, identity: dict, action: str = "访问") -> None:
    if identity["role"] == "student" and project.user_id != identity["student"].id:
        raise HTTPException(
            status_code=403,
            detail={"code": "PROJECT_FORBIDDEN", "message": f"不能{action}其他学生的作品。"},
        )
    if identity["role"] == "teacher":
        actor = identity["teacher"]
        ensure_teaching_record_access(db, actor, project.owner_teacher_id, project.classroom_id, "作品")
        if not is_admin_actor(actor) and project.user and project.user.archived_at and action not in {"访问", "查看"}:
            raise HTTPException(
                status_code=403,
                detail={"code": "ARCHIVED_STUDENT_RECORD_READ_ONLY", "message": "归档学员的历史作品仅供查看，教师不能再修改。"},
            )


def beijing_now_naive() -> datetime:
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


def teacher_actor(db: Session, session: TeacherSession) -> User:
    actor = session.user or db.get(User, session.user_id)
    if not actor or actor.role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail={"code": "TEACHER_AUTH_REQUIRED", "message": "教师账号不可用。"})
    return actor


def claim_legacy_teaching_data_for_admin(db: Session, actor: User) -> None:
    if actor.role != "admin":
        return
    for model in (
        Classroom,
        Course,
        Lesson,
        Task,
        Project,
        TaskSubmission,
        FeedbackTemplate,
        Asset,
        Workflow,
        WorkflowRun,
        VideoTask,
        UsageLog,
        ModerationLog,
    ):
        db.query(model).filter(model.owner_teacher_id.is_(None)).update(
            {model.owner_teacher_id: actor.id}, synchronize_session=False
        )
    db.commit()


def identity_teacher(identity: dict) -> User | None:
    return identity.get("teacher") if identity.get("role") == "teacher" else None


def is_admin_actor(actor: User | None) -> bool:
    return bool(actor and actor.role == "admin")


def ensure_teacher_owns(actor: User, owner_teacher_id: int | None, resource_name: str) -> None:
    if not is_admin_actor(actor) and owner_teacher_id != actor.id:
        raise HTTPException(
            status_code=403,
            detail={"code": "TEACHING_DATA_FORBIDDEN", "message": f"不能访问其他教师的{resource_name}。"},
        )


def assigned_classroom_ids(db: Session, actor: User) -> set[int]:
    if is_admin_actor(actor):
        return {row[0] for row in db.query(Classroom.id).all()}
    return {
        row[0]
        for row in db.query(ClassroomTeacher.classroom_id)
        .filter(ClassroomTeacher.teacher_id == actor.id)
        .all()
    }


def teacher_can_manage_classroom(db: Session, actor: User, classroom_id: int | None) -> bool:
    if classroom_id is None:
        return False
    return is_admin_actor(actor) or db.query(ClassroomTeacher).filter_by(
        classroom_id=classroom_id,
        teacher_id=actor.id,
    ).first() is not None


def ensure_teaching_record_access(
    db: Session,
    actor: User,
    owner_teacher_id: int | None,
    classroom_id: int | None,
    resource_name: str,
) -> None:
    if is_admin_actor(actor):
        return
    if classroom_id is not None and teacher_can_manage_classroom(db, actor, classroom_id):
        return
    if classroom_id is None and owner_teacher_id == actor.id:
        return
    raise HTTPException(
        status_code=403,
        detail={"code": "TEACHING_DATA_FORBIDDEN", "message": f"不能访问未授权班级的{resource_name}。"},
    )


def staff_scope_condition(model, db: Session, actor: User):
    classroom_ids = assigned_classroom_ids(db, actor)
    if not classroom_ids:
        return (model.classroom_id.is_(None)) & (model.owner_teacher_id == actor.id)
    return or_(
        model.classroom_id.in_(classroom_ids),
        (model.classroom_id.is_(None)) & (model.owner_teacher_id == actor.id),
    )


def teacher_can_manage_student(actor: User, student: User) -> bool:
    if actor.role == "admin":
        return student.role == "student"
    return actor.role == "teacher" and student.role == "student" and student.archived_at is None


def ensure_teacher_student_access(actor: User, student: User) -> None:
    if not teacher_can_manage_student(actor, student):
        if actor.role == "teacher" and student.role == "student" and student.archived_at:
            raise HTTPException(
                status_code=403,
                detail={"code": "ARCHIVED_STUDENT_FORBIDDEN", "message": "教师不能查看或操作已归档学员账号。"},
            )
        raise HTTPException(
            status_code=403,
            detail={"code": "STUDENT_FORBIDDEN", "message": "不能访问该学生账号。"},
        )


def require_owned_classroom(db: Session, classroom_id: int | None, actor: User) -> Classroom | None:
    if classroom_id is None:
        return None
    classroom = db.query(Classroom).filter(Classroom.id == classroom_id).first()
    if not classroom:
        raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "班级不存在。"})
    if not teacher_can_manage_classroom(db, actor, classroom.id):
        raise HTTPException(
            status_code=403,
            detail={"code": "CLASSROOM_FORBIDDEN", "message": "不能管理未授权的班级。"},
        )
    return classroom


COURSE_MATERIAL_LABELS = {
    "slides": "课堂PPT",
    "starter_markdown": "工程包",
    "result_markdown": "成果包",
}


def course_package_teacher_summaries(db: Session, package_id: int) -> list[dict]:
    rows = (
        db.query(CoursePackageTeacher, User)
        .join(User, User.id == CoursePackageTeacher.teacher_id)
        .filter(CoursePackageTeacher.package_id == package_id)
        .order_by(User.name.asc(), User.id.asc())
        .all()
    )
    return [
        {"id": teacher.id, "name": teacher.name, "username": teacher.username, "active": teacher.active}
        for _, teacher in rows
    ]


def course_package_author_summary(package: CoursePackage) -> dict | None:
    account = package.author_account
    if not account or account.role not in {"teacher", "admin"}:
        return None
    return {
        "id": account.id,
        "name": account.name,
        "role": account.role,
        "active": account.active,
    }


def resolve_course_package_author(
    db: Session,
    author_user_id: int,
    *,
    allow_inactive_id: int | None = None,
) -> User:
    account = db.query(User).filter(
        User.id == author_user_id,
        User.role.in_(("teacher", "admin")),
    ).first()
    if not account or (not account.active and account.id != allow_inactive_id):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "COURSE_PACKAGE_AUTHOR_INVALID",
                "message": "课程包作者必须选择一个启用中的教师或管理员账号。",
            },
        )
    return account


def teacher_has_course_package_access(db: Session, actor: User, package_id: int | None) -> bool:
    if is_admin_actor(actor):
        return True
    if actor.role != "teacher" or package_id is None:
        return False
    return db.query(CoursePackageTeacher).filter_by(package_id=package_id, teacher_id=actor.id).first() is not None


def teacher_can_preview_course_package(db: Session, actor: User, package: CoursePackage | None) -> bool:
    if not package:
        return False
    return is_admin_actor(actor) or (package.status == "published" and teacher_has_course_package_access(db, actor, package.id))


def ensure_teacher_course_package_access(db: Session, actor: User, package: CoursePackage | None) -> None:
    if teacher_can_preview_course_package(db, actor, package):
        return
    raise HTTPException(
        status_code=403,
        detail={"code": "COURSE_PACKAGE_TEACHER_FORBIDDEN", "message": "管理员尚未向当前教师开放该课程包。"},
    )


def schedule_applies_to_student(schedule: CourseSchedule, student: User) -> bool:
    if schedule.target_type == "student":
        return schedule.target_student_id == student.id
    return schedule.target_type == "classroom" and schedule.classroom_id is not None and schedule.classroom_id == student.classroom_id


def student_course_schedules(db: Session, student: User, course_id: int, include_canceled: bool = False) -> list[CourseSchedule]:
    query = db.query(CourseSchedule).filter(CourseSchedule.course_id == course_id)
    if not include_canceled:
        query = query.filter(CourseSchedule.status != "canceled")
    rows = query.order_by(CourseSchedule.starts_at.asc(), CourseSchedule.id.asc()).all()
    return [row for row in rows if schedule_applies_to_student(row, student)]


def student_has_submitted_course(db: Session, student_id: int, course_id: int) -> bool:
    return db.query(TaskSubmission.id).join(Task, TaskSubmission.task_id == Task.id).join(
        CourseSchedule, Task.course_schedule_id == CourseSchedule.id
    ).filter(
        TaskSubmission.user_id == student_id,
        CourseSchedule.course_id == course_id,
    ).first() is not None


def ensure_curriculum_course_read_access(db: Session, course: CurriculumCourse, identity: dict) -> None:
    package = course.package
    if identity["role"] == "teacher":
        actor = identity["teacher"]
        if teacher_can_preview_course_package(db, actor, package):
            return
        ensure_teacher_course_package_access(db, actor, package)
    elif identity["role"] == "student":
        student = identity["student"]
        has_current_schedule = bool(student_course_schedules(db, student, course.id))
        if package.status in {"published", "archived"} and (has_current_schedule or student_has_submitted_course(db, student.id, course.id)):
            return
    raise HTTPException(status_code=403, detail={"code": "COURSE_FORBIDDEN", "message": "不能访问未授权的课程内容。"})


def curriculum_material_permissions(db: Session, material: CourseMaterial, identity: dict) -> tuple[bool, bool]:
    course = material.course
    ensure_curriculum_course_read_access(db, course, identity)
    if identity["role"] == "teacher":
        if is_admin_actor(identity["teacher"]):
            return True, True
        if material.kind == "slides":
            return material.conversion_status == "ready" and bool(material.preview_path), False
        return True, True
    student = identity["student"]
    started = any(schedule.starts_at <= beijing_now_naive() and schedule.status != "canceled" for schedule in student_course_schedules(db, student, course.id))
    if not started:
        return False, False
    if material.kind == "slides":
        return material.conversion_status == "ready" and bool(material.preview_path), False
    if material.kind == "starter_markdown":
        return True, True
    unlocked = student_has_submitted_course(db, student.id, course.id)
    return unlocked, unlocked


def curriculum_material_payload(db: Session, course: CurriculumCourse, kind: str, identity: dict) -> dict:
    material = next((item for item in course.materials if item.kind == kind), None)
    if not material:
        return {
            "id": None,
            "kind": kind,
            "label": COURSE_MATERIAL_LABELS[kind],
            "missing": True,
            "can_preview": False,
            "can_download": False,
            "conversion_status": "missing",
            "conversion_error": "",
            "original_name": "",
            "file_size": 0,
            "updated_at": None,
        }
    try:
        can_preview, can_download = curriculum_material_permissions(db, material, identity)
    except HTTPException:
        can_preview, can_download = False, False
    actor = identity.get("teacher")
    return {
        "id": material.id,
        "kind": material.kind,
        "label": COURSE_MATERIAL_LABELS[material.kind],
        "missing": False,
        "can_preview": can_preview,
        "can_download": can_download,
        "conversion_status": material.conversion_status,
        "conversion_error": material.conversion_error if is_admin_actor(actor) else "",
        "original_name": material.original_name,
        "file_size": material.file_size,
        "updated_at": material.updated_at.isoformat() if material.updated_at else None,
    }


def curriculum_course_payload(db: Session, course: CurriculumCourse, identity: dict) -> dict:
    schedules = student_course_schedules(db, identity["student"], course.id, include_canceled=True) if identity["role"] == "student" else []
    return {
        "id": course.id,
        "package_id": course.package_id,
        "package_title": course.package.title if course.package else "",
        "title": course.title,
        "description": course.description,
        "order_index": course.order_index,
        "assignment_instructions": course.assignment_instructions,
        "tool_scope": course.tool_scope,
        "rubric": normalize_rubric_payload(course.rubric_json),
        "materials": {kind: curriculum_material_payload(db, course, kind, identity) for kind in COURSE_MATERIAL_LABELS},
        "schedule_ids": [item.id for item in schedules],
        "created_at": course.created_at.isoformat() if course.created_at else None,
        "updated_at": course.updated_at.isoformat() if course.updated_at else None,
    }


def course_package_payload(db: Session, package: CoursePackage, identity: dict) -> dict:
    package_courses = sorted(package.courses, key=lambda item: (item.order_index, item.id))
    if identity["role"] == "student":
        student = identity["student"]
        package_courses = [
            course for course in package_courses
            if student_course_schedules(db, student, course.id) or student_has_submitted_course(db, student.id, course.id)
        ]
    courses = [curriculum_course_payload(db, course, identity) for course in package_courses]
    actor = identity.get("teacher") if identity["role"] == "teacher" else None
    teachers = course_package_teacher_summaries(db, package.id) if actor else []
    author_account = course_package_author_summary(package)
    school_stages = school_stages_from_json(package.school_stages_json, package.age_range)
    can_preview = bool(actor and teacher_can_preview_course_package(db, actor, package)) or (identity["role"] == "student" and bool(courses))
    return {
        "id": package.id,
        "title": package.title,
        "description": package.description,
        "package_version": package.package_version,
        "author_user_id": author_account["id"] if author_account else package.author_user_id,
        "author": author_account["name"] if author_account else package.author,
        "author_account": author_account,
        "school_stages": school_stages,
        "age_range": school_stages_label(school_stages),
        "cover_path": package.cover_path,
        "status": package.status,
        "published_at": package.published_at.isoformat() if package.published_at else None,
        "archived_at": package.archived_at.isoformat() if package.archived_at else None,
        "course_count": len(courses),
        "courses": courses,
        "teacher_ids": [item["id"] for item in teachers],
        "teachers": teachers,
        "assignment_status": "assigned" if teachers else "unassigned",
        "can_preview": can_preview,
        "can_schedule": bool(
            actor
            and package.status == "published"
            and (is_admin_actor(actor) or teacher_has_course_package_access(db, actor, package.id))
        ),
        "created_at": package.created_at.isoformat() if package.created_at else None,
        "updated_at": package.updated_at.isoformat() if package.updated_at else None,
    }


def derived_schedule_status(schedule: CourseSchedule) -> str:
    if schedule.status == "canceled":
        return "canceled"
    current = beijing_now_naive()
    if schedule.starts_at > current:
        return "scheduled"
    if schedule.due_at and schedule.due_at < current:
        return "overdue"
    return "active"


def teacher_can_read_schedule(db: Session, actor: User, schedule: CourseSchedule) -> bool:
    if is_admin_actor(actor):
        return True
    if schedule.target_type == "classroom":
        return teacher_can_manage_classroom(db, actor, schedule.classroom_id)
    return schedule.created_by_user_id == actor.id


def teacher_can_manage_schedule(db: Session, actor: User, schedule: CourseSchedule) -> bool:
    if not teacher_can_read_schedule(db, actor, schedule):
        return False
    package = schedule.course.package if schedule.course else None
    return teacher_can_preview_course_package(db, actor, package)


def schedule_payload(db: Session, schedule: CourseSchedule, actor: User | None = None, student: User | None = None) -> dict:
    submission_count = db.query(func.count(TaskSubmission.id)).join(Task, TaskSubmission.task_id == Task.id).filter(
        Task.course_schedule_id == schedule.id
    ).scalar() or 0
    has_submitted = bool(student and db.query(TaskSubmission.id).join(Task, TaskSubmission.task_id == Task.id).filter(
        Task.course_schedule_id == schedule.id,
        TaskSubmission.user_id == student.id,
    ).first())
    target_name = schedule.target_student.name if schedule.target_type == "student" and schedule.target_student else schedule.classroom.name if schedule.classroom else ""
    course_access_status = "active"
    if actor and not teacher_can_preview_course_package(db, actor, schedule.course.package if schedule.course else None):
        course_access_status = "revoked"
    can_manage = bool(actor and teacher_can_manage_schedule(db, actor, schedule))
    expose_course_details = not actor or is_admin_actor(actor) or course_access_status == "active"
    return {
        "id": schedule.id,
        "course_id": schedule.course_id,
        "course_title": schedule.course.title if schedule.course else "",
        "package_id": schedule.course.package_id if schedule.course else None,
        "package_title": schedule.course.package.title if schedule.course and schedule.course.package else "",
        "target_type": schedule.target_type,
        "target_id": schedule.target_student_id if schedule.target_type == "student" else schedule.classroom_id,
        "target_name": target_name,
        "created_by_user_id": schedule.created_by_user_id,
        "created_by_name": schedule.creator.name if schedule.creator else "",
        "starts_at": schedule.starts_at.isoformat(),
        "due_at": schedule.due_at.isoformat() if schedule.due_at else None,
        "status": derived_schedule_status(schedule),
        "stored_status": schedule.status,
        "canceled_reason": schedule.canceled_reason,
        "submission_count": submission_count,
        "has_submitted": has_submitted,
        "can_manage": can_manage,
        "course_access_status": course_access_status,
        "read_only_reason": "course_access_revoked" if actor and course_access_status == "revoked" else "",
        "task_id": schedule.task.id if schedule.task else None,
        "assignment_instructions": schedule.course.assignment_instructions if schedule.course and expose_course_details else "",
        "tool_scope": schedule.course.tool_scope if schedule.course and expose_course_details else "",
        "student_archived": bool(schedule.target_student and schedule.target_student.archived_at),
        "created_at": schedule.created_at.isoformat() if schedule.created_at else None,
        "updated_at": schedule.updated_at.isoformat() if schedule.updated_at else None,
    }


def submission_payload(db: Session, submission: TaskSubmission, actor: User | None = None) -> dict:
    result = to_submission_dict(submission)
    schedule = submission.task.course_schedule if submission.task else None
    course_access_status = "active"
    can_review = bool(actor)
    read_only_reason = ""
    if actor and schedule and not teacher_can_preview_course_package(db, actor, schedule.course.package if schedule.course else None):
        course_access_status = "revoked"
        can_review = False
        read_only_reason = "course_access_revoked"
    elif actor and schedule:
        can_review = teacher_can_manage_schedule(db, actor, schedule)
        if not can_review:
            read_only_reason = "schedule_forbidden"
    if actor and submission.user and submission.user.archived_at and not is_admin_actor(actor):
        can_review = False
        read_only_reason = "archived_student"
    result.update({
        "package_id": schedule.course.package_id if schedule and schedule.course else None,
        "package_title": schedule.course.package.title if schedule and schedule.course and schedule.course.package else "",
        "course_access_status": course_access_status,
        "can_review": can_review,
        "read_only_reason": read_only_reason,
    })
    return result


def sync_schedule_task(db: Session, schedule: CourseSchedule) -> Task:
    course = schedule.course
    task = schedule.task
    if not task:
        task = Task(
            owner_teacher_id=schedule.created_by_user_id,
            course_schedule_id=schedule.id,
            target_student_id=schedule.target_student_id,
            classroom_id=schedule.classroom_id,
            lesson_id=None,
            title=course.title,
            instructions=course.assignment_instructions,
            tool_scope=course.tool_scope,
            status="published",
            starts_at=schedule.starts_at,
            due_at=schedule.due_at,
            rubric_json=course.rubric_json,
            task_kind="schedule",
        )
        db.add(task)
    else:
        task.title = course.title
        task.instructions = course.assignment_instructions
        task.tool_scope = course.tool_scope
        task.starts_at = schedule.starts_at
        task.due_at = schedule.due_at
        task.rubric_json = course.rubric_json
        task.status = "archived" if schedule.status == "canceled" else "published"
    return task


def sync_course_schedule_tasks(db: Session, course: CurriculumCourse) -> None:
    for schedule in course.schedules:
        sync_schedule_task(db, schedule)


def submit_project_for_task(db: Session, task: Task, student: User, project_id: int) -> TaskSubmission:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == student.id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "请选择自己的作品提交。"})
    if project.lifecycle_status != "active":
        raise HTTPException(status_code=409, detail={"code": "PROJECT_NOT_ACTIVE", "message": "归档或回收站中的作品需要先恢复后才能提交。"})
    if project.moderation_status != "approved":
        raise HTTPException(status_code=409, detail={"code": "PROJECT_MODERATION_PENDING", "message": "图片作品需要教师审核通过后才能提交。"})
    rubric = normalize_rubric(json.loads(task.rubric_json or "[]"))
    max_score = sum(int(item.get("max_score") or 0) for item in rubric) or 100
    submission = db.query(TaskSubmission).filter(
        TaskSubmission.task_id == task.id,
        TaskSubmission.user_id == student.id,
    ).first()
    is_late = bool(task.due_at and beijing_now_naive() > task.due_at)
    if submission:
        submission.owner_teacher_id = task.owner_teacher_id
        submission.project_id = project.id
        submission.classroom_id = student.classroom_id
        submission.status = "submitted"
        submission.feedback = ""
        submission.score = None
        submission.version_count = (submission.version_count or 0) + 1
        submission.is_late = is_late
        if not submission.rubric_snapshot_json or submission.rubric_snapshot_json == "[]":
            submission.rubric_snapshot_json = json_dumps(rubric)
            submission.max_score_snapshot = max_score
    else:
        submission = TaskSubmission(
            owner_teacher_id=task.owner_teacher_id,
            task_id=task.id,
            project_id=project.id,
            user_id=student.id,
            classroom_id=student.classroom_id,
            status="submitted",
            version_count=1,
            is_late=is_late,
            rubric_snapshot_json=json_dumps(rubric),
            max_score_snapshot=max_score,
        )
        db.add(submission)
        db.flush()
    db.flush()
    db.add(SubmissionVersion(
        submission_id=submission.id,
        version_number=submission.version_count,
        project_id=project.id,
        project_title=project.title,
        project_summary=project.summary,
        project_file_path=project.file_path,
        is_late=is_late,
    ))
    db.commit()
    db.refresh(submission)
    return submission


def normalize_rubric_payload(raw: str | None) -> list[dict]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        value = []
    if not isinstance(value, list) or not value:
        return [{"criterion": "完成度", "max_score": 100}]
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion") or "").strip()
        try:
            max_score = int(item.get("max_score") or 0)
        except (TypeError, ValueError):
            continue
        if criterion and max_score > 0:
            result.append({"criterion": criterion, "max_score": max_score})
    return result or [{"criterion": "完成度", "max_score": 100}]


def ensure_archived_student_record_writable(actor: User, student: User | None, resource_name: str) -> None:
    if student and student.archived_at and not is_admin_actor(actor):
        raise HTTPException(
            status_code=403,
            detail={"code": "ARCHIVED_STUDENT_RECORD_READ_ONLY", "message": f"归档学员的历史{resource_name}仅供查看。"},
        )


def ensure_curriculum_admin(db: Session, teacher: TeacherSession) -> User:
    actor = teacher_actor(db, teacher)
    if not is_admin_actor(actor):
        raise HTTPException(
            status_code=403,
            detail={"code": "CURRICULUM_ADMIN_REQUIRED", "message": "只有管理员可以维护课程内容和资料。"},
        )
    return actor


def classroom_payload(db: Session, classroom: Classroom, actor: User) -> dict:
    assignments = (
        db.query(ClassroomTeacher, User)
        .join(User, User.id == ClassroomTeacher.teacher_id)
        .filter(ClassroomTeacher.classroom_id == classroom.id)
        .order_by(User.name.asc(), User.id.asc())
        .all()
    )
    teachers = [
        {"id": user.id, "name": user.name, "username": user.username, "active": user.active}
        for _, user in assignments
    ]
    return {
        **to_classroom_dict(classroom),
        "teacher_ids": [item["id"] for item in teachers],
        "teachers": teachers,
        "can_manage": teacher_can_manage_classroom(db, actor, classroom.id),
        "assignment_status": "assigned" if teachers else "unassigned",
    }


def image_extension(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return None


def validate_asset_content(extension: str, content: bytes):
    signatures = {
        ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        ".webp": lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        ".gif": lambda data: data.startswith((b"GIF87a", b"GIF89a")),
        ".pdf": lambda data: data.startswith(b"%PDF"),
        ".docx": lambda data: data.startswith(b"PK"),
        ".pptx": lambda data: data.startswith(b"PK"),
        ".xlsx": lambda data: data.startswith(b"PK"),
        ".sb3": lambda data: data.startswith(b"PK"),
        ".wav": lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE",
        ".ogg": lambda data: data.startswith(b"OggS"),
        ".webm": lambda data: data.startswith(b"\x1aE\xdf\xa3"),
        ".mp4": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
        ".mov": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
        ".m4a": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
        ".mp3": lambda data: data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0),
    }
    validator = signatures.get(extension)
    if validator and not validator(content):
        raise HTTPException(status_code=400, detail={"code": "ASSET_SIGNATURE_INVALID", "message": "文件内容与后缀不一致，已拒绝保存。"})
    if extension in {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css"} and b"\x00" in content[:8192]:
        raise HTTPException(status_code=400, detail={"code": "ASSET_TEXT_INVALID", "message": "文本或代码素材包含二进制内容。"})


def inspect_asset(asset_type: str, file_path: str, content: bytes | None = None) -> dict:
    if asset_type not in ASSET_EXTENSIONS:
        raise HTTPException(status_code=400, detail={"code": "ASSET_TYPE_INVALID", "message": "素材类型不受支持。"})
    remote = file_path.startswith(("http://", "https://"))
    parsed_path = urlparse(file_path).path if remote else file_path
    extension = Path(parsed_path).suffix.lower()
    if extension not in ASSET_EXTENSIONS[asset_type]:
        raise HTTPException(status_code=400, detail={"code": "ASSET_EXTENSION_INVALID", "message": f"{asset_type} 素材不支持 {extension or '无后缀'} 文件。"})
    if remote:
        if not file_path.startswith("https://"):
            raise HTTPException(status_code=400, detail={"code": "ASSET_URL_INSECURE", "message": "远程素材必须使用 HTTPS 链接。"})
        return {"size": 0, "extension": extension, "mime_type": mimetypes.guess_type(parsed_path)[0] or "application/octet-stream", "checksum": "", "safety_status": "remote_unverified"}
    if content is None:
        path = Path(file_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "ASSET_FILE_MISSING", "message": "素材文件不存在，请检查本地路径。"})
        if path.stat().st_size > MAX_ASSET_BYTES:
            raise HTTPException(status_code=413, detail={"code": "ASSET_FILE_TOO_LARGE", "message": "素材文件不能超过 50 MB。"})
        content = path.read_bytes()
    if not content:
        raise HTTPException(status_code=400, detail={"code": "ASSET_FILE_EMPTY", "message": "上传文件为空。"})
    if len(content) > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail={"code": "ASSET_FILE_TOO_LARGE", "message": "素材文件不能超过 50 MB。"})
    validate_asset_content(extension, content)
    return {"size": len(content), "extension": extension, "mime_type": mimetypes.guess_type(file_path)[0] or "application/octet-stream", "checksum": hashlib.sha256(content).hexdigest(), "safety_status": "verified"}


def structured_asset_metadata(metadata_json: str, display_name: str = "", description: str = "", tags: list[str] | None = None) -> str:
    try:
        legacy = json.loads(metadata_json or "{}")
        if not isinstance(legacy, dict):
            legacy = {}
    except json.JSONDecodeError:
        legacy = {"legacy_note": metadata_json}
    return json_dumps({
        "name": display_name.strip() or str(legacy.get("name") or ""),
        "description": description.strip() or str(legacy.get("description") or legacy.get("note") or legacy.get("legacy_note") or ""),
        "tags": [str(tag).strip() for tag in (tags or legacy.get("tags") or []) if str(tag).strip()][:20],
    })


def normalize_rubric(items: list[dict]) -> list[dict]:
    if not 1 <= len(items) <= 10:
        raise HTTPException(status_code=400, detail={"code": "TASK_RUBRIC_INVALID", "message": "评分规则需要 1 到 10 个评分项。"})
    normalized: list[dict] = []
    seen: set[str] = set()
    total = 0
    for item in items:
        criterion = str(item.get("criterion") or "").strip()
        try:
            max_score = int(item.get("max_score") or 0)
        except (TypeError, ValueError):
            max_score = 0
        if not criterion or criterion in seen or max_score <= 0:
            raise HTTPException(status_code=400, detail={"code": "TASK_RUBRIC_INVALID", "message": "评分项名称必须唯一且分值必须大于 0。"})
        seen.add(criterion)
        total += max_score
        normalized.append({"criterion": criterion[:80], "max_score": max_score})
    if total > 100:
        raise HTTPException(status_code=400, detail={"code": "TASK_RUBRIC_TOTAL_INVALID", "message": "评分规则总分不能超过 100 分。"})
    return normalized


def owned_ai_input_path(identity: dict, raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    if raw_path.startswith(("http://", "https://")):
        return raw_path
    if identity["role"] == "anonymous":
        raise HTTPException(status_code=403, detail={"code": "AUTH_REQUIRED", "message": "请先登录后上传和使用参考图片。"})
    owner = f"student-{identity['student'].id}" if identity["role"] == "student" else "teacher"
    owner_dir = (AI_INPUT_DIR / owner).resolve()
    candidate = Path(raw_path).resolve()
    if not candidate.is_file() or owner_dir not in candidate.parents:
        raise HTTPException(status_code=403, detail={"code": "AI_INPUT_FORBIDDEN", "message": "参考图片不存在或不属于当前账号。"})
    return str(candidate)


def parse_backup_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return local_naive(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "CoderAI 学堂 API"}


def ensure_tool_allowed_for_student(db: Session, identity: dict, tool: str):
    if identity["role"] != "student":
        return
    student = identity["student"]
    ensure_student_ai_consent(db, student)
    tasks = (
        db.query(Task)
        .filter((Task.classroom_id.is_(None)) | (Task.classroom_id == student.classroom_id))
        .all()
    )
    if not tasks:
        return
    allowed_tools = {
        scope.strip()
        for task in tasks
        for scope in task.tool_scope.split(",")
        if scope.strip()
    }
    if tool not in allowed_tools:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TOOL_NOT_ALLOWED",
                "message": f"教师当前没有为你的课堂开放{tool_label(tool)}。",
            },
        )


def tool_label(tool: str) -> str:
    return {
        "text": "文字生成",
        "image": "图片生成",
        "video": "视频生成",
        "workflow": "工作流",
    }.get(tool, tool)


@app.post("/api/auth/teacher-login")
def teacher_login(payload: TeacherLoginRequest, request: Request, db: Session = Depends(get_db)):
    username = payload.username.strip().lower() or "admin"
    client_host = request.client.host if request.client else "local"
    identifier = f"{username}:{client_host}"
    check_login_allowed(db, "teacher", identifier)
    try:
        user = login_teacher_by_credentials(db, username, payload.password)
    except HTTPException:
        retry_after = record_login_failure(db, "teacher", identifier)
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail={"code": "LOGIN_RATE_LIMITED", "message": f"登录失败次数过多，请在 {retry_after} 秒后重试。"},
                headers={"Retry-After": str(retry_after)},
            )
        raise HTTPException(status_code=403, detail={"code": "ACCOUNT_INVALID", "message": "教师用户名或密码不正确。"})
    clear_login_failures(db, "teacher", identifier)
    return {
        "role": "teacher",
        "password_change_required": teacher_password_change_required(db, user),
        "password_is_weak": user.role == "teacher" and teacher_password_is_weak(payload.password),
        **issue_teacher_session(db, user, payload.device_name),
    }


@app.post("/api/auth/teacher-refresh")
def teacher_refresh(payload: TeacherRefreshRequest, db: Session = Depends(get_db)):
    auth = refresh_teacher_session(db, payload.refresh_token)
    return {"role": "teacher", "password_change_required": bool((auth.get("user") or {}).get("password_change_required")), **auth}


@app.post("/api/auth/teacher-logout")
def teacher_logout(payload: TeacherLogoutRequest, db: Session = Depends(get_db)):
    revoke_teacher_session_by_refresh_token(db, payload.refresh_token)
    return {"status": "ok"}


@app.get("/api/auth/teacher-sessions")
def teacher_sessions(
    current: TeacherSession = Depends(require_teacher_session),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(TeacherSession)
        .filter(
            TeacherSession.user_id == current.user_id,
            TeacherSession.revoked_at.is_(None),
            TeacherSession.expires_at > now(),
        )
        .order_by(TeacherSession.last_seen_at.desc())
        .all()
    )
    return {"sessions": [{**teacher_session_payload(item), "current": item.id == current.id} for item in sessions]}


@app.delete("/api/auth/teacher-sessions/{session_id}")
def delete_teacher_session(
    session_id: int,
    current: TeacherSession = Depends(require_teacher_session),
    db: Session = Depends(get_db),
):
    session = db.get(TeacherSession, session_id)
    if not session or session.user_id != current.user_id or session.revoked_at is not None:
        raise HTTPException(status_code=404, detail={"code": "TEACHER_SESSION_NOT_FOUND", "message": "教师会话不存在或已失效。"})
    revoke_teacher_session(db, session)
    return {"status": "ok", "revoked_session_id": session.id, "current_revoked": session.id == current.id}


@app.post("/api/auth/student-login")
def student_login(payload: StudentLoginRequest, request: Request, db: Session = Depends(get_db)):
    login_name = payload.username.strip().lower()
    if not login_name or not payload.password:
        raise HTTPException(status_code=400, detail={"code": "LOGIN_FIELDS_REQUIRED", "message": "请输入用户名和密码。"})
    client_host = request.client.host if request.client else "local"
    identifier = f"{login_name}:{client_host}"
    check_login_allowed(db, "student", identifier)
    try:
        student = login_student_by_credentials(db, payload.username, payload.password)
    except HTTPException:
        retry_after = record_login_failure(db, "student", identifier)
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail={"code": "LOGIN_RATE_LIMITED", "message": f"登录失败次数过多，请在 {retry_after} 秒后重试。"},
                headers={"Retry-After": str(retry_after)},
            )
        raise
    clear_login_failures(db, "student", identifier)
    return {
        "role": "student",
        "legacy_login": False,
        "registration_required": False,
        "student": to_user_dict(student),
        **issue_student_session(db, student, payload.device_name),
    }


@app.post("/api/auth/student-register")
def student_register(payload: StudentRegisterRequest, request: Request, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=410,
        detail={"code": "STUDENT_SELF_REGISTRATION_DISABLED", "message": "学生账号只能由管理员创建。"},
    )


@app.post("/api/auth/student-logout")
def student_logout(
    x_coderai_student_token: str = Header(default="", alias="X-CoderAI-Student-Token"),
    db: Session = Depends(get_db),
):
    revoke_student_session_by_token(db, x_coderai_student_token)
    return {"status": "ok"}


@app.post("/api/auth/change-student-password")
def update_student_password(
    payload: StudentPasswordChangeRequest,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    change_student_password(db, student, payload.current_password, payload.next_password)
    db.refresh(student)
    return {
        "role": "student",
        "student": to_user_dict(student),
        **issue_student_session(db, student, "密码修改后的当前设备"),
    }


@app.post("/api/auth/change-teacher-password")
def update_teacher_password(
    payload: TeacherPasswordChangeRequest,
    teacher: TeacherSession = Depends(require_teacher_session),
    db: Session = Depends(get_db),
):
    user = teacher.user or db.get(User, teacher.user_id)
    if not user:
        raise HTTPException(status_code=403, detail={"code": "TEACHER_AUTH_REQUIRED", "message": "教师账号不可用。"})
    change_teacher_password(db, user, payload.current_password, payload.next_password)
    record_teacher_audit(
        db,
        teacher,
        "teacher.password.changed",
        target_type="teacher_account",
        target_id=user.id,
        summary=f"教师 {user.username} 修改本人密码并撤销旧会话",
    )
    db.refresh(user)
    return {
        "status": "ok",
        "role": "teacher",
        "password_change_required": False,
        "password_is_weak": user.role == "teacher" and teacher_password_is_weak(payload.next_password),
        **issue_teacher_session(db, user, "密码修改后的当前设备"),
    }


@app.get("/api/accounts/teachers")
def list_teacher_accounts(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    accounts = db.query(User).filter(User.role.in_(("teacher", "admin"))).order_by(User.created_at.asc(), User.id.asc()).all()
    return {"accounts": [account_payload(item) for item in accounts]}


@app.post("/api/accounts/teachers")
def create_teacher_account(
    payload: TeacherAccountCreateRequest,
    current: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    username = validate_username(payload.username)
    if find_account_by_username(db, username):
        raise HTTPException(status_code=409, detail={"code": "USERNAME_EXISTS", "message": "用户名已被使用，请更换。"})
    temporary_password = generate_temporary_password()
    (validate_admin_password if payload.role == "admin" else validate_teacher_password)(temporary_password)
    account = User(
        name=payload.name.strip(),
        username=username,
        password_hash=hash_password(temporary_password),
        password_change_required=True,
        credential_version=1,
        role=payload.role,
        active=True,
        registered_at=now(),
        created_by_user_id=current.user_id,
        age_level="",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    record_teacher_audit(
        db,
        current,
        "teacher_account.created",
        target_type="teacher_account",
        target_id=account.id,
        summary=f"创建{('管理员' if account.role == 'admin' else '教师')}账号：{account.username}",
        details={"username": account.username, "role": account.role},
    )
    return {"account": account_payload(account), "temporary_password": temporary_password}


@app.put("/api/accounts/teachers/{account_id}")
def update_teacher_account(
    account_id: int,
    payload: TeacherAccountUpdateRequest,
    current: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = db.query(User).filter(User.id == account_id, User.role.in_(("teacher", "admin"))).first()
    if not account:
        raise HTTPException(status_code=404, detail={"code": "TEACHER_ACCOUNT_NOT_FOUND", "message": "教师账号不存在。"})
    if account.id == current.user_id and (not payload.active or payload.role != "admin"):
        raise HTTPException(status_code=409, detail={"code": "CURRENT_ADMIN_PROTECTED", "message": "当前管理员不能停用或降级自己的账号。"})
    active_admins = db.query(User).filter(User.role == "admin", User.active.is_(True)).count()
    removing_admin = account.role == "admin" and account.active and (payload.role != "admin" or not payload.active)
    if removing_admin and active_admins <= 1:
        raise HTTPException(status_code=409, detail={"code": "LAST_ADMIN_PROTECTED", "message": "系统至少需要保留一个启用的管理员账号。"})
    changed_security = account.role != payload.role or account.active != payload.active
    remove_classroom_assignments = account.role == "teacher" and (payload.role != "teacher" or not payload.active)
    account.name = payload.name.strip()
    account.role = payload.role
    account.active = payload.active
    if remove_classroom_assignments:
        db.query(ClassroomTeacher).filter(ClassroomTeacher.teacher_id == account.id).delete(synchronize_session=False)
    db.commit()
    if changed_security:
        revoke_all_teacher_sessions(db, account.id)
    db.refresh(account)
    record_teacher_audit(
        db,
        current,
        "teacher_account.updated",
        target_type="teacher_account",
        target_id=account.id,
        summary=f"更新教师账号：{account.username}",
        details={"username": account.username, "role": account.role, "active": account.active},
    )
    return {"account": account_payload(account)}


@app.post("/api/accounts/teachers/{account_id}/reset-password")
def reset_teacher_account_password(
    account_id: int,
    current: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = db.query(User).filter(User.id == account_id, User.role.in_(("teacher", "admin"))).first()
    if not account:
        raise HTTPException(status_code=404, detail={"code": "TEACHER_ACCOUNT_NOT_FOUND", "message": "教师账号不存在。"})
    if account.id == current.user_id:
        raise HTTPException(status_code=409, detail={"code": "USE_SELF_PASSWORD_CHANGE", "message": "请在安全设置中修改当前账号密码。"})
    temporary_password = generate_temporary_password()
    account.password_hash = hash_password(temporary_password)
    account.password_change_required = True
    account.credential_version = max(1, account.credential_version or 1) + 1
    db.commit()
    revoke_all_teacher_sessions(db, account.id)
    db.refresh(account)
    record_teacher_audit(
        db,
        current,
        "teacher_account.password_reset",
        target_type="teacher_account",
        target_id=account.id,
        summary=f"重置教师账号密码：{account.username}",
        details={"username": account.username},
    )
    return {"account": account_payload(account), "temporary_password": temporary_password}


@app.post("/api/text/generate")
async def text_generate(
    payload: TextGenerateRequest,
    identity: dict = Depends(optional_teacher_or_student),
    db: Session = Depends(get_db),
):
    ensure_tool_allowed_for_student(db, identity, "text")
    student = identity.get("student")
    actor = identity_teacher(identity)
    owner_teacher_id = None if student else (actor.id if actor else None)
    run_moderation(db, payload.prompt, user_id=student.id if student else None, owner_teacher_id=owner_teacher_id)
    age_level = identity["student"].age_level if identity["role"] == "student" else payload.age_level
    text = await generate_text(db, payload.prompt, payload.mode, age_level, user_id=student.id if student else None)
    project = None
    if payload.save_project:
        if identity["role"] == "anonymous":
            raise HTTPException(status_code=403, detail={"code": "AUTH_REQUIRED", "message": "请先登录学生端后再保存作品。"})
        project = save_project(
            db,
            f"文字作品：{payload.prompt[:24]}",
            "text",
            text,
            student=identity["student"],
            owner_teacher_id=owner_teacher_id,
        )
    return {"text": text, "project": to_project_dict(project) if project else None}


@app.post("/api/image/generate")
async def image_generate(
    payload: ImageGenerateRequest,
    identity: dict = Depends(optional_teacher_or_student),
    db: Session = Depends(get_db),
):
    ensure_tool_allowed_for_student(db, identity, "image")
    student = identity.get("student")
    actor = identity_teacher(identity)
    owner_teacher_id = None if student else (actor.id if actor else None)
    run_moderation(db, payload.prompt, user_id=student.id if student else None, owner_teacher_id=owner_teacher_id)
    is_primary_lower = identity["role"] == "student" and identity["student"].age_level == "primary_lower"
    source_image_path = owned_ai_input_path(identity, payload.source_image_path)
    result = await generate_image(
        db,
        payload.prompt,
        "明亮卡通" if is_primary_lower else payload.style,
        "1024x1024" if is_primary_lower else payload.size,
        source_image_path,
        user_id=student.id if student else None,
    )
    project = None
    if payload.save_project:
        if identity["role"] == "anonymous":
            raise HTTPException(status_code=403, detail={"code": "AUTH_REQUIRED", "message": "请先登录学生端后再保存作品。"})
        project = save_project(
            db,
            f"图片作品：{payload.prompt[:24]}",
            "image",
            payload.prompt,
            result.get("file_path") or result.get("url", ""),
            student=identity["student"],
            moderation_status=str(result.get("moderation_status") or "pending"),
            moderation_reason=str(result.get("moderation_reason") or ""),
            moderation_log_id=result.get("moderation_log_id"),
            owner_teacher_id=owner_teacher_id,
        )
        attach_image_moderation(db, project, result.get("moderation_log_id"))
    public_result = safe_image_output(result, identity)
    return {**public_result, "project": project_payload_for_identity(project, identity) if project else None}


@app.post("/api/video/generate")
async def video_generate(
    payload: VideoGenerateRequest,
    identity: dict = Depends(optional_teacher_or_student),
    db: Session = Depends(get_db),
):
    ensure_tool_allowed_for_student(db, identity, "video")
    student = identity.get("student")
    actor = identity_teacher(identity)
    owner_teacher_id = None if student else (actor.id if actor else None)
    run_moderation(db, payload.prompt, user_id=student.id if student else None, owner_teacher_id=owner_teacher_id)
    if payload.save_project and identity["role"] == "anonymous":
        raise HTTPException(status_code=403, detail={"code": "AUTH_REQUIRED", "message": "请先登录学生端后再保存视频任务。"})
    task, project = await generate_video_task(
        db,
        payload.prompt,
        owned_ai_input_path(identity, payload.source_image_path),
        min(payload.duration_seconds, 5) if identity["role"] == "student" and identity["student"].age_level == "primary_lower" else payload.duration_seconds,
        student=identity["student"],
        save_as_project=payload.save_project and identity["role"] != "anonymous",
        owner_teacher_id=owner_teacher_id,
    )
    return {
        "message": "视频生成任务已提交，可稍后刷新任务状态。",
        "task": to_video_task_dict(task),
        "project": to_project_dict(project) if project else None,
    }


@app.post("/api/ai-inputs/upload")
async def upload_ai_input(
    file: UploadFile = File(...),
    identity: dict = Depends(require_student_or_teacher),
):
    content = await file.read(MAX_AI_INPUT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail={"code": "AI_INPUT_EMPTY", "message": "参考图片为空。"})
    if len(content) > MAX_AI_INPUT_BYTES:
        raise HTTPException(status_code=413, detail={"code": "AI_INPUT_TOO_LARGE", "message": "参考图片不能超过 10 MB。"})
    extension = image_extension(content)
    if not extension:
        raise HTTPException(status_code=400, detail={"code": "AI_INPUT_TYPE_INVALID", "message": "仅支持 PNG、JPEG 或 WebP 图片。"})
    owner = f"student-{identity['student'].id}" if identity["role"] == "student" else f"teacher-{identity['teacher'].id}"
    owner_dir = AI_INPUT_DIR / owner
    owner_dir.mkdir(parents=True, exist_ok=True)
    target = owner_dir / f"{secrets.token_hex(16)}{extension}"
    target.write_bytes(content)
    return {"input": {"file_name": file.filename or target.name, "file_path": str(target), "size": len(content), "content_type": file.content_type or "image/*"}}


@app.get("/api/video/tasks")
def list_video_tasks(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(VideoTask)
    if identity["role"] == "student":
        query = query.filter(VideoTask.user_id == identity["student"].id)
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(VideoTask, db, identity["teacher"]))
    tasks = query.order_by(VideoTask.updated_at.desc()).limit(100).all()
    return {"tasks": [to_video_task_dict(task) for task in tasks]}


@app.post("/api/video/tasks/{task_id}/refresh")
async def refresh_video_task_status(
    task_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    task = db.query(VideoTask).filter(VideoTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={"code": "VIDEO_TASK_NOT_FOUND", "message": "视频任务不存在。"})
    if identity["role"] == "student" and task.user_id != identity["student"].id:
        raise HTTPException(status_code=403, detail={"code": "VIDEO_TASK_FORBIDDEN", "message": "不能查看其他学生的视频任务。"})
    if identity["role"] == "teacher":
        ensure_teaching_record_access(db, identity["teacher"], task.owner_teacher_id, task.classroom_id, "视频任务")
    task = await refresh_video_task(db, task)
    return {"task": to_video_task_dict(task)}


@app.post("/api/video/tasks/{task_id}/retry")
async def retry_video_task_status(
    task_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    task = db.query(VideoTask).filter(VideoTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={"code": "VIDEO_TASK_NOT_FOUND", "message": "视频任务不存在。"})
    if identity["role"] == "student" and task.user_id != identity["student"].id:
        raise HTTPException(status_code=403, detail={"code": "VIDEO_TASK_FORBIDDEN", "message": "不能重试其他学生的视频任务。"})
    if identity["role"] == "teacher":
        ensure_teaching_record_access(db, identity["teacher"], task.owner_teacher_id, task.classroom_id, "视频任务")
    if identity["role"] == "student":
        ensure_tool_allowed_for_student(db, identity, "video")
    elif task.user:
        ensure_student_ai_consent(db, task.user)
    run_moderation(db, task.prompt, user_id=task.user_id)
    task = await retry_video_task(db, task)
    return {"message": "视频任务已重试。", "task": to_video_task_dict(task)}


@app.post("/api/video/tasks/{task_id}/cancel")
def cancel_video_task_status(
    task_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    task = db.query(VideoTask).filter(VideoTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={"code": "VIDEO_TASK_NOT_FOUND", "message": "视频任务不存在。"})
    if identity["role"] == "student" and task.user_id != identity["student"].id:
        raise HTTPException(status_code=403, detail={"code": "VIDEO_TASK_FORBIDDEN", "message": "不能取消其他学生的视频任务。"})
    if identity["role"] == "teacher":
        ensure_teaching_record_access(db, identity["teacher"], task.owner_teacher_id, task.classroom_id, "视频任务")
    if task.status in {"success", "failed", "timed_out", "download_failed", "expired", "canceled"}:
        raise HTTPException(status_code=409, detail={"code": "VIDEO_TASK_FINAL", "message": "任务已经结束，不能取消。"})
    task.status = "canceled"
    task.error_message = "已在本地取消；云端服务可能仍会继续处理并产生费用。"
    if task.project:
        task.project.summary = f"# 视频任务\n\n状态：已取消\n\n提示词：{task.prompt}"
    db.commit()
    db.refresh(task)
    return {"task": to_video_task_dict(task)}


WORKFLOW_NODE_TYPES = {"input", "text.generate", "image.generate"}


def validate_workflow_definition(definition: dict) -> dict:
    nodes = definition.get("nodes") if isinstance(definition, dict) else None
    edges = definition.get("edges") if isinstance(definition, dict) else None
    if not isinstance(nodes, list) or not nodes or len(nodes) > 20:
        raise HTTPException(status_code=400, detail={"code": "WORKFLOW_NODES_INVALID", "message": "工作流需要 1 到 20 个节点。"})
    if not isinstance(edges, list) or len(edges) > 40:
        raise HTTPException(status_code=400, detail={"code": "WORKFLOW_EDGES_INVALID", "message": "工作流连线格式不正确。"})
    node_ids: list[str] = []
    normalized_nodes: list[dict] = []
    for index, raw in enumerate(nodes):
        node_id = str(raw.get("id") or "").strip()
        node_type = str(raw.get("type") or "").strip()
        if not node_id or node_id in node_ids or node_type not in WORKFLOW_NODE_TYPES:
            raise HTTPException(status_code=400, detail={"code": "WORKFLOW_NODE_INVALID", "message": "节点 ID 重复、为空或节点类型不受支持。"})
        node_ids.append(node_id)
        position = raw.get("position") if isinstance(raw.get("position"), dict) else {}
        params = dict(raw.get("params")) if isinstance(raw.get("params"), dict) else {}
        if node_type in {"text.generate", "image.generate"}:
            raw_provider_id = params.get("provider_id")
            if raw_provider_id not in (None, "", "auto"):
                try:
                    provider_id = int(raw_provider_id)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail={"code": "WORKFLOW_MODEL_INVALID", "message": "节点模型服务配置无效。"})
                if provider_id < 1:
                    raise HTTPException(status_code=400, detail={"code": "WORKFLOW_MODEL_INVALID", "message": "节点模型服务配置无效。"})
                params["provider_id"] = provider_id
            else:
                params.pop("provider_id", None)
            params["model"] = str(params.get("model") or "")[:120]
            params["provider_type"] = str(params.get("provider_type") or "")[:40]
        normalized_nodes.append({
            "id": node_id,
            "type": node_type,
            "label": str(raw.get("label") or node_type)[:80],
            "params": params,
            "position": {"x": float(position.get("x", 40 + index * 220)), "y": float(position.get("y", 100))},
        })
    if sum(1 for node in normalized_nodes if node["type"] == "input") != 1:
        raise HTTPException(status_code=400, detail={"code": "WORKFLOW_INPUT_REQUIRED", "message": "工作流必须且只能包含一个输入节点。"})
    normalized_edges: list[dict] = []
    successors = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for index, raw in enumerate(edges):
        source, target = str(raw.get("source") or ""), str(raw.get("target") or "")
        if source not in indegree or target not in indegree or source == target:
            raise HTTPException(status_code=400, detail={"code": "WORKFLOW_EDGE_INVALID", "message": "工作流包含无效或悬空连线。"})
        edge_id = str(raw.get("id") or f"e-{source}-{target}-{index}")
        normalized_edges.append({"id": edge_id, "source": source, "target": target})
        successors[source].append(target)
        indegree[target] += 1
    queue = [node_id for node_id, count in indegree.items() if count == 0]
    visited: list[str] = []
    while queue:
        current = queue.pop(0)
        visited.append(current)
        for target in successors[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(visited) != len(node_ids):
        raise HTTPException(status_code=400, detail={"code": "WORKFLOW_CYCLE", "message": "工作流不能包含循环连线。"})
    return {"nodes": normalized_nodes, "edges": normalized_edges}


def workflow_payload(workflow: Workflow) -> dict:
    try:
        definition = json.loads(workflow.definition_json or "{}")
    except json.JSONDecodeError:
        definition = {"nodes": [], "edges": []}
    return {
        "id": workflow.id,
        "owner_teacher_id": workflow.owner_teacher_id,
        "name": workflow.name,
        "description": workflow.description,
        "owner_user_id": workflow.owner_user_id,
        "owner_name": workflow.owner.name if workflow.owner else "教师",
        "classroom_id": workflow.classroom_id,
        "classroom_name": workflow.classroom.name if workflow.classroom else "",
        "status": workflow.status,
        "version": workflow.version,
        "definition": definition,
        "created_at": workflow.created_at.isoformat() if workflow.created_at else "",
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else "",
    }


def can_access_workflow(db: Session, workflow: Workflow, identity: dict, edit: bool = False) -> bool:
    if identity["role"] == "teacher":
        actor = identity_teacher(identity)
        if is_admin_actor(actor):
            return True
        return (
            workflow.classroom_id is not None and teacher_can_manage_classroom(db, actor, workflow.classroom_id)
        ) or (
            workflow.classroom_id is None and workflow.owner_teacher_id == actor.id
        )
    student = identity["student"]
    if workflow.owner_user_id == student.id:
        return True
    return not edit and workflow.status == "published" and workflow.classroom_id in (None, student.classroom_id)


@app.get("/api/workflows")
def list_saved_workflows(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(Workflow)
    if identity["role"] == "student":
        student = identity["student"]
        query = query.filter(
            (Workflow.owner_user_id == student.id)
            | ((Workflow.status == "published") & ((Workflow.classroom_id.is_(None)) | (Workflow.classroom_id == student.classroom_id)))
        )
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(Workflow, db, identity["teacher"]))
    items = query.order_by(Workflow.updated_at.desc(), Workflow.id.desc()).limit(200).all()
    return {"workflows": [workflow_payload(item) for item in items]}


@app.post("/api/workflows")
def create_saved_workflow(
    payload: WorkflowSaveRequest,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    definition = validate_workflow_definition(payload.definition)
    student = identity["student"]
    actor = identity_teacher(identity)
    if actor:
        require_owned_classroom(db, payload.classroom_id, actor)
    workflow = Workflow(
        owner_teacher_id=None if student else actor.id,
        name=payload.name,
        description=payload.description,
        owner_user_id=student.id if student else None,
        classroom_id=student.classroom_id if student else payload.classroom_id,
        status="draft" if student else (payload.status if payload.status in {"draft", "published"} else "draft"),
        definition_json=json_dumps(definition),
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    return {"workflow": workflow_payload(workflow)}


@app.put("/api/workflows/{workflow_id}")
def update_saved_workflow(
    workflow_id: int,
    payload: WorkflowSaveRequest,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND", "message": "工作流不存在。"})
    if not can_access_workflow(db, workflow, identity, edit=True):
        raise HTTPException(status_code=403, detail={"code": "WORKFLOW_FORBIDDEN", "message": "不能编辑其他人的工作流。"})
    if identity["role"] == "teacher":
        require_owned_classroom(db, payload.classroom_id, identity["teacher"])
    workflow.name = payload.name
    workflow.description = payload.description
    workflow.definition_json = json_dumps(validate_workflow_definition(payload.definition))
    workflow.version += 1
    if identity["role"] == "teacher":
        workflow.classroom_id = payload.classroom_id
        workflow.status = payload.status if payload.status in {"draft", "published"} else "draft"
    else:
        workflow.status = "draft"
    db.commit()
    db.refresh(workflow)
    return {"workflow": workflow_payload(workflow)}


@app.post("/api/workflows/{workflow_id}/copy")
def copy_saved_workflow(
    workflow_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    source = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not source or not can_access_workflow(db, source, identity):
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND", "message": "工作流不存在或不可访问。"})
    student = identity["student"]
    copied = Workflow(
        owner_teacher_id=None if student else identity["teacher"].id,
        name=f"{source.name} - 副本",
        description=source.description,
        owner_user_id=student.id if student else None,
        classroom_id=student.classroom_id if student else source.classroom_id,
        status="draft",
        definition_json=source.definition_json,
    )
    db.add(copied)
    db.commit()
    db.refresh(copied)
    return {"workflow": workflow_payload(copied)}


@app.delete("/api/workflows/{workflow_id}")
def delete_saved_workflow(
    workflow_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND", "message": "工作流不存在。"})
    if not can_access_workflow(db, workflow, identity, edit=True):
        raise HTTPException(status_code=403, detail={"code": "WORKFLOW_FORBIDDEN", "message": "不能删除其他人的工作流。"})
    db.query(WorkflowRun).filter(WorkflowRun.workflow_id == workflow_id).update({WorkflowRun.workflow_id: None}, synchronize_session=False)
    db.delete(workflow)
    db.commit()
    return {"deleted": True, "workflow_id": workflow_id}


@app.get("/api/workflows/templates")
def get_workflow_templates():
    return {"templates": workflow_templates()}


def workflow_run_payload(item: WorkflowRun) -> dict:
    return {
        "id": item.id,
        "owner_teacher_id": item.owner_teacher_id,
        "workflow_id": item.workflow_id,
        "status": item.status,
        "input_json": item.input_json,
        "output_json": item.output_json,
        "node_states_json": item.node_states_json,
        "node_states": json.loads(item.node_states_json or "{}"),
        "error_message": item.error_message,
        "cancel_requested": item.cancel_requested,
        "user_id": item.user_id,
        "classroom_id": item.classroom_id,
        "created_at": item.created_at.isoformat() if item.created_at else "",
        "updated_at": item.updated_at.isoformat() if item.updated_at else "",
    }


def require_workflow_run_access(db: Session, run_id: int, identity: dict) -> WorkflowRun:
    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_RUN_NOT_FOUND", "message": "工作流运行记录不存在。"})
    if identity["role"] == "student" and run.user_id != identity["student"].id:
        raise HTTPException(status_code=403, detail={"code": "WORKFLOW_RUN_FORBIDDEN", "message": "不能查看或操作其他学生的工作流。"})
    if identity["role"] == "teacher":
        ensure_teaching_record_access(db, identity["teacher"], run.owner_teacher_id, run.classroom_id, "工作流运行记录")
    return run


def builtin_workflow_definition(template_id: str) -> tuple[dict, str]:
    template_modes = {
        "text_to_image": ("prompt_refine", "优化提示词"),
        "idea_to_story": ("story", "故事扩写"),
        "code_explain": ("code_explain", "代码解释"),
        "project_plan": ("prompt_refine", "项目步骤规划"),
    }
    if template_id not in template_modes:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_TEMPLATE_NOT_FOUND", "message": "工作流模板不存在。"})
    mode, title = template_modes[template_id]
    nodes = [
        {"id": "input", "type": "input", "label": "输入", "params": {}, "position": {"x": 20, "y": 80}},
        {"id": "text", "type": "text.generate", "label": title, "params": {"mode": mode}, "position": {"x": 240, "y": 80}},
    ]
    edges = [{"id": "e-input-text", "source": "input", "target": "text"}]
    if template_id == "text_to_image":
        nodes.append({"id": "image", "type": "image.generate", "label": "生成图片", "params": {"style": "classroom-friendly", "size": "1024x1024"}, "position": {"x": 460, "y": 80}})
        edges.append({"id": "e-text-image", "source": "text", "target": "image"})
    return validate_workflow_definition({"nodes": nodes, "edges": edges}), title


def workflow_graph(definition: dict) -> tuple[list[str], dict[str, dict], dict[str, list[str]]]:
    nodes = {node["id"]: node for node in definition["nodes"]}
    predecessors = {node_id: [] for node_id in nodes}
    successors = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for edge in definition["edges"]:
        predecessors[edge["target"]].append(edge["source"])
        successors[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    queue = [node_id for node_id, count in indegree.items() if count == 0]
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for target in successors[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return order, nodes, predecessors


def workflow_node_provider_id(db: Session, node: dict, capability: str) -> int | None:
    raw_provider_id = node.get("params", {}).get("provider_id")
    if raw_provider_id in (None, "", "auto"):
        return None
    try:
        provider_id = int(raw_provider_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "WORKFLOW_MODEL_INVALID", "message": "节点选择的模型服务无效。"})
    provider = active_provider(db, capability, provider_id)
    selected_model = str(node.get("params", {}).get("model") or "").strip()
    current_model = str(getattr(provider, f"{capability}_model", "") or "").strip()
    if selected_model and selected_model != current_model:
        raise HTTPException(
            status_code=409,
            detail={"code": "WORKFLOW_MODEL_CHANGED", "message": f"节点选择的模型 {selected_model} 已不再由该服务配置提供，请重新选择模型。"},
        )
    return provider.id


def workflow_cache_path(db: Session, workflow: Workflow | None, node: dict, source_text: str) -> Path:
    capability = "image" if node.get("type") == "image.generate" else "text"
    try:
        selected_provider_id = workflow_node_provider_id(db, node, capability)
        provider = active_provider(db, capability, selected_provider_id) if selected_provider_id else active_provider(db, capability)
    except HTTPException:
        provider = None
    signature = {
        "workflow_id": workflow.id if workflow else None,
        "workflow_version": workflow.version if workflow else 1,
        "provider_type": provider.provider_type if provider else "",
        "provider_id": provider.id if provider else None,
        "text_model": provider.text_model if provider else "",
        "image_model": provider.image_model if provider else "",
        "node": node,
        "input": source_text,
    }
    digest = hashlib.sha256(json_dumps(signature).encode("utf-8")).hexdigest()
    return WORKFLOW_CACHE_DIR / f"{digest}.json"


async def execute_workflow_run_background(run_id: int, retry_from_node: str | None = None, bind=None):
    db = Session(bind=bind) if bind is not None else SessionLocal()
    try:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if not run or run.status == "canceled" and not retry_from_node:
            return
        request = json.loads(run.input_json or "{}")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            run.status, run.error_message = "failed", "工作流输入不能为空。"
            db.commit()
            return
        workflow = db.query(Workflow).filter(Workflow.id == run.workflow_id).first() if run.workflow_id else None
        if workflow:
            definition = validate_workflow_definition(json.loads(workflow.definition_json or "{}"))
            output_title = workflow.name
        else:
            definition, output_title = builtin_workflow_definition(str(request.get("template_id") or "text_to_image"))
        order, nodes, predecessors = workflow_graph(definition)
        if retry_from_node and retry_from_node not in nodes:
            run.status, run.error_message = "failed", "要重试的节点不存在。"
            db.commit()
            return
        states = json.loads(run.node_states_json or "{}")
        if not states:
            states = {node_id: {"status": "pending", "label": nodes[node_id]["label"], "type": nodes[node_id]["type"], "output": None, "error": "", "cached": False} for node_id in order}
        start_index = order.index(retry_from_node) if retry_from_node else 0
        if retry_from_node:
            for node_id in order[start_index:]:
                states[node_id].update({"status": "pending", "output": None, "error": "", "cached": False})
        run.status = "running"
        run.cancel_requested = False
        run.error_message = ""
        run.node_states_json = json_dumps(states)
        db.commit()
        run_moderation(db, prompt, user_id=run.user_id)
        user = db.query(User).filter(User.id == run.user_id).first() if run.user_id else None
        age_level = user.age_level if user else "mixed"
        outputs: dict[str, Any] = {}
        last_text = prompt
        last_image = None
        for index, node_id in enumerate(order):
            node = nodes[node_id]
            if index < start_index:
                previous_output = states.get(node_id, {}).get("output")
                if previous_output is not None:
                    outputs[node_id] = previous_output
                    if isinstance(previous_output, str):
                        last_text = previous_output
                    elif isinstance(previous_output, dict):
                        last_image = previous_output
                continue
            db.refresh(run)
            if run.cancel_requested:
                states[node_id]["status"] = "canceled"
                run.status = "canceled"
                run.error_message = "运行已取消；正在调用的云端节点无法即时中断。"
                run.node_states_json = json_dumps(states)
                db.commit()
                return
            if node["type"] == "input":
                outputs[node_id] = prompt
                states[node_id].update({"status": "success", "output": prompt})
                run.node_states_json = json_dumps(states)
                db.commit()
                continue
            source_values = [outputs[source] for source in predecessors[node_id] if source in outputs]
            source_text = next((value for value in reversed(source_values) if isinstance(value, str) and value.strip()), last_text)
            if not source_text.strip():
                states[node_id].update({"status": "failed", "error": "节点没有可用的文字输入。"})
                run.status, run.error_message = "failed", "节点没有可用的文字输入。"
                run.node_states_json = json_dumps(states)
                db.commit()
                return
            cache_path = workflow_cache_path(db, workflow, node, source_text)
            states[node_id].update({"status": "running", "error": "", "cached": False})
            run.node_states_json = json_dumps(states)
            db.commit()
            try:
                if node["type"] == "text.generate" and cache_path.is_file():
                    node_output = json.loads(cache_path.read_text(encoding="utf-8"))["output"]
                    states[node_id]["cached"] = True
                elif node["type"] == "text.generate":
                    provider_id = workflow_node_provider_id(db, node, "text")
                    node_output = await generate_text(
                        db,
                        source_text,
                        str(node["params"].get("mode") or "prompt_refine"),
                        age_level,
                        user_id=run.user_id,
                        provider_id=provider_id,
                    )
                    cache_path.write_text(json_dumps({"output": node_output}), encoding="utf-8")
                else:
                    provider_id = workflow_node_provider_id(db, node, "image")
                    node_output = await generate_image(
                        db,
                        source_text,
                        str(node["params"].get("style") or "classroom-friendly"),
                        str(node["params"].get("size") or "1024x1024"),
                        user_id=run.user_id,
                        provider_id=provider_id,
                    )
                    if not node_output.get("url") and not node_output.get("file_path"):
                        raise ValueError("图片节点没有返回可用结果。")
                outputs[node_id] = node_output
                if isinstance(node_output, str):
                    last_text = node_output
                else:
                    last_image = node_output
                states[node_id].update({"status": "success", "output": safe_image_output(node_output, {"role": "student"}), "error": ""})
                run.node_states_json = json_dumps(states)
                db.commit()
                db.refresh(run)
                if run.cancel_requested:
                    states[node_id]["status"] = "canceled"
                    run.status = "canceled"
                    run.error_message = "运行已取消；当前云端节点已完成，但后续结果未保存为作品。"
                    run.node_states_json = json_dumps(states)
                    db.commit()
                    return
            except Exception as exc:
                message = exc.detail.get("message", str(exc)) if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else str(exc)
                states[node_id].update({"status": "failed", "error": message})
                run.status, run.error_message = "failed", message
                run.node_states_json = json_dumps(states)
                db.commit()
                return
        output: dict[str, Any] = {"text": last_text, "output_title": output_title, "node_outputs": outputs}
        if last_image:
            output.update({"refined_prompt": last_text, "image": last_image})
        project = save_project(
            db,
            f"工作流作品：{prompt[:24]}",
            "workflow",
            "\n\n".join(["# 工作流作品", f"## 输入\n{prompt}", f"## {output_title}\n{last_text}"]),
            (last_image.get("file_path") or last_image.get("url", "")) if last_image else "",
            student=user,
            moderation_status=str(last_image.get("moderation_status") or "approved") if last_image else "approved",
            moderation_reason=str(last_image.get("moderation_reason") or "") if last_image else "",
            moderation_log_id=last_image.get("moderation_log_id") if last_image else None,
        )
        if last_image:
            attach_image_moderation(db, project, last_image.get("moderation_log_id"))
        output = safe_image_output(output, {"role": "student"})
        output["project"] = project_payload_for_identity(project, {"role": "student"})
        run.status = "success"
        run.output_json = json_dumps(output)
        run.node_states_json = json_dumps(states)
        db.commit()
    except Exception as exc:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if run:
            message = exc.detail.get("message", str(exc)) if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else str(exc)
            run.status, run.error_message = "failed", message
            db.commit()
    finally:
        db.close()


@app.post("/api/workflows/run-async")
def run_workflow_async(
    payload: WorkflowRunRequest,
    background_tasks: BackgroundTasks,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    ensure_tool_allowed_for_student(db, identity, "workflow")
    workflow = None
    if payload.workflow_id is not None:
        workflow = db.query(Workflow).filter(Workflow.id == payload.workflow_id).first()
        if not workflow or not can_access_workflow(db, workflow, identity):
            raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND", "message": "工作流不存在或不可访问。"})
        definition = validate_workflow_definition(json.loads(workflow.definition_json or "{}"))
    else:
        definition, _ = builtin_workflow_definition(payload.template_id)
    student = identity["student"]
    node_states = {
        node["id"]: {"status": "pending", "label": node["label"], "type": node["type"], "output": None, "error": "", "cached": False}
        for node in definition["nodes"]
    }
    run = WorkflowRun(
        owner_teacher_id=None if student else identity["teacher"].id,
        workflow_id=workflow.id if workflow else None,
        input_json=json_dumps(payload.model_dump()),
        node_states_json=json_dumps(node_states),
        status="pending",
        user_id=student.id if student else None,
        classroom_id=student.classroom_id if student else None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(execute_workflow_run_background, run.id, None, db.get_bind())
    return {"run": workflow_run_payload(run)}


@app.get("/api/workflows/runs/{run_id}")
def get_workflow_run(
    run_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    return {"run": workflow_run_payload(require_workflow_run_access(db, run_id, identity))}


@app.post("/api/workflows/runs/{run_id}/cancel")
def cancel_workflow_run(
    run_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    run = require_workflow_run_access(db, run_id, identity)
    if run.status in {"success", "failed", "canceled"}:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_RUN_FINAL", "message": "工作流运行已经结束。"})
    run.cancel_requested = True
    if run.status == "pending":
        run.status = "canceled"
        run.error_message = "运行已取消。"
        states = json.loads(run.node_states_json or "{}")
        first_pending = next((state for state in states.values() if state.get("status") == "pending"), None)
        if first_pending:
            first_pending["status"] = "canceled"
        run.node_states_json = json_dumps(states)
    db.commit()
    db.refresh(run)
    return {"run": workflow_run_payload(run)}


@app.post("/api/workflows/runs/{run_id}/retry-node")
def retry_workflow_node(
    run_id: int,
    payload: WorkflowNodeRetryRequest,
    background_tasks: BackgroundTasks,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    run = require_workflow_run_access(db, run_id, identity)
    if run.status not in {"failed", "canceled"}:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_RETRY_NOT_ALLOWED", "message": "只有失败或取消的运行可以从节点继续。"})
    states = json.loads(run.node_states_json or "{}")
    state = states.get(payload.node_id)
    if not state or state.get("status") not in {"failed", "canceled"}:
        raise HTTPException(status_code=400, detail={"code": "WORKFLOW_NODE_RETRY_INVALID", "message": "请选择失败或取消的节点重试。"})
    run.status = "pending"
    run.cancel_requested = False
    run.error_message = ""
    db.commit()
    db.refresh(run)
    background_tasks.add_task(execute_workflow_run_background, run.id, payload.node_id, db.get_bind())
    return {"run": workflow_run_payload(run)}


@app.get("/api/workflows/runs")
def list_workflow_runs(
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    query = db.query(WorkflowRun)
    if identity["role"] == "student":
        query = query.filter(WorkflowRun.user_id == identity["student"].id)
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(WorkflowRun, db, identity["teacher"]))
    runs = query.order_by(WorkflowRun.created_at.desc()).limit(100).all()
    return {"runs": [workflow_run_payload(item) for item in runs]}


async def execute_saved_workflow(
    db: Session,
    workflow: Workflow,
    prompt: str,
    age_level: str,
    user_id: int | None = None,
) -> tuple[dict, str, dict | None, str]:
    definition = validate_workflow_definition(json.loads(workflow.definition_json or "{}"))
    nodes = {node["id"]: node for node in definition["nodes"]}
    predecessors = {node_id: [] for node_id in nodes}
    successors = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for edge in definition["edges"]:
        predecessors[edge["target"]].append(edge["source"])
        successors[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    queue = [node_id for node_id, count in indegree.items() if count == 0]
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for target in successors[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    outputs: dict[str, Any] = {}
    last_text = prompt
    last_image = None
    for node_id in order:
        node = nodes[node_id]
        if node["type"] == "input":
            outputs[node_id] = prompt
            continue
        source_values = [outputs[source] for source in predecessors[node_id] if source in outputs]
        source_text = next((value for value in reversed(source_values) if isinstance(value, str)), last_text)
        if node["type"] == "text.generate":
            mode = str(node["params"].get("mode") or "prompt_refine")
            provider_id = workflow_node_provider_id(db, node, "text")
            last_text = await generate_text(db, source_text, mode, age_level, user_id=user_id, provider_id=provider_id)
            outputs[node_id] = last_text
        elif node["type"] == "image.generate":
            style = str(node["params"].get("style") or "classroom-friendly")
            size = str(node["params"].get("size") or "1024x1024")
            provider_id = workflow_node_provider_id(db, node, "image")
            last_image = await generate_image(db, source_text, style, size, user_id=user_id, provider_id=provider_id)
            outputs[node_id] = last_image
    output = {"text": last_text, "output_title": workflow.name, "node_outputs": outputs}
    if last_image:
        output["refined_prompt"] = last_text
        output["image"] = last_image
    return output, last_text, last_image, workflow.name


@app.post("/api/workflows/run")
async def run_workflow(
    payload: WorkflowRunRequest,
    identity: dict = Depends(optional_teacher_or_student),
    db: Session = Depends(get_db),
):
    ensure_tool_allowed_for_student(db, identity, "workflow")
    student = identity.get("student")
    saved_workflow = None
    if payload.workflow_id is not None:
        if identity["role"] == "anonymous":
            raise HTTPException(status_code=403, detail={"code": "AUTH_REQUIRED", "message": "请先登录后运行自定义工作流。"})
        saved_workflow = db.query(Workflow).filter(Workflow.id == payload.workflow_id).first()
        if not saved_workflow or not can_access_workflow(db, saved_workflow, identity):
            raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND", "message": "工作流不存在或不可访问。"})
    run = WorkflowRun(
        owner_teacher_id=(None if student else (identity["teacher"].id if identity["role"] == "teacher" else None)),
        workflow_id=saved_workflow.id if saved_workflow else None,
        input_json=json_dumps(payload.model_dump()),
        status="running",
        user_id=student.id if student else None,
        classroom_id=student.classroom_id if student else None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        run_moderation(
            db,
            payload.prompt,
            user_id=student.id if student else None,
            owner_teacher_id=run.owner_teacher_id,
        )
        age_level = identity["student"].age_level if identity["role"] == "student" else "mixed"
        if saved_workflow:
            output, text, image, output_title = await execute_saved_workflow(
                db,
                saved_workflow,
                payload.prompt,
                age_level,
                user_id=student.id if student else None,
            )
        else:
            template_modes = {
                "text_to_image": ("prompt_refine", "优化提示词"),
                "idea_to_story": ("story", "故事扩写"),
                "code_explain": ("code_explain", "代码解释"),
                "project_plan": ("prompt_refine", "项目步骤规划"),
            }
            if payload.template_id not in template_modes:
                raise HTTPException(status_code=404, detail={"code": "WORKFLOW_TEMPLATE_NOT_FOUND", "message": "工作流模板不存在。"})
            mode, output_title = template_modes[payload.template_id]
            text = await generate_text(db, payload.prompt, mode, age_level, user_id=student.id if student else None)
            output = {"text": text, "output_title": output_title}
            image = None
            if payload.template_id == "text_to_image":
                image = await generate_image(db, text, "classroom-friendly", "1024x1024", user_id=student.id if student else None)
                output = {"refined_prompt": text, "image": image, "output_title": output_title}
        project = None
        if identity["role"] != "anonymous":
            summary_parts = ["# 工作流作品", f"## 输入\n{payload.prompt}", f"## {output_title}\n{text}"]
            if image:
                summary_parts.extend(
                    [
                        "## 生成图片",
                        f"- 图片链接：{image.get('url') or '无'}",
                        f"- 本地文件：{image.get('file_path') or '无'}",
                    ]
                )
            summary = "\n\n".join(summary_parts)
            project = save_project(
                db,
                f"工作流作品：{payload.prompt[:24]}",
                "workflow",
                summary,
                (image.get("file_path") or image.get("url", "")) if image else "",
                student=identity["student"],
                moderation_status=str(image.get("moderation_status") or "approved") if image else "approved",
                moderation_reason=str(image.get("moderation_reason") or "") if image else "",
                moderation_log_id=image.get("moderation_log_id") if image else None,
                owner_teacher_id=run.owner_teacher_id,
            )
            if image:
                attach_image_moderation(db, project, image.get("moderation_log_id"))
        public_output = safe_image_output(output, identity)
        run.status = "success"
        run.output_json = json_dumps(public_output)
        db.commit()
        return {
            "run_id": run.id,
            "status": run.status,
            "output": public_output,
            "project": project_payload_for_identity(project, identity) if project else None,
        }
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        run.status = "failed"
        run.error_message = detail.get("message", str(exc.detail))
        db.commit()
        raise
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        db.commit()
        raise


@app.post("/api/projects")
def create_project(
    payload: ProjectCreateRequest,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
    ):
    student = identity["student"]
    actor = identity_teacher(identity)
    owner_teacher_id = None if student else actor.id
    if identity["role"] == "student" and payload.project_type != "text":
        raise HTTPException(
            status_code=403,
            detail={"code": "PROJECT_TYPE_RESTRICTED", "message": "图片、视频和工作流作品必须通过对应 AI 工具保存，不能手工绕过审核。"},
        )
    if identity["role"] == "student" and payload.file_path.strip():
        raise HTTPException(
            status_code=403,
            detail={"code": "PROJECT_FILE_PATH_RESTRICTED", "message": "学生手工作品不能登记本地文件路径，请通过对应工具安全保存文件。"},
        )
    run_moderation(db, payload.summary, user_id=student.id if student else None, owner_teacher_id=owner_teacher_id)
    project = Project(
        owner_teacher_id=owner_teacher_id,
        title=payload.title,
        project_type=payload.project_type,
        owner_name=student.name if student else payload.owner_name,
        summary=payload.summary,
        file_path=payload.file_path,
        user_id=student.id if student else None,
        classroom_id=student.classroom_id if student else None,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"project": to_project_dict(project)}


@app.get("/api/projects")
def list_projects(scope: str = "active", identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    if scope not in {"active", "archived", "trash", "all"}:
        raise HTTPException(status_code=400, detail={"code": "PROJECT_SCOPE_INVALID", "message": "作品范围不合法。"})
    query = db.query(Project)
    if identity["role"] == "student":
        query = query.filter(Project.user_id == identity["student"].id, Project.moderation_status == "approved")
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(Project, db, identity["teacher"]))
    if scope != "all":
        query = query.filter(Project.lifecycle_status == ("trashed" if scope == "trash" else scope))
    projects = query.order_by(Project.updated_at.desc()).limit(300).all()
    return {"projects": [to_project_dict(project) for project in projects]}


@app.get("/api/projects/{project_id}")
def get_project(project_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "作品不存在或已被删除。"})
    ensure_project_access(db, project, identity, "查看")
    ensure_student_can_access_moderated_project(project, identity)
    return {"project": project_payload_for_identity(project, identity)}


@app.get("/api/projects/{project_id}/file")
def get_project_file(project_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "作品不存在或已被删除。"})
    ensure_project_access(db, project, identity, "查看")
    ensure_student_can_access_moderated_project(project, identity)
    if not project.file_path.strip() or project.file_path.startswith(("http://", "https://")):
        raise HTTPException(status_code=404, detail={"code": "PROJECT_FILE_UNAVAILABLE", "message": "这个作品没有可预览的本地文件。"})
    file_path = Path(project.file_path)
    try:
        file_path.resolve().relative_to(DATA_DIR.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=403, detail={"code": "PROJECT_FILE_FORBIDDEN", "message": "作品文件不在受管工作区中，不能读取。"})
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail={"code": "PROJECT_FILE_MISSING", "message": "作品记录存在，但本地文件已缺失。"})
    return FileResponse(file_path)


@app.put("/api/projects/{project_id}")
def update_project(
    project_id: int,
    payload: ProjectUpdateRequest,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "作品不存在或已被删除。"})
    ensure_project_access(db, project, identity, "编辑")
    ensure_student_can_access_moderated_project(project, identity)
    if project.lifecycle_status == "trashed":
        raise HTTPException(status_code=409, detail={"code": "PROJECT_TRASHED", "message": "回收站中的作品需要先恢复后才能编辑。"})
    project.title = payload.title[:160]
    project.summary = payload.summary
    db.commit()
    db.refresh(project)
    return {"project": to_project_dict(project)}


@app.delete("/api/projects/{project_id}")
def delete_project(
    project_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "作品不存在或已被删除。"})
    ensure_project_access(db, project, identity, "删除")
    ensure_student_can_access_moderated_project(project, identity)
    if db.query(TaskSubmission).filter(TaskSubmission.project_id == project_id).first():
        raise HTTPException(
            status_code=409,
            detail={"code": "PROJECT_HAS_SUBMISSION", "message": "这个作品已经提交为作业，需要保留批改记录，不能直接删除。"},
        )
    if project.lifecycle_status == "trashed":
        raise HTTPException(status_code=409, detail={"code": "PROJECT_ALREADY_TRASHED", "message": "作品已经在回收站中。"})
    project.lifecycle_status = "trashed"
    project.trashed_at = beijing_now_naive()
    project.archived_at = None
    db.commit()
    db.refresh(project)
    return {"trashed": True, "project": to_project_dict(project)}


@app.post("/api/projects/{project_id}/archive")
def archive_project(project_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "作品不存在。"})
    ensure_project_access(db, project, identity, "归档")
    ensure_student_can_access_moderated_project(project, identity)
    if project.lifecycle_status == "trashed":
        raise HTTPException(status_code=409, detail={"code": "PROJECT_TRASHED", "message": "回收站中的作品需要先恢复。"})
    project.lifecycle_status = "archived"
    project.archived_at = beijing_now_naive()
    project.trashed_at = None
    db.commit()
    db.refresh(project)
    return {"project": to_project_dict(project)}


@app.post("/api/projects/{project_id}/restore")
def restore_project(project_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "作品不存在。"})
    ensure_project_access(db, project, identity, "恢复")
    ensure_student_can_access_moderated_project(project, identity)
    if project.lifecycle_status == "active":
        raise HTTPException(status_code=409, detail={"code": "PROJECT_ALREADY_ACTIVE", "message": "作品已经在正常作品库中。"})
    project.lifecycle_status = "active"
    project.archived_at = None
    project.trashed_at = None
    db.commit()
    db.refresh(project)
    return {"project": to_project_dict(project)}


@app.delete("/api/projects/{project_id}/permanent")
def permanently_delete_project(project_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "作品不存在。"})
    ensure_project_access(db, project, identity, "永久删除")
    ensure_student_can_access_moderated_project(project, identity)
    if project.lifecycle_status != "trashed":
        raise HTTPException(status_code=409, detail={"code": "PROJECT_NOT_IN_TRASH", "message": "只有回收站中的作品可以永久删除。"})
    if db.query(TaskSubmission).filter(TaskSubmission.project_id == project_id).first():
        raise HTTPException(status_code=409, detail={"code": "PROJECT_HAS_SUBMISSION", "message": "作品已用于作业提交，不能永久删除。"})
    file_path = Path(project.file_path) if project.file_path and not project.file_path.startswith(("http://", "https://")) else None
    db.query(Asset).filter(Asset.project_id == project_id).update({Asset.project_id: None}, synchronize_session=False)
    db.delete(project)
    db.commit()
    file_deleted = False
    if file_path and file_path.is_file():
        try:
            file_path.resolve().relative_to(DATA_DIR.resolve())
            file_path.unlink()
            file_deleted = True
        except (ValueError, OSError):
            pass
    return {"deleted": True, "project_id": project_id, "file_deleted": file_deleted}


@app.get("/api/assets")
def list_assets(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(Asset)
    if identity["role"] == "student":
        student = identity["student"]
        own_project_ids = db.query(Project.id).filter(Project.user_id == student.id)
        public_lesson_ids = db.query(Lesson.id).join(Course, Lesson.course_id == Course.id).filter(
            Course.classroom_id.is_(None), Course.status == "published"
        )
        query = query.filter(or_(
            Asset.project_id.in_(own_project_ids),
            Asset.classroom_id == student.classroom_id,
            Asset.lesson_id.in_(public_lesson_ids),
        ))
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(Asset, db, identity["teacher"]))
    assets = query.order_by(Asset.created_at.desc()).limit(300).all()
    return {"assets": [to_asset_dict(asset) for asset in assets]}


@app.post("/api/assets/register")
def register_asset(payload: AssetRegisterRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    project = db.query(Project).filter(Project.id == payload.project_id).first() if payload.project_id else None
    if payload.project_id and not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "关联作品不存在。"})
    if project:
        ensure_teaching_record_access(db, actor, project.owner_teacher_id, project.classroom_id, "作品")
    require_owned_classroom(db, payload.classroom_id, actor)
    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id).first() if payload.lesson_id else None
    if payload.lesson_id and not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": "课时不存在。"})
    if lesson:
        ensure_teaching_record_access(db, actor, lesson.owner_teacher_id, lesson.course.classroom_id if lesson.course else None, "课时")
    inspection = inspect_asset(payload.asset_type, payload.file_path)
    original_name = Path(urlparse(payload.file_path).path if payload.file_path.startswith(("http://", "https://")) else payload.file_path).name
    asset = Asset(
        owner_teacher_id=actor.id,
        project_id=payload.project_id,
        classroom_id=payload.classroom_id,
        lesson_id=payload.lesson_id,
        asset_type=payload.asset_type,
        file_path=payload.file_path,
        metadata_json=structured_asset_metadata(payload.metadata_json, payload.display_name, payload.description, payload.tags),
        original_name=original_name,
        mime_type=inspection["mime_type"],
        file_size=inspection["size"],
        file_extension=inspection["extension"],
        checksum_sha256=inspection["checksum"],
        safety_status=inspection["safety_status"],
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return {"asset": to_asset_dict(asset)}


@app.post("/api/assets/upload")
async def upload_asset(
    asset_type: str = Form(default="document"),
    metadata_json: str = Form(default="{}"),
    display_name: str = Form(default=""),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    project_id: int | None = Form(default=None),
    classroom_id: int | None = Form(default=None),
    lesson_id: int | None = Form(default=None),
    file: UploadFile = File(...),
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    project = db.query(Project).filter(Project.id == project_id).first() if project_id else None
    if project_id and not project:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "关联作品不存在。"})
    if project:
        ensure_teaching_record_access(db, actor, project.owner_teacher_id, project.classroom_id, "作品")
    require_owned_classroom(db, classroom_id, actor)
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first() if lesson_id else None
    if lesson_id and not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": "课时不存在。"})
    if lesson:
        ensure_teaching_record_access(db, actor, lesson.owner_teacher_id, lesson.course.classroom_id if lesson.course else None, "课时")
    original_name = Path(file.filename or "asset").name
    suffix = Path(original_name).suffix.lower()
    target = ASSET_LIBRARY_DIR / f"{secrets.token_hex(8)}{suffix}"
    content = await file.read(MAX_ASSET_BYTES + 1)
    inspection = inspect_asset(asset_type, original_name, content)
    target.write_bytes(content)
    asset = Asset(
        owner_teacher_id=actor.id,
        project_id=project_id,
        classroom_id=classroom_id,
        lesson_id=lesson_id,
        asset_type=asset_type,
        file_path=str(target),
        metadata_json=structured_asset_metadata(metadata_json, display_name, description, [tag.strip() for tag in tags.split(",")]),
        original_name=original_name,
        mime_type=inspection["mime_type"],
        file_size=inspection["size"],
        file_extension=inspection["extension"],
        checksum_sha256=inspection["checksum"],
        safety_status=inspection["safety_status"],
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return {"asset": to_asset_dict(asset), "original_name": original_name}


@app.get("/api/assets/{asset_id}/file")
def get_asset_file(asset_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "素材不存在。"})
    if identity["role"] == "student":
        student = identity["student"]
        own_project = asset.project_id and db.query(Project).filter(Project.id == asset.project_id, Project.user_id == student.id).first()
        public_lesson = asset.lesson and asset.lesson.course and asset.lesson.course.classroom_id is None and asset.lesson.course.status == "published"
        if not own_project and asset.classroom_id != student.classroom_id and not public_lesson:
            raise HTTPException(status_code=403, detail={"code": "ASSET_FORBIDDEN", "message": "不能查看未授权的素材。"})
    if identity["role"] == "teacher":
        ensure_teaching_record_access(db, identity["teacher"], asset.owner_teacher_id, asset.classroom_id, "素材")
    if asset.file_path.startswith(("http://", "https://")):
        raise HTTPException(status_code=404, detail={"code": "ASSET_REMOTE_FILE", "message": "远程素材请直接打开原始链接。"})
    file_path = Path(asset.file_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail={"code": "ASSET_FILE_MISSING", "message": "素材记录存在，但本地文件已缺失。"})
    return FileResponse(file_path)


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail={"code": "ASSET_NOT_FOUND", "message": "素材不存在。"})
    ensure_teaching_record_access(db, teacher_actor(db, teacher), asset.owner_teacher_id, asset.classroom_id, "素材")
    file_path = Path(asset.file_path) if not asset.file_path.startswith(("http://", "https://")) else None
    db.delete(asset)
    db.commit()
    if file_path and file_path.is_file():
        try:
            file_path.resolve().relative_to(ASSET_LIBRARY_DIR.resolve())
            file_path.unlink()
        except (ValueError, OSError):
            pass
    return {"deleted": True, "asset_id": asset_id}


@app.post("/api/courses/import")
def import_course(payload: CourseImportRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = ensure_curriculum_admin(db, teacher)
    validate_course_package(payload)
    require_owned_classroom(db, payload.classroom_id, actor)
    if payload.conflict_strategy not in {"skip", "rename", "replace"}:
        raise HTTPException(status_code=400, detail={"code": "COURSE_CONFLICT_STRATEGY_INVALID", "message": "课程冲突策略必须是跳过、重命名或替换。"})
    if not payload.package_version.strip() or len(payload.package_version) > 40:
        raise HTTPException(status_code=400, detail={"code": "COURSE_VERSION_INVALID", "message": "课程包版本不能为空且不能超过 40 个字符。"})
    if len(payload.dependencies) > 100 or any(not str(item.get("name") or "").strip() for item in payload.dependencies):
        raise HTTPException(status_code=400, detail={"code": "COURSE_DEPENDENCIES_INVALID", "message": "依赖素材必须包含名称，且不能超过 100 项。"})
    if len(payload.checklist) > 100 or any(not str(item).strip() for item in payload.checklist):
        raise HTTPException(status_code=400, detail={"code": "COURSE_CHECKLIST_INVALID", "message": "验收清单不能包含空项，且不能超过 100 项。"})
    existing = db.query(Course).filter(
        Course.title == payload.title,
        Course.classroom_id == payload.classroom_id,
    ).first()
    if existing and payload.conflict_strategy == "skip":
        return {"course": to_course_dict(existing), "conflict": "skipped"}
    title = payload.title
    if existing and payload.conflict_strategy == "rename":
        index = 2
        while db.query(Course).filter(
            Course.title == f"{payload.title}（导入 {index}）",
            Course.classroom_id == payload.classroom_id,
        ).first():
            index += 1
        title = f"{payload.title}（导入 {index}）"
        existing = None
    course = existing or Course(title=title, owner_teacher_id=actor.id)
    if existing:
        old_lesson_ids = [item.id for item in db.query(Lesson).filter(Lesson.course_id == course.id).all()]
        if old_lesson_ids:
            db.query(Task).filter(Task.lesson_id.in_(old_lesson_ids)).update({Task.lesson_id: None}, synchronize_session=False)
            db.query(Asset).filter(Asset.lesson_id.in_(old_lesson_ids)).update({Asset.lesson_id: None}, synchronize_session=False)
            db.query(Lesson).filter(Lesson.id.in_(old_lesson_ids)).delete(synchronize_session=False)
    else:
        db.add(course)
    course.title = title
    course.description = payload.description
    course.classroom_id = payload.classroom_id
    course.package_version = payload.package_version.strip()
    course.author = payload.author.strip()
    course.age_range = payload.age_range.strip() or "全年龄"
    course.cover_path = payload.cover_path.strip()
    course.dependencies_json = json_dumps(payload.dependencies)
    course.checklist_json = json_dumps([str(item).strip() for item in payload.checklist])
    course.status = payload.status if payload.status in {"draft", "published"} else "draft"
    course.starts_at = local_naive(payload.starts_at)
    course.ends_at = local_naive(payload.ends_at)
    db.flush()
    lesson_map: dict[str, int] = {}
    for lesson_payload in payload.lessons:
        lesson = Lesson(
            owner_teacher_id=actor.id,
            course_id=course.id,
            title=lesson_payload.title,
            content=lesson_payload.content,
            order_index=lesson_payload.order_index,
        )
        db.add(lesson)
        db.flush()
        lesson_map[lesson.title] = lesson.id
    for task_payload in payload.tasks:
        db.add(
            Task(
                owner_teacher_id=actor.id,
                title=task_payload.title,
                instructions=task_payload.instructions,
                tool_scope=task_payload.tool_scope,
                classroom_id=payload.classroom_id,
                lesson_id=lesson_map.get(task_payload.lesson_title),
                status=task_payload.status if task_payload.status in {"draft", "published"} else "published",
                starts_at=local_naive(task_payload.starts_at),
                due_at=local_naive(task_payload.due_at),
                rubric_json=json_dumps(normalize_rubric(task_payload.rubric)),
            )
        )
    db.commit()
    db.refresh(course)
    return {"course": to_course_dict(course), "conflict": "replaced" if existing else ("renamed" if title != payload.title else "created")}


@app.get("/api/courses/export")
def export_courses(teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    course_query = db.query(Course)
    lesson_query = db.query(Lesson)
    task_query = db.query(Task)
    if not is_admin_actor(actor):
        classroom_ids = assigned_classroom_ids(db, actor)
        course_query = course_query.filter(staff_scope_condition(Course, db, actor))
        lesson_query = lesson_query.outerjoin(Course, Lesson.course_id == Course.id).filter(or_(
            Course.classroom_id.in_(classroom_ids) if classroom_ids else False,
            (Lesson.course_id.is_(None)) & (Lesson.owner_teacher_id == actor.id),
            (Course.classroom_id.is_(None)) & (Course.owner_teacher_id == actor.id),
        ))
        task_query = task_query.filter(staff_scope_condition(Task, db, actor))
    courses = course_query.order_by(Course.created_at.desc()).limit(100).all()
    lessons = lesson_query.order_by(Lesson.course_id.asc(), Lesson.order_index.asc(), Lesson.id.asc()).limit(500).all()
    tasks = task_query.order_by(Task.created_at.desc()).limit(200).all()
    return {
        "format": "coderai-course-package",
        "schema_version": 1,
        "exported_at": datetime.now(BEIJING_TZ).isoformat(),
        "courses": [to_course_dict(course) for course in courses],
        "lessons": [to_lesson_dict(lesson) for lesson in lessons],
        "tasks": [to_task_dict(task) for task in tasks],
    }


@app.get("/api/courses")
def list_courses(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(Course)
    if identity["role"] == "student":
        current = beijing_now_naive()
        query = query.filter((Course.classroom_id.is_(None)) | (Course.classroom_id == identity["student"].classroom_id))
        query = query.filter(
            (Course.status == "published")
            & ((Course.starts_at.is_(None)) | (Course.starts_at <= current))
            & ((Course.ends_at.is_(None)) | (Course.ends_at >= current))
        )
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(Course, db, identity["teacher"]))
    courses = query.order_by(Course.created_at.desc()).limit(100).all()
    return {"courses": [to_course_dict(course) for course in courses]}


@app.put("/api/courses/{course_id}")
def update_course(
    course_id: int,
    payload: CourseUpdateRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail={"code": "COURSE_NOT_FOUND", "message": "课程不存在。"})
    actor = ensure_curriculum_admin(db, teacher)
    ensure_teaching_record_access(db, actor, course.owner_teacher_id, course.classroom_id, "课程")
    require_owned_classroom(db, payload.classroom_id, actor)
    course.title = payload.title
    course.description = payload.description
    course.package_version = payload.package_version.strip() or "1.0.0"
    course.author = payload.author.strip()
    course.age_range = payload.age_range.strip() or "全年龄"
    course.cover_path = payload.cover_path.strip()
    course.dependencies_json = json_dumps(payload.dependencies)
    course.checklist_json = json_dumps([str(item).strip() for item in payload.checklist])
    course.classroom_id = payload.classroom_id
    course.status = payload.status if payload.status in {"draft", "published"} else "draft"
    course.starts_at = local_naive(payload.starts_at)
    course.ends_at = local_naive(payload.ends_at)
    db.commit()
    db.refresh(course)
    return {"course": to_course_dict(course)}


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail={"code": "COURSE_NOT_FOUND", "message": "课程不存在。"})
    actor = ensure_curriculum_admin(db, teacher)
    ensure_teaching_record_access(db, actor, course.owner_teacher_id, course.classroom_id, "课程")
    lesson_ids = [item.id for item in db.query(Lesson).filter(Lesson.course_id == course_id).all()]
    if lesson_ids:
        db.query(Task).filter(Task.lesson_id.in_(lesson_ids)).update({Task.lesson_id: None}, synchronize_session=False)
        db.query(Asset).filter(Asset.lesson_id.in_(lesson_ids)).update({Asset.lesson_id: None}, synchronize_session=False)
        db.query(Lesson).filter(Lesson.id.in_(lesson_ids)).delete(synchronize_session=False)
    db.delete(course)
    db.commit()
    return {"deleted": True, "course_id": course_id, "deleted_lessons": len(lesson_ids)}


@app.get("/api/lessons")
def list_lessons(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(Lesson).outerjoin(Course, Lesson.course_id == Course.id)
    if identity["role"] == "student":
        current = beijing_now_naive()
        query = query.filter(
            (
                ((Course.classroom_id.is_(None)) | (Course.classroom_id == identity["student"].classroom_id))
                & (Course.status == "published")
                & ((Course.starts_at.is_(None)) | (Course.starts_at <= current))
                & ((Course.ends_at.is_(None)) | (Course.ends_at >= current))
            )
        )
    elif not is_admin_actor(identity_teacher(identity)):
        actor = identity["teacher"]
        classroom_ids = assigned_classroom_ids(db, actor)
        query = query.filter(or_(
            Course.classroom_id.in_(classroom_ids) if classroom_ids else False,
            (Lesson.course_id.is_(None)) & (Lesson.owner_teacher_id == actor.id),
            (Course.classroom_id.is_(None)) & (Course.owner_teacher_id == actor.id),
        ))
    lessons = query.order_by(Lesson.course_id.asc(), Lesson.order_index.asc(), Lesson.id.asc()).limit(500).all()
    return {"lessons": [to_lesson_dict(lesson) for lesson in lessons]}


@app.post("/api/lessons")
def create_lesson(payload: LessonRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = ensure_curriculum_admin(db, teacher)
    course = db.query(Course).filter(Course.id == payload.course_id).first() if payload.course_id else None
    if payload.course_id and not course:
        raise HTTPException(status_code=404, detail={"code": "COURSE_NOT_FOUND", "message": "课程不存在。"})
    if course:
        ensure_teaching_record_access(db, actor, course.owner_teacher_id, course.classroom_id, "课程")
    lesson = Lesson(
        owner_teacher_id=actor.id,
        course_id=payload.course_id,
        title=payload.title,
        content=payload.content,
        order_index=payload.order_index,
    )
    db.add(lesson)
    db.commit()
    db.refresh(lesson)
    return {"lesson": to_lesson_dict(lesson)}


@app.put("/api/lessons/{lesson_id}")
def update_lesson(
    lesson_id: int,
    payload: LessonRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": "课时不存在。"})
    actor = ensure_curriculum_admin(db, teacher)
    ensure_teaching_record_access(db, actor, lesson.owner_teacher_id, lesson.course.classroom_id if lesson.course else None, "课时")
    course = db.query(Course).filter(Course.id == payload.course_id).first() if payload.course_id else None
    if payload.course_id and not course:
        raise HTTPException(status_code=404, detail={"code": "COURSE_NOT_FOUND", "message": "课程不存在。"})
    if course:
        ensure_teaching_record_access(db, actor, course.owner_teacher_id, course.classroom_id, "课程")
    lesson.course_id = payload.course_id
    lesson.title = payload.title
    lesson.content = payload.content
    lesson.order_index = payload.order_index
    db.commit()
    db.refresh(lesson)
    return {"lesson": to_lesson_dict(lesson)}


@app.delete("/api/lessons/{lesson_id}")
def delete_lesson(lesson_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": "课时不存在。"})
    actor = ensure_curriculum_admin(db, teacher)
    ensure_teaching_record_access(db, actor, lesson.owner_teacher_id, lesson.course.classroom_id if lesson.course else None, "课时")
    db.query(Task).filter(Task.lesson_id == lesson_id).update({Task.lesson_id: None}, synchronize_session=False)
    db.query(Asset).filter(Asset.lesson_id == lesson_id).update({Asset.lesson_id: None}, synchronize_session=False)
    db.delete(lesson)
    db.commit()
    return {"deleted": True, "lesson_id": lesson_id}


def curriculum_package_or_404(db: Session, package_id: int) -> CoursePackage:
    package = db.query(CoursePackage).filter(CoursePackage.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail={"code": "COURSE_PACKAGE_NOT_FOUND", "message": "课程包不存在。"})
    return package


def curriculum_course_or_404(db: Session, course_id: int) -> CurriculumCourse:
    course = db.query(CurriculumCourse).filter(CurriculumCourse.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail={"code": "CURRICULUM_COURSE_NOT_FOUND", "message": "课程不存在。"})
    return course


def curriculum_material_or_404(db: Session, course_id: int, kind: str) -> tuple[CurriculumCourse, CourseMaterial]:
    if kind not in MATERIAL_KINDS:
        raise HTTPException(status_code=400, detail={"code": "COURSE_MATERIAL_KIND_INVALID", "message": "课程资料类型不受支持。"})
    course = curriculum_course_or_404(db, course_id)
    material = db.query(CourseMaterial).filter_by(course_id=course_id, kind=kind).first()
    if not material:
        raise HTTPException(status_code=404, detail={"code": "COURSE_MATERIAL_MISSING", "message": "管理员暂未补充这项资料。"})
    return course, material


def managed_curriculum_path(raw_path: str, missing_code: str = "COURSE_MATERIAL_FILE_MISSING") -> Path:
    path = Path(raw_path)
    try:
        resolved = path.resolve()
        resolved.relative_to(CURRICULUM_DIR.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=403, detail={"code": "COURSE_MATERIAL_FILE_FORBIDDEN", "message": "课程资料不在受管目录中。"})
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail={"code": missing_code, "message": "课程资料记录存在，但文件已缺失。"})
    return resolved


def delete_curriculum_file(raw_path: str) -> None:
    if not raw_path:
        return
    try:
        path = Path(raw_path).resolve()
        path.relative_to(CURRICULUM_DIR.resolve())
        path.unlink(missing_ok=True)
    except (ValueError, OSError):
        return


def apply_course_package_request(package: CoursePackage, payload: CoursePackageRequest, author: User) -> None:
    package.title = payload.title.strip()
    package.description = payload.description.strip()
    package.package_version = payload.package_version.strip()
    package.author_user_id = author.id
    package.author = author.name
    school_stages = normalize_school_stages(payload.school_stages)
    package.school_stages_json = json_dumps(school_stages)
    package.age_range = school_stages_label(school_stages)
    package.cover_path = payload.cover_path.strip()


def apply_curriculum_course_request(course: CurriculumCourse, payload: CurriculumCourseRequest) -> None:
    course.title = payload.title.strip()
    course.description = payload.description.strip()
    course.order_index = payload.order_index
    course.assignment_instructions = payload.assignment_instructions.strip()
    allowed_tools = [item for item in dict.fromkeys(part.strip() for part in payload.tool_scope.split(",")) if item in {"text", "image", "video", "workflow"}]
    course.tool_scope = ",".join(allowed_tools)
    course.rubric_json = json_dumps(normalize_rubric(payload.rubric))


@app.get("/api/course-packages")
def list_curriculum_packages(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(CoursePackage)
    if identity["role"] == "teacher" and not is_admin_actor(identity["teacher"]):
        query = query.join(
            CoursePackageTeacher,
            CoursePackageTeacher.package_id == CoursePackage.id,
        ).filter(
            CoursePackage.status == "published",
            CoursePackageTeacher.teacher_id == identity["teacher"].id,
        )
    elif identity["role"] == "student":
        query = query.filter(CoursePackage.status.in_(["published", "archived"]))
    packages = query.order_by(CoursePackage.updated_at.desc(), CoursePackage.id.desc()).all()
    payloads = [course_package_payload(db, package, identity) for package in packages]
    if identity["role"] == "student":
        payloads = [item for item in payloads if item["courses"]]
    return {"packages": payloads}


@app.post("/api/course-packages")
def create_curriculum_package(
    payload: CoursePackageRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    validate_course_package(payload)
    author = resolve_course_package_author(db, payload.author_user_id)
    package = CoursePackage(created_by_user_id=actor.id, title=payload.title.strip(), status="draft")
    apply_course_package_request(package, payload, author)
    db.add(package)
    db.commit()
    db.refresh(package)
    record_teacher_audit(db, teacher, "curriculum.package.created", target_type="course_package", target_id=package.id, summary=f"创建课程包：{package.title}")
    return {"package": course_package_payload(db, package, {"role": "teacher", "teacher": actor, "student": None})}


@app.post("/api/course-packages/import")
async def import_curriculum_package(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    upload_handle = tempfile.NamedTemporaryFile(prefix="coderai-curriculum-import-", suffix=".zip", delete=False)
    upload_path = Path(upload_handle.name)
    total = 0
    package_dir: Path | None = None
    conversion_ids: list[int] = []
    try:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > 200 * 1024 * 1024:
                raise HTTPException(status_code=413, detail={"code": "CURRICULUM_PACKAGE_TOO_LARGE", "message": "课程包 ZIP 不能超过 200 MB。"})
            upload_handle.write(chunk)
        upload_handle.close()
        if total == 0:
            raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_EMPTY", "message": "课程包文件为空。"})
        try:
            archive = zipfile.ZipFile(upload_path, "r")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_ZIP_INVALID", "message": "文件不是有效的课程包 ZIP。"})
        with archive:
            infos = archive.infolist()
            if len(infos) > 1000 or sum(item.file_size for item in infos) > 600 * 1024 * 1024:
                raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_EXPANDED_LIMIT", "message": "课程包解压后的文件数量或大小超过限制。"})
            names: set[str] = set()
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_PATH_INVALID", "message": "课程包包含不安全路径。"})
                names.add(str(path))
            if "manifest.json" not in names:
                raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_MANIFEST_MISSING", "message": "课程包缺少 manifest.json。"})
            manifest_bytes = archive.read("manifest.json")
            if len(manifest_bytes) > 2 * 1024 * 1024:
                raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_MANIFEST_TOO_LARGE", "message": "课程包清单过大。"})
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
                if str(manifest.get("schema_version")) != "2.0":
                    raise ValueError("schema")
                package_data = dict(manifest.get("package") or {})
                if not package_data.get("school_stages"):
                    package_data["school_stages"] = infer_school_stages(package_data.get("age_range"))
                package_request = CoursePackageRequest(**{**package_data, "author_user_id": actor.id})
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
                raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_MANIFEST_INVALID", "message": "课程包清单格式或版本不正确。"})
            validate_course_package(package_request)
            course_rows = manifest.get("courses") or []
            if not isinstance(course_rows, list) or not 1 <= len(course_rows) <= 200:
                raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_COURSES_INVALID", "message": "课程包必须包含 1 到 200 门课程。"})
            package = CoursePackage(created_by_user_id=actor.id, title=package_request.title.strip(), status="draft")
            apply_course_package_request(package, package_request, actor)
            db.add(package)
            db.flush()
            package_dir = CURRICULUM_DIR / str(package.id)
            for index, row in enumerate(course_rows):
                try:
                    course_request = CurriculumCourseRequest(**{key: value for key, value in row.items() if key != "materials"})
                except (ValidationError, TypeError, AttributeError):
                    raise HTTPException(status_code=400, detail={"code": "CURRICULUM_COURSE_MANIFEST_INVALID", "message": f"第 {index + 1} 门课程信息不正确。"})
                course = CurriculumCourse(package_id=package.id, title=course_request.title.strip())
                apply_curriculum_course_request(course, course_request)
                db.add(course)
                db.flush()
                materials = row.get("materials") or {}
                if not isinstance(materials, dict):
                    raise HTTPException(status_code=400, detail={"code": "CURRICULUM_MATERIAL_MANIFEST_INVALID", "message": f"第 {index + 1} 门课程资料清单不正确。"})
                for kind, item in materials.items():
                    if kind not in MATERIAL_KINDS or not isinstance(item, dict):
                        raise HTTPException(status_code=400, detail={"code": "CURRICULUM_MATERIAL_MANIFEST_INVALID", "message": "课程资料类型不正确。"})
                    member = str(PurePosixPath(str(item.get("path") or "")))
                    if not member or member not in names or ".." in PurePosixPath(member).parts:
                        raise HTTPException(status_code=400, detail={"code": "CURRICULUM_MATERIAL_FILE_MISSING", "message": f"课程资料文件缺失：{member}"})
                    content = archive.read(member)
                    metadata = validate_course_material(kind, str(item.get("original_name") or Path(member).name), content)
                    if item.get("sha256") and str(item["sha256"]) != metadata["checksum_sha256"]:
                        raise HTTPException(status_code=400, detail={"code": "CURRICULUM_MATERIAL_CHECKSUM_INVALID", "message": f"课程资料校验失败：{member}"})
                    target_dir = curriculum_course_dir(package.id, course.id)
                    target = target_dir / ("slides.pptx" if kind == "slides" else "starter.md" if kind == "starter_markdown" else "result.md")
                    target.write_bytes(content)
                    material = CourseMaterial(
                        course_id=course.id,
                        kind=kind,
                        original_name=str(item.get("original_name") or Path(member).name)[:255],
                        source_path=str(target),
                        mime_type=metadata["mime_type"],
                        file_size=metadata["file_size"],
                        checksum_sha256=metadata["checksum_sha256"],
                        conversion_status="pending" if kind == "slides" else "ready",
                    )
                    db.add(material)
                    db.flush()
                    if kind == "slides":
                        conversion_ids.append(material.id)
            db.commit()
            db.refresh(package)
            for material_id in conversion_ids:
                background_tasks.add_task(convert_slides_material, material_id)
            record_teacher_audit(db, teacher, "curriculum.package.imported", target_type="course_package", target_id=package.id, summary=f"导入课程包：{package.title}")
            return {"package": course_package_payload(db, package, {"role": "teacher", "teacher": actor, "student": None}), "conversion_jobs": len(conversion_ids)}
    except HTTPException:
        db.rollback()
        if package_dir and package_dir.exists():
            shutil.rmtree(package_dir, ignore_errors=True)
        raise
    except Exception as exc:
        db.rollback()
        if package_dir and package_dir.exists():
            shutil.rmtree(package_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail={"code": "CURRICULUM_PACKAGE_IMPORT_FAILED", "message": f"课程包导入失败：{exc}"})
    finally:
        upload_handle.close()
        upload_path.unlink(missing_ok=True)


@app.get("/api/course-packages/{package_id}")
def get_curriculum_package(package_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    package = curriculum_package_or_404(db, package_id)
    if identity["role"] == "teacher":
        actor = identity["teacher"]
        if not is_admin_actor(actor):
            ensure_teacher_course_package_access(db, actor, package)
    elif package.status not in {"published", "archived"}:
        raise HTTPException(status_code=403, detail={"code": "COURSE_PACKAGE_FORBIDDEN", "message": "课程包尚未发布。"})
    result = course_package_payload(db, package, identity)
    if identity["role"] == "student" and not result["courses"]:
        raise HTTPException(status_code=403, detail={"code": "COURSE_PACKAGE_FORBIDDEN", "message": "这项课程尚未排给当前学生。"})
    return {"package": result}


@app.put("/api/course-packages/{package_id}/teachers")
def update_course_package_teachers(
    package_id: int,
    payload: CoursePackageTeachersUpdateRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    package = curriculum_package_or_404(db, package_id)
    if len(payload.teacher_ids) != len(set(payload.teacher_ids)):
        raise HTTPException(
            status_code=400,
            detail={"code": "COURSE_PACKAGE_TEACHERS_DUPLICATED", "message": "教师授权列表包含重复账号。"},
        )
    teacher_ids = list(payload.teacher_ids)
    accounts = (
        db.query(User)
        .filter(User.id.in_(teacher_ids), User.role == "teacher", User.active.is_(True))
        .all()
        if teacher_ids
        else []
    )
    if len(accounts) != len(teacher_ids):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "COURSE_PACKAGE_TEACHERS_INVALID",
                "message": "授权列表只能包含存在且已启用的教师账号。",
            },
        )
    previous_ids = sorted(
        row[0]
        for row in db.query(CoursePackageTeacher.teacher_id)
        .filter(CoursePackageTeacher.package_id == package.id)
        .all()
    )
    db.query(CoursePackageTeacher).filter(CoursePackageTeacher.package_id == package.id).delete(
        synchronize_session=False
    )
    for teacher_id in teacher_ids:
        db.add(
            CoursePackageTeacher(
                package_id=package.id,
                teacher_id=teacher_id,
                assigned_by_user_id=actor.id,
            )
        )
    record_teacher_audit(
        db,
        teacher,
        "curriculum.package.teachers_updated",
        target_type="course_package",
        target_id=package.id,
        summary=f"更新课程包教师权限：{package.title}",
        details={"previous_teacher_ids": previous_ids, "teacher_ids": sorted(teacher_ids)},
        commit=False,
    )
    db.commit()
    db.refresh(package)
    return {"package": course_package_payload(db, package, {"role": "teacher", "teacher": actor, "student": None})}


@app.put("/api/course-packages/{package_id}")
def update_curriculum_package(
    package_id: int,
    payload: CoursePackageRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    validate_course_package(payload)
    package = curriculum_package_or_404(db, package_id)
    author = resolve_course_package_author(db, payload.author_user_id, allow_inactive_id=package.author_user_id)
    apply_course_package_request(package, payload, author)
    db.commit()
    db.refresh(package)
    return {"package": course_package_payload(db, package, {"role": "teacher", "teacher": actor, "student": None})}


@app.post("/api/course-packages/{package_id}/publish")
def publish_curriculum_package(package_id: int, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    actor = ensure_curriculum_admin(db, teacher)
    package = curriculum_package_or_404(db, package_id)
    if not package.courses:
        raise HTTPException(status_code=409, detail={"code": "COURSE_PACKAGE_EMPTY", "message": "课程包至少包含一门课程后才能发布。"})
    package.status = "published"
    package.published_at = beijing_now_naive()
    package.archived_at = None
    db.commit()
    db.refresh(package)
    return {"package": course_package_payload(db, package, {"role": "teacher", "teacher": actor, "student": None})}


@app.post("/api/course-packages/{package_id}/archive")
def archive_curriculum_package(package_id: int, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    actor = ensure_curriculum_admin(db, teacher)
    package = curriculum_package_or_404(db, package_id)
    package.status = "archived"
    package.archived_at = beijing_now_naive()
    db.commit()
    db.refresh(package)
    return {"package": course_package_payload(db, package, {"role": "teacher", "teacher": actor, "student": None})}


@app.delete("/api/course-packages/{package_id}")
def delete_curriculum_package(package_id: int, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    ensure_curriculum_admin(db, teacher)
    package = curriculum_package_or_404(db, package_id)
    course_ids = [course.id for course in package.courses]
    if package.legacy_course_id is not None or (course_ids and db.query(CourseSchedule.id).filter(CourseSchedule.course_id.in_(course_ids)).first()):
        raise HTTPException(status_code=409, detail={"code": "COURSE_PACKAGE_ARCHIVE_REQUIRED", "message": "已迁移或已排课的课程包只能归档，不能删除。"})
    for course in package.courses:
        for material in course.materials:
            delete_curriculum_file(material.source_path)
            delete_curriculum_file(material.preview_path)
            db.delete(material)
        db.delete(course)
    db.delete(package)
    db.commit()
    shutil.rmtree(CURRICULUM_DIR / str(package_id), ignore_errors=True)
    return {"deleted": True, "package_id": package_id}


@app.get("/api/course-packages/{package_id}/export")
def export_curriculum_package(
    package_id: int,
    background_tasks: BackgroundTasks,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ensure_curriculum_admin(db, teacher)
    package = curriculum_package_or_404(db, package_id)
    export_dir = DATA_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f"coderai-course-{package_id}-", suffix=".zip", dir=export_dir, delete=False)
    handle.close()
    export_path = Path(handle.name)
    manifest = {
        "schema_version": "2.0",
        "exported_at": datetime.now(BEIJING_TZ).isoformat(),
        "package": {
            "title": package.title,
            "description": package.description,
            "package_version": package.package_version,
            "author": package.author_account.name if package.author_account else package.author,
            "school_stages": school_stages_from_json(package.school_stages_json, package.age_range),
            "age_range": school_stages_label(school_stages_from_json(package.school_stages_json, package.age_range)),
            "cover_path": package.cover_path,
        },
        "courses": [],
    }
    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for index, course in enumerate(sorted(package.courses, key=lambda item: (item.order_index, item.id)), start=1):
            course_row = {
                "title": course.title,
                "description": course.description,
                "order_index": course.order_index,
                "assignment_instructions": course.assignment_instructions,
                "tool_scope": course.tool_scope,
                "rubric": normalize_rubric_payload(course.rubric_json),
                "materials": {},
            }
            for material in course.materials:
                source = managed_curriculum_path(material.source_path)
                member = f"materials/{index:03d}-{course.id}/{material.kind}{source.suffix.lower()}"
                archive.write(source, member)
                course_row["materials"][material.kind] = {
                    "path": member,
                    "original_name": material.original_name,
                    "sha256": material.checksum_sha256,
                }
            manifest["courses"].append(course_row)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    background_tasks.add_task(export_path.unlink, missing_ok=True)
    return FileResponse(export_path, media_type="application/zip", filename=f"coderai-course-package-{package_id}.zip")


@app.post("/api/course-packages/{package_id}/courses")
def create_curriculum_course(
    package_id: int,
    payload: CurriculumCourseRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    package = curriculum_package_or_404(db, package_id)
    if package.status == "archived":
        raise HTTPException(status_code=409, detail={"code": "COURSE_PACKAGE_ARCHIVED", "message": "归档课程包不能继续添加课程。"})
    course = CurriculumCourse(package_id=package.id, title=payload.title.strip())
    apply_curriculum_course_request(course, payload)
    db.add(course)
    db.commit()
    db.refresh(course)
    return {"course": curriculum_course_payload(db, course, {"role": "teacher", "teacher": actor, "student": None})}


@app.put("/api/course-packages/{package_id}/courses/order")
def reorder_curriculum_courses(
    package_id: int,
    payload: CurriculumCourseOrderRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    package = curriculum_package_or_404(db, package_id)
    existing_ids = {course.id for course in package.courses}
    if len(payload.course_ids) != len(set(payload.course_ids)) or set(payload.course_ids) != existing_ids:
        raise HTTPException(status_code=400, detail={"code": "CURRICULUM_COURSE_ORDER_INVALID", "message": "排序列表必须完整包含课程包中的全部课程。"})
    for order_index, course_id in enumerate(payload.course_ids):
        db.get(CurriculumCourse, course_id).order_index = order_index
    db.commit()
    db.refresh(package)
    return {"package": course_package_payload(db, package, {"role": "teacher", "teacher": actor, "student": None})}


@app.get("/api/curriculum-courses/{course_id}")
def get_curriculum_course(course_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    course = curriculum_course_or_404(db, course_id)
    ensure_curriculum_course_read_access(db, course, identity)
    return {"course": curriculum_course_payload(db, course, identity)}


@app.put("/api/curriculum-courses/{course_id}")
def update_curriculum_course(
    course_id: int,
    payload: CurriculumCourseRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    course = curriculum_course_or_404(db, course_id)
    apply_curriculum_course_request(course, payload)
    sync_course_schedule_tasks(db, course)
    db.commit()
    db.refresh(course)
    return {"course": curriculum_course_payload(db, course, {"role": "teacher", "teacher": actor, "student": None})}


@app.delete("/api/curriculum-courses/{course_id}")
def delete_curriculum_course(course_id: int, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    ensure_curriculum_admin(db, teacher)
    course = curriculum_course_or_404(db, course_id)
    if db.query(CourseSchedule.id).filter(CourseSchedule.course_id == course_id).first():
        raise HTTPException(status_code=409, detail={"code": "CURRICULUM_COURSE_IN_USE", "message": "已排课的课程不能删除，请归档整个课程包。"})
    package_id = course.package_id
    for material in course.materials:
        delete_curriculum_file(material.source_path)
        delete_curriculum_file(material.preview_path)
        db.delete(material)
    db.delete(course)
    db.commit()
    shutil.rmtree(CURRICULUM_DIR / str(package_id) / str(course_id), ignore_errors=True)
    return {"deleted": True, "course_id": course_id}


@app.put("/api/curriculum-courses/{course_id}/materials/{kind}")
async def upload_curriculum_material(
    course_id: int,
    kind: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = ensure_curriculum_admin(db, teacher)
    if kind not in MATERIAL_KINDS:
        raise HTTPException(status_code=400, detail={"code": "COURSE_MATERIAL_KIND_INVALID", "message": "课程资料类型不受支持。"})
    course = curriculum_course_or_404(db, course_id)
    content = await file.read(50 * 1024 * 1024 + 1)
    try:
        metadata = validate_course_material(kind, file.filename or "", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "COURSE_MATERIAL_INVALID", "message": str(exc)})
    target_dir = curriculum_course_dir(course.package_id, course.id)
    target = target_dir / ("slides.pptx" if kind == "slides" else "starter.md" if kind == "starter_markdown" else "result.md")
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(6)}.upload")
    temporary.write_bytes(content)
    temporary.replace(target)
    material = db.query(CourseMaterial).filter_by(course_id=course.id, kind=kind).first()
    old_source = material.source_path if material else ""
    old_preview = material.preview_path if material else ""
    if not material:
        material = CourseMaterial(course_id=course.id, kind=kind)
        db.add(material)
    material.original_name = (file.filename or target.name)[:255]
    material.source_path = str(target)
    material.preview_path = "" if kind == "slides" else material.preview_path
    material.mime_type = metadata["mime_type"]
    material.file_size = metadata["file_size"]
    material.checksum_sha256 = metadata["checksum_sha256"]
    material.conversion_status = "pending" if kind == "slides" else "ready"
    material.conversion_error = ""
    db.commit()
    db.refresh(material)
    if old_source and Path(old_source).resolve() != target.resolve():
        delete_curriculum_file(old_source)
    if kind == "slides":
        delete_curriculum_file(old_preview)
        background_tasks.add_task(convert_slides_material, material.id)
    record_teacher_audit(db, teacher, "curriculum.material.updated", target_type="course_material", target_id=material.id, summary=f"更新{COURSE_MATERIAL_LABELS[kind]}：{course.title}")
    return {"material": curriculum_material_payload(db, course, kind, {"role": "teacher", "teacher": actor, "student": None})}


@app.delete("/api/curriculum-courses/{course_id}/materials/{kind}")
def delete_curriculum_material(
    course_id: int,
    kind: str,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ensure_curriculum_admin(db, teacher)
    _, material = curriculum_material_or_404(db, course_id, kind)
    source_path, preview_path = material.source_path, material.preview_path
    db.delete(material)
    db.commit()
    delete_curriculum_file(source_path)
    delete_curriculum_file(preview_path)
    return {"deleted": True, "kind": kind}


@app.post("/api/curriculum-courses/{course_id}/materials/slides/retry")
def retry_curriculum_slides_conversion(
    course_id: int,
    background_tasks: BackgroundTasks,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ensure_curriculum_admin(db, teacher)
    _, material = curriculum_material_or_404(db, course_id, "slides")
    material.conversion_status = "pending"
    material.conversion_error = ""
    db.commit()
    background_tasks.add_task(convert_slides_material, material.id)
    return {"queued": True, "material_id": material.id}


@app.get("/api/curriculum/converter-status")
def curriculum_converter_status(_: TeacherSession = Depends(require_admin)):
    return {"converter": libreoffice_status()}


@app.get("/api/curriculum-courses/{course_id}/materials/{kind}/preview")
def preview_curriculum_material(
    course_id: int,
    kind: str,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    _, material = curriculum_material_or_404(db, course_id, kind)
    can_preview, _ = curriculum_material_permissions(db, material, identity)
    if not can_preview:
        code = "COURSE_RESULT_LOCKED" if kind == "result_markdown" and identity["role"] == "student" else "COURSE_MATERIAL_PREVIEW_FORBIDDEN"
        raise HTTPException(status_code=403, detail={"code": code, "message": "当前账号暂不能预览这项课程资料。"})
    if kind == "slides":
        if material.conversion_status != "ready" or not material.preview_path:
            raise HTTPException(status_code=409, detail={"code": "COURSE_SLIDES_PREVIEW_UNAVAILABLE", "message": "PPT 预览暂不可用，管理员可检查转换状态并重试。"})
        path = managed_curriculum_path(material.preview_path, "COURSE_SLIDES_PREVIEW_MISSING")
        # A neutral MIME type keeps browser download managers from hijacking the
        # authenticated XHR. The frontend restores application/pdf in memory.
        return FileResponse(path, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff"})
    path = managed_curriculum_path(material.source_path)
    return FileResponse(path, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


@app.get("/api/curriculum-courses/{course_id}/materials/{kind}/download")
def download_curriculum_material(
    course_id: int,
    kind: str,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    _, material = curriculum_material_or_404(db, course_id, kind)
    _, can_download = curriculum_material_permissions(db, material, identity)
    if not can_download:
        code = "COURSE_MATERIAL_DOWNLOAD_FORBIDDEN" if kind == "slides" else "COURSE_RESULT_LOCKED"
        raise HTTPException(status_code=403, detail={"code": code, "message": "当前账号不能下载这项课程资料。"})
    path = managed_curriculum_path(material.source_path)
    return FileResponse(path, media_type=material.mime_type or "application/octet-stream", filename=material.original_name or path.name)


@app.post("/api/classrooms")
def create_classroom(payload: ClassroomCreateRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    classroom = Classroom(owner_teacher_id=actor.id, name=payload.name, grade_level=payload.grade_level)
    db.add(classroom)
    db.flush()
    if actor.role == "teacher":
        db.add(ClassroomTeacher(classroom_id=classroom.id, teacher_id=actor.id, assigned_by_user_id=actor.id))
    db.commit()
    db.refresh(classroom)
    return {"classroom": classroom_payload(db, classroom, actor)}


@app.get("/api/classrooms")
def list_classrooms(teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    classrooms = db.query(Classroom).order_by(Classroom.created_at.desc()).limit(500).all()
    return {"classrooms": [classroom_payload(db, classroom, actor) for classroom in classrooms]}


@app.get("/api/teachers/options")
def teacher_options(teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    teacher_actor(db, teacher)
    accounts = db.query(User).filter(User.role == "teacher", User.active.is_(True)).order_by(User.name.asc(), User.id.asc()).all()
    return {"teachers": [{"id": item.id, "name": item.name, "username": item.username} for item in accounts]}


@app.get("/api/course-authors/options")
def course_author_options(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    accounts = (
        db.query(User)
        .filter(User.role.in_(("teacher", "admin")))
        .order_by(User.active.desc(), User.role.asc(), User.name.asc(), User.id.asc())
        .all()
    )
    return {
        "authors": [
            {
                "id": item.id,
                "name": item.name,
                "username": item.username,
                "role": item.role,
                "active": item.active,
            }
            for item in accounts
        ]
    }


@app.put("/api/classrooms/{classroom_id}/teachers")
def update_classroom_teachers(
    classroom_id: int,
    payload: ClassroomTeachersUpdateRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    classroom = require_owned_classroom(db, classroom_id, actor)
    teacher_ids = list(dict.fromkeys(payload.teacher_ids))
    if actor.role == "teacher" and actor.id not in teacher_ids:
        raise HTTPException(
            status_code=409,
            detail={"code": "CLASSROOM_SELF_UNASSIGN_FORBIDDEN", "message": "教师不能移除自己，请由管理员调整。"},
        )
    accounts = db.query(User).filter(User.id.in_(teacher_ids), User.role == "teacher", User.active.is_(True)).all() if teacher_ids else []
    if len(accounts) != len(teacher_ids):
        raise HTTPException(status_code=400, detail={"code": "CLASSROOM_TEACHER_INVALID", "message": "授课教师不存在、已停用或角色不正确。"})
    db.query(ClassroomTeacher).filter(ClassroomTeacher.classroom_id == classroom_id).delete(synchronize_session=False)
    db.add_all([
        ClassroomTeacher(classroom_id=classroom_id, teacher_id=account.id, assigned_by_user_id=actor.id)
        for account in accounts
    ])
    db.commit()
    record_teacher_audit(
        db,
        teacher,
        "classroom.teachers_updated",
        target_type="classroom",
        target_id=classroom_id,
        summary=f"更新班级授课教师：{classroom.name}",
        details={"teacher_ids": teacher_ids},
    )
    return {"classroom": classroom_payload(db, classroom, actor)}


@app.put("/api/classrooms/{classroom_id}")
def update_classroom(
    classroom_id: int,
    payload: ClassroomUpdateRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    classroom = db.query(Classroom).filter(Classroom.id == classroom_id).first()
    if not classroom:
        raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "班级不存在。"})
    actor = teacher_actor(db, teacher)
    require_owned_classroom(db, classroom_id, actor)
    classroom.name = payload.name
    classroom.grade_level = payload.grade_level
    db.commit()
    db.refresh(classroom)
    return {"classroom": classroom_payload(db, classroom, actor)}


@app.delete("/api/classrooms/{classroom_id}")
def delete_classroom(classroom_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    classroom = db.query(Classroom).filter(Classroom.id == classroom_id).first()
    if not classroom:
        raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "班级不存在。"})
    actor = teacher_actor(db, teacher)
    require_owned_classroom(db, classroom_id, actor)
    student_count = db.query(User).filter(User.classroom_id == classroom_id).count()
    if student_count:
        raise HTTPException(
            status_code=409,
            detail={"code": "CLASSROOM_HAS_STUDENTS", "message": f"班级中还有 {student_count} 名学生，请先将学生转移到其他班级。"},
        )
    related_counts = {
        "课程": db.query(Course).filter(Course.classroom_id == classroom_id).count(),
        "任务": db.query(Task).filter(Task.classroom_id == classroom_id).count(),
        "素材": db.query(Asset).filter(Asset.classroom_id == classroom_id).count(),
        "作品": db.query(Project).filter(Project.classroom_id == classroom_id).count(),
        "提交": db.query(TaskSubmission).filter(TaskSubmission.classroom_id == classroom_id).count(),
        "视频任务": db.query(VideoTask).filter(VideoTask.classroom_id == classroom_id).count(),
        "工作流": db.query(Workflow).filter(Workflow.classroom_id == classroom_id).count(),
        "工作流运行": db.query(WorkflowRun).filter(WorkflowRun.classroom_id == classroom_id).count(),
        "安全记录": db.query(ModerationLog).filter(ModerationLog.classroom_id == classroom_id).count(),
        "用量记录": db.query(UsageLog).filter(UsageLog.classroom_id == classroom_id).count(),
    }
    related_total = sum(related_counts.values())
    if related_total:
        raise HTTPException(
            status_code=409,
            detail={"code": "CLASSROOM_HAS_HISTORY", "message": f"班级仍有关联教学历史（{related_total} 条），不能删除。"},
        )
    classroom_name = classroom.name
    db.query(ClassroomTeacher).filter(ClassroomTeacher.classroom_id == classroom_id).delete(synchronize_session=False)
    db.delete(classroom)
    db.commit()
    record_teacher_audit(
        db,
        teacher,
        "classroom.deleted",
        target_type="classroom",
        target_id=classroom_id,
        summary=f"删除空班级：{classroom_name}",
    )
    return {"deleted": True, "classroom_id": classroom_id}


@app.post("/api/students")
def create_student(payload: StudentCreateRequest, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    classroom = require_owned_classroom(db, payload.classroom_id, actor)
    username = validate_username(payload.username)
    if find_account_by_username(db, username):
        raise HTTPException(status_code=409, detail={"code": "USERNAME_EXISTS", "message": "用户名已被使用，请更换。"})
    ensure_student_seat_capacity(db)
    student = User(
        name=payload.name.strip(),
        role="student",
        classroom_id=classroom.id if classroom else None,
        username=username,
        password_hash=hash_password(DEFAULT_STUDENT_PASSWORD),
        password_change_required=False,
        registered_at=now(),
        access_code="",
        age_level=payload.age_level,
        active=True,
        created_by_user_id=actor.id,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    record_teacher_audit(
        db,
        teacher,
        "student_account.created",
        target_type="student",
        target_id=student.id,
        summary=f"创建学生账号：{student.name}",
        details={"classroom_id": student.classroom_id, "username": student.username},
    )
    return {"student": to_user_dict(student), "initial_password": DEFAULT_STUDENT_PASSWORD}


@app.get("/api/students")
def list_students(teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    query = db.query(User).filter(User.role == "student")
    if not is_admin_actor(actor):
        query = query.filter(User.archived_at.is_(None))
    students = query.order_by(User.created_at.desc()).limit(2000).all()
    return {"students": [to_user_dict(student) for student in students]}


def csv_row_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def csv_safe(value: Any) -> str:
    text_value = "" if value is None else str(value)
    return "'" + text_value if text_value.startswith(("=", "+", "-", "@")) else text_value


@app.post("/api/students/import")
async def import_students_csv(
    file: UploadFile = File(...),
    conflict_strategy: str = Form("skip"),
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    if conflict_strategy not in {"skip", "update"}:
        raise HTTPException(status_code=400, detail={"code": "STUDENT_IMPORT_STRATEGY_INVALID", "message": "冲突策略必须是 skip 或 update。"})
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"code": "STUDENT_IMPORT_TOO_LARGE", "message": "学生 CSV 不能超过 2 MB。"})
    try:
        csv_text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail={"code": "STUDENT_IMPORT_ENCODING_INVALID", "message": "学生 CSV 必须使用 UTF-8 编码。"})
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = set(reader.fieldnames or [])
    if not headers.intersection({"name", "姓名"}) or not headers.intersection({"username", "用户名"}):
        raise HTTPException(status_code=400, detail={"code": "STUDENT_IMPORT_HEADER_INVALID", "message": "CSV 必须包含 name/姓名 和 username/用户名 列。"})
    classrooms = db.query(Classroom).order_by(Classroom.id.asc()).all()
    classrooms_by_name = {item.name: item for item in classrooms}
    classrooms_by_id = {item.id: item for item in classrooms}
    imported = updated = skipped = 0
    errors: list[dict[str, Any]] = []

    def reserve_active_seat(row_number: int) -> bool:
        try:
            ensure_student_seat_capacity(db)
            return True
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            errors.append(
                {
                    "row": row_number,
                    "code": detail.get("code", "LICENSE_SEAT_LIMIT"),
                    "message": detail.get("message", "当前许可证没有可用学生席位。"),
                }
            )
            return False

    for row_number, row in enumerate(reader, start=2):
        if row_number > 1001:
            errors.append({"row": row_number, "message": "单次最多导入 1000 名学生。"})
            break
        name = csv_row_value(row, "name", "姓名")
        username_raw = csv_row_value(row, "username", "用户名")
        if not name or not username_raw:
            errors.append({"row": row_number, "message": "学生姓名和用户名不能为空。"})
            continue
        try:
            username = validate_username(username_raw)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            errors.append({"row": row_number, "code": detail.get("code", "USERNAME_INVALID"), "message": detail.get("message", "用户名格式不正确。")})
            continue
        classroom_name = csv_row_value(row, "classroom", "班级")
        classroom_id_text = csv_row_value(row, "classroom_id", "班级ID")
        classroom = None
        if classroom_id_text:
            classroom = classrooms_by_id.get(int(classroom_id_text)) if classroom_id_text.isdigit() else None
        elif classroom_name:
            classroom = classrooms_by_name.get(classroom_name)
        if (classroom_id_text or classroom_name) and not classroom:
            errors.append({"row": row_number, "message": f"班级不存在：{classroom_name or classroom_id_text}"})
            continue
        age_raw = csv_row_value(row, "age_level", "学龄分类", "学生阶段", "年龄阶段")
        age_level = parse_school_stage(age_raw) if age_raw else "primary_lower"
        if not age_level:
            errors.append({"row": row_number, "code": "STUDENT_SCHOOL_STAGE_INVALID", "message": f"学龄分类不正确：{age_raw}"})
            continue
        active_raw = csv_row_value(row, "active", "账号状态").lower()
        is_archived = active_raw in {"archived", "已归档"}
        active = active_raw not in {"0", "false", "disabled", "停用", "已停用", "archived", "已归档"}
        archived_classroom_name = csv_row_value(row, "archived_classroom_name", "原班级")
        archived_at = parse_backup_datetime(csv_row_value(row, "archived_at", "归档时间")) if is_archived else None
        existing = find_account_by_username(db, username)
        if existing:
            if existing.role != "student" or conflict_strategy == "skip":
                skipped += 1
                continue
            consumes_new_seat = not is_archived and active and (existing.archived_at is not None or not existing.active)
            if consumes_new_seat and not reserve_active_seat(row_number):
                continue
            existing.name = name[:120]
            existing.classroom_id = None if is_archived else (classroom.id if classroom else None)
            existing.age_level = age_level
            existing.active = False if is_archived else active
            existing.archived_at = (archived_at or beijing_now_naive()) if is_archived else None
            existing.archived_classroom_name = (archived_classroom_name or (classroom.name if classroom else "未分配班级")) if is_archived else ""
            updated += 1
            continue
        if not is_archived and active and not reserve_active_seat(row_number):
            continue
        db.add(User(
            name=name[:120], role="student", classroom_id=None if is_archived else (classroom.id if classroom else None),
            username=username, password_hash=hash_password(DEFAULT_STUDENT_PASSWORD), password_change_required=False,
            registered_at=now(), access_code="", age_level=age_level, active=False if is_archived else active,
            created_by_user_id=actor.id,
            archived_at=(archived_at or beijing_now_naive()) if is_archived else None,
            archived_classroom_name=(archived_classroom_name or (classroom.name if classroom else "未分配班级")) if is_archived else "",
        ))
        db.flush()
        imported += 1
    db.commit()
    return {"imported": imported, "updated": updated, "skipped": skipped, "errors": errors, "initial_password": DEFAULT_STUDENT_PASSWORD}


@app.get("/api/students/export")
def export_students_csv(teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    teacher_actor(db, teacher)
    students = db.query(User).filter(User.role == "student").order_by(User.created_at.asc()).all()
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["姓名", "用户名", "班级", "学龄分类", "账号状态", "原班级", "归档时间", "创建时间"])
    for student in students:
        payload = to_user_dict(student)
        writer.writerow([
            csv_safe(student.name), csv_safe(student.username), csv_safe(payload["classroom_name"]), school_stage_label(student.age_level), payload["account_status"],
            csv_safe(student.archived_classroom_name), payload["archived_at"] or "", payload["created_at"],
        ])
    content = ("\ufeff" + output.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(content), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=coderai-students.csv"},
    )


def batch_students(db: Session, student_ids: list[int], actor: User) -> list[User]:
    ids = list(dict.fromkeys(student_ids))
    students = db.query(User).filter(User.role == "student", User.id.in_(ids)).all()
    found = {student.id for student in students}
    missing = [student_id for student_id in ids if student_id not in found]
    if missing:
        raise HTTPException(status_code=404, detail={"code": "STUDENT_NOT_FOUND", "message": f"学生账号不存在：{missing}"})
    for student in students:
        ensure_teacher_student_access(actor, student)
    return students


@app.post("/api/students/batch-transfer")
def batch_transfer_students(payload: StudentBatchTransferRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    classroom = db.get(Classroom, payload.classroom_id)
    if not classroom:
        raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "班级不存在。"})
    students = batch_students(db, payload.student_ids, actor)
    if any(student.archived_at for student in students):
        raise HTTPException(status_code=409, detail={"code": "STUDENT_ARCHIVED", "message": "已归档学生需要使用批量恢复并指定班级。"})
    for student in students:
        student.classroom_id = classroom.id
    db.commit()
    return {"transferred": len(students), "classroom": classroom_payload(db, classroom, actor)}


@app.put("/api/students/{student_id}/classroom")
def update_student_classroom(
    student_id: int,
    payload: StudentClassroomUpdateRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail={"code": "STUDENT_NOT_FOUND", "message": "学生账号不存在。"})
    ensure_teacher_student_access(actor, student)
    if student.archived_at:
        raise HTTPException(status_code=409, detail={"code": "STUDENT_ARCHIVED", "message": "已归档学生只能由管理员恢复。"})
    classroom = db.get(Classroom, payload.classroom_id) if payload.classroom_id is not None else None
    if payload.classroom_id is not None and not classroom:
        raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "班级不存在。"})
    student.classroom_id = classroom.id if classroom else None
    db.commit()
    db.refresh(student)
    record_teacher_audit(
        db,
        teacher,
        "student.classroom_updated",
        target_type="student",
        target_id=student.id,
        summary=f"调整学生班级：{student.name}",
        details={"classroom_id": student.classroom_id},
    )
    return {"student": to_user_dict(student)}


@app.post("/api/students/batch-archive")
def batch_archive_students(payload: StudentBatchRequest, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    students = batch_students(db, payload.student_ids, actor)
    archived = 0
    canceled_schedules = 0
    for student in students:
        if student.archived_at:
            continue
        student.archived_classroom_name = student.classroom.name if student.classroom else "未分配班级"
        student.classroom_id = None
        student.active = False
        student.archived_at = beijing_now_naive()
        personal_schedules = db.query(CourseSchedule).filter(
            CourseSchedule.target_type == "student",
            CourseSchedule.target_student_id == student.id,
            CourseSchedule.status != "canceled",
        ).all()
        for schedule in personal_schedules:
            has_submission = db.query(TaskSubmission.id).join(Task, TaskSubmission.task_id == Task.id).filter(
                Task.course_schedule_id == schedule.id,
                TaskSubmission.user_id == student.id,
            ).first()
            if has_submission:
                continue
            schedule.status = "canceled"
            schedule.canceled_reason = "学员账号已归档，未完成个人排课自动取消。"
            schedule.canceled_at = beijing_now_naive()
            sync_schedule_task(db, schedule)
            canceled_schedules += 1
        archived += 1
    db.commit()
    for student in students:
        if student.archived_at:
            revoke_all_student_sessions(db, student.id)
    return {"archived": archived, "skipped": len(students) - archived, "canceled_schedules": canceled_schedules}


@app.post("/api/students/batch-restore")
def batch_restore_students(payload: StudentBatchRestoreRequest, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    classroom = db.get(Classroom, payload.classroom_id)
    if not classroom:
        raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "班级不存在。"})
    students = batch_students(db, payload.student_ids, actor)
    restorable = [student for student in students if student.archived_at]
    ensure_student_seat_capacity(db, len(restorable))
    restored = 0
    for student in students:
        if not student.archived_at:
            continue
        student.classroom_id = classroom.id
        student.active = True
        student.archived_at = None
        student.archived_classroom_name = ""
        restored += 1
    db.commit()
    return {"restored": restored, "skipped": len(students) - restored, "classroom": classroom_payload(db, classroom, actor)}


@app.put("/api/students/{student_id}")
def update_student(
    student_id: int,
    payload: StudentUpdateRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail={"code": "STUDENT_NOT_FOUND", "message": "学生账号不存在。"})
    actor = teacher_actor(db, teacher)
    ensure_teacher_student_access(actor, student)
    if student.archived_at and (payload.active or payload.classroom_id is not None):
        raise HTTPException(
            status_code=409,
            detail={"code": "STUDENT_ARCHIVED", "message": "已归档学生必须通过批量恢复并指定目标班级。"},
        )
    classroom = db.get(Classroom, payload.classroom_id) if payload.classroom_id is not None else None
    if payload.classroom_id is not None and not classroom:
        raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "班级不存在。"})
    username = validate_username(payload.username) if payload.username is not None else student.username
    existing = find_account_by_username(db, username) if username else None
    if existing and existing.id != student.id:
        raise HTTPException(status_code=409, detail={"code": "USERNAME_EXISTS", "message": "用户名已被使用，请更换。"})
    if not student.active and payload.active and not student.archived_at:
        ensure_student_seat_capacity(db)
    student.name = payload.name
    student.username = username
    student.age_level = payload.age_level
    if not student.archived_at:
        student.classroom_id = payload.classroom_id
        student.active = payload.active
    db.commit()
    if not student.active:
        revoke_all_student_sessions(db, student.id)
    db.refresh(student)
    return {"student": to_user_dict(student)}


@app.post("/api/students/{student_id}/reset-access-code")
def reset_student_access_code(
    student_id: int,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    raise HTTPException(
        status_code=410,
        detail={"code": "STUDENT_SELF_REGISTRATION_DISABLED", "message": "学生邀请码注册已停用。"},
    )


@app.post("/api/students/{student_id}/reset-password")
def reset_student_password(
    student_id: int,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail={"code": "STUDENT_NOT_FOUND", "message": "学生账号不存在。"})
    ensure_teacher_student_access(teacher_actor(db, teacher), student)
    if not student.username or not student.password_hash:
        raise HTTPException(
            status_code=409,
            detail={"code": "STUDENT_ACCOUNT_INCOMPLETE", "message": "学生账号资料不完整，请由管理员重新创建或修复。"},
        )
    temporary_password = DEFAULT_STUDENT_PASSWORD
    student.password_hash = hash_password(temporary_password)
    student.password_change_required = False
    student.credential_version = max(1, student.credential_version or 1) + 1
    db.commit()
    revoke_all_student_sessions(db, student.id)
    db.refresh(student)
    record_teacher_audit(
        db,
        teacher,
        "student_account.password_reset",
        target_type="student",
        target_id=student.id,
        summary=f"重置学生账号密码：{student.name}",
        details={"username": student.username},
    )
    return {"student": to_user_dict(student), "temporary_password": temporary_password}


def schedule_target_values(item) -> tuple[int | None, int | None]:
    if item.target_type == "student":
        return item.target_id, None
    return None, item.target_id


def validate_schedule_times(starts_at: datetime, due_at: datetime | None) -> tuple[datetime, datetime | None]:
    starts = local_naive(starts_at)
    due = local_naive(due_at)
    if starts is None:
        raise HTTPException(status_code=400, detail={"code": "COURSE_SCHEDULE_START_REQUIRED", "message": "排课开始时间不能为空。"})
    if due is not None and due <= starts:
        raise HTTPException(status_code=400, detail={"code": "COURSE_SCHEDULE_TIME_INVALID", "message": "截止时间必须晚于开始时间。"})
    return starts, due


def schedule_intervals_overlap(
    left_start: datetime,
    left_due: datetime | None,
    right_start: datetime,
    right_due: datetime | None,
) -> bool:
    left_end = left_due or (left_start + timedelta(hours=1))
    right_end = right_due or (right_start + timedelta(hours=1))
    return left_start < right_end and right_start < left_end


def schedule_target_query(query, target_type: str, student_id: int | None, classroom_id: int | None):
    if target_type == "student":
        return query.filter(CourseSchedule.target_type == "student", CourseSchedule.target_student_id == student_id)
    return query.filter(CourseSchedule.target_type == "classroom", CourseSchedule.classroom_id == classroom_id)


@app.post("/api/course-schedules/batch")
def create_course_schedules(
    payload: CourseScheduleBatchRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    prepared: list[dict[str, Any]] = []
    payload_keys: set[tuple] = set()
    conflicts: list[dict[str, Any]] = []
    for index, item in enumerate(payload.items):
        course = curriculum_course_or_404(db, item.course_id)
        if not course.package or course.package.status != "published":
            raise HTTPException(status_code=409, detail={"code": "COURSE_PACKAGE_NOT_PUBLISHED", "message": f"课程“{course.title}”所在课程包尚未发布。"})
        ensure_teacher_course_package_access(db, actor, course.package)
        starts, due = validate_schedule_times(item.starts_at, item.due_at)
        target_student_id, classroom_id = schedule_target_values(item)
        target_name = ""
        if item.target_type == "student":
            student = db.query(User).filter(User.id == target_student_id, User.role == "student").first()
            if not student:
                raise HTTPException(status_code=404, detail={"code": "STUDENT_NOT_FOUND", "message": "排课学员不存在。"})
            ensure_teacher_student_access(actor, student)
            if not student.active or student.archived_at:
                raise HTTPException(status_code=409, detail={"code": "STUDENT_NOT_ACTIVE", "message": f"学员“{student.name}”当前不能排课。"})
            target_name = student.name
        else:
            classroom = db.get(Classroom, classroom_id)
            if not classroom:
                raise HTTPException(status_code=404, detail={"code": "CLASSROOM_NOT_FOUND", "message": "排课班级不存在。"})
            if not teacher_can_manage_classroom(db, actor, classroom.id):
                raise HTTPException(status_code=403, detail={"code": "CLASSROOM_FORBIDDEN", "message": "教师只能为获授权班级排课。"})
            target_name = classroom.name
        duplicate_key = (course.id, item.target_type, target_student_id, classroom_id, starts)
        if duplicate_key in payload_keys:
            raise HTTPException(status_code=409, detail={"code": "COURSE_SCHEDULE_DUPLICATE", "message": f"第 {index + 1} 条排课与本批次其他记录重复。"})
        payload_keys.add(duplicate_key)
        target_rows = schedule_target_query(
            db.query(CourseSchedule).filter(CourseSchedule.status != "canceled"),
            item.target_type,
            target_student_id,
            classroom_id,
        ).all()
        exact = next((row for row in target_rows if row.course_id == course.id and row.starts_at == starts), None)
        if exact:
            raise HTTPException(status_code=409, detail={"code": "COURSE_SCHEDULE_DUPLICATE", "message": f"“{target_name}”已有同一课程和开始时间的排课。"})
        for existing in target_rows:
            if schedule_intervals_overlap(starts, due, existing.starts_at, existing.due_at):
                conflicts.append({
                    "item_index": index,
                    "target_name": target_name,
                    "course_title": course.title,
                    "conflicts_with_schedule_id": existing.id,
                    "conflicts_with_course_title": existing.course.title if existing.course else "",
                })
        for previous in prepared:
            if previous["target_type"] == item.target_type and previous["target_student_id"] == target_student_id and previous["classroom_id"] == classroom_id and schedule_intervals_overlap(starts, due, previous["starts_at"], previous["due_at"]):
                conflicts.append({
                    "item_index": index,
                    "target_name": target_name,
                    "course_title": course.title,
                    "conflicts_with_item_index": previous["item_index"],
                    "conflicts_with_course_title": previous["course"].title,
                })
        prepared.append({
            "item_index": index,
            "course": course,
            "target_type": item.target_type,
            "target_student_id": target_student_id,
            "classroom_id": classroom_id,
            "starts_at": starts,
            "due_at": due,
        })
    schedules: list[CourseSchedule] = []
    for row in prepared:
        schedule = CourseSchedule(
            course_id=row["course"].id,
            created_by_user_id=actor.id,
            target_type=row["target_type"],
            target_student_id=row["target_student_id"],
            classroom_id=row["classroom_id"],
            starts_at=row["starts_at"],
            due_at=row["due_at"],
            status="scheduled",
        )
        db.add(schedule)
        db.flush()
        schedule.course = row["course"]
        sync_schedule_task(db, schedule)
        schedules.append(schedule)
    db.commit()
    for schedule in schedules:
        db.refresh(schedule)
    record_teacher_audit(db, teacher, "course_schedule.batch_created", target_type="course_schedule", summary=f"创建 {len(schedules)} 条排课", details={"schedule_ids": [item.id for item in schedules]})
    return {"created": len(schedules), "schedules": [schedule_payload(db, item, actor=actor) for item in schedules], "conflicts": conflicts}


@app.get("/api/course-schedules")
def list_course_schedules(
    include_canceled: bool = False,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    query = db.query(CourseSchedule)
    if not include_canceled:
        query = query.filter(CourseSchedule.status != "canceled")
    if identity["role"] == "student":
        student = identity["student"]
        rows = query.order_by(CourseSchedule.starts_at.asc(), CourseSchedule.id.asc()).limit(2000).all()
        schedules = [row for row in rows if schedule_applies_to_student(row, student)]
        return {"schedules": [schedule_payload(db, row, student=student) for row in schedules]}
    actor = identity["teacher"]
    if not is_admin_actor(actor):
        classroom_ids = assigned_classroom_ids(db, actor)
        query = query.filter(or_(
            (CourseSchedule.target_type == "student") & (CourseSchedule.created_by_user_id == actor.id),
            (CourseSchedule.target_type == "classroom") & (CourseSchedule.classroom_id.in_(classroom_ids) if classroom_ids else False),
        ))
    schedules = query.order_by(CourseSchedule.starts_at.desc(), CourseSchedule.id.desc()).limit(2000).all()
    return {"schedules": [schedule_payload(db, row, actor=actor) for row in schedules]}


@app.get("/api/course-schedules/{schedule_id}")
def get_course_schedule(schedule_id: int, identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    schedule = db.get(CourseSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail={"code": "COURSE_SCHEDULE_NOT_FOUND", "message": "排课记录不存在。"})
    if identity["role"] == "student":
        student = identity["student"]
        if not schedule_applies_to_student(schedule, student):
            raise HTTPException(status_code=403, detail={"code": "COURSE_SCHEDULE_FORBIDDEN", "message": "这项课程没有排给当前学生。"})
        return {"schedule": schedule_payload(db, schedule, student=student)}
    actor = identity["teacher"]
    if not teacher_can_read_schedule(db, actor, schedule):
        raise HTTPException(status_code=403, detail={"code": "COURSE_SCHEDULE_FORBIDDEN", "message": "不能访问这条排课记录。"})
    return {"schedule": schedule_payload(db, schedule, actor=actor)}


@app.put("/api/course-schedules/{schedule_id}")
def update_course_schedule(
    schedule_id: int,
    payload: CourseScheduleUpdateRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    schedule = db.get(CourseSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail={"code": "COURSE_SCHEDULE_NOT_FOUND", "message": "排课记录不存在。"})
    if not teacher_can_manage_schedule(db, actor, schedule):
        raise HTTPException(status_code=403, detail={"code": "COURSE_SCHEDULE_FORBIDDEN", "message": "不能调整这条排课记录。"})
    if schedule.status == "canceled":
        raise HTTPException(status_code=409, detail={"code": "COURSE_SCHEDULE_CANCELED", "message": "已取消排课不能再调整时间。"})
    starts, due = validate_schedule_times(payload.starts_at, payload.due_at)
    duplicate_query = schedule_target_query(
        db.query(CourseSchedule).filter(CourseSchedule.id != schedule.id, CourseSchedule.status != "canceled", CourseSchedule.course_id == schedule.course_id),
        schedule.target_type,
        schedule.target_student_id,
        schedule.classroom_id,
    )
    if duplicate_query.filter(CourseSchedule.starts_at == starts).first():
        raise HTTPException(status_code=409, detail={"code": "COURSE_SCHEDULE_DUPLICATE", "message": "相同目标已有同一课程和开始时间的排课。"})
    conflicts = [
        schedule_payload(db, row, actor=actor)
        for row in schedule_target_query(
            db.query(CourseSchedule).filter(CourseSchedule.id != schedule.id, CourseSchedule.status != "canceled"),
            schedule.target_type,
            schedule.target_student_id,
            schedule.classroom_id,
        ).all()
        if schedule_intervals_overlap(starts, due, row.starts_at, row.due_at)
    ]
    schedule.starts_at = starts
    schedule.due_at = due
    sync_schedule_task(db, schedule)
    db.commit()
    db.refresh(schedule)
    return {"schedule": schedule_payload(db, schedule, actor=actor), "conflicts": conflicts}


@app.post("/api/course-schedules/{schedule_id}/cancel")
def cancel_course_schedule(
    schedule_id: int,
    payload: CourseScheduleCancelRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    schedule = db.get(CourseSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail={"code": "COURSE_SCHEDULE_NOT_FOUND", "message": "排课记录不存在。"})
    if not teacher_can_manage_schedule(db, actor, schedule):
        raise HTTPException(status_code=403, detail={"code": "COURSE_SCHEDULE_FORBIDDEN", "message": "不能取消这条排课记录。"})
    if schedule.status != "canceled":
        schedule.status = "canceled"
        schedule.canceled_reason = payload.reason.strip()
        schedule.canceled_at = beijing_now_naive()
        sync_schedule_task(db, schedule)
        db.commit()
        db.refresh(schedule)
    return {"schedule": schedule_payload(db, schedule, actor=actor)}


@app.get("/api/course-schedules/{schedule_id}/submissions")
def list_course_schedule_submissions(
    schedule_id: int,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    schedule = db.get(CourseSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail={"code": "COURSE_SCHEDULE_NOT_FOUND", "message": "排课记录不存在。"})
    if not teacher_can_read_schedule(db, actor, schedule):
        raise HTTPException(status_code=403, detail={"code": "COURSE_SCHEDULE_FORBIDDEN", "message": "不能查看这条排课的提交。"})
    task = schedule.task
    rows = db.query(TaskSubmission).filter(TaskSubmission.task_id == task.id).order_by(TaskSubmission.updated_at.desc()).all() if task else []
    return {"submissions": [submission_payload(db, row, actor) for row in rows]}


@app.post("/api/course-schedules/{schedule_id}/submissions")
def submit_course_schedule_project(
    schedule_id: int,
    payload: SubmissionCreateRequest,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    if identity["role"] != "student":
        raise HTTPException(status_code=403, detail={"code": "STUDENT_REQUIRED", "message": "只有学生可以提交课程作业。"})
    student = identity["student"]
    schedule = db.get(CourseSchedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail={"code": "COURSE_SCHEDULE_NOT_FOUND", "message": "排课记录不存在。"})
    if schedule.status == "canceled" or not schedule_applies_to_student(schedule, student):
        raise HTTPException(status_code=403, detail={"code": "COURSE_SCHEDULE_FORBIDDEN", "message": "这项课程没有排给当前学生或已被取消。"})
    if schedule.starts_at > beijing_now_naive():
        raise HTTPException(status_code=403, detail={"code": "COURSE_SCHEDULE_NOT_STARTED", "message": "课程尚未开始，暂时不能提交作品。"})
    task = sync_schedule_task(db, schedule)
    db.flush()
    submission = submit_project_for_task(db, task, student, payload.project_id)
    return {"submission": to_submission_dict(submission), "schedule": schedule_payload(db, schedule, student=student)}


@app.post("/api/classes/tasks")
def create_class_task(payload: ClassTaskRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = ensure_curriculum_admin(db, teacher)
    require_owned_classroom(db, payload.classroom_id, actor)
    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id).first() if payload.lesson_id else None
    if payload.lesson_id and not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": "课时不存在。"})
    if lesson:
        ensure_teaching_record_access(db, actor, lesson.owner_teacher_id, lesson.course.classroom_id if lesson.course else None, "课时")
    task = Task(
        owner_teacher_id=actor.id,
        title=payload.title,
        instructions=payload.instructions,
        tool_scope=payload.tool_scope,
        classroom_id=payload.classroom_id,
        lesson_id=payload.lesson_id,
        status=payload.status if payload.status in {"draft", "published"} else "published",
        starts_at=local_naive(payload.starts_at),
        due_at=local_naive(payload.due_at),
        rubric_json=json_dumps(normalize_rubric(payload.rubric)),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {"task": to_task_dict(task)}


@app.put("/api/classes/tasks/{task_id}")
def update_class_task(
    task_id: int,
    payload: ClassTaskRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "课堂任务不存在。"})
    actor = ensure_curriculum_admin(db, teacher)
    ensure_teaching_record_access(db, actor, task.owner_teacher_id, task.classroom_id, "课堂任务")
    require_owned_classroom(db, payload.classroom_id, actor)
    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id).first() if payload.lesson_id else None
    if payload.lesson_id and not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": "课时不存在。"})
    if lesson:
        ensure_teaching_record_access(db, actor, lesson.owner_teacher_id, lesson.course.classroom_id if lesson.course else None, "课时")
    task.title = payload.title
    task.instructions = payload.instructions
    task.tool_scope = payload.tool_scope
    task.classroom_id = payload.classroom_id
    task.lesson_id = payload.lesson_id
    task.status = payload.status if payload.status in {"draft", "published"} else "published"
    task.starts_at = local_naive(payload.starts_at)
    task.due_at = local_naive(payload.due_at)
    task.rubric_json = json_dumps(normalize_rubric(payload.rubric))
    db.commit()
    db.refresh(task)
    return {"task": to_task_dict(task)}


@app.delete("/api/classes/tasks/{task_id}")
def delete_class_task(task_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "课堂任务不存在。"})
    actor = ensure_curriculum_admin(db, teacher)
    ensure_teaching_record_access(db, actor, task.owner_teacher_id, task.classroom_id, "课堂任务")
    submission_ids = [row[0] for row in db.query(TaskSubmission.id).filter(TaskSubmission.task_id == task_id).all()]
    deleted_versions = 0
    if submission_ids:
        deleted_versions = db.query(SubmissionVersion).filter(SubmissionVersion.submission_id.in_(submission_ids)).delete(synchronize_session=False)
    deleted_submissions = db.query(TaskSubmission).filter(TaskSubmission.task_id == task_id).delete(synchronize_session=False)
    db.delete(task)
    db.commit()
    return {"deleted": True, "task_id": task_id, "deleted_submissions": deleted_submissions, "deleted_versions": deleted_versions}


@app.get("/api/classes/tasks")
def list_class_tasks(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(Task)
    if identity["role"] == "student":
        current = beijing_now_naive()
        student = identity["student"]
        query = query.filter(or_(
            Task.target_student_id == student.id,
            Task.target_student_id.is_(None) & ((Task.classroom_id.is_(None)) | (Task.classroom_id == student.classroom_id)),
        ))
        query = query.filter((Task.status == "published") & ((Task.starts_at.is_(None)) | (Task.starts_at <= current)))
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(Task, db, identity["teacher"]))
    tasks = query.order_by(Task.created_at.desc()).limit(100).all()
    return {"tasks": [to_task_dict(task) for task in tasks]}


@app.post("/api/classes/tasks/{task_id}/submissions")
def submit_task_project(
    task_id: int,
    payload: SubmissionCreateRequest,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    if identity["role"] != "student":
        raise HTTPException(status_code=403, detail={"code": "STUDENT_REQUIRED", "message": "只有学生端可以提交作业。"})
    student = identity["student"]
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "课堂任务不存在。"})
    if task.target_student_id is not None and task.target_student_id != student.id:
        raise HTTPException(status_code=403, detail={"code": "TASK_FORBIDDEN", "message": "这项任务没有排给当前学生。"})
    if task.course_schedule and not schedule_applies_to_student(task.course_schedule, student):
        raise HTTPException(status_code=403, detail={"code": "TASK_FORBIDDEN", "message": "这项排课当前不属于该学生。"})
    if task.classroom_id and task.classroom_id != student.classroom_id:
        raise HTTPException(status_code=403, detail={"code": "TASK_FORBIDDEN", "message": "不能提交其他班级的任务。"})
    if task.status != "published" or (task.starts_at and task.starts_at > beijing_now_naive()):
        raise HTTPException(status_code=403, detail={"code": "TASK_NOT_OPEN", "message": "课堂任务尚未发布或还未开始。"})
    submission = submit_project_for_task(db, task, student, payload.project_id)
    return {"submission": to_submission_dict(submission)}


@app.get("/api/submissions")
def list_submissions(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    query = db.query(TaskSubmission)
    if identity["role"] == "student":
        query = query.filter(TaskSubmission.user_id == identity["student"].id)
    elif not is_admin_actor(identity_teacher(identity)):
        query = query.filter(staff_scope_condition(TaskSubmission, db, identity["teacher"]))
    submissions = query.order_by(TaskSubmission.updated_at.desc()).limit(200).all()
    actor = identity_teacher(identity)
    return {"submissions": [submission_payload(db, submission, actor) for submission in submissions]}


def filtered_submissions(
    db: Session,
    classroom_id: int | None = None,
    task_id: int | None = None,
    actor: User | None = None,
) -> list[TaskSubmission]:
    query = db.query(TaskSubmission)
    if actor and not is_admin_actor(actor):
        query = query.filter(staff_scope_condition(TaskSubmission, db, actor))
    if classroom_id is not None:
        query = query.filter(TaskSubmission.classroom_id == classroom_id)
    if task_id is not None:
        query = query.filter(TaskSubmission.task_id == task_id)
    return query.order_by(TaskSubmission.updated_at.desc()).all()


@app.get("/api/submissions/statistics")
def submission_statistics(
    classroom_id: int | None = None,
    task_id: int | None = None,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    if classroom_id is not None:
        require_owned_classroom(db, classroom_id, actor)
    if task_id is not None:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "课堂任务不存在。"})
        ensure_teaching_record_access(db, actor, task.owner_teacher_id, task.classroom_id, "课堂任务")
    items = filtered_submissions(db, classroom_id, task_id, actor)

    def summary(rows: list[TaskSubmission]) -> dict:
        scores = [item.score for item in rows if item.score is not None]
        return {
            "total": len(rows),
            "reviewed": sum(1 for item in rows if item.status == "reviewed"),
            "returned": sum(1 for item in rows if item.status == "returned"),
            "late": sum(1 for item in rows if item.is_late),
            "featured": sum(1 for item in rows if item.is_featured),
            "average_score": round(sum(scores) / len(scores), 2) if scores else None,
        }

    classroom_groups: dict[int | None, list[TaskSubmission]] = {}
    task_groups: dict[int, list[TaskSubmission]] = {}
    for item in items:
        classroom_groups.setdefault(item.classroom_id, []).append(item)
        task_groups.setdefault(item.task_id, []).append(item)
    return {
        "overall": summary(items),
        "by_classroom": [{"classroom_id": key, "classroom_name": rows[0].classroom.name if rows[0].classroom else "全部班级", **summary(rows)} for key, rows in classroom_groups.items()],
        "by_task": [{"task_id": key, "task_title": rows[0].task.title if rows[0].task else "已删除任务", **summary(rows)} for key, rows in task_groups.items()],
    }


@app.get("/api/submissions/export")
def export_submissions_csv(
    classroom_id: int | None = None,
    task_id: int | None = None,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    actor = teacher_actor(db, teacher)
    if classroom_id is not None:
        require_owned_classroom(db, classroom_id, actor)
    if task_id is not None:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "课堂任务不存在。"})
        ensure_teaching_record_access(db, actor, task.owner_teacher_id, task.classroom_id, "课堂任务")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["班级", "学生", "任务", "作品", "状态", "分数", "满分", "逾期", "优秀作品", "教师反馈", "提交时间", "更新时间"])
    for item in filtered_submissions(db, classroom_id, task_id, actor):
        data = to_submission_dict(item)
        writer.writerow([
            data["classroom_name"], data["student_name"], data["task_title"], data["project_title"],
            data["status"], "" if data["score"] is None else data["score"], data["max_score"],
            "是" if data["is_late"] else "否", "是" if data["is_featured"] else "否", data["feedback"],
            data["created_at"], data["updated_at"],
        ])
    content = "\ufeff" + output.getvalue()
    filename = f"coderai-submissions-{datetime.now(BEIJING_TZ).strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(iter([content.encode("utf-8")]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/submissions/{submission_id}/versions")
def list_submission_versions(
    submission_id: int,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    submission = db.query(TaskSubmission).filter(TaskSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail={"code": "SUBMISSION_NOT_FOUND", "message": "提交记录不存在。"})
    if identity["role"] == "student" and submission.user_id != identity["student"].id:
        raise HTTPException(status_code=403, detail={"code": "SUBMISSION_FORBIDDEN", "message": "不能查看其他学生的提交历史。"})
    if identity["role"] == "teacher":
        ensure_teaching_record_access(db, identity["teacher"], submission.owner_teacher_id, submission.classroom_id, "作业提交")
    versions = (
        db.query(SubmissionVersion)
        .filter(SubmissionVersion.submission_id == submission_id)
        .order_by(SubmissionVersion.version_number.desc())
        .all()
    )
    return {"versions": [to_submission_version_dict(version) for version in versions]}


@app.put("/api/submissions/{submission_id}/review")
def review_submission(
    submission_id: int,
    payload: SubmissionReviewRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    submission = db.query(TaskSubmission).filter(TaskSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail={"code": "SUBMISSION_NOT_FOUND", "message": "提交记录不存在。"})
    actor = teacher_actor(db, teacher)
    schedule = submission.task.course_schedule if submission.task else None
    if schedule:
        if not teacher_can_manage_schedule(db, actor, schedule):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "SUBMISSION_REVIEW_FORBIDDEN",
                    "message": "当前账号没有这项排课的批改权限。课程权限撤销后仅可查看历史记录。",
                },
            )
    else:
        ensure_teaching_record_access(db, actor, submission.owner_teacher_id, submission.classroom_id, "作业提交")
    ensure_archived_student_record_writable(actor, submission.user, "提交与评分")
    if payload.status not in {"submitted", "reviewed", "returned"}:
        raise HTTPException(status_code=400, detail={"code": "SUBMISSION_STATUS_INVALID", "message": "提交状态不合法。"})
    max_score = submission.max_score_snapshot or 100
    if payload.score is not None and (payload.score < 0 or payload.score > max_score):
        raise HTTPException(status_code=400, detail={"code": "SUBMISSION_SCORE_INVALID", "message": f"分数必须在 0 到 {max_score} 之间。"})
    submission.status = payload.status
    submission.feedback = payload.feedback
    submission.score = payload.score
    if payload.is_featured is not None:
        submission.is_featured = payload.is_featured
    db.commit()
    db.refresh(submission)
    record_teacher_audit(
        db,
        teacher,
        "submission.reviewed",
        target_type="submission",
        target_id=submission_id,
        summary=f"批改学生提交 #{submission_id}",
        details={"status": payload.status, "score": payload.score, "is_featured": submission.is_featured},
    )
    return {"submission": submission_payload(db, submission, actor)}


def feedback_template_payload(template: FeedbackTemplate) -> dict:
    return {
        "id": template.id,
        "owner_teacher_id": template.owner_teacher_id,
        "name": template.name,
        "content": template.content,
        "created_at": template.created_at.isoformat() if template.created_at else "",
    }


@app.get("/api/feedback-templates")
def list_feedback_templates(teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    query = db.query(FeedbackTemplate)
    if not is_admin_actor(actor):
        query = query.filter(FeedbackTemplate.owner_teacher_id == actor.id)
    return {"templates": [feedback_template_payload(item) for item in query.order_by(FeedbackTemplate.id.desc()).all()]}


@app.post("/api/feedback-templates")
def create_feedback_template(payload: FeedbackTemplateRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    if db.query(FeedbackTemplate).filter(
        FeedbackTemplate.owner_teacher_id == actor.id,
        FeedbackTemplate.name == payload.name.strip(),
    ).first():
        raise HTTPException(status_code=409, detail={"code": "FEEDBACK_TEMPLATE_EXISTS", "message": "同名评语模板已经存在。"})
    template = FeedbackTemplate(owner_teacher_id=actor.id, name=payload.name.strip(), content=payload.content.strip())
    db.add(template)
    db.commit()
    db.refresh(template)
    return {"template": feedback_template_payload(template)}


@app.delete("/api/feedback-templates/{template_id}")
def delete_feedback_template(template_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    template = db.query(FeedbackTemplate).filter(FeedbackTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail={"code": "FEEDBACK_TEMPLATE_NOT_FOUND", "message": "评语模板不存在。"})
    ensure_teacher_owns(teacher_actor(db, teacher), template.owner_teacher_id, "评语模板")
    db.delete(template)
    db.commit()
    return {"deleted": True}


@app.post("/api/moderation/check")
def moderation_check(payload: ModerationRequest, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    return run_moderation(db, payload.text, owner_teacher_id=actor.id)


@app.get("/api/moderation/settings")
def get_moderation_settings(_: bool = Depends(require_teacher), db: Session = Depends(get_db)):
    return {"blocked_words": get_blocked_words(db)}


@app.post("/api/moderation/settings")
def save_moderation_settings(
    payload: ModerationSettingsRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    blocked_words = save_blocked_words(db, payload.blocked_words)
    record_teacher_audit(
        db,
        teacher,
        "moderation.settings.updated",
        target_type="moderation_settings",
        summary="修改内容审核敏感词规则",
        details={"blocked_word_count": len(blocked_words)},
    )
    return {"blocked_words": blocked_words}


@app.get("/api/moderation/logs")
def list_moderation_logs(teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    query = db.query(ModerationLog)
    if not is_admin_actor(actor):
        query = query.filter(staff_scope_condition(ModerationLog, db, actor))
        archived_student_ids = db.query(User.id).filter(User.role == "student", User.archived_at.is_not(None))
        query = query.filter((ModerationLog.user_id.is_(None)) | (~ModerationLog.user_id.in_(archived_student_ids)))
    logs = query.order_by(ModerationLog.created_at.desc()).limit(100).all()
    return {"logs": [to_moderation_log_dict(log) for log in logs]}


@app.get("/api/moderation/images")
def list_image_moderation(status: str = "pending", teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    if status not in {"pending", "approved", "rejected", "all"}:
        raise HTTPException(status_code=400, detail={"code": "MODERATION_STATUS_INVALID", "message": "图片审核状态不合法。"})
    query = db.query(ModerationLog).filter(ModerationLog.resource_type == "image")
    actor = teacher_actor(db, teacher)
    if not is_admin_actor(actor):
        query = query.filter(staff_scope_condition(ModerationLog, db, actor))
        archived_student_ids = db.query(User.id).filter(User.role == "student", User.archived_at.is_not(None))
        query = query.filter((ModerationLog.user_id.is_(None)) | (~ModerationLog.user_id.in_(archived_student_ids)))
    if status != "all":
        query = query.filter(ModerationLog.status == status)
    logs = query.order_by(ModerationLog.created_at.desc()).limit(200).all()
    return {"items": [to_moderation_log_dict(log) for log in logs]}


@app.get("/api/moderation/images/{log_id}/file")
def get_image_moderation_file(log_id: int, teacher: TeacherSession = Depends(require_teacher), db: Session = Depends(get_db)):
    log = db.query(ModerationLog).filter(ModerationLog.id == log_id, ModerationLog.resource_type == "image").first()
    if not log:
        raise HTTPException(status_code=404, detail={"code": "IMAGE_REVIEW_NOT_FOUND", "message": "图片审核记录不存在。"})
    actor = teacher_actor(db, teacher)
    ensure_teaching_record_access(db, actor, log.owner_teacher_id, log.classroom_id, "图片审核记录")
    ensure_archived_student_record_writable(actor, db.get(User, log.user_id) if log.user_id else None, "安全审核记录")
    if log.resource_path.startswith(("http://", "https://")):
        return {"remote_url": log.resource_path}
    file_path = Path(log.resource_path)
    try:
        file_path.resolve().relative_to(DATA_DIR.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=403, detail={"code": "IMAGE_REVIEW_FILE_FORBIDDEN", "message": "审核图片不在受管工作区中。"})
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail={"code": "IMAGE_REVIEW_FILE_MISSING", "message": "待审核图片文件已缺失。"})
    return FileResponse(file_path)


@app.post("/api/moderation/images/{log_id}/review")
def review_image_moderation(
    log_id: int,
    payload: ImageModerationReviewRequest,
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    log = db.query(ModerationLog).filter(ModerationLog.id == log_id, ModerationLog.resource_type == "image").first()
    if not log:
        raise HTTPException(status_code=404, detail={"code": "IMAGE_REVIEW_NOT_FOUND", "message": "图片审核记录不存在。"})
    actor = teacher_actor(db, teacher)
    ensure_teaching_record_access(db, actor, log.owner_teacher_id, log.classroom_id, "图片审核记录")
    ensure_archived_student_record_writable(actor, db.get(User, log.user_id) if log.user_id else None, "安全审核记录")
    log.status = payload.status
    log.passed = payload.status == "approved"
    log.review_note = payload.note.strip()
    log.reviewed_at = beijing_now_naive()
    if log.project_id:
        project = db.query(Project).filter(Project.id == log.project_id).first()
        if project:
            project.moderation_status = payload.status
            project.moderation_reason = payload.note.strip() or log.reason
    db.commit()
    db.refresh(log)
    record_teacher_audit(
        db,
        teacher,
        "moderation.image.reviewed",
        target_type="moderation_log",
        target_id=log_id,
        summary=f"人工复核图片：{payload.status}",
        details={"status": payload.status, "project_id": log.project_id},
    )
    return {"item": to_moderation_log_dict(log)}


@app.get("/api/audit-logs")
def list_teacher_audit_logs(
    action: str = "",
    teacher: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    query = db.query(TeacherAuditLog)
    actor = teacher_actor(db, teacher)
    if not is_admin_actor(actor):
        query = query.filter(TeacherAuditLog.actor_user_id == actor.id)
    if action.strip():
        query = query.filter(TeacherAuditLog.action == action.strip())
    logs = query.order_by(TeacherAuditLog.created_at.desc(), TeacherAuditLog.id.desc()).limit(200).all()
    return {"logs": [teacher_audit_payload(log) for log in logs], "current_session_id": teacher.id}


@app.get("/api/usage")
def usage(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(UsageLog.feature, UsageLog.status, func.count(UsageLog.id))
        .group_by(UsageLog.feature, UsageLog.status)
        .all()
    )
    return {"usage": [{"feature": row[0], "status": row[1], "count": row[2]} for row in rows]}


@app.get("/api/system/backup")
def export_system_backup(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    classroom_teachers = (
        db.query(ClassroomTeacher, User)
        .join(User, User.id == ClassroomTeacher.teacher_id)
        .order_by(ClassroomTeacher.classroom_id.asc(), ClassroomTeacher.teacher_id.asc())
        .all()
    )
    return {
        "version": "0.2",
        "exported_at": datetime.now(BEIJING_TZ).isoformat(),
        "classrooms": [to_classroom_dict(item) for item in db.query(Classroom).order_by(Classroom.id.asc()).all()],
        "classroom_teachers": [
            {
                "classroom_id": assignment.classroom_id,
                "teacher_id": assignment.teacher_id,
                "teacher_username": account.username,
                "assigned_by_user_id": assignment.assigned_by_user_id,
                "created_at": assignment.created_at.isoformat() if assignment.created_at else None,
            }
            for assignment, account in classroom_teachers
        ],
        "students": [to_user_dict(item) for item in db.query(User).filter(User.role == "student").order_by(User.id.asc()).all()],
        "courses": [to_course_dict(item) for item in db.query(Course).order_by(Course.id.asc()).all()],
        "lessons": [to_lesson_dict(item) for item in db.query(Lesson).order_by(Lesson.course_id.asc(), Lesson.order_index.asc(), Lesson.id.asc()).all()],
        "tasks": [to_task_dict(item) for item in db.query(Task).order_by(Task.id.asc()).all()],
        "assets": [to_asset_dict(item) for item in db.query(Asset).order_by(Asset.id.asc()).all()],
        "projects": [to_project_dict(item) for item in db.query(Project).order_by(Project.id.asc()).all()],
        "submissions": [to_submission_dict(item) for item in db.query(TaskSubmission).order_by(TaskSubmission.id.asc()).all()],
        "submission_versions": [to_submission_version_dict(item) for item in db.query(SubmissionVersion).order_by(SubmissionVersion.id.asc()).all()],
        "feedback_templates": [feedback_template_payload(item) for item in db.query(FeedbackTemplate).order_by(FeedbackTemplate.id.asc()).all()],
        "moderation_logs": [to_moderation_log_dict(item) for item in db.query(ModerationLog).order_by(ModerationLog.id.asc()).all()],
    }


@app.post("/api/system/restore")
def restore_system_backup(payload: SystemRestoreRequest, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    actor = teacher_actor(db, teacher)
    classroom_id_map: dict[int, int] = {}
    user_id_map: dict[int, int] = {}
    course_id_map: dict[int, int] = {}
    lesson_id_map: dict[int, int] = {}
    task_id_map: dict[int, int] = {}
    project_id_map: dict[int, int] = {}
    submission_id_map: dict[int, int] = {}
    imported = {
        "classrooms": 0, "classroom_teachers": 0, "students": 0, "courses": 0, "lessons": 0, "tasks": 0,
        "projects": 0, "assets": 0, "submissions": 0, "submission_versions": 0, "feedback_templates": 0, "moderation_logs": 0,
    }

    for item in payload.classrooms:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        classroom = db.query(Classroom).filter(Classroom.name == name).first()
        if not classroom:
            grade_level = str(item.get("grade_level") or "mixed")
            classroom = Classroom(
                owner_teacher_id=actor.id,
                name=name,
                grade_level="mixed" if grade_level == "mixed" else normalize_school_stage(grade_level, default="mixed"),
            )
            db.add(classroom)
            db.flush()
            imported["classrooms"] += 1
        if item.get("id") is not None:
            classroom_id_map[int(item["id"])] = classroom.id

    for item in payload.classroom_teachers:
        old_classroom_id = item.get("classroom_id")
        classroom_id = classroom_id_map.get(int(old_classroom_id)) if old_classroom_id is not None else None
        username = str(item.get("teacher_username") or "").strip().lower()
        account = find_account_by_username(db, username) if username else None
        if not classroom_id or not account or account.role != "teacher" or not account.active:
            continue
        exists = db.query(ClassroomTeacher).filter_by(classroom_id=classroom_id, teacher_id=account.id).first()
        if exists:
            continue
        db.add(ClassroomTeacher(
            classroom_id=classroom_id,
            teacher_id=account.id,
            assigned_by_user_id=actor.id,
        ))
        imported["classroom_teachers"] += 1

    for item in payload.students:
        name = str(item.get("name", "")).strip()
        username_raw = str(item.get("username") or "").strip().lower()
        if not name:
            continue
        try:
            username = validate_username(username_raw) if username_raw else ""
        except HTTPException:
            username = ""
        student = find_account_by_username(db, username) if username else db.query(User).filter(
            User.role == "student", User.username == "", User.name == name,
        ).first()
        old_classroom_id = item.get("classroom_id")
        classroom_id = classroom_id_map.get(int(old_classroom_id)) if old_classroom_id is not None else None
        if not student:
            archived_at = parse_backup_datetime(item.get("archived_at"))
            legacy_unregistered = not username
            if legacy_unregistered and archived_at is None:
                archived_at = beijing_now_naive()
            student = User(
                name=name,
                role="student",
                classroom_id=None if archived_at else classroom_id,
                username=username,
                password_hash=hash_password(DEFAULT_STUDENT_PASSWORD) if username else "",
                password_change_required=False,
                registered_at=now() if username else None,
                access_code="",
                age_level=normalize_school_stage(item.get("age_level")),
                active=False if archived_at else bool(item.get("active", True)),
                archived_at=archived_at,
                archived_classroom_name=str(
                    item.get("archived_classroom_name")
                    or (db.get(Classroom, classroom_id).name if classroom_id and db.get(Classroom, classroom_id) else "未分配班级")
                ) if archived_at else "",
                created_by_user_id=actor.id,
            )
            db.add(student)
            db.flush()
            imported["students"] += 1
        if item.get("id") is not None:
            user_id_map[int(item["id"])] = student.id

    for item in payload.courses:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        course = db.query(Course).filter(Course.title == title).first()
        if not course:
            old_classroom_id = item.get("classroom_id")
            classroom_id = classroom_id_map.get(int(old_classroom_id)) if old_classroom_id is not None else None
            course = Course(
                owner_teacher_id=actor.id,
                title=title,
                description=str(item.get("description") or ""),
                classroom_id=classroom_id,
                package_version=str(item.get("package_version") or "1.0.0"),
                author=str(item.get("author") or ""),
                age_range=str(item.get("age_range") or "全年龄"),
                cover_path=str(item.get("cover_path") or ""),
                dependencies_json=json_dumps(item.get("dependencies") or []),
                checklist_json=json_dumps(item.get("checklist") or []),
                status=str(item.get("status") or "published"),
                starts_at=parse_backup_datetime(item.get("starts_at")),
                ends_at=parse_backup_datetime(item.get("ends_at")),
            )
            db.add(course)
            db.flush()
            imported["courses"] += 1
        if item.get("id") is not None:
            course_id_map[int(item["id"])] = course.id

    for item in payload.lessons:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        old_course_id = item.get("course_id")
        course_id = course_id_map.get(int(old_course_id)) if old_course_id is not None else None
        lesson = db.query(Lesson).filter(Lesson.title == title, Lesson.course_id == course_id).first()
        if not lesson:
            lesson = Lesson(
                owner_teacher_id=actor.id,
                course_id=course_id,
                title=title,
                content=str(item.get("content") or ""),
                order_index=int(item.get("order_index") or 0),
            )
            db.add(lesson)
            db.flush()
            imported["lessons"] += 1
        if item.get("id") is not None:
            lesson_id_map[int(item["id"])] = lesson.id

    for item in payload.tasks:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        task = db.query(Task).filter(Task.title == title).first()
        old_classroom_id = item.get("classroom_id")
        classroom_id = classroom_id_map.get(int(old_classroom_id)) if old_classroom_id is not None else None
        old_lesson_id = item.get("lesson_id")
        lesson_id = lesson_id_map.get(int(old_lesson_id)) if old_lesson_id is not None else None
        if not task:
            task = Task(
                owner_teacher_id=actor.id,
                title=title,
                instructions=str(item.get("instructions") or ""),
                tool_scope=str(item.get("tool_scope") or "text,image,workflow"),
                classroom_id=classroom_id,
                lesson_id=lesson_id,
                status=str(item.get("status") or "published"),
                starts_at=parse_backup_datetime(item.get("starts_at")),
                due_at=parse_backup_datetime(item.get("due_at")),
                rubric_json=json_dumps(item.get("rubric") or [{"criterion": "完成度", "max_score": 100}]),
            )
            db.add(task)
            db.flush()
            imported["tasks"] += 1
        if item.get("id") is not None:
            task_id_map[int(item["id"])] = task.id

    for item in payload.projects:
        title = str(item.get("title", "")).strip()
        old_user_id = item.get("user_id")
        user_id = user_id_map.get(int(old_user_id)) if old_user_id is not None else None
        if not title:
            continue
        project = db.query(Project).filter(Project.title == title, Project.user_id == user_id).first()
        if not project:
            old_classroom_id = item.get("classroom_id")
            classroom_id = classroom_id_map.get(int(old_classroom_id)) if old_classroom_id is not None else None
            project = Project(
                owner_teacher_id=actor.id,
                title=title,
                project_type=str(item.get("project_type") or "text"),
                user_id=user_id,
                classroom_id=classroom_id,
                owner_name=str(item.get("owner_name") or "未归属学生"),
                summary=str(item.get("summary") or ""),
                file_path=str(item.get("file_path") or ""),
                lifecycle_status=str(item.get("lifecycle_status") or "active"),
                archived_at=parse_backup_datetime(item.get("archived_at")),
                trashed_at=parse_backup_datetime(item.get("trashed_at")),
                moderation_status=str(item.get("moderation_status") or "approved"),
                moderation_reason=str(item.get("moderation_reason") or ""),
            )
            db.add(project)
            db.flush()
            imported["projects"] += 1
        if item.get("id") is not None:
            project_id_map[int(item["id"])] = project.id

    for item in payload.assets:
        file_path = str(item.get("file_path", "")).strip()
        asset_type = str(item.get("asset_type") or "document")
        if not file_path or db.query(Asset).filter(Asset.file_path == file_path, Asset.asset_type == asset_type).first():
            continue
        old_project_id = item.get("project_id")
        old_classroom_id = item.get("classroom_id")
        old_lesson_id = item.get("lesson_id")
        db.add(Asset(
            owner_teacher_id=actor.id,
            project_id=project_id_map.get(int(old_project_id)) if old_project_id is not None else None,
            classroom_id=classroom_id_map.get(int(old_classroom_id)) if old_classroom_id is not None else None,
            lesson_id=lesson_id_map.get(int(old_lesson_id)) if old_lesson_id is not None else None,
            asset_type=asset_type,
            file_path=file_path,
            metadata_json=str(item.get("metadata_json") or "{}"),
            original_name=str(item.get("original_name") or Path(file_path).name),
            mime_type=str(item.get("mime_type") or ""),
            file_size=int(item.get("file_size") or 0),
            file_extension=str(item.get("file_extension") or Path(file_path).suffix.lower()),
            checksum_sha256=str(item.get("checksum_sha256") or ""),
            safety_status=str(item.get("safety_status") or "unverified"),
        ))
        imported["assets"] += 1

    db.flush()
    for item in payload.submissions:
        old_task_id, old_project_id, old_user_id = item.get("task_id"), item.get("project_id"), item.get("user_id")
        task_id = task_id_map.get(int(old_task_id)) if old_task_id is not None else None
        project_id = project_id_map.get(int(old_project_id)) if old_project_id is not None else None
        user_id = user_id_map.get(int(old_user_id)) if old_user_id is not None else None
        if not task_id or not project_id or not user_id:
            continue
        existing_submission = db.query(TaskSubmission).filter(TaskSubmission.task_id == task_id, TaskSubmission.user_id == user_id).first()
        if existing_submission:
            if item.get("id") is not None:
                submission_id_map[int(item["id"])] = existing_submission.id
            continue
        old_classroom_id = item.get("classroom_id")
        submission = TaskSubmission(
            owner_teacher_id=actor.id,
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
            classroom_id=classroom_id_map.get(int(old_classroom_id)) if old_classroom_id is not None else None,
            status=str(item.get("status") or "submitted"),
            feedback=str(item.get("feedback") or ""),
            score=item.get("score"),
            version_count=max(1, int(item.get("version_count") or 1)),
            is_late=bool(item.get("is_late", False)),
            is_featured=bool(item.get("is_featured", False)),
        )
        db.add(submission)
        db.flush()
        if item.get("id") is not None:
            submission_id_map[int(item["id"])] = submission.id
        imported["submissions"] += 1

    for item in payload.submission_versions:
        old_submission_id = item.get("submission_id")
        old_project_id = item.get("project_id")
        submission_id = submission_id_map.get(int(old_submission_id)) if old_submission_id is not None else None
        project_id = project_id_map.get(int(old_project_id)) if old_project_id is not None else None
        if not submission_id or not project_id:
            continue
        version_number = max(1, int(item.get("version_number") or 1))
        if db.query(SubmissionVersion).filter(
            SubmissionVersion.submission_id == submission_id,
            SubmissionVersion.version_number == version_number,
        ).first():
            continue
        db.add(SubmissionVersion(
            submission_id=submission_id,
            version_number=version_number,
            project_id=project_id,
            project_title=str(item.get("project_title") or ""),
            project_summary=str(item.get("project_summary") or ""),
            project_file_path=str(item.get("project_file_path") or ""),
            is_late=bool(item.get("is_late", False)),
        ))
        imported["submission_versions"] += 1

    for item in payload.feedback_templates:
        name = str(item.get("name") or "").strip()
        content = str(item.get("content") or "").strip()
        if not name or not content or db.query(FeedbackTemplate).filter(FeedbackTemplate.name == name).first():
            continue
        db.add(FeedbackTemplate(owner_teacher_id=actor.id, name=name, content=content))
        imported["feedback_templates"] += 1

    for item in payload.moderation_logs:
        input_text = str(item.get("input_text") or "")
        reason = str(item.get("reason") or "")
        if not input_text or db.query(ModerationLog).filter(ModerationLog.input_text == input_text, ModerationLog.reason == reason).first():
            continue
        old_project_id = item.get("project_id")
        old_user_id = item.get("user_id")
        moderation_log = ModerationLog(
            owner_teacher_id=actor.id,
            user_id=user_id_map.get(int(old_user_id)) if old_user_id is not None else None,
            classroom_id=(
                classroom_id_map.get(int(item["classroom_id"]))
                if item.get("classroom_id") is not None
                else None
            ),
            input_text=input_text,
            content_stage=str(item.get("content_stage") or "input"),
            passed=bool(item.get("passed", True)),
            reason=reason,
            resource_type=str(item.get("resource_type") or "text"),
            resource_path=str(item.get("resource_path") or ""),
            status=str(item.get("status") or ("approved" if item.get("passed", True) else "rejected")),
            project_id=project_id_map.get(int(old_project_id)) if old_project_id is not None else None,
            review_note=str(item.get("review_note") or ""),
            reviewed_at=parse_backup_datetime(item.get("reviewed_at")),
        )
        db.add(moderation_log)
        db.flush()
        if moderation_log.project_id:
            linked_project = db.query(Project).filter(Project.id == moderation_log.project_id).first()
            if linked_project:
                if moderation_log.classroom_id is None:
                    moderation_log.classroom_id = linked_project.classroom_id
                if linked_project.moderation_log_id is None:
                    linked_project.moderation_log_id = moderation_log.id
        imported["moderation_logs"] += 1

    db.commit()
    record_teacher_audit(
        db,
        teacher,
        "backup.legacy_restored",
        target_type="backup",
        summary="追加恢复兼容 JSON 备份",
        details={"imported": imported},
    )
    return {
        "status": "ok",
        "imported": imported,
        "skipped": {},
    }


@app.get("/api/system/license")
def get_license(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    return {"license": get_license_status(db)}


@app.post("/api/system/license")
def save_license(
    payload: LicenseSettingsRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    status = save_license_status(db, payload.model_dump())
    record_teacher_audit(
        db,
        teacher,
        "license.updated",
        target_type="license",
        target_id=status["license_id"],
        summary="更新签名许可证" if status["plan"] != "community" else "切换为社区授权",
        details={
            "organization": status["organization"],
            "plan": status["plan"],
            "status": status["status"],
            "seats": status["seats"],
            "expires_at": status["expires_at"],
            "device_bound": status["device_bound"],
        },
    )
    return {"license": status}


def _provider_settings_values(payload: ProviderSettingsRequest) -> dict[str, Any]:
    values = payload.model_dump()
    presets = list_provider_presets()
    supported_types = {preset["provider_type"] for preset in presets}
    if values["provider_type"] not in supported_types:
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_TYPE_INVALID", "message": "模型服务商类型不受支持。"})
    preset = next(item for item in presets if item["provider_type"] == values["provider_type"])
    values["name"] = values["name"].strip()
    values["base_url"] = values["base_url"].strip().rstrip("/")
    values["text_model"] = values["text_model"].strip()
    values["image_model"] = values["image_model"].strip()
    values["video_model"] = values["video_model"].strip()
    for capability in ("text", "image", "video"):
        if capability not in preset["capabilities"]:
            values[f"{capability}_model"] = ""
    if not values["base_url"].startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_BASE_URL_INVALID", "message": "Base URL 必须使用 http:// 或 https://。"})
    return values


def _provider_audit_details(provider: AIProvider, api_key_changed: bool) -> dict[str, Any]:
    return {
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "text_model": provider.text_model,
        "image_model": provider.image_model,
        "video_model": provider.video_model,
        "enabled": provider.enabled,
        "api_key_changed": api_key_changed,
    }


@app.get("/api/settings/provider")
def get_provider(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        provider = active_provider(db, "text")
    except HTTPException:
        provider = db.query(AIProvider).order_by(AIProvider.id.desc()).first()
    return {"provider": provider_payload(provider)}


@app.get("/api/settings/providers")
def get_providers(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    return provider_management_payload(db)


@app.get("/api/settings/provider-status")
def get_provider_status(db: Session = Depends(get_db)):
    return {"provider": provider_status_payload(db)}


@app.get("/api/models/catalog")
def get_model_catalog(_: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    return provider_model_catalog_payload(db)


@app.get("/api/settings/provider-presets")
def get_provider_presets(_: TeacherSession = Depends(require_admin)):
    return {"presets": list_provider_presets()}


@app.post("/api/settings/provider")
def save_provider(payload: ProviderSettingsRequest, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    ensure_default_provider_routes(db)
    provider = db.query(AIProvider).order_by(AIProvider.id.desc()).first()
    values = _provider_settings_values(payload)
    if values["api_key"]:
        values["api_key"] = encrypt_secret(values["api_key"])
    if provider:
        for key, value in values.items():
            if key == "api_key" and value == "":
                continue
            setattr(provider, key, value)
    else:
        provider = AIProvider(**values)
        db.add(provider)
    db.commit()
    db.refresh(provider)
    ensure_default_provider_routes(db)
    record_teacher_audit(
        db,
        teacher,
        "provider.settings.updated",
        target_type="ai_provider",
        target_id=provider.id,
        summary=f"更新 AI 服务配置：{provider.name}",
        details=_provider_audit_details(provider, bool(payload.api_key)),
    )
    return {"provider": provider_payload(provider)}


@app.post("/api/settings/providers")
def create_provider(payload: ProviderSettingsRequest, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    ensure_default_provider_routes(db)
    values = _provider_settings_values(payload)
    if values["api_key"]:
        values["api_key"] = encrypt_secret(values["api_key"])
    provider = AIProvider(**values)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    ensure_default_provider_routes(db)
    record_teacher_audit(
        db,
        teacher,
        "provider.created",
        target_type="ai_provider",
        target_id=provider.id,
        summary=f"新增 AI 模型服务：{provider.name}",
        details=_provider_audit_details(provider, bool(payload.api_key)),
    )
    return {"provider": provider_payload(provider), **provider_management_payload(db)}


@app.put("/api/settings/providers/{provider_id}")
def update_provider(
    provider_id: int,
    payload: ProviderSettingsRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = db.query(AIProvider).filter(AIProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": "模型服务配置不存在。"})
    values = _provider_settings_values(payload)
    if values["api_key"]:
        values["api_key"] = encrypt_secret(values["api_key"])
    for key, value in values.items():
        if key == "api_key" and value == "":
            continue
        setattr(provider, key, value)
    provider.last_test_status = "untested"
    provider.last_test_message = "配置已修改，等待重新测试。"
    provider.last_tested_at = None
    db.commit()
    db.refresh(provider)
    record_teacher_audit(
        db,
        teacher,
        "provider.updated",
        target_type="ai_provider",
        target_id=provider.id,
        summary=f"更新 AI 模型服务：{provider.name}",
        details=_provider_audit_details(provider, bool(payload.api_key)),
    )
    return {"provider": provider_payload(provider)}


@app.patch("/api/settings/providers/{provider_id}/enabled")
def set_provider_enabled(
    provider_id: int,
    payload: ProviderEnabledRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = db.query(AIProvider).filter(AIProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": "模型服务配置不存在。"})
    provider.enabled = payload.enabled
    db.commit()
    db.refresh(provider)
    record_teacher_audit(
        db,
        teacher,
        "provider.enabled_changed",
        target_type="ai_provider",
        target_id=provider.id,
        summary=f"{'启用' if provider.enabled else '停用'} AI 模型服务：{provider.name}",
        details={"enabled": provider.enabled, "provider_type": provider.provider_type},
    )
    return {"provider": provider_payload(provider)}


@app.delete("/api/settings/providers/{provider_id}")
def delete_provider(provider_id: int, teacher: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    provider = db.query(AIProvider).filter(AIProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": "模型服务配置不存在。"})
    bound_tasks = db.query(VideoTask).filter(
        (VideoTask.provider_id == provider.id)
        | ((VideoTask.provider_id.is_(None)) & (VideoTask.provider_type == provider.provider_type))
    ).count()
    if bound_tasks:
        raise HTTPException(
            status_code=409,
            detail={"code": "PROVIDER_HAS_VIDEO_TASKS", "message": f"该配置仍绑定 {bound_tasks} 个视频任务，请停用并保留配置。"},
        )
    provider_name = provider.name
    provider_type = provider.provider_type
    remove_provider_from_routes(db, provider.id)
    db.delete(provider)
    db.commit()
    record_teacher_audit(
        db,
        teacher,
        "provider.deleted",
        target_type="ai_provider",
        target_id=provider_id,
        summary=f"删除 AI 模型服务：{provider_name}",
        details={"provider_type": provider_type},
    )
    return {"message": "模型服务配置已删除。", **provider_management_payload(db)}


@app.put("/api/settings/provider-routes")
def update_provider_routes(
    payload: ProviderRoutesRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    routes = save_provider_routes(db, payload.routes)
    record_teacher_audit(
        db,
        teacher,
        "provider.routes_updated",
        target_type="ai_provider_routes",
        summary="更新文字、图片和视频模型路由",
        details={"routes": routes},
    )
    return {"routes": routes, "status": provider_status_payload(db)}


@app.post("/api/settings/provider/test")
async def test_saved_provider(
    payload: ProviderConnectionTestRequest,
    _: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return await test_provider_connection(db, payload.capability, payload.provider_id)


@app.post("/api/settings/providers/{provider_id}/test")
async def test_selected_provider(
    provider_id: int,
    payload: ProviderConnectionTestRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        result = await test_provider_connection(db, payload.capability, provider_id)
    except HTTPException as exc:
        record_teacher_audit(
            db,
            teacher,
            "provider.connection_tested",
            target_type="ai_provider",
            target_id=provider_id,
            summary=f"模型服务连通测试失败：{payload.capability}",
            details={"capability": payload.capability, "status": "failed", "error_code": exc.detail.get("code", "") if isinstance(exc.detail, dict) else ""},
        )
        raise
    record_teacher_audit(
        db,
        teacher,
        "provider.connection_tested",
        target_type="ai_provider",
        target_id=provider_id,
        summary=f"模型服务连通测试通过：{payload.capability}",
        details={"capability": payload.capability, "status": "success", "latency_ms": result.get("latency_ms", 0)},
    )
    return result
