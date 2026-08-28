from __future__ import annotations

import json
import mimetypes
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.auth import require_student_account
from backend.app.db import DATA_DIR, SessionLocal, get_db
from backend.app.models import (
    AIGenerationJob,
    AIProvider,
    AgentArtifact,
    AgentConversation,
    AgentMessage,
    ModerationLog,
    Project,
    Task,
    User,
    VideoTask,
    Workflow,
    now,
)
from backend.app.privacy import ensure_student_ai_consent
from backend.app.provider_presets import provider_capabilities
from backend.app.schemas import (
    AIGenerationJobRequest,
    AgentArtifactSaveRequest,
    AgentConversationCreateRequest,
    AgentConversationUpdateRequest,
    AgentMessageCreateRequest,
    AgentToolRunRequest,
)
from backend.app.services import (
    generate_image,
    generate_text,
    generate_video_task,
    json_dumps,
    project_title_from_prompt,
    require_student_image_teacher_review,
    run_moderation,
    save_project,
)
from backend.app.storage import (
    delete_object,
    is_object_reference,
    object_exists,
    put_bytes,
    read_bytes,
    reference_in_category,
    storage_response,
)
from backend.app.task_queue import enqueue_ai_generation
from backend.app.tenancy import organization_context


router = APIRouter(prefix="/api", tags=["student-ai"])

AI_JOB_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timed_out", "canceled"})
AGENT_RECENT_MESSAGE_LIMIT = 20
AGENT_ARTIFACT_RETENTION = timedelta(days=7)
TOOL_PATTERN = re.compile(r"\[\[TOOL:(image|video|workflow)\|(.+?)\]\]", re.IGNORECASE | re.DOTALL)


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _http_detail(exc: HTTPException) -> tuple[str, str]:
    if isinstance(exc.detail, dict):
        return str(exc.detail.get("code") or "AI_REQUEST_FAILED"), str(exc.detail.get("message") or exc.detail)
    return "AI_REQUEST_FAILED", str(exc.detail)


def _provider_is_student_selectable(provider: AIProvider | None) -> bool:
    return bool(
        provider
        and provider.enabled
        and provider.student_selectable
        and provider.text_model.strip()
        and "text" in provider_capabilities(provider.provider_type)
    )


def _require_selectable_provider(db: Session, provider_id: int | None) -> AIProvider | None:
    if provider_id is None:
        return None
    provider = db.query(AIProvider).filter(AIProvider.id == provider_id).first()
    if not _provider_is_student_selectable(provider):
        raise HTTPException(
            status_code=403,
            detail={"code": "AGENT_MODEL_FORBIDDEN", "message": "管理员未向学生开放该文字模型。"},
        )
    return provider


def _ensure_student_tool(db: Session, student: User, tool: str) -> None:
    ensure_student_ai_consent(db, student)
    tasks = db.query(Task).filter((Task.classroom_id.is_(None)) | (Task.classroom_id == student.classroom_id)).all()
    if not tasks:
        return
    allowed = {
        name.strip()
        for task in tasks
        for name in str(task.tool_scope or "").split(",")
        if name.strip()
    }
    if tool not in allowed:
        labels = {"text": "文字生成", "image": "图片生成", "video": "视频生成", "workflow": "工作流"}
        raise HTTPException(
            status_code=403,
            detail={"code": "TOOL_NOT_ALLOWED", "message": f"教师当前没有为你的课堂开放{labels.get(tool, tool)}。"},
        )


def _owned_ai_input(student: User, reference: str | None) -> str | None:
    if not reference:
        return None
    if reference.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=403,
            detail={"code": "AI_INPUT_FORBIDDEN", "message": "参考图片必须先上传到当前账号的受管空间。"},
        )
    category = f"ai_inputs/student-{student.id}"
    if is_object_reference(reference):
        if not reference_in_category(reference, category) or not object_exists(reference):
            raise HTTPException(status_code=403, detail={"code": "AI_INPUT_FORBIDDEN", "message": "参考图片不存在或不属于当前账号。"})
        return reference
    owner_dir = (DATA_DIR / category).resolve()
    candidate = Path(reference).resolve()
    if not candidate.is_file() or owner_dir not in candidate.parents:
        raise HTTPException(status_code=403, detail={"code": "AI_INPUT_FORBIDDEN", "message": "参考图片不存在或不属于当前账号。"})
    return str(candidate)


