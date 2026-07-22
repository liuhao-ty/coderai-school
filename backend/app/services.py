import asyncio
import base64
import json
import mimetypes
from time import perf_counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db import DATA_DIR
from backend.app.models import AIProvider, AIProviderRoute, AppSetting, Asset, Classroom, Course, Lesson, ModerationLog, Project, SubmissionVersion, Task, TaskSubmission, UsageLog, User, VideoTask, now
from backend.app.provider_presets import get_provider_preset, list_provider_presets, provider_capabilities
from backend.app.school_stages import SCHOOL_STAGES, normalize_school_stage, school_stage_label
from backend.app.secrets import SecretProtectionError, decrypt_secret
from backend.app.storage import object_exists, put_bytes, read_bytes as read_stored_bytes, reference_name


OUTPUT_DIR = DATA_DIR / "outputs"
PROJECT_DIR = DATA_DIR / "projects"
ASSET_DIR = DATA_DIR / "assets"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
VIDEO_TASK_TIMEOUT = timedelta(minutes=30)
VIDEO_DOWNLOAD_URL_TTL = timedelta(hours=24)
VIDEO_MAX_RETRIES = 3
VIDEO_ACTIVE_STATUSES = frozenset({"submitted", "processing"})
VIDEO_RETRYABLE_STATUSES = frozenset({"failed", "timed_out", "download_failed", "expired"})
VIDEO_EXPIRED_HTTP_STATUSES = frozenset({401, 403, 404, 410})
QWEN_IMAGE_TIMEOUT_SECONDS = 180
QWEN_IMAGE_POLL_SECONDS = 2

for directory in [OUTPUT_DIR, PROJECT_DIR, ASSET_DIR, CACHE_DIR, LOG_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


DEFAULT_BLOCKED_WORDS = ["暴力血腥", "自残", "成人内容", "赌博", "违法"]
MODERATION_WORDS_SETTING_KEY = "moderation_blocked_words"
PROVIDER_CAPABILITIES = ("text", "image", "video")
RETRYABLE_PROVIDER_ERROR_CODES = frozenset({
    "PROVIDER_SECRET_INVALID",
    "PROVIDER_CAPABILITY_UNAVAILABLE",
    "TEXT_MODEL_REQUIRED",
    "IMAGE_MODEL_REQUIRED",
    "VIDEO_MODEL_REQUIRED",
    "IMAGE_ADAPTER_UNAVAILABLE",
    "IMAGE_EDIT_ADAPTER_UNAVAILABLE",
    "VIDEO_ADAPTER_UNAVAILABLE",
    "AI_AUTH_ERROR",
    "AI_QUOTA_EXHAUSTED",
    "AI_RATE_LIMITED",
    "AI_PROVIDER_UNAVAILABLE",
    "AI_PROVIDER_ERROR",
    "AI_NETWORK_ERROR",
    "AI_TIMEOUT",
    "AI_RESPONSE_INVALID",
})
T = TypeVar("T")


def provider_model(provider: AIProvider, capability: str) -> str:
    return str(getattr(provider, f"{capability}_model", "") or "").strip()


def _route_provider_ids(route: AIProviderRoute | None) -> list[int]:
    if not route:
        return []
    try:
        values = json.loads(route.provider_ids_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    result: list[int] = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, int) and value > 0 and value not in result:
            result.append(value)
    return result


def ensure_default_provider_routes(db: Session) -> None:
    providers = db.query(AIProvider).filter(AIProvider.enabled.is_(True)).order_by(AIProvider.id.desc()).all()
    if not providers:
        return
    changed = False
    for capability in PROVIDER_CAPABILITIES:
        route = db.query(AIProviderRoute).filter(AIProviderRoute.capability == capability).first()
        if route:
            continue
        provider = next(
            (
                item
                for item in providers
                if item.api_key.strip()
                and capability in provider_capabilities(item.provider_type)
                and provider_model(item, capability)
            ),
            None,
        )
        db.add(AIProviderRoute(capability=capability, provider_ids_json=json.dumps([provider.id] if provider else [])))
        changed = True
    if changed:
        db.commit()


def provider_route_ids(db: Session, capability: str) -> list[int]:
    ensure_default_provider_routes(db)
    route = db.query(AIProviderRoute).filter(AIProviderRoute.capability == capability).first()
    return _route_provider_ids(route)


def provider_candidates(db: Session, capability: str) -> list[AIProvider]:
    if capability not in PROVIDER_CAPABILITIES:
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_CAPABILITY_INVALID", "message": "模型能力类型不正确。"})
    ids = provider_route_ids(db, capability)
    if not ids:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PROVIDER_REQUIRED",
                "message": f"教师端还没有为 {capability} 能力配置可用的模型路由。请先进入 AI 服务完成配置。",
            },
        )
    rows = db.query(AIProvider).filter(AIProvider.id.in_(ids)).all()
    by_id = {provider.id: provider for provider in rows}
    candidates = [
        by_id[provider_id]
        for provider_id in ids
        if provider_id in by_id
        and by_id[provider_id].enabled
        and by_id[provider_id].api_key.strip()
        and capability in provider_capabilities(by_id[provider_id].provider_type)
    ]
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PROVIDER_REQUIRED",
                "message": f"{capability} 路由中没有已启用且配置密钥的模型服务，请教师检查服务商状态。",
            },
        )
    return candidates


def active_provider(
    db: Session,
    capability: str | None = None,
    provider_id: int | None = None,
    include_disabled: bool = False,
) -> AIProvider:
    if provider_id is not None:
        provider = db.query(AIProvider).filter(AIProvider.id == provider_id).first()
        if not provider:
            raise HTTPException(status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": "模型服务配置不存在。"})
        if not include_disabled and not provider.enabled:
            raise HTTPException(status_code=400, detail={"code": "PROVIDER_DISABLED", "message": "模型服务已停用。"})
    elif capability:
        return provider_candidates(db, capability)[0]
    else:
        provider = db.query(AIProvider).filter(AIProvider.enabled.is_(True)).order_by(AIProvider.id.desc()).first()
    if not provider or not provider.api_key.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "PROVIDER_REQUIRED", "message": "教师端还没有配置可用的云端AI API密钥。请先进入教师设置完成配置。"},
        )
    if capability:
        ensure_provider_capability(provider, capability)
    return provider


def provider_api_key(provider: AIProvider) -> str:
    try:
        return decrypt_secret(provider.api_key)
    except SecretProtectionError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PROVIDER_SECRET_INVALID",
                "message": "已保存的 API Key 无法解密，请教师重新输入并保存密钥。",
            },
        ) from exc


def ensure_provider_capability(provider: AIProvider, capability: str):
    capabilities = provider_capabilities(provider.provider_type)
    if capability not in capabilities:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PROVIDER_CAPABILITY_UNAVAILABLE",
                "message": f"{provider.name} 当前预设不支持 {capability} 能力，请在教师设置中选择支持该能力的模型服务。",
            },
        )


def _provider_failure(
    status_code: int,
    code: str,
    message: str,
    failure_source: str,
    diagnostic_summary: str,
    observed_status_code: str = "",
) -> HTTPException:
    detail = {
        "code": code,
        "message": message,
        "failure_source": failure_source,
        "diagnostic_summary": diagnostic_summary,
    }
    if observed_status_code:
        detail["observed_status_code"] = observed_status_code
    return HTTPException(status_code=status_code, detail=detail)


def provider_http_exception(exc: httpx.HTTPStatusError) -> HTTPException:
    status = exc.response.status_code
    response_hint = exc.response.text[:300].lower()
    diagnostic = f"上游服务返回 HTTP {status}。"
    if status in (401, 403):
        return _provider_failure(502, "AI_AUTH_ERROR", "AI 服务认证失败，请教师检查 API Key 和接口权限。", "provider_http", diagnostic, str(status))
    if status == 402 or "quota" in response_hint or "balance" in response_hint:
        return _provider_failure(502, "AI_QUOTA_EXHAUSTED", "AI 服务额度或余额不足，请教师检查账户。", "provider_http", diagnostic, str(status))
    if status == 429:
        return _provider_failure(429, "AI_RATE_LIMITED", "AI 服务请求过于频繁，请稍后重试。", "provider_http", diagnostic, str(status))
    if status >= 500:
        return _provider_failure(502, "AI_PROVIDER_UNAVAILABLE", "AI 服务暂时不可用，请稍后重试。", "provider_http", diagnostic, str(status))
    return _provider_failure(502, "AI_PROVIDER_ERROR", f"AI 服务拒绝了请求（HTTP {status}）。", "provider_http", diagnostic, str(status))


def provider_request_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.TimeoutException):
        return _provider_failure(
            504,
            "AI_TIMEOUT",
            "AI 服务响应超时，请检查网络或稍后重试。",
            "timeout",
            "请求在等待上游服务响应时超时。",
        )
    if isinstance(exc, httpx.RequestError):
        return _provider_failure(
            502,
            "AI_NETWORK_ERROR",
            "无法连接 AI 服务，请检查网络和 Base URL。",
            "network",
            "请求未收到上游服务的 HTTP 响应。",
        )
    return _provider_failure(
        502,
        "AI_RESPONSE_INVALID",
        "AI 服务返回了无法解析的数据，请检查模型配置。",
        "response_validation",
        "上游响应未通过本地结构校验。",
    )


