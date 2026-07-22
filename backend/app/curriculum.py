from __future__ import annotations

import hashlib
import mimetypes
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile

from backend.app.db import DATA_DIR, SessionLocal
from backend.app.models import CourseMaterial
from backend.app.storage import STORAGE_BACKEND, delete_object, is_object_reference, materialize, put_bytes, put_file
from backend.app.tenancy import current_organization_code, current_organization_id, organization_context


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


def store_curriculum_bytes(
    package_id: int,
    course_id: int,
    filename: str,
    content: bytes,
    content_type: str,
) -> str:
    if STORAGE_BACKEND == "local":
        target = curriculum_course_dir(package_id, course_id) / filename
        target.write_bytes(content)
        return str(target)
    return put_bytes(
        f"curriculum/{package_id}/{course_id}",
        filename,
        content,
        content_type=content_type,
        stable_name=filename,
    )


@contextmanager
def materialize_curriculum(reference: str, *, suffix: str = ""):
    if is_object_reference(reference):
        with materialize(reference, suffix=suffix) as path:
            yield path
        return
    path = Path(reference).resolve()
    path.relative_to(CURRICULUM_DIR.resolve())
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(reference)
    yield path


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


def convert_slides_material(
    material_id: int,
    organization_id: int | None = None,
    organization_code: str = "",
) -> None:
    tenant_id = organization_id or current_organization_id()
    tenant_code = organization_code or current_organization_code()
    with organization_context(tenant_id, tenant_code):
        _convert_slides_material(material_id)


def _convert_slides_material(material_id: int) -> None:
    db = SessionLocal()
    try:
        material = db.get(CourseMaterial, material_id)
        if not material or material.kind != "slides":
            return
        executable = find_libreoffice()
        if not material.source_path:
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
        with materialize_curriculum(material.source_path, suffix=".pptx") as source:
            with tempfile.TemporaryDirectory(prefix="coderai-ppt-") as temp_dir:
                kwargs: dict = {
                    "args": [str(executable), "--headless", "--convert-to", "pdf", "--outdir", temp_dir, str(source)],
                    "capture_output": True,
                    "text": True,
                    "encoding": "mbcs" if os.name == "nt" else "utf-8",
                    "errors": "replace",
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
                preview_reference = put_file(
                    f"curriculum/{material.course.package_id}/{material.course_id}",
                    converted,
                    filename="slides.pdf",
                    content_type="application/pdf",
                    stable_name="slides.pdf",
                )
                material = db.get(CourseMaterial, material_id)
                if not material:
                    delete_object(preview_reference)
                    return
                old_preview = material.preview_path
                material.preview_path = preview_reference
                material.conversion_status = "ready"
                material.conversion_error = ""
                db.commit()
                if old_preview and old_preview != preview_reference:
                    delete_object(old_preview)
    except Exception as exc:
        db.rollback()
        material = db.get(CourseMaterial, material_id)
        if material:
            material.conversion_status = "failed"
            material.conversion_error = str(exc)[:1000]
            db.commit()
    finally:
        db.close()
