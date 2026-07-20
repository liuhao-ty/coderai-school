from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.audit import record_teacher_audit
from backend.app.auth import require_admin
from backend.app.db import DATA_DIR, get_db
from backend.app.models import (
    AIProvider,
    AppSetting,
    Classroom,
    Course,
    GuardianConsent,
    PrivacyPolicy,
    ProviderAcceptanceRun,
    Task,
    TaskSubmission,
    TeacherSession,
    User,
    VideoTask,
    now,
)
from backend.app.provider_presets import get_provider_preset, list_provider_presets, provider_capabilities
from backend.app.schemas import (
    ClassroomAcceptanceRequest,
    ProviderAcceptanceScopeRequest,
    ProviderAcceptanceTestRequest,
    ProviderFailureEvidencePolicyRequest,
)
from backend.app.services import (
    active_provider,
    generate_image,
    generate_text,
    generate_video_task,
    provider_candidates,
    provider_model,
    provider_status_payload,
    run_moderation,
)


router = APIRouter(prefix="/api/readiness", tags=["readiness"])

PROVIDER_COST_CONFIRMATION = "我确认本次调用可能产生费用"
CLASSROOM_CONFIRMATION = "我确认以上数据来自真实课堂"
CLASSROOM_ATTESTATION_KEY = "p0_classroom_attestation"
PROVIDER_SCOPE_KEY = "p0_provider_acceptance_scope"
FAILURE_EVIDENCE_POLICY_KEY = "p0_failure_evidence_policy"
FAILURE_EVIDENCE_WAIVER_CONFIRMATION = "我确认本期跳过失败证据链收集"
EVIDENCE_ROOT = DATA_DIR / "acceptance" / "evidence"
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
ALLOWED_EVIDENCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".json", ".txt"}
FAILURE_SCENARIOS = ("network", "timeout", "rate_limit", "quota", "provider_unavailable", "content_blocked")
REQUIRED_SCOPE_CAPABILITIES = ("text", "image")

SAFE_PROMPTS = {
    "text": "请用两句话介绍一个适合小学生的安全编程小游戏创意。",
    "image": "一个友好的机器人在明亮教室里教孩子学习基础编程，无文字，适合儿童",
    "video": "一个友好的玩具机器人在明亮教室里挥手，适合儿童，无文字",
}

ERROR_SCENARIOS = {
    "AI_AUTH_ERROR": "auth",
    "AI_NETWORK_ERROR": "network",
    "AI_TIMEOUT": "timeout",
    "AI_RATE_LIMITED": "rate_limit",
    "AI_QUOTA_EXHAUSTED": "quota",
    "AI_PROVIDER_UNAVAILABLE": "provider_unavailable",
    "CONTENT_BLOCKED": "content_blocked",
    "IMAGE_OUTPUT_REJECTED": "content_blocked",
}

SCENARIO_ERROR_CODES = {
    "network": "AI_NETWORK_ERROR",
    "timeout": "AI_TIMEOUT",
    "rate_limit": "AI_RATE_LIMITED",
    "quota": "AI_QUOTA_EXHAUSTED",
    "provider_unavailable": "AI_PROVIDER_UNAVAILABLE",
    "content_blocked": "CONTENT_BLOCKED",
    "auth": "AI_AUTH_ERROR",
    "other_error": "AI_PROVIDER_ERROR",
}

LIVE_FAILURE_SOURCES = frozenset({
    "provider_http",
    "network",
    "timeout",
    "response_validation",
    "content_moderation",
    "configuration",
    "application",
    "internal",
})
PUBLIC_RUN_DETAIL_KEYS = frozenset({
    "targeted_provider_id",
    "fallback_disabled",
    "output_characters",
    "moderation_status",
    "result_kind",
    "provider_task_status",
})
ERROR_FAILURE_SOURCES = {
    "AI_AUTH_ERROR": "provider_http",
    "AI_QUOTA_EXHAUSTED": "provider_http",
    "AI_RATE_LIMITED": "provider_http",
    "AI_PROVIDER_UNAVAILABLE": "provider_http",
    "AI_NETWORK_ERROR": "network",
    "AI_TIMEOUT": "timeout",
    "AI_RESPONSE_INVALID": "response_validation",
    "CONTENT_BLOCKED": "content_moderation",
    "IMAGE_OUTPUT_REJECTED": "content_moderation",
    "P0_TEXT_RESULT_EMPTY": "response_validation",
    "P0_IMAGE_RESULT_MISSING": "response_validation",
    "P0_PROVIDER_TEST_INTERNAL_ERROR": "internal",
}

SENSITIVE_EVIDENCE = re.compile(
    r"(?i)(?:api[_ -]?key|authorization|bearer|access[_ -]?token|secret)\s*[:=]\s*[\"']?[A-Za-z0-9._/+\-=]{8,}|\bsk-[A-Za-z0-9_-]{8,}",
)


def _iso(value: Any) -> str:
    return value.isoformat() if value else ""


def _count_distinct(db: Session, column: Any, *conditions: Any) -> int:
    return int(db.query(func.count(func.distinct(column))).filter(*conditions).scalar() or 0)


def _check(key: str, label: str, current: int, target: int, detail: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "current": current,
        "target": target,
        "passed": current >= target,
        "detail": detail,
    }


