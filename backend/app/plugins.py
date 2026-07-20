from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app.audit import record_teacher_audit
from backend.app.auth import require_admin, require_student_or_teacher
from backend.app.db import DATA_DIR, get_db
from backend.app.extensions import BUILTIN_PLUGINS
from backend.app.licensing import ensure_license_feature, get_license_status
from backend.app.models import AppSetting, Task, TeacherSession
from backend.app.privacy import ensure_student_ai_consent
from backend.app.schemas import PluginEnabledRequest, PluginRunRequest, TrustedPluginPublisherRequest
from backend.app.services import generate_text, run_moderation, save_project, to_project_dict


APP_VERSION = "0.1.0"
PLUGIN_PROTOCOL_VERSION = 1
PLUGIN_ROOT = DATA_DIR / "plugins"
TRUSTED_PUBLISHERS_SETTING_KEY = "plugin_trusted_publishers"
MAX_PLUGIN_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_PLUGIN_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_PLUGIN_FILES = 50
MAX_PLUGIN_MANIFEST_BYTES = 64 * 1024
MAX_PLUGIN_RUNTIME_BYTES = 128 * 1024
MAX_COMPRESSION_RATIO = 100
ALLOWED_PERMISSIONS = {"ai.text", "projects.write"}
ALLOWED_TEXT_MODES = {"story", "polish", "code_explain", "prompt_refine"}
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
PLUGIN_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
KEY_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
TOOL_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
SEMVER_PATTERN = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?")
DANGEROUS_TEXT = re.compile(r"<\s*(script|iframe|object|embed)\b|javascript\s*:|vbscript\s*:", re.IGNORECASE)