def provider_failure_log_detail(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("code") or "AI_PROVIDER_ERROR")[:80]
    summary = str(detail.get("diagnostic_summary") or detail.get("message") or "AI 服务调用失败。")[:300]
    return f"{code}: {summary}"


def _provider_error_code(exc: HTTPException) -> str:
    return str(exc.detail.get("code") or "") if isinstance(exc.detail, dict) else ""


def is_retryable_provider_error(exc: HTTPException) -> bool:
    return _provider_error_code(exc) in RETRYABLE_PROVIDER_ERROR_CODES


def _fallback_error(exc: HTTPException, failures: list[dict[str, Any]]) -> HTTPException:
    detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"code": "AI_PROVIDER_ERROR", "message": str(exc.detail)}
    if len(failures) > 1:
        detail["message"] = f"{detail.get('message', '模型服务调用失败')} 已依次尝试 {len(failures)} 个模型服务。"
    detail["attempted_providers"] = failures
    return HTTPException(status_code=exc.status_code, detail=detail)


async def run_with_provider_fallback(
    db: Session,
    capability: str,
    operation: Callable[[AIProvider], Awaitable[T]],
) -> T:
    failures: list[dict[str, Any]] = []
    candidates = provider_candidates(db, capability)
    for index, provider in enumerate(candidates):
        try:
            return await operation(provider)
        except HTTPException as exc:
            failures.append({"provider_id": provider.id, "provider_name": provider.name, "code": _provider_error_code(exc)})
            if not is_retryable_provider_error(exc) or index == len(candidates) - 1:
                raise _fallback_error(exc, failures) from exc
    raise HTTPException(status_code=502, detail={"code": "AI_PROVIDER_ERROR", "message": "模型路由没有返回结果。"})


def _record_provider_test(db: Session, provider: AIProvider, status: str, message: str) -> None:
    provider.last_test_status = status
    provider.last_test_message = message[:500]
    provider.last_tested_at = now()
    db.commit()


async def test_provider_connection(db: Session, capability: str = "text", provider_id: int | None = None) -> dict[str, Any]:
    provider = active_provider(db, capability if provider_id is None else None, provider_id, include_disabled=provider_id is not None)
    try:
        ensure_provider_capability(provider, capability)
        model = provider_model(provider, capability)
        if not model:
            code = f"{capability.upper()}_MODEL_REQUIRED"
            raise HTTPException(status_code=400, detail={"code": code, "message": f"请先配置{capability}模型。"})
        provider_api_key(provider)
        if capability != "text":
            message = "适配器、模型和密钥配置检查通过；真实生成请在对应工具中验收。"
            _record_provider_test(db, provider, "success", message)
            return {"ok": True, "capability": capability, "provider_id": provider.id, "provider": provider.name, "model": model, "message": message}
        started = perf_counter()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                provider.base_url.rstrip("/") + "/chat/completions",
                headers=_headers(provider_api_key(provider)),
                json={
                    "model": provider.text_model,
                    "messages": [{"role": "user", "content": "Reply with OK only."}],
                    "temperature": 0,
                    "max_tokens": 5,
                },
            )
            response.raise_for_status()
        data = response.json()
        if not data.get("choices"):
            raise ValueError("missing choices")
        result = {
            "ok": True,
            "capability": capability,
            "provider_id": provider.id,
            "provider": provider.name,
            "model": provider.text_model,
            "latency_ms": round((perf_counter() - started) * 1000),
            "message": "文字模型连接成功。",
        }
        _record_provider_test(db, provider, "success", result["message"])
        return result
    except HTTPException as exc:
        message = exc.detail.get("message", str(exc.detail)) if isinstance(exc.detail, dict) else str(exc.detail)
        _record_provider_test(db, provider, "failed", message)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        _record_provider_test(db, provider, "failed", mapped.detail["message"])
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        _record_provider_test(db, provider, "failed", mapped.detail["message"])
        raise mapped


def resolve_owner_teacher_id(db: Session, user_id: int | None, owner_teacher_id: int | None = None) -> int | None:
    if owner_teacher_id is not None:
        return owner_teacher_id
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return user.created_by_user_id if user and user.role != "student" else None


def resolve_classroom_id(db: Session, user_id: int | None, classroom_id: int | None = None) -> int | None:
    if classroom_id is not None:
        return classroom_id
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return user.classroom_id if user and user.role == "student" else None


def run_moderation(
    db: Session,
    text: str,
    content_stage: str = "input",
    user_id: int | None = None,
    owner_teacher_id: int | None = None,
    classroom_id: int | None = None,
) -> dict[str, Any]:
    matched = [word for word in get_blocked_words(db) if word in text]
    passed = not matched
    reason = "" if passed else f"包含不适合课堂使用的词语：{', '.join(matched)}"
    db.add(ModerationLog(
        owner_teacher_id=resolve_owner_teacher_id(db, user_id, owner_teacher_id),
        user_id=user_id,
        classroom_id=resolve_classroom_id(db, user_id, classroom_id),
        input_text=text,
        content_stage=content_stage,
        passed=passed,
        reason=reason,
    ))
    db.commit()
    if not passed:
        raise HTTPException(status_code=400, detail={"code": "CONTENT_BLOCKED", "message": reason})
    return {"passed": True, "reason": ""}


def _record_image_moderation(
    db: Session,
    prompt: str,
    resource_path: str,
    status: str,
    reason: str,
    user_id: int | None = None,
    owner_teacher_id: int | None = None,
    classroom_id: int | None = None,
) -> dict[str, Any]:
    log = ModerationLog(
        owner_teacher_id=resolve_owner_teacher_id(db, user_id, owner_teacher_id),
        user_id=user_id,
        classroom_id=resolve_classroom_id(db, user_id, classroom_id),
        input_text=prompt,
        content_stage="image_output",
        passed=status == "approved",
        reason=reason,
        resource_type="image",
        resource_path=resource_path,
        status=status,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"moderation_status": status, "moderation_reason": reason, "moderation_log_id": log.id}


def _image_moderation_reference(file_path: str, image_url: str) -> str:
    if image_url:
        return image_url
    try:
        content = read_stored_bytes(file_path, maximum_bytes=20 * 1024 * 1024)
    except (FileNotFoundError, OSError, ValueError):
        # Provider adapters may return a temporary local file before it is
        # copied into managed storage. It is safe to read here because this
        # helper is only called with server-produced output, never a client
        # supplied download path.
        try:
            temporary_path = Path(file_path).resolve()
            if not temporary_path.is_file() or temporary_path.stat().st_size > 20 * 1024 * 1024:
                return ""
            content = temporary_path.read_bytes()
        except (FileNotFoundError, OSError, ValueError):
            return ""
    mime_type = mimetypes.guess_type(reference_name(file_path))[0] or "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


async def moderate_image_output(
    db: Session,
    provider: AIProvider,
    prompt: str,
    result: dict[str, Any],
    user_id: int | None = None,
    owner_teacher_id: int | None = None,
) -> dict[str, Any]:
    image_url = str(result.get("url") or "")
    file_path = str(result.get("file_path") or "")
    resource_path = file_path or image_url
    if not resource_path:
        moderation = _record_image_moderation(db, prompt, "", "rejected", "图片服务没有返回可审核的图片结果。", user_id, owner_teacher_id)
        log_usage(db, "image_moderation", provider.image_model, "rejected", moderation["moderation_reason"], user_id, provider)
        return {**result, **moderation}

    if provider.provider_type != "openai_compatible":
        reason = "当前图片服务商未提供已接入的自动图片审核接口，需要教师复核。"
        moderation = _record_image_moderation(db, prompt, resource_path, "pending", reason, user_id, owner_teacher_id)
        log_usage(db, "image_moderation", provider.image_model, "pending", reason, user_id, provider)
        return {**result, **moderation}

    image_reference = _image_moderation_reference(file_path, image_url)
    if not image_reference:
        reason = "生成图片无法读取或超过 20 MB，需要教师复核。"
        moderation = _record_image_moderation(db, prompt, resource_path, "pending", reason, user_id, owner_teacher_id)
        log_usage(db, "image_moderation", provider.image_model, "pending", reason, user_id, provider)
        return {**result, **moderation}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                provider.base_url.rstrip("/") + "/moderations",
                headers=_headers(provider_api_key(provider)),
                json={
                    "model": "omni-moderation-latest",
                    "input": [{"type": "image_url", "image_url": {"url": image_reference}}],
                },
            )
            response.raise_for_status()
        moderation_result = response.json()["results"][0]
        flagged = bool(moderation_result.get("flagged"))
        categories = [name for name, matched in (moderation_result.get("categories") or {}).items() if matched]
        status = "rejected" if flagged else "approved"
        reason = f"自动图片审核命中风险类别：{', '.join(categories) or '未分类风险'}" if flagged else "自动图片审核通过。"
    except Exception:
        status = "pending"
        reason = "自动图片审核服务暂时不可用，结果已转入教师复核队列。"
    moderation = _record_image_moderation(db, prompt, resource_path, status, reason, user_id, owner_teacher_id)
    log_usage(db, "image_moderation", provider.image_model, status, reason, user_id, provider)
    return {**result, **moderation}