def _safe_evidence_name(value: str) -> str:
    name = Path(str(value or "evidence")).name.strip()[:180]
    if not name or any(ord(char) < 32 for char in name):
        return "evidence"
    return name


def _default_provider_scope() -> list[dict[str, Any]]:
    return [
        {
            "provider_type": str(preset["provider_type"]),
            "name": str(preset["name"]),
            "capabilities": list(preset["capabilities"]),
        }
        for preset in list_provider_presets()
    ]


def _read_provider_scope(db: Session) -> dict[str, Any]:
    setting = db.query(AppSetting).filter(AppSetting.key == PROVIDER_SCOPE_KEY).first()
    configured = False
    providers = _default_provider_scope()
    if setting:
        try:
            raw = json.loads(setting.value)
        except (json.JSONDecodeError, TypeError):
            raw = {}
        stored = raw.get("providers") if isinstance(raw, dict) else None
        if isinstance(stored, list):
            preset_map = {str(item["provider_type"]): item for item in list_provider_presets()}
            normalized = []
            seen = set()
            for item in stored:
                if not isinstance(item, dict):
                    continue
                provider_type = str(item.get("provider_type") or "")
                preset = preset_map.get(provider_type)
                if not preset or provider_type in seen:
                    continue
                capabilities = [
                    capability
                    for capability in preset["capabilities"]
                    if capability in set(item.get("capabilities") or [])
                ]
                if not capabilities:
                    continue
                seen.add(provider_type)
                normalized.append({
                    "provider_type": provider_type,
                    "name": str(preset["name"]),
                    "capabilities": capabilities,
                })
            if normalized:
                providers = normalized
                configured = True
    covered = sorted({
        capability
        for provider in providers
        for capability in provider["capabilities"]
    })
    missing = [item for item in REQUIRED_SCOPE_CAPABILITIES if item not in covered]
    return {
        "configured": configured,
        "providers": providers,
        "required_capabilities": list(REQUIRED_SCOPE_CAPABILITIES),
        "covered_capabilities": covered,
        "requirements_met": configured and not missing,
        "missing_capabilities": missing,
        "updated_at": _iso(setting.updated_at) if setting else "",
    }


def _read_failure_evidence_policy(db: Session) -> dict[str, Any]:
    setting = db.query(AppSetting).filter(AppSetting.key == FAILURE_EVIDENCE_POLICY_KEY).first()
    raw: dict[str, Any] = {}
    if setting:
        try:
            parsed = json.loads(setting.value)
            raw = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            raw = {}
    required = raw.get("required") is not False
    return {
        "required": required,
        "waived": not required,
        "reason": str(raw.get("reason") or ""),
        "confirmed_at": str(raw.get("confirmed_at") or ""),
        "waiver_confirmation": FAILURE_EVIDENCE_WAIVER_CONFIRMATION,
        "message": (
            "失败证据链仍是当前 P0 必需项。"
            if required
            else "失败证据链已明确跳过；这不代表六类失败场景已经验证。"
        ),
    }


def _validate_evidence(content: bytes, extension: str) -> str:
    if not content:
        raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_EMPTY", "message": "验收证据文件为空。"})
    if len(content) > MAX_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail={"code": "P0_EVIDENCE_TOO_LARGE", "message": "验收证据不能超过 5 MB。"})
    if extension not in ALLOWED_EVIDENCE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={"code": "P0_EVIDENCE_TYPE_INVALID", "message": "验收证据只支持 PNG、JPEG、PDF、JSON 或 TXT。"},
        )
    if extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_SIGNATURE_INVALID", "message": "PNG 文件签名不正确。"})
    if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_SIGNATURE_INVALID", "message": "JPEG 文件签名不正确。"})
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_SIGNATURE_INVALID", "message": "PDF 文件签名不正确。"})
    if extension in {".json", ".txt"}:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_ENCODING_INVALID", "message": "文本证据必须使用 UTF-8。"}) from exc
        if "\x00" in text or any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_CONTROL_CHARACTERS", "message": "文本证据包含非法控制字符。"})
        if extension == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_JSON_INVALID", "message": "JSON 证据格式不正确。"}) from exc
        if SENSITIVE_EVIDENCE.search(text):
            raise HTTPException(
                status_code=400,
                detail={"code": "P0_EVIDENCE_SECRET_DETECTED", "message": "证据疑似包含 API Key 或访问令牌，请脱敏后重新上传。"},
            )
    return hashlib.sha256(content).hexdigest()


def _resolve_evidence(relative: str) -> Path:
    normalized = str(relative or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_PATH_INVALID", "message": "验收证据路径不安全。"})
    root = EVIDENCE_ROOT.resolve()
    target = EVIDENCE_ROOT.joinpath(*path.parts)
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail={"code": "P0_EVIDENCE_NOT_FOUND", "message": "验收证据文件不存在。"}) from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise HTTPException(status_code=404, detail={"code": "P0_EVIDENCE_NOT_FOUND", "message": "验收证据文件不存在或不安全。"})
    return resolved