router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _error(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _b64url_decode(value: str, label: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise _error("PLUGIN_SIGNATURE_INVALID", f"插件{label}不是有效的 Base64URL。")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise _error("PLUGIN_SIGNATURE_INVALID", f"插件{label}无法解码。") from exc


def _safe_text(value: Any, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        raise _error("PLUGIN_FIELD_TOO_LONG", f"{field}不能超过 {max_length} 个字符。")
    if "\x00" in text or any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        raise _error("PLUGIN_CONTROL_CHARACTERS", f"{field}包含非法控制字符。")
    if DANGEROUS_TEXT.search(text):
        raise _error("PLUGIN_DANGEROUS_CONTENT", f"{field}包含脚本或危险协议。")
    return text


def _safe_plugin_id(value: Any) -> str:
    plugin_id = str(value or "").strip().lower()
    if not PLUGIN_ID_PATTERN.fullmatch(plugin_id) or ".." in plugin_id:
        raise _error("PLUGIN_ID_INVALID", "插件 ID 只能使用小写字母、数字、点、横线和下划线，且不能包含连续点。")
    return plugin_id


def _safe_package_path(value: Any, field: str = "插件文件路径") -> str:
    raw = str(value or "").strip()
    if (
        not raw
        or "\\" in raw
        or "//" in raw
        or ":" in raw
        or raw.startswith("/")
        or not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", raw)
    ):
        raise _error("PLUGIN_PATH_INVALID", f"{field}必须是包内 POSIX 相对路径。")
    path = PurePosixPath(raw)
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if path.is_absolute() or any(
        part in {"", ".", ".."}
        or part.endswith((".", " "))
        or part.split(".", 1)[0].lower() in reserved
        for part in path.parts
    ):
        raise _error("PLUGIN_PATH_INVALID", f"{field}包含路径穿越或空目录段。")
    if len(path.parts) > 8 or len(raw) > 240:
        raise _error("PLUGIN_PATH_INVALID", f"{field}层级或长度超过限制。")
    return path.as_posix()


def _semver(value: Any, field: str) -> tuple[int, int, int]:
    text = str(value or "").strip()
    match = SEMVER_PATTERN.fullmatch(text)
    if not match:
        raise _error("PLUGIN_VERSION_INVALID", f"{field}必须使用 major.minor.patch 语义化版本。")
    return tuple(int(match.group(index)) for index in range(1, 4))


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _error("PLUGIN_JSON_DUPLICATE_KEY", f"{label}包含重复字段：{key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("PLUGIN_JSON_INVALID", f"{label}不是有效的 UTF-8 JSON。") from exc
    if not isinstance(value, dict):
        raise _error("PLUGIN_JSON_INVALID", f"{label}必须是 JSON 对象。")
    return value


def _strict_fields(raw: dict[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    extra = set(raw) - allowed
    missing = required - set(raw)
    if extra:
        raise _error("PLUGIN_FIELDS_INVALID", f"{label}包含不支持的字段：{sorted(extra)}")
    if missing:
        raise _error("PLUGIN_FIELDS_INVALID", f"{label}缺少字段：{sorted(missing)}")


def normalize_plugin_manifest(raw: dict[str, Any], require_signature: bool = True) -> dict[str, Any]:
    allowed = {
        "schema_version", "id", "name", "description", "category", "version", "publisher",
        "compatibility", "permissions", "entry", "files", "signature",
    }
    required = allowed if require_signature else allowed - {"signature"}
    _strict_fields(raw, allowed, required, "插件 Manifest")
    try:
        schema_version = int(raw.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise _error("PLUGIN_PROTOCOL_UNSUPPORTED", "插件协议版本无效。") from exc
    if schema_version != PLUGIN_PROTOCOL_VERSION:
        raise _error("PLUGIN_PROTOCOL_UNSUPPORTED", f"当前只支持插件协议版本 {PLUGIN_PROTOCOL_VERSION}。")

    plugin_id = _safe_plugin_id(raw.get("id"))
    name = _safe_text(raw.get("name"), "插件名称", 120)
    description = _safe_text(raw.get("description"), "插件说明", 2_000)
    category = str(raw.get("category") or "").strip()
    if category != "ai_tool":
        raise _error("PLUGIN_CATEGORY_UNSUPPORTED", "声明式插件协议 v1 仅支持 ai_tool 分类。")
    version = str(raw.get("version") or "").strip()
    _semver(version, "插件版本")

    publisher = raw.get("publisher")
    if not isinstance(publisher, dict):
        raise _error("PLUGIN_PUBLISHER_INVALID", "插件发布者声明不合法。")
    _strict_fields(publisher, {"key_id", "name"}, {"key_id", "name"}, "插件发布者")
    key_id = str(publisher.get("key_id") or "").strip().lower()
    if not KEY_ID_PATTERN.fullmatch(key_id) or ".." in key_id:
        raise _error("PLUGIN_PUBLISHER_INVALID", "插件发布者 Key ID 不合法。")
    publisher_name = _safe_text(publisher.get("name"), "插件发布者名称", 120)

    compatibility = raw.get("compatibility")
    if not isinstance(compatibility, dict):
        raise _error("PLUGIN_COMPATIBILITY_INVALID", "插件兼容版本声明不合法。")
    _strict_fields(
        compatibility,
        {"min_app_version", "max_app_version"},
        {"min_app_version", "max_app_version"},
        "插件兼容版本",
    )
    min_app_version = str(compatibility.get("min_app_version") or "").strip()
    max_app_version = str(compatibility.get("max_app_version") or "").strip()
    if _semver(min_app_version, "最低应用版本") > _semver(max_app_version, "最高应用版本"):
        raise _error("PLUGIN_COMPATIBILITY_INVALID", "最低应用版本不能高于最高应用版本。")

    raw_permissions = raw.get("permissions")
    if not isinstance(raw_permissions, list) or len(raw_permissions) > 20:
        raise _error("PLUGIN_PERMISSION_INVALID", "插件权限必须是最多 20 项的数组。")
    permissions = sorted({str(item).strip().lower() for item in raw_permissions})
    unsupported_permissions = set(permissions) - ALLOWED_PERMISSIONS
    if unsupported_permissions:
        raise _error(
            "PLUGIN_PERMISSION_UNSUPPORTED",
            f"声明式沙箱不支持权限：{sorted(unsupported_permissions)}；当前不允许网络、进程或任意文件访问。",
        )

    entry = raw.get("entry")
    if not isinstance(entry, dict):
        raise _error("PLUGIN_ENTRY_INVALID", "插件入口声明不合法。")
    _strict_fields(entry, {"type", "path"}, {"type", "path"}, "插件入口")
    if entry.get("type") != "declarative":
        raise _error("PLUGIN_ENTRY_UNSUPPORTED", "插件协议 v1 只执行声明式入口，不执行 Python、JavaScript 或本机程序。")
    entry_path = _safe_package_path(entry.get("path"), "插件入口路径")
    if not entry_path.endswith(".json"):
        raise _error("PLUGIN_ENTRY_INVALID", "声明式插件入口必须是 JSON 文件。")

    raw_files = raw.get("files")
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= MAX_PLUGIN_FILES:
        raise _error("PLUGIN_FILES_INVALID", f"插件必须声明 1 到 {MAX_PLUGIN_FILES} 个文件。")
    files: list[dict[str, Any]] = []
    paths: set[str] = set()
    for index, item in enumerate(raw_files, start=1):
        if not isinstance(item, dict):
            raise _error("PLUGIN_FILES_INVALID", f"插件文件 {index} 声明不合法。")
        _strict_fields(item, {"path", "size", "sha256"}, {"path", "size", "sha256"}, f"插件文件 {index}")
        path = _safe_package_path(item.get("path"), f"插件文件 {index} 路径")
        if path == "manifest.json" or path in paths:
            raise _error("PLUGIN_FILES_INVALID", f"插件文件路径重复或保留：{path}")
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError) as exc:
            raise _error("PLUGIN_FILES_INVALID", f"插件文件 {path} 大小不合法。") from exc
        checksum = str(item.get("sha256") or "").strip().lower()
        if not 0 <= size <= MAX_PLUGIN_EXPANDED_BYTES or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise _error("PLUGIN_FILES_INVALID", f"插件文件 {path} 的大小或 SHA-256 不合法。")
        paths.add(path)
        files.append({"path": path, "size": size, "sha256": checksum})
    files.sort(key=lambda item: item["path"])
    if entry_path not in paths:
        raise _error("PLUGIN_ENTRY_INVALID", "插件入口文件未在 files 清单中声明。")

    normalized = {
        "schema_version": schema_version,
        "id": plugin_id,
        "name": name,
        "description": description,
        "category": category,
        "version": version,
        "publisher": {"key_id": key_id, "name": publisher_name},
        "compatibility": {"min_app_version": min_app_version, "max_app_version": max_app_version},
        "permissions": permissions,
        "entry": {"type": "declarative", "path": entry_path},
        "files": files,
    }
    if require_signature:
        signature = raw.get("signature")
        if not isinstance(signature, dict):
            raise _error("PLUGIN_SIGNATURE_INVALID", "插件签名声明不合法。")
        _strict_fields(signature, {"algorithm", "value"}, {"algorithm", "value"}, "插件签名")
        if signature.get("algorithm") != "Ed25519":
            raise _error("PLUGIN_SIGNATURE_INVALID", "插件只支持 Ed25519 签名。")
        signature_value = str(signature.get("value") or "").strip()
        if len(_b64url_decode(signature_value, "签名")) != 64:
            raise _error("PLUGIN_SIGNATURE_INVALID", "Ed25519 插件签名长度不正确。")
        normalized["signature"] = {"algorithm": "Ed25519", "value": signature_value}
    return normalized


def canonical_plugin_manifest(raw: dict[str, Any]) -> bytes:
    unsigned = dict(raw)
    unsigned.pop("signature", None)
    normalized = normalize_plugin_manifest(unsigned, require_signature=False)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def normalize_plugin_runtime(raw: dict[str, Any], permissions: list[str]) -> dict[str, Any]:
    _strict_fields(raw, {"protocol_version", "tools"}, {"protocol_version", "tools"}, "插件运行入口")
    try:
        protocol_version = int(raw.get("protocol_version"))
    except (TypeError, ValueError) as exc:
        raise _error("PLUGIN_RUNTIME_INVALID", "插件运行协议版本无效。") from exc
    if protocol_version != PLUGIN_PROTOCOL_VERSION:
        raise _error("PLUGIN_RUNTIME_INVALID", f"当前只支持声明式运行协议版本 {PLUGIN_PROTOCOL_VERSION}。")
    raw_tools = raw.get("tools")
    if not isinstance(raw_tools, list) or not 1 <= len(raw_tools) <= 20:
        raise _error("PLUGIN_RUNTIME_INVALID", "插件必须声明 1 到 20 个工具。")
    tools: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw_tool in enumerate(raw_tools, start=1):
        if not isinstance(raw_tool, dict):
            raise _error("PLUGIN_RUNTIME_INVALID", f"插件工具 {index} 必须是对象。")
        allowed = {"id", "name", "description", "action", "prompt_template", "mode", "save_project", "project_title"}
        required = {"id", "name", "action", "prompt_template"}
        _strict_fields(raw_tool, allowed, required, f"插件工具 {index}")
        tool_id = str(raw_tool.get("id") or "").strip().lower()
        if not TOOL_ID_PATTERN.fullmatch(tool_id) or ".." in tool_id or tool_id in ids:
            raise _error("PLUGIN_TOOL_INVALID", f"插件工具 {index} 的 ID 不合法或重复。")
        ids.add(tool_id)
        name = _safe_text(raw_tool.get("name"), f"插件工具 {index} 名称", 120)
        description = _safe_text(raw_tool.get("description"), f"插件工具 {index} 说明", 1_000)
        action = str(raw_tool.get("action") or "").strip()
        if action != "text.generate":
            raise _error("PLUGIN_ACTION_UNSUPPORTED", "声明式协议 v1 仅支持 text.generate 动作。")
        if "ai.text" not in permissions:
            raise _error("PLUGIN_PERMISSION_REQUIRED", f"插件工具 {tool_id} 使用 text.generate，必须声明 ai.text 权限。")
        prompt_template = _safe_text(raw_tool.get("prompt_template"), f"插件工具 {index} 提示词模板", 10_000)
        if "{{prompt}}" not in prompt_template or re.findall(r"\{\{([^{}]+)\}\}", prompt_template) != ["prompt"]:
            raise _error("PLUGIN_TEMPLATE_INVALID", f"插件工具 {tool_id} 必须且只能包含一次 {{{{prompt}}}} 占位符。")
        mode = str(raw_tool.get("mode") or "story").strip()
        if mode not in ALLOWED_TEXT_MODES:
            raise _error("PLUGIN_TOOL_INVALID", f"插件工具 {tool_id} 的文字模式不受支持。")
        save_as_project = bool(raw_tool.get("save_project", True))
        if save_as_project and "projects.write" not in permissions:
            raise _error("PLUGIN_PERMISSION_REQUIRED", f"插件工具 {tool_id} 保存作品时必须声明 projects.write 权限。")
        project_title = _safe_text(raw_tool.get("project_title") or name, f"插件工具 {index} 作品标题", 120)
        tools.append(
            {
                "id": tool_id,
                "name": name,
                "description": description,
                "action": action,
                "prompt_template": prompt_template,
                "mode": mode,
                "save_project": save_as_project,
                "project_title": project_title,
            }
        )
    return {"protocol_version": protocol_version, "tools": tools}


def _publisher_records(db: Session) -> list[dict[str, str]]:
    setting = db.query(AppSetting).filter(AppSetting.key == TRUSTED_PUBLISHERS_SETTING_KEY).first()
    if not setting or not setting.value.strip():
        return []
    try:
        value = json.loads(setting.value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    records = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key_id = str(item.get("key_id") or "").strip().lower()
        name = str(item.get("name") or "").strip()
        public_key = str(item.get("public_key") or "").strip()
        created_at = str(item.get("created_at") or "")
        if KEY_ID_PATTERN.fullmatch(key_id) and name and public_key:
            records.append({"key_id": key_id, "name": name, "public_key": public_key, "created_at": created_at})
    return sorted(records, key=lambda item: item["key_id"])


def _save_publishers(db: Session, records: list[dict[str, str]]) -> None:
    value = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    setting = db.query(AppSetting).filter(AppSetting.key == TRUSTED_PUBLISHERS_SETTING_KEY).first()
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=TRUSTED_PUBLISHERS_SETTING_KEY, value=value))
    db.commit()


def _public_key(value: str) -> Ed25519PublicKey:
    raw = _b64url_decode(value, "发布者公钥")
    if len(raw) != 32:
        raise _error("PLUGIN_PUBLISHER_KEY_INVALID", "发布者 Ed25519 公钥长度不正确。")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise _error("PLUGIN_PUBLISHER_KEY_INVALID", "发布者 Ed25519 公钥无效。") from exc


def publisher_payload(record: dict[str, str]) -> dict[str, str]:
    raw = _b64url_decode(record["public_key"], "发布者公钥")
    return {
        **record,
        "fingerprint": "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("="),
    }


def add_trusted_publisher(db: Session, payload: dict[str, Any]) -> dict[str, str]:
    key_id = str(payload.get("key_id") or "").strip().lower()
    name = _safe_text(payload.get("name"), "发布者名称", 120)
    if not KEY_ID_PATTERN.fullmatch(key_id) or ".." in key_id:
        raise _error("PLUGIN_PUBLISHER_INVALID", "发布者 Key ID 不合法。")
    public_key = str(payload.get("public_key") or "").strip()
    _public_key(public_key)
    records = _publisher_records(db)
    existing = next((item for item in records if item["key_id"] == key_id), None)
    if existing and existing["public_key"] != public_key:
        raise _error("PLUGIN_PUBLISHER_KEY_CONFLICT", "相同 Key ID 已绑定不同公钥；请先移除旧发布者。", 409)
    record = {
        "key_id": key_id,
        "name": name,
        "public_key": public_key,
        "created_at": existing["created_at"] if existing else datetime.now(BEIJING_TZ).isoformat(),
    }
    records = [item for item in records if item["key_id"] != key_id] + [record]
    _save_publishers(db, sorted(records, key=lambda item: item["key_id"]))
    return record


def _verify_manifest_signature(db: Session, manifest: dict[str, Any]) -> dict[str, str]:
    key_id = manifest["publisher"]["key_id"]
    publisher = next((item for item in _publisher_records(db) if item["key_id"] == key_id), None)
    if not publisher:
        raise _error("PLUGIN_PUBLISHER_UNTRUSTED", f"插件发布者 {key_id} 尚未加入本机信任列表。", 403)
    signature = _b64url_decode(manifest["signature"]["value"], "签名")
    try:
        _public_key(publisher["public_key"]).verify(signature, canonical_plugin_manifest(manifest))
    except InvalidSignature as exc:
        raise _error("PLUGIN_SIGNATURE_INVALID", "插件签名无效，Manifest 或文件清单可能已被修改。") from exc
    return publisher


def _ensure_compatible(manifest: dict[str, Any]) -> None:
    current = _semver(APP_VERSION, "应用版本")
    minimum = _semver(manifest["compatibility"]["min_app_version"], "最低应用版本")
    maximum = _semver(manifest["compatibility"]["max_app_version"], "最高应用版本")
    if not minimum <= current <= maximum:
        raise _error(
            "PLUGIN_INCOMPATIBLE",
            f"插件支持 {manifest['compatibility']['min_app_version']} 至 {manifest['compatibility']['max_app_version']}，当前应用为 {APP_VERSION}。",
            409,
        )


def _verify_manifest_files(
    db: Session,
    manifest: dict[str, Any],
    reader: Callable[[str], bytes],
) -> tuple[dict[str, Any], dict[str, str]]:
    publisher = _verify_manifest_signature(db, manifest)
    _ensure_compatible(manifest)
    for item in manifest["files"]:
        content = reader(item["path"])
        if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise _error("PLUGIN_FILE_INTEGRITY_FAILED", f"插件文件校验失败：{item['path']}")
    runtime_bytes = reader(manifest["entry"]["path"])
    if len(runtime_bytes) > MAX_PLUGIN_RUNTIME_BYTES:
        raise _error("PLUGIN_RUNTIME_TOO_LARGE", "声明式插件入口不能超过 128 KB。")
    runtime = normalize_plugin_runtime(_json_object(runtime_bytes, "插件运行入口"), manifest["permissions"])
    return runtime, publisher


def inspect_plugin_package(content: bytes, db: Session) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes], dict[str, str]]:
    if not content:
        raise _error("PLUGIN_PACKAGE_EMPTY", "插件安装包为空。")
    if len(content) > MAX_PLUGIN_PACKAGE_BYTES:
        raise _error("PLUGIN_PACKAGE_TOO_LARGE", "插件安装包不能超过 5 MB。", 413)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_PLUGIN_FILES + 1:
                raise _error("PLUGIN_PACKAGE_FILES_INVALID", f"插件安装包最多包含 {MAX_PLUGIN_FILES + 1} 个文件。")
            names: set[str] = set()
            total_expanded = 0
            for info in infos:
                if info.is_dir():
                    raise _error("PLUGIN_PACKAGE_FILES_INVALID", "插件安装包不能包含显式目录项。")
                name = _safe_package_path(info.filename)
                if name in names:
                    raise _error("PLUGIN_PACKAGE_FILES_INVALID", f"插件安装包包含重复文件：{name}")
                names.add(name)
                if info.flag_bits & 0x1:
                    raise _error("PLUGIN_PACKAGE_ENCRYPTED", "插件安装包不能包含加密文件。")
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise _error("PLUGIN_PACKAGE_SYMLINK", "插件安装包不能包含符号链接。")
                total_expanded += info.file_size
                if total_expanded > MAX_PLUGIN_EXPANDED_BYTES:
                    raise _error("PLUGIN_PACKAGE_EXPANDED_TOO_LARGE", "插件解压后不能超过 20 MB。", 413)
                if info.file_size > 0 and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
                    raise _error("PLUGIN_PACKAGE_COMPRESSION_RATIO", "插件安装包压缩率异常，疑似压缩炸弹。")
            if "manifest.json" not in names:
                raise _error("PLUGIN_MANIFEST_MISSING", "插件安装包缺少 manifest.json。")
            manifest_bytes = archive.read("manifest.json")
            if len(manifest_bytes) > MAX_PLUGIN_MANIFEST_BYTES:
                raise _error("PLUGIN_MANIFEST_TOO_LARGE", "插件 Manifest 不能超过 64 KB。", 413)
            manifest = normalize_plugin_manifest(_json_object(manifest_bytes, "插件 Manifest"))
            expected_names = {"manifest.json", *(item["path"] for item in manifest["files"])}
            if names != expected_names:
                raise _error(
                    "PLUGIN_PACKAGE_FILES_MISMATCH",
                    f"插件包文件必须与签名清单完全一致；多余 {sorted(names - expected_names)}，缺少 {sorted(expected_names - names)}。",
                )
            try:
                files = {item["path"]: archive.read(item["path"]) for item in manifest["files"]}
            except (KeyError, RuntimeError) as exc:
                raise _error("PLUGIN_PACKAGE_INVALID", "插件包文件无法安全读取。") from exc
    except zipfile.BadZipFile as exc:
        raise _error("PLUGIN_PACKAGE_INVALID", "插件安装包不是有效的 ZIP 文件。") from exc
    runtime, publisher = _verify_manifest_files(db, manifest, lambda path: files[path])
    return manifest, runtime, files, publisher


def _state_path(plugin_dir: Path) -> Path:
    return plugin_dir / "state.json"


def _read_state(plugin_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(plugin_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "installed_at": str(raw.get("installed_at") or ""),
    }


def _write_state(plugin_dir: Path, state: dict[str, Any]) -> None:
    target = _state_path(plugin_dir)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(target)


def _installed_plugin(db: Session, plugin_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    plugin_id = _safe_plugin_id(plugin_id)
    plugin_dir = PLUGIN_ROOT / plugin_id
    if not plugin_dir.is_dir() or plugin_dir.is_symlink():
        raise _error("PLUGIN_NOT_FOUND", "插件不存在或安装目录不安全。", 404)
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise _error("PLUGIN_CORRUPT", "插件 Manifest 已丢失或不安全。")
    manifest = normalize_plugin_manifest(_json_object(manifest_path.read_bytes(), "插件 Manifest"))
    if manifest["id"] != plugin_id:
        raise _error("PLUGIN_CORRUPT", "插件目录与 Manifest ID 不一致。")
    allowed_files = {"manifest.json", "state.json", *(item["path"] for item in manifest["files"])}
    actual_files: set[str] = set()
    for path in plugin_dir.rglob("*"):
        if path.is_symlink():
            raise _error("PLUGIN_CORRUPT", "插件目录包含符号链接。")
        if path.is_file():
            actual_files.add(path.relative_to(plugin_dir).as_posix())
    if actual_files != allowed_files:
        raise _error("PLUGIN_CORRUPT", "插件安装目录包含未签名文件或缺少已签名文件。")

    def read_file(relative: str) -> bytes:
        path = plugin_dir.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(plugin_dir.resolve())
        except (OSError, ValueError) as exc:
            raise _error("PLUGIN_CORRUPT", f"插件文件路径不安全：{relative}") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise _error("PLUGIN_CORRUPT", f"插件文件不存在或不安全：{relative}")
        return resolved.read_bytes()

    runtime, publisher = _verify_manifest_files(db, manifest, read_file)
    return manifest, runtime, _read_state(plugin_dir), publisher


def _plugin_payload(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    state: dict[str, Any],
    publisher: dict[str, str],
) -> dict[str, Any]:
    enabled = bool(state["enabled"])
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "description": manifest["description"],
        "category": manifest["category"],
        "version": manifest["version"],
        "entry": f"declarative://{manifest['id']}",
        "capabilities": ["text"],
        "permissions": manifest["permissions"],
        "enabled": enabled,
        "builtin": False,
        "status": "ready" if enabled else "disabled",
        "signed": True,
        "runtime_protocol": f"declarative-v{PLUGIN_PROTOCOL_VERSION}",
        "publisher": publisher_payload(publisher),
        "compatibility": manifest["compatibility"],
        "tools": [
            {key: tool[key] for key in ("id", "name", "description", "action")}
            for tool in runtime["tools"]
        ],
        "installed_at": state["installed_at"],
        "can_uninstall": True,
    }


def _broken_plugin_payload(plugin_id: str, exc: Exception, legacy: bool = False) -> dict[str, Any]:
    detail = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else {}
    return {
        "id": plugin_id,
        "name": plugin_id,
        "description": detail.get("message", "插件无法验证。"),
        "category": "custom",
        "version": "",
        "entry": "",
        "capabilities": [],
        "permissions": [],
        "enabled": False,
        "builtin": False,
        "status": "legacy_manifest" if legacy else "invalid",
        "signed": False,
        "runtime_protocol": "none",
        "publisher": None,
        "compatibility": None,
        "tools": [],
        "installed_at": "",
        "can_uninstall": True,
        "error_code": "PLUGIN_LEGACY_MANIFEST" if legacy else detail.get("code", "PLUGIN_CORRUPT"),
    }


def list_plugins(db: Session) -> list[dict[str, Any]]:
    PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
    plugins = [
        {
            **item,
            "permissions": [],
            "signed": True,
            "runtime_protocol": "builtin",
            "publisher": {"key_id": "coderai-core", "name": "CoderAI", "fingerprint": "built-in"},
            "compatibility": {"min_app_version": APP_VERSION, "max_app_version": APP_VERSION},
            "tools": [],
            "installed_at": "",
            "can_uninstall": False,
        }
        for item in BUILTIN_PLUGINS
    ]
    builtin_ids = {item["id"] for item in plugins}
    for path in sorted(PLUGIN_ROOT.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.name in builtin_ids:
            continue
        if path.is_file() and path.suffix == ".json":
            plugins.append(_broken_plugin_payload(path.stem, _error("PLUGIN_LEGACY_MANIFEST", "旧版 Manifest 不可运行，请卸载后安装签名插件包。"), legacy=True))
            continue
        if not path.is_dir():
            continue
        try:
            manifest, runtime, state, publisher = _installed_plugin(db, path.name)
            plugins.append(_plugin_payload(manifest, runtime, state, publisher))
        except Exception as exc:
            plugins.append(_broken_plugin_payload(path.name, exc))
    return plugins


def install_plugin_package(db: Session, content: bytes) -> dict[str, Any]:
    manifest, _, files, _ = inspect_plugin_package(content, db)
    if manifest["id"] in {item["id"] for item in BUILTIN_PLUGINS}:
        raise _error("PLUGIN_BUILTIN_READONLY", "签名插件不能覆盖内置插件。")
    PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
    target = PLUGIN_ROOT / manifest["id"]
    previous_state = {"enabled": True, "installed_at": ""}
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise _error("PLUGIN_INSTALL_TARGET_UNSAFE", "现有插件安装路径不安全，请先手工检查。")
        old_manifest = normalize_plugin_manifest(_json_object((target / "manifest.json").read_bytes(), "现有插件 Manifest"))
        if _semver(manifest["version"], "新插件版本") <= _semver(old_manifest["version"], "现有插件版本"):
            raise _error("PLUGIN_VERSION_NOT_NEWER", "升级包版本必须高于当前已安装版本。", 409)
        previous_state = _read_state(target)
    legacy_path = PLUGIN_ROOT / f"{manifest['id']}.json"
    if legacy_path.exists():
        raise _error("PLUGIN_LEGACY_CONFLICT", "存在同 ID 的旧版 Manifest，请先在界面卸载。", 409)

    staging = Path(tempfile.mkdtemp(prefix=f".install-{manifest['id']}-", dir=PLUGIN_ROOT))
    backup = PLUGIN_ROOT / f".backup-{manifest['id']}-{datetime.now(BEIJING_TZ):%Y%m%d%H%M%S%f}"
    try:
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        for relative, content_bytes in files.items():
            path = staging.joinpath(*PurePosixPath(relative).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content_bytes)
        _write_state(
            staging,
            {
                "enabled": previous_state["enabled"],
                "installed_at": previous_state["installed_at"] or datetime.now(BEIJING_TZ).isoformat(),
            },
        )
        if target.exists():
            target.replace(backup)
        try:
            staging.replace(target)
        except Exception:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    installed_manifest, runtime, state, publisher = _installed_plugin(db, manifest["id"])
    return _plugin_payload(installed_manifest, runtime, state, publisher)


def set_plugin_enabled(db: Session, plugin_id: str, enabled: bool) -> dict[str, Any]:
    manifest, runtime, state, publisher = _installed_plugin(db, plugin_id)
    state["enabled"] = bool(enabled)
    _write_state(PLUGIN_ROOT / manifest["id"], state)
    return _plugin_payload(manifest, runtime, state, publisher)


def uninstall_plugin(plugin_id: str) -> bool:
    plugin_id = _safe_plugin_id(plugin_id)
    if plugin_id in {item["id"] for item in BUILTIN_PLUGINS}:
        raise _error("PLUGIN_BUILTIN_READONLY", "内置插件不能卸载。")
    target = PLUGIN_ROOT / plugin_id
    legacy = PLUGIN_ROOT / f"{plugin_id}.json"
    removed = False
    if target.is_symlink():
        target.unlink()
        removed = True
    elif target.is_dir():
        try:
            target.resolve().relative_to(PLUGIN_ROOT.resolve())
        except ValueError as exc:
            raise _error("PLUGIN_INSTALL_TARGET_UNSAFE", "插件卸载路径不安全。") from exc
        shutil.rmtree(target)
        removed = True
    if legacy.is_file() and not legacy.is_symlink():
        legacy.unlink()
        removed = True
    if not removed:
        raise _error("PLUGIN_NOT_FOUND", "插件不存在。", 404)
    return True


def _student_text_allowed(db: Session, student: Any) -> bool:
    tasks = db.query(Task).filter((Task.classroom_id.is_(None)) | (Task.classroom_id == student.classroom_id)).all()
    if not tasks:
        return True
    return any("text" in {scope.strip() for scope in task.tool_scope.split(",")} for task in tasks)


def _catalog(db: Session, identity: dict) -> dict[str, Any]:
    license_status = get_license_status(db)
    available = license_status["valid"] and license_status["usable"] and "plugins" in license_status["features"]
    if not available:
        return {"available": False, "message": "当前授权未开放自定义插件。", "plugins": []}
    if identity["role"] == "student" and not _student_text_allowed(db, identity["student"]):
        return {"available": True, "message": "当前课堂未开放文字工具。", "plugins": []}
    ready = [item for item in list_plugins(db) if not item["builtin"] and item["enabled"] and item["status"] == "ready"]
    return {"available": True, "message": "", "plugins": ready}


@router.get("")
def get_plugins(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    return {"plugins": list_plugins(db)}


@router.get("/catalog")
def plugin_catalog(identity: dict = Depends(require_student_or_teacher), db: Session = Depends(get_db)):
    return _catalog(db, identity)


@router.get("/publishers")
def get_publishers(_: TeacherSession = Depends(require_admin), db: Session = Depends(get_db)):
    return {"publishers": [publisher_payload(item) for item in _publisher_records(db)]}


@router.post("/publishers")
def trust_publisher(
    payload: TrustedPluginPublisherRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ensure_license_feature(db, "plugins")
    record = add_trusted_publisher(db, payload.model_dump())
    public = publisher_payload(record)
    record_teacher_audit(
        db,
        teacher,
        "plugin.publisher.trusted",
        target_type="plugin_publisher",
        target_id=record["key_id"],
        summary=f"信任插件发布者：{record['name']}",
        details={"fingerprint": public["fingerprint"]},
    )
    return {"publisher": public}


@router.delete("/publishers/{key_id}")
def untrust_publisher(
    key_id: str,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    key_id = str(key_id).strip().lower()
    records = _publisher_records(db)
    existing = next((item for item in records if item["key_id"] == key_id), None)
    if not existing:
        raise _error("PLUGIN_PUBLISHER_NOT_FOUND", "受信任发布者不存在。", 404)
    _save_publishers(db, [item for item in records if item["key_id"] != key_id])
    record_teacher_audit(
        db,
        teacher,
        "plugin.publisher.untrusted",
        target_type="plugin_publisher",
        target_id=key_id,
        summary=f"移除插件发布者信任：{existing['name']}",
    )
    return {"removed": True, "key_id": key_id}


@router.post("/install")
async def install_plugin(
    file: UploadFile = File(...),
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ensure_license_feature(db, "plugins")
    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        content.extend(chunk)
        if len(content) > MAX_PLUGIN_PACKAGE_BYTES:
            raise _error("PLUGIN_PACKAGE_TOO_LARGE", "插件安装包不能超过 5 MB。", 413)
    plugin = install_plugin_package(db, bytes(content))
    record_teacher_audit(
        db,
        teacher,
        "plugin.installed",
        target_type="plugin",
        target_id=plugin["id"],
        summary=f"安装签名插件：{plugin['name']} {plugin['version']}",
        details={
            "publisher": plugin["publisher"]["key_id"],
            "permissions": plugin["permissions"],
            "runtime_protocol": plugin["runtime_protocol"],
        },
    )
    return {"plugin": plugin}


@router.put("/{plugin_id}/enabled")
def update_plugin_enabled(
    plugin_id: str,
    payload: PluginEnabledRequest,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.enabled:
        ensure_license_feature(db, "plugins")
    plugin = set_plugin_enabled(db, plugin_id, payload.enabled)
    record_teacher_audit(
        db,
        teacher,
        "plugin.enabled" if payload.enabled else "plugin.disabled",
        target_type="plugin",
        target_id=plugin["id"],
        summary=f"{'启用' if payload.enabled else '停用'}插件：{plugin['name']}",
    )
    return {"plugin": plugin}


@router.delete("/{plugin_id}")
def delete_plugin(
    plugin_id: str,
    teacher: TeacherSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    uninstall_plugin(plugin_id)
    record_teacher_audit(
        db,
        teacher,
        "plugin.uninstalled",
        target_type="plugin",
        target_id=plugin_id,
        summary=f"卸载插件：{plugin_id}",
    )
    return {"removed": True, "plugin_id": plugin_id}


@router.post("/{plugin_id}/tools/{tool_id}/run")
async def run_plugin_tool(
    plugin_id: str,
    tool_id: str,
    payload: PluginRunRequest,
    identity: dict = Depends(require_student_or_teacher),
    db: Session = Depends(get_db),
):
    ensure_license_feature(db, "plugins")
    manifest, runtime, state, publisher = _installed_plugin(db, plugin_id)
    if not state["enabled"]:
        raise _error("PLUGIN_DISABLED", "插件已被教师停用。", 409)
    tool = next((item for item in runtime["tools"] if item["id"] == tool_id), None)
    if not tool:
        raise _error("PLUGIN_TOOL_NOT_FOUND", "插件工具不存在。", 404)
    student = identity.get("student")
    owner_teacher_id = None if student else identity["teacher"].id
    if identity["role"] == "student":
        ensure_student_ai_consent(db, student)
        if not _student_text_allowed(db, student):
            raise _error("TOOL_NOT_ALLOWED", "教师当前没有为你的课堂开放文字生成。", 403)
    run_moderation(db, payload.prompt, user_id=student.id if student else None, owner_teacher_id=owner_teacher_id)
    rendered_prompt = tool["prompt_template"].replace("{{prompt}}", payload.prompt)
    if len(rendered_prompt) > 12_000:
        raise _error("PLUGIN_INPUT_TOO_LARGE", "插件模板展开后的输入超过 12000 个字符。", 413)
    run_moderation(db, rendered_prompt, user_id=student.id if student else None, owner_teacher_id=owner_teacher_id)
    age_level = student.age_level if student else payload.age_level
    text = await generate_text(
        db,
        rendered_prompt,
        tool["mode"],
        age_level,
        user_id=student.id if student else None,
    )
    project = None
    if payload.save_project and tool["save_project"]:
        project = save_project(
            db,
            f"{tool['project_title']}：{payload.prompt[:24]}",
            "plugin_text",
            text,
            student=student,
            owner_teacher_id=owner_teacher_id,
        )
    return {
        "text": text,
        "project": to_project_dict(project) if project else None,
        "plugin": {
            "id": manifest["id"],
            "name": manifest["name"],
            "version": manifest["version"],
            "publisher": publisher["name"],
        },
        "tool": {"id": tool["id"], "name": tool["name"]},
    }


__all__ = [
    "APP_VERSION",
    "PLUGIN_PROTOCOL_VERSION",
    "PLUGIN_ROOT",
    "TRUSTED_PUBLISHERS_SETTING_KEY",
    "canonical_plugin_manifest",
    "inspect_plugin_package",
    "list_plugins",
    "normalize_plugin_manifest",
    "normalize_plugin_runtime",
    "router",
]