def age_generation_policy(age_level: str) -> dict[str, Any]:
    normalized = age_level if age_level in {*SCHOOL_STAGES, "mixed"} else normalize_school_stage(age_level)
    policies = {
        "primary_lower": {
            "label": "小学低龄",
            "max_tokens": 600,
            "temperature": 0.6,
            "instruction": "面向小学低龄学生，使用短句和常用词，避免恐怖、冲突、成人、医疗、赌博、违法和危险操作；最多给出 5 个清晰步骤。",
        },
        "primary_upper": {
            "label": "小学高龄",
            "max_tokens": 1200,
            "temperature": 0.7,
            "instruction": "面向小学高龄学生，可以解释基础技术原理，但不得提供危险操作、违法绕过、成人或自伤内容；鼓励验证和独立思考。",
        },
        "secondary": {
            "label": "初中高中",
            "max_tokens": 1600,
            "temperature": 0.7,
            "instruction": "面向初中高中学生，可以使用准确术语、解释技术原理并给出验证方法；仍不得提供危险操作、违法绕过、成人或自伤内容。",
        },
        "mixed": {
            "label": "混合学龄",
            "max_tokens": 900,
            "temperature": 0.65,
            "instruction": "面向混合年龄课堂，语言清晰并解释术语，不得包含危险、成人、违法或不适龄内容。",
        },
    }
    return {"age_level": normalized, **policies[normalized]}


def get_blocked_words(db: Session) -> list[str]:
    setting = db.query(AppSetting).filter(AppSetting.key == MODERATION_WORDS_SETTING_KEY).first()
    if not setting:
        return DEFAULT_BLOCKED_WORDS
    try:
        words = json.loads(setting.value)
    except json.JSONDecodeError:
        return DEFAULT_BLOCKED_WORDS
    if not isinstance(words, list):
        return DEFAULT_BLOCKED_WORDS
    return [str(word).strip() for word in words if str(word).strip()]


def save_blocked_words(db: Session, words: list[str]) -> list[str]:
    cleaned = []
    for word in words:
        normalized = word.strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    setting = db.query(AppSetting).filter(AppSetting.key == MODERATION_WORDS_SETTING_KEY).first()
    value = json_dumps(cleaned)
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=MODERATION_WORDS_SETTING_KEY, value=value))
    db.commit()
    return cleaned


def log_usage(
    db: Session,
    feature: str,
    model: str,
    status: str,
    detail: str = "",
    user_id: int | None = None,
    provider: AIProvider | None = None,
    owner_teacher_id: int | None = None,
    classroom_id: int | None = None,
):
    db.add(UsageLog(
        owner_teacher_id=resolve_owner_teacher_id(db, user_id, owner_teacher_id),
        user_id=user_id,
        classroom_id=resolve_classroom_id(db, user_id, classroom_id),
        provider_id=provider.id if provider else None,
        provider_name=provider.name if provider else "",
        feature=feature,
        model=model,
        status=status,
        detail=detail[:1000],
    ))
    db.commit()


def save_project(
    db: Session,
    title: str,
    project_type: str,
    summary: str,
    file_path: str = "",
    student: User | None = None,
    moderation_status: str = "approved",
    moderation_reason: str = "",
    moderation_log_id: int | None = None,
    owner_teacher_id: int | None = None,
) -> Project:
    project = Project(
        owner_teacher_id=resolve_owner_teacher_id(db, student.id if student else None, owner_teacher_id),
        title=title[:160],
        project_type=project_type,
        summary=summary,
        file_path=file_path,
        user_id=student.id if student else None,
        classroom_id=student.classroom_id if student else None,
        owner_name=student.name if student else "未归属学生",
        moderation_status=moderation_status,
        moderation_reason=moderation_reason,
        moderation_log_id=moderation_log_id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


async def _generate_text_with_provider(
    db: Session,
    provider: AIProvider,
    prompt: str,
    mode: str,
    age_level: str,
    user_id: int | None = None,
) -> str:
    ensure_provider_capability(provider, "text")
    if not provider.text_model.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "TEXT_MODEL_REQUIRED",
                "message": "当前服务没有配置文字模型，请在教师设置中选择或填写文字模型。",
            },
        )
    policy = age_generation_policy(age_level)
    system_prompt = (
        "你是少儿AI课程助手。输出必须适合课堂，语言清晰，鼓励学生自己思考，"
        f"不要提供危险、成人或不适龄内容。{policy['instruction']}"
    )
    user_prompt = f"模式：{mode}\n学龄分类：{policy['label']}\n任务：{prompt}"
    url = provider.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": provider.text_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": policy["temperature"],
        "max_tokens": policy["max_tokens"],
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(url, headers=_headers(provider_api_key(provider)), json=payload)
            response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        run_moderation(db, text, "output", user_id)
        log_usage(db, "text", provider.text_model, "success", user_id=user_id, provider=provider)
        return text
    except HTTPException as exc:
        status = "blocked" if _provider_error_code(exc) == "CONTENT_BLOCKED" else "failed"
        detail = "Output moderation blocked the response" if status == "blocked" else str(exc.detail)
        log_usage(db, "text", provider.text_model, status, detail, user_id, provider)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "text", provider.text_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "text", provider.text_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped


async def generate_text(
    db: Session,
    prompt: str,
    mode: str,
    age_level: str,
    user_id: int | None = None,
    provider_id: int | None = None,
) -> str:
    if provider_id is not None:
        provider = active_provider(db, "text", provider_id)
        return await _generate_text_with_provider(db, provider, prompt, mode, age_level, user_id)
    return await run_with_provider_fallback(
        db,
        "text",
        lambda provider: _generate_text_with_provider(db, provider, prompt, mode, age_level, user_id),
    )


async def generate_image(
    db: Session,
    prompt: str,
    style: str,
    size: str,
    source_image_path: str | None = None,
    user_id: int | None = None,
    provider_id: int | None = None,
) -> dict[str, Any]:
    if provider_id is not None:
        provider = active_provider(db, "image", provider_id)
        return await _generate_image_with_provider(db, provider, prompt, style, size, source_image_path, user_id)
    return await run_with_provider_fallback(
        db,
        "image",
        lambda provider: _generate_image_with_provider(db, provider, prompt, style, size, source_image_path, user_id),
    )


async def _generate_image_with_provider(
    db: Session,
    provider: AIProvider,
    prompt: str,
    style: str,
    size: str,
    source_image_path: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    ensure_provider_capability(provider, "image")
    if not provider.image_model.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "IMAGE_MODEL_REQUIRED",
                "message": "当前服务没有配置图片模型，请在教师设置中选择或填写图片模型。",
            },
        )
    if source_image_path:
        if provider.provider_type != "openai_compatible" or source_image_path.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=501,
                detail={"code": "IMAGE_EDIT_ADAPTER_UNAVAILABLE", "message": f"{provider.name} 当前未接入图生图编辑接口，请移除参考图或切换 OpenAI 兼容图片服务。"},
            )
        result = await generate_openai_image_edit(db, provider, prompt, style, size, source_image_path, user_id)
        return await moderate_image_output(db, provider, prompt, result, user_id)

    adapters = {
        "openai_compatible": generate_openai_image,
        "minimax": generate_minimax_image,
        "volcengine_jimeng": generate_volcengine_image,
        "qwen": generate_qwen_image,
        "zhipu": generate_zhipu_image,
    }
    adapter = adapters.get(provider.provider_type)
    if not adapter:
        raise HTTPException(
            status_code=501,
            detail={"code": "IMAGE_ADAPTER_UNAVAILABLE", "message": f"{provider.name} 的图片生成适配器尚未接入。"},
        )
    result = await adapter(db, provider, prompt, style, size, user_id)
    return await moderate_image_output(db, provider, prompt, result, user_id)


def _classroom_image_prompt(prompt: str, style: str) -> str:
    return f"{prompt}\n风格：{style}。适合少儿编程课堂，明亮、清晰、无危险内容。"


def _first_image_item(data: dict[str, Any], provider_name: str) -> dict[str, Any]:
    items = data.get("data")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise HTTPException(
            status_code=502,
            detail={"code": "AI_RESPONSE_INVALID", "message": f"{provider_name} 未返回有效的图片结果。"},
        )
    return items[0]


def _image_result_from_item(item: dict[str, Any], target_name: str) -> dict[str, str]:
    image_url = str(item.get("url") or item.get("image_url") or "")
    file_path = ""
    encoded = item.get("b64_json") or item.get("base64")
    if encoded:
        try:
            file_path = str(_write_bytes(OUTPUT_DIR / target_name, base64.b64decode(str(encoded), validate=True)))
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "AI_RESPONSE_INVALID", "message": "图片服务返回了无效的 Base64 数据。"},
            ) from exc
    if not image_url and not file_path:
        raise HTTPException(
            status_code=502,
            detail={"code": "AI_RESPONSE_INVALID", "message": "图片服务没有返回图片 URL 或图片数据。"},
        )
    return {"url": image_url, "file_path": file_path}


def _provider_business_error(data: dict[str, Any], default_message: str) -> HTTPException:
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    code = str(data.get("code") or error.get("code") or "AI_PROVIDER_ERROR")
    message = str(data.get("message") or error.get("message") or default_message)
    return HTTPException(status_code=502, detail={"code": code[:80], "message": message[:300]})