def _classroom_snapshot(db: Session) -> tuple[dict[str, Any], dict[str, Any]]:
    active_students = db.query(User).filter(
        User.role == "student",
        User.active.is_(True),
        User.archived_at.is_(None),
    ).order_by(User.id).all()
    active_ids = [student.id for student in active_students]
    classrooms = db.query(Classroom).order_by(Classroom.id).all()
    published_courses = db.query(Course).filter(Course.status == "published").order_by(Course.id).all()
    published_tasks = db.query(Task).filter(Task.status == "published").order_by(Task.id).all()
    reviewed_submissions = db.query(TaskSubmission).filter(
        TaskSubmission.status.in_(["reviewed", "returned"]),
        TaskSubmission.feedback != "",
    ).order_by(TaskSubmission.id).all()
    current_policy = db.query(PrivacyPolicy).filter(
        PrivacyPolicy.status == "published",
    ).order_by(PrivacyPolicy.effective_at.desc(), PrivacyPolicy.id.desc()).first()

    classrooms_with_students = len({student.classroom_id for student in active_students if student.classroom_id is not None})
    classrooms_with_courses = len({course.classroom_id for course in published_courses if course.classroom_id is not None})
    submission_students = len({item.user_id for item in reviewed_submissions})
    reviewed_classrooms = len({item.classroom_id for item in reviewed_submissions if item.classroom_id is not None})
    resubmitted = db.query(TaskSubmission).filter(TaskSubmission.version_count >= 2).count()

    consent_target = len(active_ids) if current_policy and current_policy.require_guardian_consent else 0
    consent_count = 0
    if consent_target:
        consent_count = _count_distinct(
            db,
            GuardianConsent.user_id,
            GuardianConsent.policy_id == current_policy.id,
            GuardianConsent.revoked_at.is_(None),
            GuardianConsent.user_id.in_(active_ids),
        )

    checks = [
        _check("classrooms", "至少 2 个班级", len(classrooms), 2, "班级必须真实用于本次课堂。"),
        _check("active_students", "至少 10 名在读学生", len(active_students), 10, "停用或离班归档账号不计入。"),
        _check("student_classrooms", "至少 2 个班级有在读学生", classrooms_with_students, 2, "不能把全部学生只放在一个班级。"),
        _check("published_courses", "至少 2 门已发布课程", len(published_courses), 2, "草稿课程不计入课堂验收。"),
        _check("course_classrooms", "至少 2 个班级有已发布课程", classrooms_with_courses, 2, "每个验收班级都应有课程。"),
        _check("published_tasks", "至少 2 个已发布任务", len(published_tasks), 2, "任务应关联实际课堂内容。"),
        _check("reviewed_students", "至少 2 名学生完成批改闭环", submission_students, 2, "提交必须有教师反馈，状态为已批改或需修改。"),
        _check("reviewed_classrooms", "两个班级均有批改记录", reviewed_classrooms, 2, "必须证明两个班级都走过提交与批改。"),
        _check("resubmission", "至少 1 次重新提交", resubmitted, 1, "用于验证版本历史和反馈后的再次提交。"),
        _check("privacy_policy", "已发布隐私政策", 1 if current_policy else 0, 1, "真实课堂开始前必须发布当前政策。"),
    ]
    if consent_target:
        checks.append(_check(
            "guardian_consents",
            "监护人授权覆盖全部在读学生",
            consent_count,
            consent_target,
            "仅当前政策下未撤回的授权计入。",
        ))

    counts = {
        "classrooms": len(classrooms),
        "active_students": len(active_students),
        "classrooms_with_students": classrooms_with_students,
        "published_courses": len(published_courses),
        "classrooms_with_courses": classrooms_with_courses,
        "published_tasks": len(published_tasks),
        "reviewed_students": submission_students,
        "reviewed_classrooms": reviewed_classrooms,
        "resubmissions": resubmitted,
        "guardian_consents": consent_count,
        "guardian_consents_required": consent_target,
    }
    evidence = {
        "students": [[item.id, item.classroom_id, item.active, _iso(item.archived_at)] for item in active_students],
        "courses": [[item.id, item.classroom_id, item.status, _iso(item.updated_at)] for item in published_courses],
        "tasks": [[item.id, item.classroom_id, item.lesson_id, item.status] for item in published_tasks],
        "submissions": [
            [item.id, item.user_id, item.classroom_id, item.status, item.score, item.version_count, _iso(item.updated_at)]
            for item in reviewed_submissions
        ],
        "policy": [current_policy.id, current_policy.version, _iso(current_policy.effective_at)] if current_policy else [],
        "consent_user_ids": sorted(
            row[0]
            for row in db.query(GuardianConsent.user_id).filter(
                GuardianConsent.policy_id == current_policy.id,
                GuardianConsent.revoked_at.is_(None),
            ).distinct().all()
        ) if current_policy and current_policy.require_guardian_consent else [],
    }
    evidence_hash = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    snapshot = {
        "checks": checks,
        "counts": counts,
        "checks_passed": all(item["passed"] for item in checks),
        "passed_checks": sum(1 for item in checks if item["passed"]),
        "total_checks": len(checks),
        "evidence_hash": evidence_hash,
        "policy": {
            "id": current_policy.id,
            "version": current_policy.version,
            "require_guardian_consent": current_policy.require_guardian_consent,
        } if current_policy else None,
    }
    return snapshot, evidence