def _job_payload(job: AIGenerationJob, db: Session | None = None) -> dict[str, Any]:
    result = _json_object(job.result_json)
    result.pop("file_path", None)
    result.pop("provider_url", None)
    moderation_status = str(result.get("moderation_status") or "approved")
    moderation_reason = str(result.get("moderation_reason") or "")
    if job.capability == "image":
        if job.project:
            moderation_status = job.project.moderation_status
            moderation_reason = job.project.moderation_reason
        elif db and result.get("moderation_log_id"):
            log = db.get(ModerationLog, int(result["moderation_log_id"]))
            if log:
                moderation_status = log.status
                moderation_reason = log.review_note or log.reason
        result["moderation_status"] = moderation_status
        result["moderation_reason"] = moderation_reason
    project_file_available = bool(
        job.project
        and job.project.file_path
        and moderation_status == "approved"
        and object_exists(job.project.file_path)
    )
    result["file_available"] = project_file_available or bool(
        job.conversation_id
        and any(
            item.status == "available" and item.file_path and object_exists(item.file_path)
            for item in getattr(job, "artifacts", [])
        )
    )
    return {
        "id": job.id,
        "client_request_id": job.client_request_id,
        "capability": job.capability,
        "operation": job.operation,
        "provider_id": job.provider_id,
        "model": job.model,
        "status": job.status,
        "result": result,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "retry_count": job.retry_count,
        "cancel_requested": job.cancel_requested,
        "project_id": job.project_id,
        "conversation_id": job.conversation_id,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
    }


def _conversation_payload(conversation: AgentConversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "selected_provider_id": conversation.selected_provider_id,
        "status": conversation.status,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else "",
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else "",
    }


def _message_payload(message: AgentMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "provider_id": message.provider_id,
        "model": message.model,
        "sequence": message.sequence,
        "tool_suggestion": _json_object(message.tool_suggestion_json),
        "created_at": message.created_at.isoformat() if message.created_at else "",
    }


def _artifact_payload(artifact: AgentArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "conversation_id": artifact.conversation_id,
        "message_id": artifact.message_id,
        "generation_job_id": artifact.generation_job_id,
        "saved_project_id": artifact.saved_project_id,
        "artifact_type": artifact.artifact_type,
        "title": artifact.title,
        "original_file_name": artifact.original_file_name,
        "mime_type": artifact.mime_type,
        "file_size": artifact.file_size,
        "status": artifact.status,
        "file_available": bool(artifact.file_path and object_exists(artifact.file_path)),
        "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else "",
    }


def _expire_artifacts(db: Session, student_id: int | None = None) -> None:
    query = db.query(AgentArtifact).filter(
        AgentArtifact.saved_project_id.is_(None),
        AgentArtifact.status.in_(("available", "pending_review")),
        AgentArtifact.expires_at <= now(),
    )
    if student_id is not None:
        query = query.filter(AgentArtifact.user_id == student_id)
    rows = query.all()
    for artifact in rows:
        reference = artifact.file_path
        artifact.file_path = ""
        artifact.status = "expired"
        if reference:
            try:
                delete_object(reference)
            except (OSError, ValueError):
                pass
    if rows:
        db.commit()


def expire_agent_artifacts(db: Session) -> int:
    before = db.query(AgentArtifact.id).filter(
        AgentArtifact.saved_project_id.is_(None),
        AgentArtifact.status.in_(("available", "pending_review")),
        AgentArtifact.expires_at <= now(),
    ).count()
    _expire_artifacts(db)
    return before


async def _managed_image_reference(result: dict[str, Any], student_id: int, job_id: int) -> tuple[str, int, str]:
    existing = str(result.get("file_path") or "")
    if existing and object_exists(existing):
        content = read_bytes(existing, maximum_bytes=20 * 1024 * 1024)
        return existing, len(content), mimetypes.guess_type(Path(existing).name)[0] or "image/png"
    url = str(result.get("url") or "")
    if not url.startswith("https://"):
        raise HTTPException(status_code=502, detail={"code": "AI_RESPONSE_INVALID", "message": "图片服务没有返回可保存的图片文件。"})
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail={"code": "AI_IMAGE_DOWNLOAD_FAILED", "message": "图片已生成，但保存到受管空间失败。"}) from exc
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=502, detail={"code": "AI_IMAGE_DOWNLOAD_FAILED", "message": "图片文件为空或超过 20 MB。"})
    media_type = response.headers.get("content-type", "image/png").split(";", 1)[0]
    extension = mimetypes.guess_extension(media_type) or ".png"
    reference = put_bytes(
        f"agent/student-{student_id}" if job_id else f"outputs/student-{student_id}",
        f"generation-{job_id}{extension}",
        content,
        content_type=media_type,
    )
    return reference, len(content), media_type


def _extract_tool_suggestion(content: str) -> tuple[str, dict[str, str]]:
    match = TOOL_PATTERN.search(content)
    if not match:
        return content.strip(), {}
    clean = TOOL_PATTERN.sub("", content).strip()
    return clean, {"capability": match.group(1).lower(), "prompt": " ".join(match.group(2).split())[:20_000]}