async def generate_openai_image(
    db: Session,
    provider: AIProvider,
    prompt: str,
    style: str,
    size: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    url = provider.base_url.rstrip("/") + "/images/generations"
    payload = {"model": provider.image_model, "prompt": _classroom_image_prompt(prompt, style), "size": size}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(url, headers=_headers(provider_api_key(provider)), json=payload)
            response.raise_for_status()
        data = response.json()
        result = _image_result_from_item(_first_image_item(data, provider.name), "image-openai.png")
        log_usage(db, "image", provider.image_model, "success", user_id=user_id, provider=provider)
        return result
    except HTTPException:
        log_usage(db, "image", provider.image_model, "failed", "OpenAI-compatible image response failed", user_id, provider)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped


async def generate_volcengine_image(
    db: Session,
    provider: AIProvider,
    prompt: str,
    style: str,
    size: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    url = provider.base_url.rstrip("/") + "/images/generations"
    payload = {
        "model": provider.image_model,
        "prompt": _classroom_image_prompt(prompt, style),
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=_headers(provider_api_key(provider)), json=payload)
            response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise _provider_business_error(data, "火山引擎图片生成失败")
        result = _image_result_from_item(_first_image_item(data, provider.name), "image-jimeng.png")
        log_usage(db, "image", provider.image_model, "success", user_id=user_id, provider=provider)
        return result
    except HTTPException:
        log_usage(db, "image", provider.image_model, "failed", "Volcengine image response failed", user_id, provider)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped


def _qwen_api_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    compatible_suffix = "/compatible-mode/v1"
    if base.endswith(compatible_suffix):
        base = base[: -len(compatible_suffix)]
    if base.endswith("/api/v1"):
        return base
    return base + "/api/v1"


async def generate_qwen_image(
    db: Session,
    provider: AIProvider,
    prompt: str,
    style: str,
    size: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    base_url = _qwen_api_base(provider.base_url)
    create_url = base_url + "/services/aigc/text2image/image-synthesis"
    payload = {
        "model": provider.image_model,
        "input": {"prompt": _classroom_image_prompt(prompt, style)},
        "parameters": {
            "size": size.replace("x", "*"),
            "n": 1,
            "prompt_extend": True,
            "watermark": False,
        },
    }
    headers = {**_headers(provider_api_key(provider)), "X-DashScope-Async": "enable"}
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(create_url, headers=headers, json=payload)
            response.raise_for_status()
            submitted = response.json()
            task_id = _pick_first_string(submitted, [], ["output.task_id"])
            if not task_id:
                raise _provider_business_error(submitted, "通义万相未返回任务 ID")

            while perf_counter() - started < QWEN_IMAGE_TIMEOUT_SECONDS:
                query = await client.get(base_url + f"/tasks/{task_id}", headers=_headers(provider_api_key(provider)))
                query.raise_for_status()
                result_data = query.json()
                output = result_data.get("output") if isinstance(result_data.get("output"), dict) else {}
                status = str(output.get("task_status") or "").upper()
                if status == "SUCCEEDED":
                    results = output.get("results")
                    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
                        raise _provider_business_error(result_data, "通义万相任务完成但没有返回图片")
                    result = _image_result_from_item(results[0], "image-qwen.png")
                    log_usage(db, "image", provider.image_model, "success", user_id=user_id, provider=provider)
                    return result
                if status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                    raise _provider_business_error(result_data, f"通义万相任务状态为 {status}")
                await asyncio.sleep(QWEN_IMAGE_POLL_SECONDS)
        raise HTTPException(
            status_code=504,
            detail={"code": "AI_TIMEOUT", "message": "通义万相图片任务超过 3 分钟仍未完成，请稍后重试。"},
        )
    except HTTPException:
        log_usage(db, "image", provider.image_model, "failed", "Qwen image task failed", user_id, provider)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped


async def generate_zhipu_image(
    db: Session,
    provider: AIProvider,
    prompt: str,
    style: str,
    size: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    url = provider.base_url.rstrip("/") + "/images/generations"
    payload = {
        "model": provider.image_model,
        "prompt": _classroom_image_prompt(prompt, style),
        "size": size,
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=_headers(provider_api_key(provider)), json=payload)
            response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise _provider_business_error(data, "智谱图片生成失败")
        result = _image_result_from_item(_first_image_item(data, provider.name), "image-zhipu.png")
        log_usage(db, "image", provider.image_model, "success", user_id=user_id, provider=provider)
        return result
    except HTTPException:
        log_usage(db, "image", provider.image_model, "failed", "Zhipu image response failed", user_id, provider)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped


async def generate_openai_image_edit(
    db: Session,
    provider: AIProvider,
    prompt: str,
    style: str,
    size: str,
    source_image_path: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    try:
        image_content = read_stored_bytes(source_image_path, maximum_bytes=10 * 1024 * 1024)
    except (FileNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "AI_INPUT_MISSING", "message": "参考图片文件已丢失，请重新上传。"})
    image_name = reference_name(source_image_path)
    url = provider.base_url.rstrip("/") + "/images/edits"
    classroom_prompt = f"{prompt}\n风格：{style}。适合少儿编程课堂，明亮、清晰、无危险内容。"
    content_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(Path(image_name).suffix.lower(), "application/octet-stream")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {provider_api_key(provider)}"},
                data={"model": provider.image_model, "prompt": classroom_prompt, "size": size},
                files={"image": (image_name, image_content, content_type)},
            )
            response.raise_for_status()
        data = response.json()
        item = data["data"][0]
        image_url = item.get("url", "")
        file_path = ""
        if item.get("b64_json"):
            file_path = str(_write_bytes(OUTPUT_DIR / "image-edit.png", base64.b64decode(item["b64_json"])))
        log_usage(db, "image_edit", provider.image_model, "success", user_id=user_id, provider=provider)
        return {"url": image_url, "file_path": file_path}
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "image_edit", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "image_edit", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped


async def generate_minimax_image(
    db: Session,
    provider: AIProvider,
    prompt: str,
    style: str,
    size: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    url = "https://api.minimax.io/v1/image_generation"
    classroom_prompt = f"{prompt}\n风格：{style}。适合少儿编程课堂，明亮、清晰、无危险内容。"
    payload = {
        "model": provider.image_model,
        "prompt": classroom_prompt,
        "aspect_ratio": _size_to_aspect_ratio(size),
        "response_format": "url",
        "n": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(url, headers=_headers(provider_api_key(provider)), json=payload)
            response.raise_for_status()
        data = response.json()
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") not in (None, 0):
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "AI_PROVIDER_ERROR",
                    "message": base_resp.get("status_msg", "MiniMax 图片生成失败"),
                },
            )
        image_url = _extract_minimax_image_url(data)
        if not image_url:
            raise HTTPException(
                status_code=502,
                detail={"code": "AI_RESPONSE_INVALID", "message": "MiniMax 未返回有效的图片结果。"},
            )
        log_usage(db, "image", provider.image_model, "success", user_id=user_id, provider=provider)
        return {"url": image_url, "file_path": ""}
    except HTTPException:
        log_usage(db, "image", provider.image_model, "failed", "MiniMax image generation failed", user_id, provider)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "image", provider.image_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
        raise mapped


def _minimax_video_payload(
    provider: AIProvider,
    prompt: str,
    source_image_path: str | None,
    duration_seconds: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": provider.video_model,
        "prompt": f"{prompt}\n适合少儿编程课堂，画面明亮、清晰、安全，无危险内容。",
    }
    if source_image_path:
        payload["first_frame_image"] = (
            local_image_data_url(source_image_path)
            if not source_image_path.startswith(("http://", "https://"))
            else source_image_path
        )
    if duration_seconds:
        payload["duration"] = max(1, min(int(duration_seconds), 10))
    return payload


async def _submit_minimax_video(
    provider: AIProvider,
    prompt: str,
    source_image_path: str | None,
    duration_seconds: int,
) -> str:
    payload = _minimax_video_payload(provider, prompt, source_image_path, duration_seconds)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.minimax.io/v1/video_generation",
            headers=_headers(provider_api_key(provider)),
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0):
        raise HTTPException(
            status_code=502,
            detail={"code": "AI_PROVIDER_ERROR", "message": base_resp.get("status_msg", "MiniMax 视频任务提交失败")},
        )
    provider_task_id = _pick_first_string(data, ["task_id", "id"], ["data.task_id", "data.id"])
    if not provider_task_id:
        raise HTTPException(status_code=502, detail={"code": "AI_PROVIDER_ERROR", "message": "视频服务未返回任务 ID。"})
    return provider_task_id


async def _query_minimax_video(provider: AIProvider, provider_task_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            "https://api.minimax.io/v1/query/video_generation",
            headers=_headers(provider_api_key(provider)),
            params={"task_id": provider_task_id},
        )
        response.raise_for_status()
    data = response.json()
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0):
        raise HTTPException(
            status_code=502,
            detail={"code": "AI_PROVIDER_ERROR", "message": base_resp.get("status_msg", "MiniMax 视频任务查询失败")},
        )
    return data


def _video_provider_for_task(db: Session, task: VideoTask) -> AIProvider:
    if task.provider_id:
        provider = db.query(AIProvider).filter(AIProvider.id == task.provider_id).first()
    else:
        provider = (
            db.query(AIProvider)
            .filter(AIProvider.provider_type == task.provider_type)
            .order_by(AIProvider.id.desc())
            .first()
        )
    if not provider:
        raise HTTPException(
            status_code=404,
            detail={"code": "VIDEO_PROVIDER_MISSING", "message": "该视频任务使用的模型服务配置已删除，无法继续查询或下载。"},
        )
    if provider.provider_type != task.provider_type:
        raise HTTPException(status_code=409, detail={"code": "VIDEO_PROVIDER_MISMATCH", "message": "视频任务绑定的服务商信息不一致。"})
    if not provider.api_key.strip():
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_REQUIRED", "message": "该视频任务使用的服务商缺少 API Key。"})
    if task.provider_type != "minimax":
        raise HTTPException(status_code=501, detail={"code": "VIDEO_ADAPTER_UNAVAILABLE", "message": "当前任务的视频服务商适配器尚未接入。"})
    ensure_provider_capability(provider, "video")
    if not provider.video_model.strip():
        raise HTTPException(status_code=400, detail={"code": "VIDEO_MODEL_REQUIRED", "message": "当前服务没有配置视频模型。"})
    return provider


async def generate_video_task(
    db: Session,
    prompt: str,
    source_image_path: str | None,
    duration_seconds: int,
    student: User | None = None,
    save_as_project: bool = True,
    provider_id: int | None = None,
    owner_teacher_id: int | None = None,
) -> tuple[VideoTask, Project | None]:
    user_id = student.id if student else None

    async def submit(provider: AIProvider) -> tuple[AIProvider, str]:
        ensure_provider_capability(provider, "video")
        if not provider.video_model.strip():
            raise HTTPException(
                status_code=400,
                detail={"code": "VIDEO_MODEL_REQUIRED", "message": "当前服务没有配置视频模型，请在教师设置中填写视频模型。"},
            )
        if provider.provider_type != "minimax":
            raise HTTPException(
                status_code=501,
                detail={"code": "VIDEO_ADAPTER_UNAVAILABLE", "message": f"{provider.name} 的视频运行适配器尚未接入。当前支持 MiniMax。"},
            )
        try:
            task_id = await _submit_minimax_video(provider, prompt, source_image_path, duration_seconds)
            return provider, task_id
        except HTTPException:
            log_usage(db, "video", provider.video_model, "failed", "MiniMax video submission failed", user_id, provider)
            raise
        except httpx.HTTPStatusError as exc:
            mapped = provider_http_exception(exc)
            log_usage(db, "video", provider.video_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
            raise mapped
        except Exception as exc:
            mapped = provider_request_exception(exc)
            log_usage(db, "video", provider.video_model, "failed", provider_failure_log_detail(mapped), user_id, provider)
            raise mapped

    if provider_id is not None:
        provider, provider_task_id = await submit(active_provider(db, "video", provider_id))
    else:
        provider, provider_task_id = await run_with_provider_fallback(db, "video", submit)
    project = None
    if save_as_project:
        summary = _video_project_summary(prompt, "submitted", provider_task_id, "", "", "")
        project = save_project(
            db,
            f"视频任务：{prompt[:24]}",
            "video",
            summary,
            "",
            student=student,
            owner_teacher_id=owner_teacher_id,
        )
    task = VideoTask(
        owner_teacher_id=resolve_owner_teacher_id(db, user_id, owner_teacher_id),
        provider_task_id=provider_task_id,
        provider_id=provider.id,
        provider_type=provider.provider_type,
        model=provider.video_model,
        prompt=prompt,
        source_image_path=source_image_path or "",
        duration_seconds=duration_seconds,
        status="submitted",
        timeout_at=now() + VIDEO_TASK_TIMEOUT,
        project_id=project.id if project else None,
        user_id=user_id,
        classroom_id=student.classroom_id if student else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    log_usage(db, "video", provider.video_model, "submitted", user_id=user_id, provider=provider)
    return task, project


async def refresh_video_task(db: Session, task: VideoTask) -> VideoTask:
    checked_at = now()
    if task.status in VIDEO_ACTIVE_STATUSES:
        task.timeout_at = task.timeout_at or ((task.created_at or checked_at) + VIDEO_TASK_TIMEOUT)
        if task.timeout_at <= checked_at:
            task.status = "timed_out"
            task.last_checked_at = checked_at
            task.error_message = "视频任务超过 30 分钟仍未完成，已停止自动查询。可以重试生成。"
            _sync_video_project(task)
            db.commit()
            db.refresh(task)
            log_usage(db, "video", task.model, "timed_out", task.error_message, task.user_id)
            return task

    if task.status in {"canceled", "failed", "timed_out"}:
        task.last_checked_at = checked_at
        db.commit()
        db.refresh(task)
        return task

    if task.status in {"success", "download_failed", "expired"}:
        if task.status == "expired" and not task.file_id:
            task.last_checked_at = checked_at
            db.commit()
            db.refresh(task)
            return task
        await _ensure_video_result(db, task)
        task.last_checked_at = checked_at
        _sync_video_project(task)
        db.commit()
        db.refresh(task)
        log_usage(db, "video_download", task.model, "success" if task.status == "success" else task.status, task.error_message, task.user_id)
        return task

    provider = _video_provider_for_task(db, task)
    try:
        data = await _query_minimax_video(provider, task.provider_task_id)
        task.status = _normalise_video_status(_pick_first_string(data, ["status", "task_status"], ["data.status", "data.task_status"])) or task.status
        task.last_checked_at = checked_at
        file_id = _pick_first_string(data, ["file_id"], ["data.file_id", "file.file_id"])
        direct_url = _pick_first_string(data, ["download_url", "url"], ["data.download_url", "data.url", "file.download_url", "file.url"])
        if file_id:
            task.file_id = file_id
        if direct_url:
            task.download_url = direct_url
            task.download_url_expires_at = _provider_expiration(data) or (checked_at + VIDEO_DOWNLOAD_URL_TTL)
        if task.status == "failed":
            task.error_message = _pick_first_string(data, ["error_message", "reason"], ["data.error_message", "data.reason"]) or "视频服务返回生成失败。"
        elif task.status == "success":
            task.error_message = ""
            await _ensure_video_result(db, task, provider)
        _sync_video_project(task)
        db.commit()
        db.refresh(task)
        log_usage(db, "video", task.model, "success" if task.status == "success" else "query", user_id=task.user_id)
        return task
    except HTTPException:
        log_usage(db, "video", task.model, "failed", "MiniMax video query failed", task.user_id)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "video", task.model, "failed", provider_failure_log_detail(mapped), task.user_id)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "video", task.model, "failed", provider_failure_log_detail(mapped), task.user_id)
        raise mapped


async def retry_video_task(db: Session, task: VideoTask) -> VideoTask:
    if task.status not in VIDEO_RETRYABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"code": "VIDEO_TASK_NOT_RETRYABLE", "message": "当前视频任务状态不能重试。"},
        )
    if task.retry_count >= VIDEO_MAX_RETRIES:
        raise HTTPException(
            status_code=409,
            detail={"code": "VIDEO_RETRY_LIMIT", "message": f"视频任务最多重试 {VIDEO_MAX_RETRIES} 次，请新建任务或联系教师。"},
        )

    can_retry_download = (
        task.status == "download_failed" and bool(task.download_url or task.file_id)
    ) or (task.status == "expired" and bool(task.file_id))
    if can_retry_download:
        provider = None
        if task.status == "expired" or not task.download_url or _video_link_expired(task):
            provider = _video_provider_for_task(db, task)
        task.retry_count += 1
        task.error_message = ""
        if task.status == "expired":
            task.download_url = ""
            task.download_url_expires_at = None
        await _ensure_video_result(db, task, provider)
        task.last_checked_at = now()
        _sync_video_project(task)
        db.commit()
        db.refresh(task)
        log_usage(db, "video_retry", task.model, task.status, task.error_message, task.user_id)
        return task

    provider = _video_provider_for_task(db, task)
    try:
        provider_task_id = await _submit_minimax_video(
            provider,
            task.prompt,
            task.source_image_path or None,
            task.duration_seconds,
        )
        task.retry_count += 1
        task.provider_task_id = provider_task_id
        task.provider_type = provider.provider_type
        task.model = provider.video_model
        task.status = "submitted"
        task.file_id = ""
        task.download_url = ""
        task.file_path = ""
        task.error_message = ""
        task.timeout_at = now() + VIDEO_TASK_TIMEOUT
        task.last_checked_at = None
        task.download_url_expires_at = None
        _sync_video_project(task)
        db.commit()
        db.refresh(task)
        log_usage(db, "video_retry", task.model, "submitted", user_id=task.user_id)
        return task
    except HTTPException:
        log_usage(db, "video_retry", task.model, "failed", "MiniMax video retry submission failed", task.user_id)
        raise
    except httpx.HTTPStatusError as exc:
        mapped = provider_http_exception(exc)
        log_usage(db, "video_retry", task.model, "failed", provider_failure_log_detail(mapped), task.user_id)
        raise mapped
    except Exception as exc:
        mapped = provider_request_exception(exc)
        log_usage(db, "video_retry", task.model, "failed", provider_failure_log_detail(mapped), task.user_id)
        raise mapped


