from __future__ import annotations

import codecs
import mimetypes
from pathlib import Path, PurePosixPath
import zipfile


MAX_SUBMISSION_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_SUBMISSION_EXTENSIONS = (
    ".md",
    ".txt",
    ".pdf",
    ".zip",
    ".sb3",
    ".py",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".json",
    ".csv",
    ".docx",
    ".pptx",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".mp4",
)
ALLOWED_SUBMISSION_EXTENSIONS = frozenset(DEFAULT_SUBMISSION_EXTENSIONS)
TEXT_SUBMISSION_EXTENSIONS = frozenset({
    ".md", ".txt", ".py", ".html", ".css", ".js", ".ts", ".json", ".csv",
})
IMAGE_SUBMISSION_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
VIDEO_SUBMISSION_EXTENSIONS = frozenset({".mp4"})
ZIP_SUBMISSION_EXTENSIONS = frozenset({".zip", ".sb3", ".docx", ".pptx", ".xlsx"})
EXECUTABLE_ARCHIVE_EXTENSIONS = frozenset({".exe", ".dll", ".msi", ".com", ".scr", ".bat", ".cmd", ".ps1", ".vbs"})


class SubmissionFileValidationError(ValueError):
    pass


def normalize_submission_extensions(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        extension = str(value or "").strip().lower()
        if extension and not extension.startswith("."):
            extension = f".{extension}"
        if extension in ALLOWED_SUBMISSION_EXTENSIONS and extension not in normalized:
            normalized.append(extension)
    if not normalized:
        raise SubmissionFileValidationError("请至少选择一种系统支持的作品文件类型。")
    return normalized


def submission_project_type(extension: str) -> str:
    if extension in IMAGE_SUBMISSION_EXTENSIONS:
        return "image"
    if extension in VIDEO_SUBMISSION_EXTENSIONS:
        return "video"
    return "uploaded_file"


def _validate_text_file(path: Path) -> str:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    sample_parts: list[str] = []
    sample_length = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(256 * 1024):
                if b"\x00" in chunk:
                    raise SubmissionFileValidationError("文本或代码文件不能包含二进制空字节。")
                decoded = decoder.decode(chunk)
                if sample_length < 200_000:
                    remaining = 200_000 - sample_length
                    sample_parts.append(decoded[:remaining])
                    sample_length += len(decoded[:remaining])
            decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise SubmissionFileValidationError("文本、代码和 Markdown 文件必须使用 UTF-8 编码。") from exc
    return "".join(sample_parts)


def _validate_zip_file(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise SubmissionFileValidationError("压缩包或工程文件结构无效。")
    with zipfile.ZipFile(path, "r") as archive:
        entries = archive.infolist()
        if len(entries) > 2_000:
            raise SubmissionFileValidationError("压缩包内文件数量不能超过 2000 个。")
        for entry in entries:
            entry_path = PurePosixPath(entry.filename.replace("\\", "/"))
            if entry_path.is_absolute() or ".." in entry_path.parts:
                raise SubmissionFileValidationError("压缩包包含不安全的文件路径。")
            if Path(entry_path.name).suffix.lower() in EXECUTABLE_ARCHIVE_EXTENSIONS:
                raise SubmissionFileValidationError("压缩包不能包含可执行程序或系统脚本。")


def validate_submission_file(
    path: Path,
    original_name: str,
    allowed_extensions: list[str] | tuple[str, ...],
    maximum_bytes: int,
) -> dict[str, object]:
    filename = Path(original_name or "").name.strip()
    if not filename or len(filename) > 255:
        raise SubmissionFileValidationError("文件名为空或超过 255 个字符。")
    extension = Path(filename).suffix.lower()
    normalized_allowed = normalize_submission_extensions(allowed_extensions)
    if extension not in normalized_allowed:
        raise SubmissionFileValidationError(f"当前课程不接受 {extension or '无扩展名'} 文件。")
    size = path.stat().st_size
    if size <= 0:
        raise SubmissionFileValidationError("不能提交空文件。")
    if size > maximum_bytes:
        raise SubmissionFileValidationError(f"文件不能超过 {maximum_bytes // 1024 // 1024} MB。")

    with path.open("rb") as handle:
        header = handle.read(64)
    if header.startswith(b"MZ") or header.startswith(b"\x7fELF"):
        raise SubmissionFileValidationError("不能提交可执行程序。")

    text_sample = ""
    if extension in TEXT_SUBMISSION_EXTENSIONS:
        text_sample = _validate_text_file(path)
    elif extension in ZIP_SUBMISSION_EXTENSIONS:
        _validate_zip_file(path)
    elif extension == ".pdf" and not header.startswith(b"%PDF-"):
        raise SubmissionFileValidationError("PDF 文件头无效。")
    elif extension == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SubmissionFileValidationError("PNG 文件头无效。")
    elif extension in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
        raise SubmissionFileValidationError("JPEG 文件头无效。")
    elif extension == ".webp" and not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
        raise SubmissionFileValidationError("WebP 文件头无效。")
    elif extension == ".mp4" and b"ftyp" not in header[:32]:
        raise SubmissionFileValidationError("MP4 文件头无效。")

    return {
        "original_name": filename,
        "extension": extension,
        "file_size": size,
        "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "project_type": submission_project_type(extension),
        "text_sample": text_sample,
    }
