from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from backend.app.db import DATA_DIR, SessionLocal
from backend.app.models import CourseMaterial


CURRICULUM_DIR = DATA_DIR / "curriculum"
CURRICULUM_DIR.mkdir(parents=True, exist_ok=True)

MATERIAL_KINDS = {"slides", "starter_markdown", "result_markdown"}
MAX_COURSE_MATERIAL_BYTES = 50 * 1024 * 1024
DEFAULT_LIBREOFFICE_PATHS = (
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
)


def curriculum_course_dir(package_id: int, course_id: int) -> Path:
    target = CURRICULUM_DIR / str(package_id) / str(course_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def find_libreoffice() -> Path | None:
    configured = os.environ.get("CODERAI_LIBREOFFICE_PATH", "").strip()
    candidates = ([Path(configured)] if configured else []) + list(DEFAULT_LIBREOFFICE_PATHS)
    command = shutil.which("soffice") or shutil.which("libreoffice")
    if command:
        candidates.append(Path(command))
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def libreoffice_status() -> dict:
    executable = find_libreoffice()
    return {
        "available": executable is not None,
        "path": str(executable) if executable else "",
        "message": "LibreOffice 转换器可用" if executable else "未找到 LibreOffice，请安装后配置 CODERAI_LIBREOFFICE_PATH。",
    }


def validate_course_material(kind: str, filename: str, content: bytes) -> dict:
    if kind not in MATERIAL_KINDS:
        raise ValueError("课程资料类型不受支持。")
    if not content:
        raise ValueError("上传文件为空。")
    if len(content) > MAX_COURSE_MATERIAL_BYTES:
        raise ValueError("课程资料不能超过 50 MB。")
    extension = Path(filename).suffix.lower()
    expected = ".pptx" if kind == "slides" else ".md"
    if extension != expected:
        raise ValueError(f"{kind} 资料必须使用 {expected} 文件。")
    if extension == ".pptx" and not content.startswith(b"PK"):
        raise ValueError("PPTX 文件内容与后缀不一致。")
    if extension == ".md":
        if b"\x00" in content[:8192]:
            raise ValueError("Markdown 文件包含二进制内容。")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Markdown 文件必须使用 UTF-8 编码。") from exc
    return {
        "extension": extension,
        "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "file_size": len(content),
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
    }


def convert_slides_material(material_id: int) -> None:
    db = SessionLocal()
    try:
        material = db.get(CourseMaterial, material_id)
        if not material or material.kind != "slides":
            return
        source = Path(material.source_path)
        executable = find_libreoffice()
        if not source.is_file():
            material.conversion_status = "failed"
            material.conversion_error = "PPTX 原件不存在。"
            db.commit()
            return
        if not executable:
            material.conversion_status = "failed"
            material.conversion_error = "未找到 LibreOffice 转换器。"
            db.commit()
            return
        material.conversion_status = "processing"
        material.conversion_error = ""
        db.commit()
        with tempfile.TemporaryDirectory(prefix="coderai-ppt-") as temp_dir:
            kwargs: dict = {
                "args": [str(executable), "--headless", "--convert-to", "pdf", "--outdir", temp_dir, str(source)],
                "capture_output": True,
                "text": True,
                "timeout": int(os.environ.get("CODERAI_PPT_CONVERT_TIMEOUT_SECONDS", "120")),
                "check": False,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(**kwargs)
            converted = Path(temp_dir) / f"{source.stem}.pdf"
            if result.returncode != 0 or not converted.is_file():
                detail = (result.stderr or result.stdout or "LibreOffice 未生成PDF预览。").strip()
                raise RuntimeError(detail[-1000:])
            content = converted.read_bytes()
            if not content.startswith(b"%PDF"):
                raise RuntimeError("LibreOffice 生成的预览文件不是有效PDF。")
            preview = source.with_suffix(".pdf")
            shutil.copy2(converted, preview)
            material = db.get(CourseMaterial, material_id)
            if not material:
                return
            old_preview = Path(material.preview_path) if material.preview_path else None
            material.preview_path = str(preview)
            material.conversion_status = "ready"
            material.conversion_error = ""
            db.commit()
            if old_preview and old_preview != preview and old_preview.is_file():
                old_preview.unlink(missing_ok=True)
    except Exception as exc:
        db.rollback()
        material = db.get(CourseMaterial, material_id)
        if material:
            material.conversion_status = "failed"
            material.conversion_error = str(exc)[:1000]
            db.commit()
    finally:
        db.close()