def workflow_templates():
    return [
        {
            "id": "text_to_image",
            "name": "文字生成图片",
            "description": "先把学生创意扩写成提示词，再生成课堂作品图片。",
            "nodes": [
                {"id": "input", "type": "input", "label": "学生创意"},
                {"id": "text", "type": "text.generate", "label": "提示词优化"},
                {"id": "image", "type": "image.generate", "label": "生成图片"},
            ],
        },
        {
            "id": "idea_to_story",
            "name": "创意扩写成故事",
            "description": "把学生的简单创意扩写成适合课堂项目的故事文本。",
            "nodes": [
                {"id": "input", "type": "input", "label": "学生创意"},
                {"id": "story", "type": "text.generate", "label": "故事扩写"},
            ],
        },
        {
            "id": "code_explain",
            "name": "代码解释助手",
            "description": "把代码或报错解释成学生能理解的调试建议。",
            "nodes": [
                {"id": "input", "type": "input", "label": "代码/问题"},
                {"id": "explain", "type": "text.generate", "label": "解释与建议"},
            ],
        },
        {
            "id": "project_plan",
            "name": "项目步骤规划",
            "description": "把项目目标拆成课堂可执行的步骤和检查清单。",
            "nodes": [
                {"id": "input", "type": "input", "label": "项目目标"},
                {"id": "plan", "type": "text.generate", "label": "步骤规划"},
            ],
        },
    ]


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _write_bytes(target: Path, data: bytes) -> str:
    return put_bytes(
        "outputs",
        target.name,
        data,
        content_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
    )


