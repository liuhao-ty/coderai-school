from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

from backend.app.db import DATA_DIR


MAX_COURSE_PACKAGE_BYTES = 2 * 1024 * 1024
DANGEROUS_CONTENT = re.compile(
    r"<\s*(script|iframe|object|embed|link|meta)\b|javascript\s*:|data\s*:\s*text/html|vbscript\s*:",
    re.IGNORECASE,
)
PATH_TRAVERSAL = re.compile(r"(^|[\\/])\.\.([\\/]|$)")


def _reject_text(value: str, field: str) -> None:
    if "\x00" in value or any(ord(char) < 32 and char not in "\r\n\t" for char in value):
        raise HTTPException(status_code=400, detail={"code": "PACKAGE_CONTROL_CHARACTERS", "message": f"{field} 包含非法控制字符。"})
    if DANGEROUS_CONTENT.search(value):
        raise HTTPException(status_code=400, detail={"code": "PACKAGE_DANGEROUS_CONTENT", "message": f"{field} 包含不可信脚本或危险链接。"})


def _reject_path_traversal(value: str, field: str) -> None:
    decoded_dots = re.sub("%2e", ".", value, flags=re.IGNORECASE)
    if PATH_TRAVERSAL.search(decoded_dots) or value.startswith(("\\\\", "//")):
        raise HTTPException(status_code=400, detail={"code": "PACKAGE_PATH_TRAVERSAL", "message": f"{field} 包含路径穿越或网络共享路径。"})


def validate_course_package(payload: object) -> None:
    raw = payload.model_dump(mode="json")
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_COURSE_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail={"code": "COURSE_PACKAGE_TOO_LARGE", "message": "课程包不能超过 2 MB。"})

    for field in ("title", "description", "author", "age_range"):
        _reject_text(str(raw.get(field) or ""), f"课程{field}")
    cover_path = str(raw.get("cover_path") or "").strip()
    if cover_path:
        _reject_text(cover_path, "课程封面")
        _reject_path_traversal(cover_path, "课程封面")
        parsed = urlparse(cover_path)
        if parsed.scheme and parsed.scheme != "https":
            raise HTTPException(status_code=400, detail={"code": "COURSE_COVER_SCHEME_INVALID", "message": "课程封面只允许 HTTPS 链接或受管素材路径。"})
        if not parsed.scheme:
            path = Path(cover_path)
            if path.is_absolute():
                try:
                    path.resolve().relative_to((DATA_DIR / "assets").resolve())
                except (ValueError, OSError):
                    raise HTTPException(status_code=400, detail={"code": "COURSE_COVER_PATH_FORBIDDEN", "message": "本地课程封面必须位于受管素材目录。"})

    for index, dependency in enumerate(raw.get("dependencies") or []):
        if not isinstance(dependency, dict) or set(dependency) - {"name", "type", "required"}:
            raise HTTPException(status_code=400, detail={"code": "COURSE_DEPENDENCY_FIELDS_INVALID", "message": "依赖素材包含不支持的字段。"})
        name = str(dependency.get("name") or "").strip()
        _reject_text(name, f"依赖素材 {index + 1}")
        _reject_path_traversal(name, f"依赖素材 {index + 1}")
    for index, item in enumerate(raw.get("checklist") or []):
        _reject_text(str(item), f"验收项 {index + 1}")
    for index, lesson in enumerate(raw.get("lessons") or []):
        _reject_text(str(lesson.get("title") or ""), f"课时 {index + 1} 标题")
        _reject_text(str(lesson.get("content") or ""), f"课时 {index + 1} 内容")
    for index, task in enumerate(raw.get("tasks") or []):
        _reject_text(str(task.get("title") or ""), f"任务 {index + 1} 标题")
        _reject_text(str(task.get("instructions") or ""), f"任务 {index + 1} 说明")