def _agent_context(db: Session, conversation: AgentConversation) -> str:
    messages = (
        db.query(AgentMessage)
        .filter(AgentMessage.conversation_id == conversation.id, AgentMessage.status == "completed")
        .order_by(AgentMessage.sequence.desc())
        .limit(AGENT_RECENT_MESSAGE_LIMIT)
        .all()
    )
    messages.reverse()
    history = "\n".join(f"{('学生' if item.role == 'user' else '助手')}：{item.content}" for item in messages)
    summary = f"较早对话摘要：\n{conversation.memory_summary}\n\n" if conversation.memory_summary else ""
    return (
        "你是 CoderAI 学堂的学习助手。请延续当前会话上下文，启发学生思考，不要替学生完成违规任务。"
        "如果确实需要调用图片、视频或工作流工具，只能提出一项建议，并在回答末尾加入"
        " [[TOOL:image|具体提示词]]、[[TOOL:video|具体提示词]] 或 [[TOOL:workflow|具体提示词]]；"
        "工具不会自动执行，必须等待学生确认。\n\n"
        f"{summary}最近对话：\n{history}"
    )


def _update_rolling_summary(db: Session, conversation: AgentConversation) -> None:
    rows = (
        db.query(AgentMessage)
        .filter(AgentMessage.conversation_id == conversation.id, AgentMessage.status == "completed")
        .order_by(AgentMessage.sequence.asc())
        .all()
    )
    if len(rows) <= AGENT_RECENT_MESSAGE_LIMIT:
        return
    older = rows[:-AGENT_RECENT_MESSAGE_LIMIT]
    lines = [f"{('学生' if item.role == 'user' else '助手')}：{' '.join(item.content.split())[:240]}" for item in older[-20:]]
    conversation.memory_summary = "\n".join(lines)[-4_000:]


def _artifact_for_image(
    db: Session,
    job: AIGenerationJob,
    reference: str,
    file_size: int,
    media_type: str,
    moderation_status: str,
    title: str,
    moderation_log_id: int | None = None,
) -> AgentArtifact:
    artifact = AgentArtifact(
        conversation_id=job.conversation_id,
        message_id=job.assistant_message_id,
        generation_job_id=job.id,
        user_id=job.user_id,
        artifact_type="image",
        title=title[:160],
        file_path=reference,
        original_file_name=f"agent-image-{job.id}{mimetypes.guess_extension(media_type) or '.png'}",
        mime_type=media_type,
        file_size=file_size,
        status="available" if moderation_status == "approved" else "pending_review",
        expires_at=now() + AGENT_ARTIFACT_RETENTION,
    )
    db.add(artifact)
    db.flush()
    if moderation_log_id:
        log = db.get(ModerationLog, moderation_log_id)
        if log:
            log.resource_path = reference
    return artifact