def local_image_data_url(source_image_path: str) -> str:
    try:
        content = read_stored_bytes(source_image_path, maximum_bytes=10 * 1024 * 1024)
    except (FileNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "AI_INPUT_MISSING", "message": "参考图片文件已丢失，请重新上传。"})
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(Path(reference_name(source_image_path)).suffix.lower())
    if not media_type:
        raise HTTPException(status_code=400, detail={"code": "AI_INPUT_TYPE_INVALID", "message": "仅支持 PNG、JPEG 或 WebP 图片。"})
    return f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"


async def _download_video_result(url: str, task_id: int) -> str:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    if not response.content:
        raise ValueError("视频下载结果为空")
    if len(response.content) > 200 * 1024 * 1024:
        raise ValueError("视频文件超过 200 MB 本地保存上限")
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type or "application/json" in content_type:
        raise ValueError("视频下载链接返回了非视频内容")
    suffix = ".webm" if "webm" in content_type else ".mp4"
    return _write_bytes(OUTPUT_DIR / f"video-{task_id}{suffix}", response.content)


def _video_file_available(task: VideoTask) -> bool:
    if not task.file_path or task.file_path.startswith(("http://", "https://")):
        return False
    return object_exists(task.file_path)


def _sync_video_project(task: VideoTask) -> None:
    if not task.project:
        return
    task.project.summary = _video_project_summary(
        task.prompt,
        task.status,
        task.provider_task_id,
        task.file_id,
        task.download_url,
        task.error_message,
    )
    task.project.file_path = task.file_path if _video_file_available(task) else ""


def _mark_video_download_error(task: VideoTask, status: str, message: str) -> None:
    task.status = status
    task.error_message = message[:500]
    task.file_path = ""
    if status == "expired":
        task.download_url = ""
        task.download_url_expires_at = None