def _read_attestation(db: Session, evidence_hash: str) -> dict[str, Any]:
    setting = db.query(AppSetting).filter(AppSetting.key == CLASSROOM_ATTESTATION_KEY).first()
    if not setting:
        return {"exists": False, "valid": False, "confirmed_at": "", "note": ""}
    try:
        raw = json.loads(setting.value)
    except (json.JSONDecodeError, TypeError):
        raw = {}
    valid = bool(raw.get("evidence_hash")) and raw.get("evidence_hash") == evidence_hash
    return {
        "exists": True,
        "valid": valid,
        "confirmed_at": str(raw.get("confirmed_at") or ""),
        "note": str(raw.get("note") or ""),
        "message": "课堂数据与确认时一致。" if valid else "课堂数据已变化，请重新核对并确认。",
    }


def classroom_readiness(db: Session) -> dict[str, Any]:
    snapshot, _ = _classroom_snapshot(db)
    attestation = _read_attestation(db, snapshot["evidence_hash"])
    snapshot["attestation"] = attestation
    snapshot["ready_for_attestation"] = snapshot["checks_passed"]
    snapshot["complete"] = snapshot["checks_passed"] and attestation["valid"]
    return snapshot


def _scenario(error_code: str, status: str) -> str:
    if status in {"success", "pending"}:
        return "success"
    return ERROR_SCENARIOS.get(error_code, "other_error")


def _json_details(run: ProviderAcceptanceRun) -> dict[str, Any]:
    try:
        value = json.loads(run.details_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _failure_source(error_code: str, detail: dict[str, Any]) -> str:
    candidate = str(detail.get("failure_source") or "")
    if candidate in LIVE_FAILURE_SOURCES:
        return candidate
    if error_code in ERROR_FAILURE_SOURCES:
        return ERROR_FAILURE_SOURCES[error_code]
    if error_code.startswith("PROVIDER_") or error_code.endswith("_MODEL_REQUIRED"):
        return "configuration"
    return "application"


def _live_failure_diagnostics(error_code: str, detail: dict[str, Any]) -> dict[str, str]:
    source = _failure_source(error_code, detail)
    raw_status = str(detail.get("observed_status_code") or "").strip()
    observed_status = raw_status if re.fullmatch(r"[1-5][0-9]{2}", raw_status) else ""
    if source == "provider_http" and observed_status:
        summary = f"上游服务返回 HTTP {observed_status}。"
    else:
        summary = {
            "provider_http": "上游服务通过 HTTP 响应拒绝了请求。",
            "network": "请求未收到上游服务的 HTTP 响应。",
            "timeout": "请求在等待上游服务响应时超时。",
            "response_validation": "上游响应未通过本地结构校验。",
            "content_moderation": "内容安全策略拦截了本次请求或结果。",
            "configuration": "本地模型服务配置未通过验收前置检查。",
            "internal": "验收执行发生内部错误，未保存原始异常内容。",
            "application": "应用在处理服务商结果时终止了本次验收。",
        }[source]
    return {
        "observed_status_code": observed_status,
        "failure_source": source,
        "diagnostic_summary": summary,
    }


def provider_run_payload(db: Session, run: ProviderAcceptanceRun) -> dict[str, Any]:
    status = run.status
    message = run.message
    error_code = run.error_code
    details = _json_details(run)
    source = "external_evidence" if details.get("source") == "external_evidence" else "live"
    evidence = details.get("evidence") if isinstance(details.get("evidence"), dict) else None
    diagnostics = details.get("diagnostics") if isinstance(details.get("diagnostics"), dict) else {}
    public_evidence = None
    if evidence:
        public_evidence = {
            "original_name": str(evidence.get("original_name") or "evidence"),
            "size_bytes": int(evidence.get("size_bytes") or 0),
            "sha256": str(evidence.get("sha256") or ""),
            "content_type": str(evidence.get("content_type") or "application/octet-stream"),
            "observed_status_code": str(evidence.get("observed_status_code") or ""),
        }
    if source == "external_evidence":
        observed_status_code = str((public_evidence or {}).get("observed_status_code") or "")[:40]
        failure_source = "external_evidence"
        diagnostic_summary = "教师上传的脱敏平台证据。"
    elif diagnostics or status == "failed":
        normalized = _live_failure_diagnostics(error_code, diagnostics)
        observed_status_code = normalized["observed_status_code"]
        failure_source = normalized["failure_source"]
        diagnostic_summary = normalized["diagnostic_summary"]
    else:
        observed_status_code = ""
        failure_source = ""
        diagnostic_summary = ""
    video_task = None
    task_id = details.get("video_task_id")
    if run.capability == "video" and isinstance(task_id, int):
        task = db.get(VideoTask, task_id)
        if task:
            local_ready = bool(task.file_path and Path(task.file_path).is_file())
            if task.status == "success" and local_ready:
                status = "success"
                message = "视频生成和本地保存已完成。"
                error_code = ""
            elif task.status in {"failed", "timed_out", "download_failed", "expired", "canceled"}:
                status = "failed"
                message = task.error_message or "视频验收任务未完成。"
                error_code = f"VIDEO_{task.status.upper()}"
            else:
                status = "pending"
                message = "视频任务已提交，请在 AI 服务的视频任务区刷新状态。"
            video_task = {
                "id": task.id,
                "status": task.status,
                "retry_count": task.retry_count,
                "local_file_ready": local_ready,
            }
    return {
        "id": run.id,
        "provider_id": run.provider_id,
        "provider_name": run.provider_name,
        "provider_type": run.provider_type,
        "capability": run.capability,
        "model": run.model,
        "status": status,
        "scenario": _scenario(error_code, status),
        "error_code": error_code,
        "message": message,
        "latency_ms": run.latency_ms,
        "source": source,
        "observed_status_code": observed_status_code,
        "failure_source": failure_source,
        "diagnostic_summary": diagnostic_summary,
        "has_evidence": bool(public_evidence),
        "evidence": public_evidence,
        "details": {
            key: value
            for key, value in details.items()
            if key in PUBLIC_RUN_DETAIL_KEYS
        },
        "video_task": video_task,
        "created_at": _iso(run.created_at),
    }


def provider_readiness(db: Session) -> dict[str, Any]:
    history = [
        provider_run_payload(db, item)
        for item in db.query(ProviderAcceptanceRun).order_by(
            ProviderAcceptanceRun.created_at.desc(), ProviderAcceptanceRun.id.desc(),
        ).limit(100).all()
    ]
    current = provider_status_payload(db)
    checks = []
    for capability in ("text", "image", "video"):
        try:
            candidates = provider_candidates(db, capability)
        except HTTPException:
            continue
        provider = candidates[0]
        targets = []
        for priority, candidate in enumerate(candidates):
            candidate_model = provider_model(candidate, capability)
            candidate_latest = next((item for item in history if (
                item["capability"] == capability
                and item["model"] == candidate_model
                and item["source"] == "live"
                and (
                    item["provider_id"] == candidate.id
                    or (
                        item["provider_id"] is None
                        and item["provider_type"] == candidate.provider_type
                    )
                )
            )), None)
            targets.append({
                "provider_id": candidate.id,
                "provider_name": candidate.name,
                "provider_type": candidate.provider_type,
                "model": candidate_model,
                "route_priority": priority,
                "passed": bool(candidate_latest and candidate_latest["status"] == "success"),
                "latest": candidate_latest,
            })
        primary = targets[0]
        checks.append({
            "capability": capability,
            "model": primary["model"],
            "passed": primary["passed"],
            "latest": primary["latest"],
            "provider_id": provider.id,
            "provider_name": provider.name,
            "provider_type": provider.provider_type,
            "fallback_provider_count": max(0, len(candidates) - 1),
            "fallback_providers": targets[1:],
            "targets": targets,
        })
    scope = _read_provider_scope(db)
    failure_evidence_policy = _read_failure_evidence_policy(db)
    failure_evidence_required = bool(failure_evidence_policy["required"])
    scope_map = {
        item["provider_type"]: set(item["capabilities"])
        for item in scope["providers"]
    }
    matrix = []
    for preset in list_provider_presets():
        selected_capabilities = scope_map.get(preset["provider_type"], set())
        capability_checks = []
        for capability in preset["capabilities"]:
            latest = next((item for item in history if (
                item["provider_type"] == preset["provider_type"]
                and item["capability"] == capability
                and item["source"] == "live"
            )), None)
            capability_checks.append({
                "capability": capability,
                "in_scope": capability in selected_capabilities,
                "passed": bool(latest and latest["status"] == "success"),
                "latest": latest,
            })
        scenarios = sorted({
            item["scenario"]
            for item in history
            if item["provider_type"] == preset["provider_type"] and item["status"] == "failed"
        })
        in_scope = bool(selected_capabilities)
        scoped_capability_checks = [item for item in capability_checks if item["in_scope"]]
        connectivity_complete = in_scope and all(item["passed"] for item in scoped_capability_checks)
        provider_failure_checks = [
            {"scenario": scenario, "passed": scenario in scenarios}
            for scenario in FAILURE_SCENARIOS
        ]
        failure_complete = in_scope and all(item["passed"] for item in provider_failure_checks)
        failure_gate_complete = failure_complete or (in_scope and not failure_evidence_required)
        matrix.append({
            "provider_type": preset["provider_type"],
            "name": preset["name"],
            "in_scope": in_scope,
            "capabilities": capability_checks,
            "core_connectivity_complete": connectivity_complete,
            "observed_failure_scenarios": scenarios,
            "failure_scenario_checks": provider_failure_checks,
            "failure_scenarios_complete": failure_complete,
            "failure_evidence_waived": in_scope and not failure_evidence_required,
            "failure_evidence_gate_complete": failure_gate_complete,
            "complete": connectivity_complete and failure_gate_complete,
        })
    scoped_matrix = [item for item in matrix if item["in_scope"]]
    connectivity_complete = (
        scope["requirements_met"]
        and bool(scoped_matrix)
        and all(item["core_connectivity_complete"] for item in scoped_matrix)
    )
    failure_scenario_checks = [
        {
            "scenario": scenario,
            "passed": scope["requirements_met"] and bool(scoped_matrix) and all(
                scenario in item["observed_failure_scenarios"] for item in scoped_matrix
            ),
            "missing_provider_types": [
                item["provider_type"]
                for item in scoped_matrix
                if scenario not in item["observed_failure_scenarios"]
            ],
        }
        for scenario in FAILURE_SCENARIOS
    ]
    failure_scenarios_complete = all(item["passed"] for item in failure_scenario_checks)
    failure_evidence_gate_complete = failure_scenarios_complete or not failure_evidence_required
    return {
        "current": current,
        "checks": checks,
        "core_connectivity_complete": bool(checks) and all(item["passed"] for item in checks),
        "observed_scenarios": sorted({item["scenario"] for item in history}),
        "scope": scope,
        "provider_matrix": matrix,
        "provider_matrix_complete": connectivity_complete,
        "failure_scenario_checks": failure_scenario_checks,
        "failure_scenarios_complete": failure_scenarios_complete,
        "failure_evidence_policy": failure_evidence_policy,
        "failure_evidence_gate_complete": failure_evidence_gate_complete,
        "complete": connectivity_complete and failure_evidence_gate_complete,
        "history": history,
        "cost_confirmation": PROVIDER_COST_CONFIRMATION,
    }


@router.get("")
def get_readiness(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    return {
        "classroom": classroom_readiness(db),
        "providers": provider_readiness(db),
        "generated_at": _iso(now()),
    }


@router.put("/provider-scope")
def update_provider_scope(
    payload: ProviderAcceptanceScopeRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    preset_map = {str(item["provider_type"]): item for item in list_provider_presets()}
    seen = set()
    providers = []
    for requested in payload.providers:
        provider_type = requested.provider_type.strip()
        preset = preset_map.get(provider_type)
        if not preset:
            raise HTTPException(
                status_code=400,
                detail={"code": "P0_SCOPE_PROVIDER_INVALID", "message": "验收范围包含不受支持的服务商。"},
            )
        if provider_type in seen:
            raise HTTPException(
                status_code=400,
                detail={"code": "P0_SCOPE_PROVIDER_DUPLICATE", "message": "同一服务商不能在验收范围中重复出现。"},
            )
        capabilities = list(dict.fromkeys(requested.capabilities))
        unsupported = [item for item in capabilities if item not in preset["capabilities"]]
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail={"code": "P0_SCOPE_CAPABILITY_INVALID", "message": "验收范围包含该服务商未实现的能力。"},
            )
        seen.add(provider_type)
        providers.append({"provider_type": provider_type, "capabilities": capabilities})
    covered = {capability for item in providers for capability in item["capabilities"]}
    missing = [item for item in REQUIRED_SCOPE_CAPABILITIES if item not in covered]
    if missing:
        labels = {"text": "文字", "image": "图片"}
        raise HTTPException(
            status_code=400,
            detail={
                "code": "P0_SCOPE_REQUIRED_CAPABILITY_MISSING",
                "message": f"首版验收范围必须包含{'、'.join(labels[item] for item in missing)}能力。",
            },
        )
    saved = {
        "version": 1,
        "providers": providers,
        "updated_at": _iso(now()),
        "teacher_session_id": teacher.id,
    }
    value = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
    setting = db.query(AppSetting).filter(AppSetting.key == PROVIDER_SCOPE_KEY).first()
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=PROVIDER_SCOPE_KEY, value=value))
    db.commit()
    record_teacher_audit(
        db,
        teacher,
        "p0.provider.scope_updated",
        target_type="p0_acceptance",
        target_id="provider-scope",
        summary="更新 P0 服务商验收范围",
        details={"providers": providers, "covered_capabilities": sorted(covered)},
    )
    return {"providers": provider_readiness(db)}


@router.put("/failure-evidence-policy")
def update_failure_evidence_policy(
    payload: ProviderFailureEvidencePolicyRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reason = payload.reason.strip()
    if not payload.required:
        if payload.confirmation != FAILURE_EVIDENCE_WAIVER_CONFIRMATION:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "P0_FAILURE_EVIDENCE_CONFIRMATION_REQUIRED",
                    "message": "跳过失败证据链需要教师明确确认。",
                },
            )
        if not reason:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "P0_FAILURE_EVIDENCE_REASON_REQUIRED",
                    "message": "请记录本期跳过失败证据链的原因。",
                },
            )
        if SENSITIVE_EVIDENCE.search(reason):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "P0_FAILURE_EVIDENCE_REASON_SECRET_DETECTED",
                    "message": "跳过原因疑似包含 API Key 或访问令牌，请删除敏感信息。",
                },
            )
    changed_at = _iso(now())
    saved = {
        "version": 1,
        "required": payload.required,
        "reason": reason,
        "confirmed_at": changed_at,
        "teacher_session_id": teacher.id,
    }
    value = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
    setting = db.query(AppSetting).filter(AppSetting.key == FAILURE_EVIDENCE_POLICY_KEY).first()
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=FAILURE_EVIDENCE_POLICY_KEY, value=value))
    db.commit()
    record_teacher_audit(
        db,
        teacher,
        "p0.provider.failure_evidence_policy_updated",
        target_type="p0_acceptance",
        target_id="failure-evidence-policy",
        summary="恢复 P0 失败证据链要求" if payload.required else "跳过本期 P0 失败证据链收集",
        details={
            "required": payload.required,
            "waived": not payload.required,
            "reason": reason,
            "confirmed_at": changed_at,
        },
    )
    return {"providers": provider_readiness(db)}


@router.get("/export")
def export_readiness_report(
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    exported_at = _iso(now())
    report = {
        "export_version": 1,
        "exported_at": exported_at,
        "classroom": classroom_readiness(db),
        "providers": provider_readiness(db),
        "privacy": {
            "contains_api_keys": False,
            "contains_prompts_or_generated_content": False,
            "evidence_files_included": False,
        },
    }
    record_teacher_audit(
        db,
        teacher,
        "p0.report.exported",
        target_type="p0_acceptance",
        summary="导出 P0 脱敏验收报告",
        details={
            "provider_runs": len(report["providers"]["history"]),
            "classroom_complete": report["classroom"]["complete"],
        },
    )
    filename = f"coderai-p0-acceptance-{now():%Y%m%d-%H%M%S}.json"
    return Response(
        content=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/provider-evidence")
async def upload_provider_evidence(
    provider_type: str = Form(...),
    capability: str = Form(...),
    scenario: str = Form(...),
    model: str = Form(""),
    observed_status_code: str = Form(""),
    note: str = Form(""),
    file: UploadFile = File(...),
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    preset = get_provider_preset(provider_type)
    if preset["provider_type"] != provider_type:
        raise HTTPException(status_code=400, detail={"code": "P0_PROVIDER_TYPE_INVALID", "message": "验收证据的服务商不受支持。"})
    if capability not in preset["capabilities"]:
        raise HTTPException(status_code=400, detail={"code": "P0_PROVIDER_CAPABILITY_INVALID", "message": "该服务商不支持所选能力。"})
    if scenario not in SCENARIO_ERROR_CODES:
        raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_SCENARIO_INVALID", "message": "验收失败场景不受支持。"})
    clean_note = note.strip()
    if len(clean_note) > 300:
        raise HTTPException(status_code=400, detail={"code": "P0_EVIDENCE_NOTE_TOO_LONG", "message": "证据说明不能超过 300 个字符。"})
    clean_status = observed_status_code.strip()[:40]
    original_name = _safe_evidence_name(file.filename or "evidence")
    extension = Path(original_name).suffix.lower()
    content = await file.read(MAX_EVIDENCE_BYTES + 1)
    await file.close()
    digest = _validate_evidence(content, extension)

    folder = EVIDENCE_ROOT / now().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{secrets.token_hex(16)}{extension}"
    target.write_bytes(content)
    relative = target.relative_to(EVIDENCE_ROOT).as_posix()
    resolved_model = model.strip()[:120] or str(preset.get(f"{capability}_model") or "")[:120]
    provider = db.query(AIProvider).filter(
        AIProvider.enabled.is_(True),
        AIProvider.provider_type == provider_type,
    ).order_by(AIProvider.id.desc()).first()
    details = {
        "source": "external_evidence",
        "evidence": {
            "relative_path": relative,
            "original_name": original_name,
            "size_bytes": len(content),
            "sha256": digest,
            "content_type": file.content_type or "application/octet-stream",
            "observed_status_code": clean_status,
        },
    }
    run = ProviderAcceptanceRun(
        provider_id=provider.id if provider else None,
        provider_name=str(preset["name"]),
        provider_type=provider_type,
        capability=capability,
        model=resolved_model,
        status="failed",
        scenario=scenario,
        error_code=SCENARIO_ERROR_CODES[scenario],
        message=clean_note or "教师上传了已脱敏的真实平台失败证据。",
        latency_ms=0,
        details_json=json.dumps(details, ensure_ascii=False, separators=(",", ":")),
    )
    try:
        db.add(run)
        db.commit()
        db.refresh(run)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    public = provider_run_payload(db, run)
    record_teacher_audit(
        db,
        teacher,
        "p0.provider.evidence_added",
        target_type="provider_acceptance_run",
        target_id=run.id,
        summary=f"录入 P0 真实失败证据：{provider_type} / {scenario}",
        details={
            "provider_type": provider_type,
            "capability": capability,
            "scenario": scenario,
            "sha256": digest,
            "size_bytes": len(content),
        },
    )
    return {"run": public, "providers": provider_readiness(db)}


@router.get("/provider-evidence/{run_id}")
def download_provider_evidence(
    run_id: int,
    _: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = db.get(ProviderAcceptanceRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail={"code": "P0_EVIDENCE_NOT_FOUND", "message": "验收证据记录不存在。"})
    details = _json_details(run)
    evidence = details.get("evidence") if isinstance(details.get("evidence"), dict) else None
    if not evidence:
        raise HTTPException(status_code=404, detail={"code": "P0_EVIDENCE_NOT_FOUND", "message": "该验收记录没有证据文件。"})
    path = _resolve_evidence(str(evidence.get("relative_path") or ""))
    if hashlib.sha256(path.read_bytes()).hexdigest() != str(evidence.get("sha256") or ""):
        raise HTTPException(status_code=409, detail={"code": "P0_EVIDENCE_INTEGRITY_FAILED", "message": "验收证据哈希不一致，请重新上传。"})
    return FileResponse(
        path,
        media_type=str(evidence.get("content_type") or "application/octet-stream"),
        filename=_safe_evidence_name(str(evidence.get("original_name") or path.name)),
    )


@router.post("/provider-tests")
async def run_provider_acceptance_test(
    payload: ProviderAcceptanceTestRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.confirmation != PROVIDER_COST_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail={"code": "P0_COST_CONFIRMATION_REQUIRED", "message": "真实验收可能产生 API 费用，请在教师端明确确认后再运行。"},
        )

    provider_error: HTTPException | None = None
    try:
        provider = active_provider(db, payload.capability, payload.provider_id)
    except HTTPException as exc:
        provider = None
        provider_error = exc
    model = provider_model(provider, payload.capability) if provider else ""
    started = perf_counter()
    status = "failed"
    error_code = ""
    message = ""
    details: dict[str, Any] = {
        "targeted_provider_id": provider.id,
        "fallback_disabled": True,
    } if provider else {}
    try:
        if provider_error:
            raise provider_error
        if not provider:
            raise HTTPException(
                status_code=400,
                detail={"code": "PROVIDER_REQUIRED", "message": "没有可用于本次验收的模型服务。"},
            )
        prompt = SAFE_PROMPTS[payload.capability]
        run_moderation(db, prompt, content_stage="p0_acceptance_input")
        if payload.capability == "text":
            output = await generate_text(db, prompt, "story", "primary_lower", provider_id=provider.id)
            if not str(output).strip():
                raise HTTPException(status_code=502, detail={"code": "P0_TEXT_RESULT_EMPTY", "message": "文字模型返回了空结果。"})
            status = "success"
            message = "文字真实生成、输出解析和内容审核通过。"
            details["output_characters"] = len(str(output))
        elif payload.capability == "image":
            result = await generate_image(db, prompt, "明亮卡通", "1024x1024", provider_id=provider.id)
            if not result.get("file_path") and not result.get("url"):
                raise HTTPException(status_code=502, detail={"code": "P0_IMAGE_RESULT_MISSING", "message": "图片服务未返回可用结果。"})
            moderation_status = str(result.get("moderation_status") or "pending")
            if moderation_status == "rejected":
                raise HTTPException(status_code=400, detail={"code": "IMAGE_OUTPUT_REJECTED", "message": "安全测试图片被输出审核拒绝。"})
            status = "success"
            message = "图片真实生成和结果解析通过。" if moderation_status == "approved" else "图片真实生成通过，结果已按策略进入教师复核。"
            details.update({
                "moderation_status": moderation_status,
                "result_kind": "local_file" if result.get("file_path") else "remote_url",
            })
        else:
            task, _ = await generate_video_task(
                db,
                prompt,
                None,
                5,
                student=None,
                save_as_project=False,
                provider_id=provider.id,
            )
            status = "pending"
            message = "视频真实任务已提交，请刷新任务直到本地文件可用。"
            details.update({"video_task_id": task.id, "provider_task_status": task.status})
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error_code = str(detail.get("code") or "P0_PROVIDER_TEST_FAILED")[:80]
        message = str(detail.get("message") or "服务商真实验收失败。")[:300]
        details["diagnostics"] = _live_failure_diagnostics(error_code, detail)
    except Exception:
        error_code = "P0_PROVIDER_TEST_INTERNAL_ERROR"
        message = "服务商验收执行出现内部错误，请查看脱敏运行日志。"
        details["diagnostics"] = _live_failure_diagnostics(error_code, {})

    run = ProviderAcceptanceRun(
        provider_id=provider.id if provider else None,
        provider_name=provider.name if provider else "未配置服务商",
        provider_type=provider.provider_type if provider else "",
        capability=payload.capability,
        model=model,
        status=status,
        scenario=_scenario(error_code, status),
        error_code=error_code,
        message=message,
        latency_ms=round((perf_counter() - started) * 1000),
        details_json=json.dumps(details, ensure_ascii=False, separators=(",", ":")),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    public = provider_run_payload(db, run)
    record_teacher_audit(
        db,
        teacher,
        "p0.provider.acceptance_tested",
        target_type="ai_provider",
        target_id=provider.id if provider else None,
        summary=f"P0 服务商验收：{payload.capability} / {status}",
        details={
            "provider_type": run.provider_type,
            "capability": run.capability,
            "model": run.model,
            "status": public["status"],
            "scenario": public["scenario"],
            "error_code": public["error_code"],
        },
    )
    return {"run": public, "providers": provider_readiness(db)}


@router.post("/classroom-attestation")
def attest_classroom_readiness(
    payload: ClassroomAcceptanceRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.confirmation != CLASSROOM_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail={"code": "P0_CLASSROOM_CONFIRMATION_REQUIRED", "message": "请由负责教师明确确认数据来自真实课堂。"},
        )
    snapshot, _ = _classroom_snapshot(db)
    if not snapshot["checks_passed"]:
        raise HTTPException(
            status_code=409,
            detail={"code": "P0_CLASSROOM_NOT_READY", "message": "课堂证据尚未达到 P0 标准，请先完成未通过项目。"},
        )
    confirmed_at = _iso(now())
    saved = {
        "evidence_hash": snapshot["evidence_hash"],
        "confirmed_at": confirmed_at,
        "teacher_session_id": teacher.id,
        "note": payload.note.strip(),
    }
    setting = db.query(AppSetting).filter(AppSetting.key == CLASSROOM_ATTESTATION_KEY).first()
    value = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=CLASSROOM_ATTESTATION_KEY, value=value))
    db.commit()
    record_teacher_audit(
        db,
        teacher,
        "p0.classroom.attested",
        target_type="classroom_acceptance",
        target_id=snapshot["evidence_hash"][:16],
        summary="确认 P0 数据来自真实课堂",
        details={"evidence_hash": snapshot["evidence_hash"], "counts": snapshot["counts"]},
    )
    return {"classroom": classroom_readiness(db)}