async def execute_ai_generation_job(
    job_id: int,
    organization_id: int,
    organization_code: str,
    bind: Any | None = None,
) -> None:
    with organization_context(organization_id, organization_code):
        session_factory = SessionLocal if bind is None else sessionmaker(autocommit=False, autoflush=False, bind=bind)
        db = session_factory()
        try:
            job = db.get(AIGenerationJob, job_id)
            if not job or job.status in AI_JOB_TERMINAL_STATUSES:
                return
            if job.cancel_requested:
                job.status = "canceled"
                job.completed_at = now()
                db.commit()
                return
            student = db.get(User, job.user_id)
            if not student or student.role != "student" or not student.active or student.archived_at:
                raise HTTPException(status_code=403, detail={"code": "STUDENT_ACCOUNT_UNAVAILABLE", "message": "学生账号不可用。"})
            request = _json_object(job.request_json)
            job.status = "running"
            job.started_at = now()
            job.error_code = ""
            job.error_message = ""
            db.commit()

            if job.operation == "agent_message":
                conversation = db.get(AgentConversation, job.conversation_id)
                if not conversation or conversation.user_id != student.id:
                    raise HTTPException(status_code=404, detail={"code": "AGENT_CONVERSATION_NOT_FOUND", "message": "Agent 会话不存在。"})
                provider = _require_selectable_provider(db, conversation.selected_provider_id)
                prompt = _agent_context(db, conversation)
                generated = await generate_text(
                    db,
                    prompt,
                    "general",
                    student.age_level,
                    user_id=student.id,
                    provider_id=provider.id if provider else None,
                    output_content_stage="agent_output",
                )
                content, suggestion = _extract_tool_suggestion(generated)
                assistant = db.get(AgentMessage, job.assistant_message_id)
                if not assistant:
                    raise HTTPException(status_code=409, detail={"code": "AGENT_MESSAGE_MISSING", "message": "Agent 回复记录已丢失。"})
                assistant.content = content
                assistant.status = "completed"
                assistant.provider_id = provider.id if provider else job.provider_id
                assistant.model = provider.text_model if provider else job.model
                assistant.tool_suggestion_json = json_dumps(suggestion)
                conversation.updated_at = now()
                _update_rolling_summary(db, conversation)
                job.result_json = json_dumps({"message_id": assistant.id})
            elif job.operation == "agent_workflow":
                from backend.app.main import can_access_workflow, execute_saved_workflow

                workflow_id = int(request.get("workflow_id") or 0)
                workflow = db.get(Workflow, workflow_id) if workflow_id else None
                identity = {"role": "student", "student": student, "teacher": None}
                if not workflow or not can_access_workflow(db, workflow, identity):
                    raise HTTPException(
                        status_code=404,
                        detail={"code": "WORKFLOW_NOT_FOUND", "message": "工作流不存在或当前账号不可访问。"},
                    )
                output, last_text, _, output_title = await execute_saved_workflow(
                    db,
                    workflow,
                    str(request.get("prompt") or ""),
                    student.age_level,
                    user_id=student.id,
                )
                artifact_ids: list[int] = []
                terminal_outputs = output.get("terminal_outputs")
                for terminal in terminal_outputs if isinstance(terminal_outputs, list) else []:
                    image_result = terminal.get("output")
                    if terminal.get("status") != "success" or terminal.get("type") != "image.generate" or not isinstance(image_result, dict):
                        continue
                    image_result = require_student_image_teacher_review(
                        db,
                        str(request.get("prompt") or ""),
                        image_result,
                        student.id,
                    )
                    reference, size, media_type = await _managed_image_reference(image_result, student.id, job.id)
                    artifact = _artifact_for_image(
                        db,
                        job,
                        reference,
                        size,
                        media_type,
                        str(image_result.get("moderation_status") or "pending"),
                        f"{output_title}：{str(terminal.get('label') or '图片结果')}"[:160],
                        int(image_result["moderation_log_id"]) if image_result.get("moderation_log_id") else None,
                    )
                    artifact_ids.append(artifact.id)
                job.result_json = json_dumps({
                    "workflow_id": workflow.id,
                    "execution_status": output.get("execution_status") or "success",
                    "text": last_text,
                    "artifact_ids": artifact_ids,
                })
            elif job.operation == "agent_tool":
                if job.capability == "video":
                    video_task, _ = await generate_video_task(
                        db,
                        str(request.get("prompt") or ""),
                        None,
                        int(request.get("duration_seconds") or 5),
                        student=student,
                        save_as_project=False,
                    )
                    video_task.generation_job_id = job.id
                    db.commit()
                    from backend.app.task_queue import TASK_QUEUE_ENABLED, celery_app

                    if TASK_QUEUE_ENABLED and celery_app is not None:
                        celery_app.send_task(
                            "coderai.poll_video",
                            args=[video_task.id, organization_id, organization_code],
                            countdown=10,
                        )
                    job.result_json = json_dumps({"video_task_id": video_task.id, "message": "视频任务已提交，完成后会显示在当前会话。"})
                    db.commit()
                    return
                else:
                    tool_prompt = str(request.get("prompt") or "")
                    image_result = await generate_image(db, tool_prompt, "明亮课堂插画", "1024x1024", user_id=student.id)
                    image_result = require_student_image_teacher_review(db, tool_prompt, image_result, student.id)
                    reference, size, media_type = await _managed_image_reference(image_result, student.id, job.id)
                    artifact = _artifact_for_image(
                        db,
                        job,
                        reference,
                        size,
                        media_type,
                        str(image_result.get("moderation_status") or "pending"),
                        project_title_from_prompt("Agent 创作：", str(request.get("prompt") or "")),
                        int(image_result["moderation_log_id"]) if image_result.get("moderation_log_id") else None,
                    )
                    job.result_json = json_dumps({"artifact_id": artifact.id})
            elif job.capability == "text":
                generated = await generate_text(
                    db,
                    str(request.get("prompt") or ""),
                    str(request.get("mode") or "general"),
                    student.age_level,
                    user_id=student.id,
                    provider_id=job.provider_id,
                )
                project = None
                if bool(request.get("save_project", True)):
                    project = save_project(
                        db,
                        project_title_from_prompt("文字作品：", str(request.get("prompt") or "")),
                        "text",
                        generated,
                        student=student,
                    )
                    job.project_id = project.id
                job.result_json = json_dumps({"text": generated, "project_id": project.id if project else None})
            elif job.capability == "image":
                image_prompt = str(request.get("prompt") or "")
                image_result = await generate_image(
                    db,
                    image_prompt,
                    str(request.get("style") or "classroom-friendly"),
                    str(request.get("size") or "1024x1024"),
                    str(request.get("source_image_path") or "") or None,
                    user_id=student.id,
                    provider_id=job.provider_id,
                )
                image_result = require_student_image_teacher_review(db, image_prompt, image_result, student.id)
                reference, _, _ = await _managed_image_reference(image_result, student.id, 0)
                project = None
                if bool(request.get("save_project", True)):
                    project = save_project(
                        db,
                        project_title_from_prompt("图片作品：", str(request.get("prompt") or "")),
                        "image",
                        str(request.get("prompt") or ""),
                        reference,
                        student=student,
                        moderation_status=str(image_result.get("moderation_status") or "pending"),
                        moderation_reason=str(image_result.get("moderation_reason") or ""),
                        moderation_log_id=image_result.get("moderation_log_id"),
                    )
                    job.project_id = project.id
                    if image_result.get("moderation_log_id"):
                        log = db.get(ModerationLog, int(image_result["moderation_log_id"]))
                        if log:
                            log.project_id = project.id
                            log.resource_path = reference
                job.result_json = json_dumps({
                    "project_id": project.id if project else None,
                    "file_path": reference,
                    "moderation_status": str(image_result.get("moderation_status") or "pending"),
                    "moderation_reason": str(image_result.get("moderation_reason") or ""),
                    "moderation_log_id": image_result.get("moderation_log_id"),
                })
            else:
                raise HTTPException(status_code=400, detail={"code": "AI_JOB_CAPABILITY_INVALID", "message": "不支持的 AI 任务类型。"})

            cancel_requested = bool(
                db.query(AIGenerationJob.cancel_requested).filter(AIGenerationJob.id == job.id).scalar()
            )
            if cancel_requested:
                job.status = "canceled"
                job.result_json = "{}"
            else:
                job.status = "succeeded"
            job.completed_at = now()
            db.commit()
        except HTTPException as exc:
            db.rollback()
            job = db.get(AIGenerationJob, job_id)
            if job:
                code, message = _http_detail(exc)
                job.status = "timed_out" if code == "AI_TIMEOUT" else "failed"
                job.error_code = code[:80]
                job.error_message = message[:2_000]
                job.completed_at = now()
                if job.assistant_message_id:
                    assistant = db.get(AgentMessage, job.assistant_message_id)
                    if assistant:
                        assistant.status = "failed"
                        assistant.content = message
                db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(AIGenerationJob, job_id)
            if job:
                job.status = "failed"
                job.error_code = "AI_JOB_FAILED"
                job.error_message = str(exc)[:2_000]
                job.completed_at = now()
                if job.assistant_message_id:
                    assistant = db.get(AgentMessage, job.assistant_message_id)
                    if assistant:
                        assistant.status = "failed"
                        assistant.content = "AI 助手暂时无法回复，请稍后重试。"
                db.commit()
        finally:
            db.close()


def _create_job(
    db: Session,
    student: User,
    *,
    client_request_id: str,
    capability: str,
    operation: str,
    request: dict[str, Any],
    provider_id: int | None = None,
    conversation_id: int | None = None,
    assistant_message_id: int | None = None,
) -> tuple[AIGenerationJob, bool]:
    request_json = json_dumps(request)
    existing = db.query(AIGenerationJob).filter(
        AIGenerationJob.user_id == student.id,
        AIGenerationJob.client_request_id == client_request_id,
    ).first()
    if existing:
        if existing.capability != capability or existing.operation != operation or existing.request_json != request_json:
            raise HTTPException(status_code=409, detail={"code": "AI_JOB_IDEMPOTENCY_CONFLICT", "message": "请求编号已用于另一项任务，请重新提交。"})
        return existing, False
    provider = None
    if provider_id:
        provider = db.query(AIProvider).filter(AIProvider.id == provider_id).first()
        if not provider or not provider.enabled:
            raise HTTPException(status_code=404, detail={"code": "AI_PROVIDER_NOT_FOUND", "message": "所选模型服务不可用。"})
    job = AIGenerationJob(
        user_id=student.id,
        classroom_id=student.classroom_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        provider_id=provider_id,
        client_request_id=client_request_id,
        capability=capability,
        operation=operation,
        model=str(getattr(provider, f"{capability}_model", "") or "") if provider else "",
        status="queued",
        request_json=request_json,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = db.query(AIGenerationJob).filter(
            AIGenerationJob.user_id == student.id,
            AIGenerationJob.client_request_id == client_request_id,
        ).first()
        if not existing:
            raise
        return existing, False
    db.commit()
    db.refresh(job)
    return job, True


@router.get("/agent/models")
def list_agent_models(student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    _ensure_student_tool(db, student, "text")
    providers = db.query(AIProvider).filter(AIProvider.enabled.is_(True), AIProvider.student_selectable.is_(True)).order_by(AIProvider.name.asc()).all()
    return {
        "models": [
            {"provider_id": provider.id, "provider_name": provider.name, "model": provider.text_model}
            for provider in providers
            if _provider_is_student_selectable(provider)
        ]
    }


@router.post("/ai/jobs")
def create_ai_job(
    payload: AIGenerationJobRequest,
    background_tasks: BackgroundTasks,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    _ensure_student_tool(db, student, payload.capability)
    provider_id = payload.provider_id
    if provider_id is not None and payload.capability == "text":
        _require_selectable_provider(db, provider_id)
    source_image_path = _owned_ai_input(student, payload.source_image_path)
    run_moderation(db, payload.prompt, "input", user_id=student.id, classroom_id=student.classroom_id)
    request = {
        "prompt": payload.prompt,
        "mode": payload.mode,
        "style": payload.style,
        "size": payload.size,
        "source_image_path": source_image_path,
        "save_project": payload.save_project,
    }
    job, created = _create_job(
        db,
        student,
        client_request_id=payload.client_request_id,
        capability=payload.capability,
        operation="generate",
        request=request,
        provider_id=provider_id,
    )
    queue_backend = enqueue_ai_generation(background_tasks, job.id, db.get_bind()) if created else "existing"
    return {"job": _job_payload(job, db), "queue_backend": queue_backend}


@router.get("/ai/jobs")
def list_ai_jobs(student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    rows = db.query(AIGenerationJob).filter(AIGenerationJob.user_id == student.id).order_by(AIGenerationJob.created_at.desc()).limit(100).all()
    return {"jobs": [_job_payload(item, db) for item in rows]}


@router.get("/ai/jobs/{job_id}")
def get_ai_job(job_id: int, student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    job = db.query(AIGenerationJob).filter(AIGenerationJob.id == job_id, AIGenerationJob.user_id == student.id).first()
    if not job:
        raise HTTPException(status_code=404, detail={"code": "AI_JOB_NOT_FOUND", "message": "AI 任务不存在。"})
    return {"job": _job_payload(job, db)}


@router.get("/ai/jobs/{job_id}/file")
def get_ai_job_file(job_id: int, student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    job = db.query(AIGenerationJob).filter(AIGenerationJob.id == job_id, AIGenerationJob.user_id == student.id).first()
    if not job:
        raise HTTPException(status_code=404, detail={"code": "AI_JOB_NOT_FOUND", "message": "AI 任务不存在。"})
    if job.capability == "image":
        result = _json_object(job.result_json)
        moderation_status = job.project.moderation_status if job.project else str(result.get("moderation_status") or "pending")
        if not job.project and result.get("moderation_log_id"):
            log = db.get(ModerationLog, int(result["moderation_log_id"]))
            moderation_status = log.status if log else moderation_status
        if moderation_status == "pending":
            raise HTTPException(
                status_code=423,
                detail={"code": "AI_IMAGE_REVIEW_PENDING", "message": "图片正在等待教师审批，审批通过后才能预览。"},
            )
        if moderation_status != "approved":
            raise HTTPException(
                status_code=403,
                detail={"code": "AI_IMAGE_REVIEW_REJECTED", "message": "图片未通过教师审批，不能预览。"},
            )
    reference = str(_json_object(job.result_json).get("file_path") or "")
    if not reference and job.project:
        reference = job.project.file_path
    if not reference or not object_exists(reference):
        raise HTTPException(status_code=404, detail={"code": "AI_JOB_FILE_MISSING", "message": "生成文件不存在或已被清理。"})
    return storage_response(reference, media_type=mimetypes.guess_type(Path(reference).name)[0] or "application/octet-stream")


@router.post("/ai/jobs/{job_id}/cancel")
def cancel_ai_job(job_id: int, student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    job = db.query(AIGenerationJob).filter(AIGenerationJob.id == job_id, AIGenerationJob.user_id == student.id).first()
    if not job:
        raise HTTPException(status_code=404, detail={"code": "AI_JOB_NOT_FOUND", "message": "AI 任务不存在。"})
    if job.status not in AI_JOB_TERMINAL_STATUSES:
        job.cancel_requested = True
        video_tasks = db.query(VideoTask).filter(
            VideoTask.generation_job_id == job.id,
            VideoTask.status.in_({"submitted", "processing"}),
        ).order_by(VideoTask.id.desc()).all()
        for video_task in video_tasks:
            video_task.status = "canceled"
            video_task.error_message = "学生已取消 Agent 视频任务；云端服务可能仍会继续处理。"
        if video_tasks:
            job.status = "canceled"
            job.completed_at = now()
        if job.status == "queued":
            job.status = "canceled"
            job.completed_at = now()
        db.commit()
        db.refresh(job)
    return {"job": _job_payload(job, db)}


@router.post("/ai/jobs/{job_id}/retry")
def retry_ai_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    job = db.query(AIGenerationJob).filter(AIGenerationJob.id == job_id, AIGenerationJob.user_id == student.id).first()
    if not job:
        raise HTTPException(status_code=404, detail={"code": "AI_JOB_NOT_FOUND", "message": "AI 任务不存在。"})
    if job.status not in {"failed", "timed_out", "canceled"}:
        raise HTTPException(status_code=409, detail={"code": "AI_JOB_NOT_RETRYABLE", "message": "当前任务状态不能重试。"})
    job.status = "queued"
    job.retry_count += 1
    job.cancel_requested = False
    job.error_code = ""
    job.error_message = ""
    job.started_at = None
    job.completed_at = None
    if job.assistant_message_id:
        assistant = db.get(AgentMessage, job.assistant_message_id)
        if assistant:
            assistant.status = "pending"
            assistant.content = ""
    db.commit()
    queue_backend = enqueue_ai_generation(background_tasks, job.id, db.get_bind())
    return {"job": _job_payload(job, db), "queue_backend": queue_backend}


def _require_conversation(db: Session, conversation_id: int, student: User) -> AgentConversation:
    conversation = db.query(AgentConversation).filter(
        AgentConversation.id == conversation_id,
        AgentConversation.user_id == student.id,
        AgentConversation.status == "active",
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "AGENT_CONVERSATION_NOT_FOUND", "message": "Agent 会话不存在。"})
    return conversation


@router.get("/agent/conversations")
def list_agent_conversations(student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    _expire_artifacts(db, student.id)
    rows = db.query(AgentConversation).filter(AgentConversation.user_id == student.id, AgentConversation.status == "active").order_by(AgentConversation.updated_at.desc()).all()
    return {"conversations": [_conversation_payload(item) for item in rows]}


@router.post("/agent/conversations")
def create_agent_conversation(
    payload: AgentConversationCreateRequest,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    _ensure_student_tool(db, student, "text")
    _require_selectable_provider(db, payload.selected_provider_id)
    conversation = AgentConversation(user_id=student.id, title=payload.title.strip(), selected_provider_id=payload.selected_provider_id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return {"conversation": _conversation_payload(conversation)}


@router.get("/agent/conversations/{conversation_id}")
def get_agent_conversation(conversation_id: int, student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    _expire_artifacts(db, student.id)
    conversation = _require_conversation(db, conversation_id, student)
    messages = db.query(AgentMessage).filter(AgentMessage.conversation_id == conversation.id).order_by(AgentMessage.sequence.asc()).all()
    artifacts = db.query(AgentArtifact).filter(AgentArtifact.conversation_id == conversation.id, AgentArtifact.user_id == student.id).order_by(AgentArtifact.created_at.asc()).all()
    return {
        "conversation": _conversation_payload(conversation),
        "messages": [_message_payload(item) for item in messages],
        "artifacts": [_artifact_payload(item) for item in artifacts],
    }


@router.put("/agent/conversations/{conversation_id}")
def update_agent_conversation(
    conversation_id: int,
    payload: AgentConversationUpdateRequest,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    conversation = _require_conversation(db, conversation_id, student)
    if payload.title is not None:
        conversation.title = payload.title.strip()
    if "selected_provider_id" in payload.model_fields_set:
        _require_selectable_provider(db, payload.selected_provider_id)
        conversation.selected_provider_id = payload.selected_provider_id
    conversation.updated_at = now()
    db.commit()
    db.refresh(conversation)
    return {"conversation": _conversation_payload(conversation)}


@router.delete("/agent/conversations/{conversation_id}")
def delete_agent_conversation(conversation_id: int, student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    conversation = _require_conversation(db, conversation_id, student)
    conversation.status = "deleted"
    conversation.updated_at = now()
    db.commit()
    return {"deleted": True, "conversation_id": conversation_id}


@router.post("/agent/conversations/{conversation_id}/messages")
def create_agent_message(
    conversation_id: int,
    payload: AgentMessageCreateRequest,
    background_tasks: BackgroundTasks,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    _ensure_student_tool(db, student, "text")
    conversation = _require_conversation(db, conversation_id, student)
    _require_selectable_provider(db, conversation.selected_provider_id)
    run_moderation(db, payload.content, "agent_input", user_id=student.id, classroom_id=student.classroom_id)
    existing = db.query(AIGenerationJob).filter(
        AIGenerationJob.user_id == student.id,
        AIGenerationJob.client_request_id == payload.client_request_id,
    ).first()
    if existing:
        if (
            existing.conversation_id != conversation.id
            or existing.operation != "agent_message"
            or _json_object(existing.request_json).get("content") != payload.content
        ):
            raise HTTPException(status_code=409, detail={"code": "AI_JOB_IDEMPOTENCY_CONFLICT", "message": "请求编号已用于另一条消息。"})
        return {"job": _job_payload(existing, db), "queue_backend": "existing"}
    next_sequence = (
        db.query(func.max(AgentMessage.sequence))
        .filter(AgentMessage.conversation_id == conversation.id)
        .scalar()
        or 0
    ) + 1
    user_message = AgentMessage(
        conversation_id=conversation.id,
        user_id=student.id,
        role="user",
        content=payload.content,
        status="completed",
        sequence=next_sequence,
    )
    assistant = AgentMessage(
        conversation_id=conversation.id,
        user_id=student.id,
        role="assistant",
        content="",
        status="pending",
        sequence=next_sequence + 1,
    )
    db.add_all([user_message, assistant])
    db.flush()
    if conversation.title == "新对话":
        conversation.title = project_title_from_prompt("", payload.content)
    job, _ = _create_job(
        db,
        student,
        client_request_id=payload.client_request_id,
        capability="text",
        operation="agent_message",
        request={"content": payload.content},
        provider_id=conversation.selected_provider_id,
        conversation_id=conversation.id,
        assistant_message_id=assistant.id,
    )
    queue_backend = enqueue_ai_generation(background_tasks, job.id, db.get_bind())
    return {"job": _job_payload(job, db), "user_message": _message_payload(user_message), "assistant_message": _message_payload(assistant), "queue_backend": queue_backend}


@router.post("/agent/conversations/{conversation_id}/tools")
def run_agent_tool(
    conversation_id: int,
    payload: AgentToolRunRequest,
    background_tasks: BackgroundTasks,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    conversation = _require_conversation(db, conversation_id, student)
    _ensure_student_tool(db, student, payload.capability)
    if payload.capability == "workflow" and payload.workflow_id is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "WORKFLOW_SELECTION_REQUIRED", "message": "请选择要运行的工作流。"},
        )
    run_moderation(db, payload.prompt, "agent_tool_input", user_id=student.id, classroom_id=student.classroom_id)
    job, created = _create_job(
        db,
        student,
        client_request_id=payload.client_request_id,
        capability=payload.capability,
        operation="agent_workflow" if payload.capability == "workflow" else "agent_tool",
        request={
            "prompt": payload.prompt,
            "duration_seconds": payload.duration_seconds,
            "workflow_id": payload.workflow_id,
        },
        conversation_id=conversation.id,
    )
    queue_backend = enqueue_ai_generation(background_tasks, job.id, db.get_bind()) if created else "existing"
    return {"job": _job_payload(job, db), "queue_backend": queue_backend}


@router.get("/agent/artifacts/{artifact_id}/file")
def get_agent_artifact_file(artifact_id: int, student: User = Depends(require_student_account), db: Session = Depends(get_db)):
    _expire_artifacts(db, student.id)
    artifact = db.query(AgentArtifact).filter(AgentArtifact.id == artifact_id, AgentArtifact.user_id == student.id).first()
    if not artifact or not artifact.file_path or not object_exists(artifact.file_path):
        raise HTTPException(status_code=404, detail={"code": "AGENT_ARTIFACT_MISSING", "message": "临时文件不存在或已到期。"})
    if artifact.status == "pending_review":
        raise HTTPException(status_code=423, detail={"code": "AGENT_ARTIFACT_REVIEW_PENDING", "message": "图片正在等待安全复核。"})
    return storage_response(artifact.file_path, media_type=artifact.mime_type or "application/octet-stream")


@router.post("/agent/artifacts/{artifact_id}/save-project")
def save_agent_artifact(
    artifact_id: int,
    payload: AgentArtifactSaveRequest,
    student: User = Depends(require_student_account),
    db: Session = Depends(get_db),
):
    _expire_artifacts(db, student.id)
    artifact = db.query(AgentArtifact).filter(AgentArtifact.id == artifact_id, AgentArtifact.user_id == student.id).first()
    if not artifact or not artifact.file_path or not object_exists(artifact.file_path):
        raise HTTPException(status_code=404, detail={"code": "AGENT_ARTIFACT_MISSING", "message": "临时文件不存在或已到期。"})
    if artifact.status == "pending_review":
        raise HTTPException(status_code=409, detail={"code": "AGENT_ARTIFACT_REVIEW_PENDING", "message": "图片审核通过后才能保存到作品库。"})
    if artifact.saved_project_id:
        project = db.get(Project, artifact.saved_project_id)
        return {"project": {"id": project.id, "title": project.title} if project else None, "artifact": _artifact_payload(artifact)}
    project = save_project(
        db,
        payload.title,
        artifact.artifact_type,
        "由学习 Agent 生成并由学生确认保存。",
        artifact.file_path,
        student=student,
    )
    artifact.saved_project_id = project.id
    artifact.status = "saved"
    db.commit()
    db.refresh(artifact)
    return {"project": {"id": project.id, "title": project.title}, "artifact": _artifact_payload(artifact)}