def _provider_value(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = data
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current not in (None, ""):
            return current
    return None


def _parse_provider_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        if numeric > 1_000_000_000:
            return datetime.fromtimestamp(numeric, BEIJING_TZ).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(BEIJING_TZ).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _provider_expiration(data: dict[str, Any]) -> datetime | None:
    absolute = _provider_value(
        data,
        "expires_at",
        "expire_time",
        "expiration",
        "data.expires_at",
        "data.expire_time",
        "data.expiration",
        "file.expires_at",
        "file.expire_time",
    )
    parsed = _parse_provider_datetime(absolute)
    if parsed:
        return parsed
    relative = _provider_value(data, "expires_in", "data.expires_in", "file.expires_in")
    try:
        seconds = max(1, min(int(relative), 7 * 24 * 60 * 60))
        return now() + timedelta(seconds=seconds)
    except (TypeError, ValueError):
        return None


def _video_link_expired(task: VideoTask) -> bool:
    return bool(task.download_url_expires_at and task.download_url_expires_at <= now())


async def _refresh_video_download_link(
    db: Session,
    task: VideoTask,
    provider: AIProvider | None = None,
) -> bool:
    if not task.file_id:
        _mark_video_download_error(task, "expired", "视频下载链接已过期，且服务未返回可用于刷新的文件 ID。")
        return False
    try:
        current_provider = provider or _video_provider_for_task(db, task)
        download_url, expires_at = await _retrieve_minimax_file_url(current_provider, task.file_id)
        if not download_url:
            _mark_video_download_error(task, "expired", "视频下载链接已过期，服务商未返回新的下载地址。")
            return False
        task.download_url = download_url
        task.download_url_expires_at = expires_at or (now() + VIDEO_DOWNLOAD_URL_TTL)
        return True
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        message = str(detail.get("message") or "当前配置无法刷新视频下载链接。")
        _mark_video_download_error(task, "expired", message)
        return False
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in VIDEO_EXPIRED_HTTP_STATUSES:
            _mark_video_download_error(task, "expired", "视频下载链接已经失效，服务商拒绝刷新。")
        else:
            _mark_video_download_error(task, "download_failed", "刷新视频下载链接时服务暂时不可用，请稍后重试。")
        return False
    except (httpx.RequestError, OSError, ValueError) as exc:
        _mark_video_download_error(task, "download_failed", f"刷新视频下载链接失败：{str(exc)[:180]}")
        return False


async def _ensure_video_result(
    db: Session,
    task: VideoTask,
    provider: AIProvider | None = None,
) -> None:
    if _video_file_available(task):
        task.status = "success"
        task.error_message = ""
        return
    task.file_path = ""

    refreshed = False
    if not task.download_url or _video_link_expired(task):
        refreshed = await _refresh_video_download_link(db, task, provider)
        if not refreshed:
            return

    try:
        task.file_path = await _download_video_result(task.download_url, task.id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in VIDEO_EXPIRED_HTTP_STATUSES and task.file_id and not refreshed:
            if await _refresh_video_download_link(db, task, provider):
                try:
                    task.file_path = await _download_video_result(task.download_url, task.id)
                except (httpx.HTTPError, OSError, ValueError) as retry_exc:
                    _mark_video_download_error(task, "download_failed", f"新链接下载视频失败：{str(retry_exc)[:180]}")
                    return
            else:
                return
        elif exc.response.status_code in VIDEO_EXPIRED_HTTP_STATUSES:
            _mark_video_download_error(task, "expired", "视频下载链接已过期且无法继续刷新。")
            return
        else:
            _mark_video_download_error(task, "download_failed", f"视频下载服务暂时不可用：HTTP {exc.response.status_code}")
            return
    except (httpx.RequestError, OSError, ValueError) as exc:
        _mark_video_download_error(task, "download_failed", f"视频已生成，但下载到本地失败：{str(exc)[:180]}")
        return

    task.status = "success"
    task.error_message = ""


def _size_to_aspect_ratio(size: str) -> str:
    if "x" not in size:
        return "1:1"
    width, height = size.split("x", 1)
    if width == height:
        return "1:1"
    return f"{width}:{height}"


def _extract_minimax_image_url(data: dict[str, Any]) -> str:
    candidates = [
        data.get("data", {}).get("image_urls") if isinstance(data.get("data"), dict) else None,
        data.get("data", {}).get("images") if isinstance(data.get("data"), dict) else None,
        data.get("image_urls"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list) and candidate:
            first = candidate[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url") or first.get("image_url") or ""
    return ""


async def _retrieve_minimax_file_url(provider: AIProvider, file_id: str) -> tuple[str, datetime | None]:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            "https://api.minimax.io/v1/files/retrieve",
            headers=_headers(provider_api_key(provider)),
            params={"file_id": file_id},
        )
        response.raise_for_status()
    data = response.json()
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0):
        raise HTTPException(
            status_code=502,
            detail={"code": "AI_PROVIDER_ERROR", "message": base_resp.get("status_msg", "MiniMax 文件链接获取失败")},
        )
    download_url = _pick_first_string(
        data,
        ["download_url", "url"],
        ["file.download_url", "file.url", "data.download_url", "data.url"],
    )
    return download_url, _provider_expiration(data)


def _pick_first_string(data: dict[str, Any], keys: list[str], dotted_paths: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value:
            return str(value)
    for path in dotted_paths:
        current: Any = data
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current:
            return str(current)
    return ""


def _normalise_video_status(raw_status: str) -> str:
    normalized = raw_status.strip().lower()
    if normalized in {"success", "succeeded", "completed", "complete", "finish", "finished"}:
        return "success"
    if normalized in {"fail", "failed", "error"}:
        return "failed"
    if normalized in {"queueing", "queued", "preparing", "processing", "running", "pending", "submitted"}:
        return "processing"
    return normalized or "submitted"


def _video_project_summary(prompt: str, status: str, provider_task_id: str, file_id: str, download_url: str, error_message: str) -> str:
    lines = [
        "# AI视频作品",
        "",
        f"## 视频描述\n{prompt}",
        "",
        "## 任务状态",
        f"- 状态：{status}",
        f"- 服务商任务ID：{provider_task_id or '无'}",
        f"- 文件ID：{file_id or '无'}",
    ]
    if download_url:
        lines.extend(["", "## 视频文件", f"[打开视频]({download_url})"])
    if error_message:
        lines.extend(["", "## 失败原因", error_message])
    return "\n".join(lines)


def provider_payload(provider: AIProvider | None) -> dict[str, Any]:
    if not provider:
        preset = get_provider_preset("openai_compatible")
        return {
            "configured": False,
            "id": None,
            "provider_type": preset["provider_type"],
            "capabilities": preset["capabilities"],
            "description": preset["description"],
            "enabled": False,
        }
    preset = get_provider_preset(provider.provider_type)
    secret_error = ""
    try:
        configured = bool(provider_api_key(provider).strip())
    except HTTPException:
        configured = False
        secret_error = "已保存的密钥无法解密，请重新输入。"
    return {
        "id": provider.id,
        "configured": configured,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "text_model": provider.text_model,
        "image_model": provider.image_model,
        "video_model": provider.video_model,
        "capabilities": preset["capabilities"],
        "description": preset["description"],
        "enabled": provider.enabled,
        "api_key_masked": "配置损坏" if secret_error else ("已配置" if configured else "未配置"),
        "api_key_error": secret_error,
        "last_test_status": provider.last_test_status or "untested",
        "last_test_message": provider.last_test_message or "",
        "last_tested_at": provider.last_tested_at.isoformat() if provider.last_tested_at else None,
        "created_at": provider.created_at.isoformat() if provider.created_at else None,
        "updated_at": provider.updated_at.isoformat() if provider.updated_at else None,
    }


def save_provider_routes(db: Session, routes: dict[str, list[int]]) -> dict[str, list[int]]:
    normalized: dict[str, list[int]] = {}
    for capability in PROVIDER_CAPABILITIES:
        raw_ids = routes.get(capability, [])
        ids: list[int] = []
        for provider_id in raw_ids:
            if provider_id not in ids:
                ids.append(provider_id)
        if len(ids) > 10:
            raise HTTPException(status_code=400, detail={"code": "PROVIDER_ROUTE_TOO_LARGE", "message": "每项能力最多配置 10 个模型服务。"})
        providers = db.query(AIProvider).filter(AIProvider.id.in_(ids)).all() if ids else []
        by_id = {provider.id: provider for provider in providers}
        for provider_id in ids:
            provider = by_id.get(provider_id)
            if not provider:
                raise HTTPException(status_code=404, detail={"code": "PROVIDER_NOT_FOUND", "message": f"路由中的服务商 {provider_id} 不存在。"})
            if not provider.enabled:
                raise HTTPException(status_code=400, detail={"code": "PROVIDER_DISABLED", "message": f"{provider.name} 已停用，不能加入运行路由。"})
            if not provider.api_key.strip():
                raise HTTPException(status_code=400, detail={"code": "PROVIDER_REQUIRED", "message": f"{provider.name} 尚未配置 API Key。"})
            provider_api_key(provider)
            ensure_provider_capability(provider, capability)
            if not provider_model(provider, capability):
                raise HTTPException(
                    status_code=400,
                    detail={"code": f"{capability.upper()}_MODEL_REQUIRED", "message": f"{provider.name} 尚未配置{capability}模型。"},
                )
        normalized[capability] = ids

    for capability, ids in normalized.items():
        route = db.query(AIProviderRoute).filter(AIProviderRoute.capability == capability).first()
        value = json.dumps(ids)
        if route:
            route.provider_ids_json = value
        else:
            db.add(AIProviderRoute(capability=capability, provider_ids_json=value))
    db.commit()
    return normalized


def remove_provider_from_routes(db: Session, provider_id: int) -> None:
    changed = False
    for route in db.query(AIProviderRoute).all():
        ids = _route_provider_ids(route)
        if provider_id in ids:
            route.provider_ids_json = json.dumps([item for item in ids if item != provider_id])
            changed = True
    if changed:
        db.commit()


def provider_management_payload(db: Session) -> dict[str, Any]:
    ensure_default_provider_routes(db)
    providers = db.query(AIProvider).order_by(AIProvider.created_at.desc(), AIProvider.id.desc()).all()
    usage_rows = (
        db.query(UsageLog.provider_id, func.count(UsageLog.id))
        .filter(UsageLog.provider_id.is_not(None))
        .group_by(UsageLog.provider_id)
        .all()
    )
    usage_counts = {provider_id: count for provider_id, count in usage_rows}
    route_map = {capability: provider_route_ids(db, capability) for capability in PROVIDER_CAPABILITIES}
    payloads = []
    for provider in providers:
        payload = provider_payload(provider)
        payload["usage_count"] = usage_counts.get(provider.id, 0)
        payload["routed_capabilities"] = [capability for capability, ids in route_map.items() if provider.id in ids]
        payloads.append(payload)
    return {"providers": payloads, "routes": route_map}


def provider_model_catalog_payload(db: Session) -> dict[str, Any]:
    providers = db.query(AIProvider).order_by(AIProvider.id.asc()).all()
    provider_states = {provider.id: provider_payload(provider) for provider in providers}
    items: list[dict[str, Any]] = []
    represented: set[tuple[int, str, str]] = set()

    def append_configured(provider: AIProvider, capability: str, model: str, display_name: str) -> None:
        state = provider_states[provider.id]
        available = bool(provider.enabled and state.get("configured"))
        reason = "" if available else ("服务已停用" if not provider.enabled else "API Key 未配置或不可用")
        items.append({
            "key": f"provider:{provider.id}:{capability}:{model}",
            "provider_id": provider.id,
            "provider_type": provider.provider_type,
            "provider_name": get_provider_preset(provider.provider_type)["name"],
            "configuration_name": provider.name,
            "capability": capability,
            "model": model,
            "display_name": display_name,
            "configured": True,
            "available": available,
            "reason": reason,
        })
        represented.add((provider.id, capability, model))

    for preset in list_provider_presets():
        preset_providers = [provider for provider in providers if provider.provider_type == preset["provider_type"]]
        for capability in PROVIDER_CAPABILITIES:
            catalog_models = preset.get("models", {}).get(capability, [])
            for catalog_model in catalog_models:
                model = str(catalog_model.get("id") or "").strip()
                if not model:
                    continue
                matches = [provider for provider in preset_providers if provider_model(provider, capability) == model]
                if matches:
                    for provider in matches:
                        append_configured(provider, capability, model, str(catalog_model.get("name") or model))
                    continue
                items.append({
                    "key": f"catalog:{preset['provider_type']}:{capability}:{model}",
                    "provider_id": None,
                    "provider_type": preset["provider_type"],
                    "provider_name": preset["name"],
                    "configuration_name": "",
                    "capability": capability,
                    "model": model,
                    "display_name": str(catalog_model.get("name") or model),
                    "configured": False,
                    "available": False,
                    "reason": "尚未在系统中配置",
                })

        for provider in preset_providers:
            for capability in PROVIDER_CAPABILITIES:
                model = provider_model(provider, capability)
                if model and (provider.id, capability, model) not in represented:
                    append_configured(provider, capability, model, model)

    return {"models": items}


def provider_status_payload(db: Session) -> dict[str, Any]:
    configured_capabilities: list[str] = []
    routed_providers: list[AIProvider] = []
    for capability in PROVIDER_CAPABILITIES:
        try:
            candidates = provider_candidates(db, capability)
        except HTTPException:
            continue
        valid = []
        for provider in candidates:
            try:
                if provider_api_key(provider).strip() and provider_model(provider, capability):
                    valid.append(provider)
            except HTTPException:
                continue
        if valid:
            configured_capabilities.append(capability)
            routed_providers.extend(valid)
    unique = {provider.id: provider for provider in routed_providers}
    primary = routed_providers[0] if routed_providers else None
    return {
        "configured": bool(configured_capabilities),
        "name": "多模型路由" if len(unique) > 1 else (primary.name if primary else "未配置"),
        "provider_type": "multi" if len(unique) > 1 else (primary.provider_type if primary else ""),
        "capabilities": configured_capabilities,
        "provider_count": len(unique),
    }


def to_project_dict(project: Project) -> dict[str, Any]:
    file_exists, file_status = project_file_status(project.file_path)
    return {
        "id": project.id,
        "owner_teacher_id": project.owner_teacher_id,
        "title": project.title,
        "project_type": project.project_type,
        "user_id": project.user_id,
        "student_archived": bool(project.user and project.user.archived_at),
        "classroom_id": project.classroom_id,
        "owner_name": project.owner_name,
        "summary": project.summary,
        "file_path": project.file_path,
        "lifecycle_status": project.lifecycle_status,
        "moderation_status": project.moderation_status,
        "moderation_reason": project.moderation_reason,
        "moderation_log_id": project.moderation_log_id,
        "archived_at": format_optional_beijing_datetime(project.archived_at),
        "trashed_at": format_optional_beijing_datetime(project.trashed_at),
        "file_exists": file_exists,
        "file_status": file_status,
        "created_at": format_beijing_datetime(project.created_at),
        "updated_at": format_beijing_datetime(project.updated_at),
    }


def project_file_status(file_path: str) -> tuple[bool, str]:
    if not file_path.strip():
        return False, "none"
    if file_path.startswith(("http://", "https://")):
        return False, "remote"
    exists = object_exists(file_path)
    return exists, "ok" if exists else "missing"


def asset_file_status(file_path: str) -> tuple[bool, str]:
    if not file_path.strip():
        return False, "none"
    if file_path.startswith(("http://", "https://")):
        return False, "remote"
    exists = object_exists(file_path)
    return exists, "ok" if exists else "missing"


def to_asset_dict(asset: Asset) -> dict[str, Any]:
    file_exists, file_status = asset_file_status(asset.file_path)
    return {
        "id": asset.id,
        "owner_teacher_id": asset.owner_teacher_id,
        "project_id": asset.project_id,
        "classroom_id": asset.classroom_id,
        "classroom_name": asset.classroom.name if asset.classroom else "",
        "lesson_id": asset.lesson_id,
        "lesson_title": asset.lesson.title if asset.lesson else "",
        "course_title": asset.lesson.course.title if asset.lesson and asset.lesson.course else "",
        "asset_type": asset.asset_type,
        "file_path": asset.file_path,
        "metadata_json": asset.metadata_json,
        "metadata": safe_json_value(asset.metadata_json, {}),
        "original_name": asset.original_name,
        "mime_type": asset.mime_type,
        "file_size": asset.file_size,
        "file_extension": asset.file_extension,
        "checksum_sha256": asset.checksum_sha256,
        "safety_status": asset.safety_status,
        "file_exists": file_exists,
        "file_status": file_status,
        "created_at": format_beijing_datetime(asset.created_at),
    }


def to_classroom_dict(classroom: Classroom) -> dict[str, Any]:
    return {
        "id": classroom.id,
        "owner_teacher_id": classroom.owner_teacher_id,
        "name": classroom.name,
        "grade_level": classroom.grade_level,
        "grade_level_label": "混合学龄" if classroom.grade_level == "mixed" else school_stage_label(classroom.grade_level),
        "created_at": format_beijing_datetime(classroom.created_at),
    }


def to_user_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "role": user.role,
        "username": user.username,
        "classroom_id": user.classroom_id,
        "classroom_name": user.classroom.name if user.classroom else "",
        "password_change_required": user.password_change_required,
        "registered_at": format_optional_beijing_datetime(user.registered_at),
        "last_login_at": format_optional_beijing_datetime(user.last_login_at),
        "age_level": user.age_level,
        "school_stage_label": school_stage_label(user.age_level),
        "active": user.active,
        "account_status": "archived" if user.archived_at else ("active" if user.active else "disabled"),
        "archived_at": format_optional_beijing_datetime(user.archived_at),
        "archived_classroom_name": user.archived_classroom_name,
        "created_at": format_beijing_datetime(user.created_at),
    }


def to_course_dict(course: Course) -> dict[str, Any]:
    return {
        "id": course.id,
        "owner_teacher_id": course.owner_teacher_id,
        "classroom_id": course.classroom_id,
        "classroom_name": course.classroom.name if course.classroom else "",
        "title": course.title,
        "description": course.description,
        "package_version": course.package_version,
        "author": course.author,
        "age_range": course.age_range,
        "cover_path": course.cover_path,
        "dependencies": safe_json_value(course.dependencies_json, []),
        "checklist": safe_json_value(course.checklist_json, []),
        "status": course.status,
        "starts_at": format_optional_beijing_datetime(course.starts_at),
        "ends_at": format_optional_beijing_datetime(course.ends_at),
        "created_at": format_beijing_datetime(course.created_at),
        "updated_at": format_beijing_datetime(course.updated_at),
    }


def to_lesson_dict(lesson: Lesson) -> dict[str, Any]:
    return {
        "id": lesson.id,
        "owner_teacher_id": lesson.owner_teacher_id,
        "course_id": lesson.course_id,
        "course_title": lesson.course.title if lesson.course else "",
        "title": lesson.title,
        "content": lesson.content,
        "order_index": lesson.order_index,
        "created_at": format_beijing_datetime(lesson.created_at),
    }


def to_task_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "owner_teacher_id": task.owner_teacher_id,
        "classroom_id": task.classroom_id,
        "lesson_id": task.lesson_id,
        "course_schedule_id": task.course_schedule_id,
        "target_student_id": task.target_student_id,
        "task_kind": task.task_kind,
        "lesson_title": task.lesson.title if task.lesson else "",
        "course_id": task.course_schedule.course_id if task.course_schedule else (task.lesson.course_id if task.lesson else None),
        "course_title": task.course_schedule.course.title if task.course_schedule and task.course_schedule.course else (task.lesson.course.title if task.lesson and task.lesson.course else ""),
        "package_id": task.course_schedule.course.package_id if task.course_schedule and task.course_schedule.course else None,
        "package_title": task.course_schedule.course.package.title if task.course_schedule and task.course_schedule.course and task.course_schedule.course.package else "",
        "title": task.title,
        "instructions": task.instructions,
        "tool_scope": task.tool_scope,
        "status": task.status,
        "starts_at": format_optional_beijing_datetime(task.starts_at),
        "due_at": format_optional_beijing_datetime(task.due_at),
        "rubric": safe_json_value(task.rubric_json, [{"criterion": "完成度", "max_score": 100}]),
        "max_score": sum(int(item.get("max_score") or 0) for item in safe_json_value(task.rubric_json, [{"max_score": 100}]) if isinstance(item, dict)),
        "created_at": format_beijing_datetime(task.created_at),
    }


def to_submission_dict(submission: TaskSubmission) -> dict[str, Any]:
    return {
        "id": submission.id,
        "owner_teacher_id": submission.owner_teacher_id,
        "task_id": submission.task_id,
        "task_title": submission.task.title if submission.task else "",
        "project_id": submission.project_id,
        "project_title": submission.project.title if submission.project else "",
        "user_id": submission.user_id,
        "student_name": submission.user.name if submission.user else "",
        "student_archived": bool(submission.user and submission.user.archived_at),
        "classroom_id": submission.classroom_id,
        "classroom_name": submission.classroom.name if submission.classroom else "",
        "status": submission.status,
        "feedback": submission.feedback,
        "score": submission.score,
        "version_count": submission.version_count,
        "is_late": submission.is_late,
        "is_featured": submission.is_featured,
        "rubric": safe_json_value(submission.rubric_snapshot_json, []),
        "max_score": submission.max_score_snapshot or 100,
        "due_at": format_optional_beijing_datetime(submission.task.due_at if submission.task else None),
        "created_at": format_beijing_datetime(submission.created_at),
        "updated_at": format_beijing_datetime(submission.updated_at),
    }


def to_submission_version_dict(version: SubmissionVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "submission_id": version.submission_id,
        "version_number": version.version_number,
        "project_id": version.project_id,
        "project_title": version.project_title,
        "project_summary": version.project_summary,
        "project_file_path": version.project_file_path,
        "is_late": version.is_late,
        "created_at": format_beijing_datetime(version.created_at),
    }


def to_moderation_log_dict(log: ModerationLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "owner_teacher_id": log.owner_teacher_id,
        "user_id": log.user_id,
        "classroom_id": log.classroom_id,
        "input_text": log.input_text,
        "content_stage": log.content_stage,
        "passed": log.passed,
        "reason": log.reason,
        "resource_type": log.resource_type,
        "resource_path": log.resource_path,
        "status": log.status,
        "project_id": log.project_id,
        "review_note": log.review_note,
        "reviewed_at": format_optional_beijing_datetime(log.reviewed_at),
        "created_at": format_beijing_datetime(log.created_at),
    }


def to_video_task_dict(task: VideoTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "owner_teacher_id": task.owner_teacher_id,
        "provider_task_id": task.provider_task_id,
        "provider_id": task.provider_id,
        "provider_type": task.provider_type,
        "model": task.model,
        "prompt": task.prompt,
        "source_image_path": task.source_image_path,
        "duration_seconds": task.duration_seconds,
        "status": task.status,
        "file_id": task.file_id,
        "download_url": task.download_url,
        "file_path": task.file_path,
        "error_message": task.error_message,
        "retry_count": task.retry_count,
        "max_retries": VIDEO_MAX_RETRIES,
        "can_retry": task.status in VIDEO_RETRYABLE_STATUSES and task.retry_count < VIDEO_MAX_RETRIES,
        "file_available": _video_file_available(task),
        "timeout_at": format_optional_beijing_datetime(task.timeout_at),
        "last_checked_at": format_optional_beijing_datetime(task.last_checked_at),
        "download_url_expires_at": format_optional_beijing_datetime(task.download_url_expires_at),
        "project_id": task.project_id,
        "user_id": task.user_id,
        "classroom_id": task.classroom_id,
        "student_name": task.user.name if task.user else "",
        "classroom_name": task.classroom.name if task.classroom else "",
        "created_at": format_beijing_datetime(task.created_at),
        "updated_at": format_beijing_datetime(task.updated_at),
    }


def format_beijing_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING_TZ).isoformat()
    return value.astimezone(BEIJING_TZ).isoformat()


def format_optional_beijing_datetime(value: datetime | None) -> str:
    return format_beijing_datetime(value) if value else ""


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def safe_json_value(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback
