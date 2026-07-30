from __future__ import annotations

import re
from typing import Any


FIELD_MARKER_RE = re.compile(r"\{\{field\b(?P<attributes>[^{}]*)\}\}")
FIELD_START_RE = re.compile(r"\{\{field\b")
ATTRIBUTE_RE = re.compile(r'\s*([a-z][a-z0-9_-]*)(?:\s*=\s*"([^"]*)")?')
FIELD_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
FIELD_TYPES = {"text", "textarea", "radio", "checkbox"}
FIELD_ATTRIBUTES = {"id", "type", "label", "required", "options"}
MAX_FIELDS = 100
MAX_ANSWER_LENGTH = 20_000
MAX_TOTAL_ANSWER_LENGTH = 200_000


class CourseFieldValidationError(ValueError):
    pass


def _parse_attributes(raw: str) -> dict[str, str | bool]:
    attributes: dict[str, str | bool] = {}
    position = 0
    while position < len(raw):
        match = ATTRIBUTE_RE.match(raw, position)
        if not match:
            if raw[position:].strip():
                raise CourseFieldValidationError("工程包填写字段包含无法识别的属性格式。")
            break
        key, value = match.group(1), match.group(2)
        if key not in FIELD_ATTRIBUTES:
            raise CourseFieldValidationError(f"工程包填写字段不支持属性 {key}。")
        if key in attributes:
            raise CourseFieldValidationError(f"工程包填写字段重复设置了属性 {key}。")
        if value is None and key != "required":
            raise CourseFieldValidationError(f"工程包填写字段属性 {key} 必须使用双引号设置值。")
        attributes[key] = True if key == "required" and value is None else (value or "")
        position = match.end()
    return attributes


def parse_course_fields(markdown: str) -> list[dict[str, Any]]:
    matches = list(FIELD_MARKER_RE.finditer(markdown))
    if len(matches) != len(FIELD_START_RE.findall(markdown)):
        raise CourseFieldValidationError("工程包中存在未闭合或格式错误的填写字段。")
    if len(matches) > MAX_FIELDS:
        raise CourseFieldValidationError(f"单个工程包最多包含 {MAX_FIELDS} 个填写字段。")

    fields: list[dict[str, Any]] = []
    field_ids: set[str] = set()
    for match in matches:
        attributes = _parse_attributes(match.group("attributes"))
        field_id = str(attributes.get("id") or "").strip()
        field_type = str(attributes.get("type") or "").strip()
        label = str(attributes.get("label") or "").strip()
        if not FIELD_ID_RE.fullmatch(field_id):
            raise CourseFieldValidationError(
                "填写字段 id 必须以英文字母开头，并且只能包含字母、数字、下划线或连字符。"
            )
        if field_id in field_ids:
            raise CourseFieldValidationError(f"填写字段 id 重复：{field_id}。")
        if field_type not in FIELD_TYPES:
            raise CourseFieldValidationError(
                f"填写字段 {field_id} 的类型必须是 text、textarea、radio 或 checkbox。"
            )
        if not label:
            raise CourseFieldValidationError(f"填写字段 {field_id} 缺少 label。")
        options = [
            option.strip()
            for option in str(attributes.get("options") or "").split(";")
            if option.strip()
        ]
        if field_type in {"radio", "checkbox"} and len(options) < 2:
            raise CourseFieldValidationError(f"选择字段 {field_id} 至少需要两个 options 选项。")
        if field_type in {"text", "textarea"} and options:
            raise CourseFieldValidationError(f"文本字段 {field_id} 不能设置 options。")
        if len(options) != len(set(options)):
            raise CourseFieldValidationError(f"选择字段 {field_id} 包含重复选项。")
        required_value = attributes.get("required", False)
        required = required_value is True or str(required_value).lower() in {"1", "true", "yes"}
        fields.append({
            "id": field_id,
            "type": field_type,
            "label": label[:160],
            "required": required,
            "options": options,
            "marker": match.group(0),
        })
        field_ids.add(field_id)
    return fields


def inspect_course_fields(markdown: str) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        return parse_course_fields(markdown), []
    except CourseFieldValidationError as exc:
        return [], [str(exc)]


def normalize_course_answers(
    fields: list[dict[str, Any]],
    answers: dict[str, Any] | None,
) -> dict[str, str | list[str]]:
    raw_answers = answers or {}
    if not isinstance(raw_answers, dict):
        raise CourseFieldValidationError("工程包答案格式不正确。")
    by_id = {field["id"]: field for field in fields}
    unknown = sorted(set(raw_answers) - set(by_id))
    if unknown:
        raise CourseFieldValidationError(f"工程包答案包含未知字段：{', '.join(unknown[:5])}。")

    normalized: dict[str, str | list[str]] = {}
    total_length = 0
    for field_id, field in by_id.items():
        value = raw_answers.get(field_id, [] if field["type"] == "checkbox" else "")
        if field["type"] == "checkbox":
            if value in (None, ""):
                values: list[str] = []
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                values = list(dict.fromkeys(item.strip() for item in value if item.strip()))
            else:
                raise CourseFieldValidationError(f"字段 {field_id} 的答案必须是选项列表。")
            invalid = [item for item in values if item not in field["options"]]
            if invalid:
                raise CourseFieldValidationError(f"字段 {field_id} 包含无效选项。")
            normalized[field_id] = values
            total_length += sum(len(item) for item in values)
        else:
            if value is None:
                text = ""
            elif isinstance(value, str):
                text = value
            else:
                raise CourseFieldValidationError(f"字段 {field_id} 的答案必须是文字。")
            if len(text) > MAX_ANSWER_LENGTH:
                raise CourseFieldValidationError(f"字段 {field_id} 的答案不能超过 {MAX_ANSWER_LENGTH} 个字符。")
            if field["type"] == "radio" and text and text not in field["options"]:
                raise CourseFieldValidationError(f"字段 {field_id} 包含无效选项。")
            normalized[field_id] = text
            total_length += len(text)
    if total_length > MAX_TOTAL_ANSWER_LENGTH:
        raise CourseFieldValidationError("工程包全部答案合计不能超过 200000 个字符。")
    return normalized


def course_answers_text(fields: list[dict[str, Any]], answers: dict[str, str | list[str]]) -> str:
    lines: list[str] = []
    for field in fields:
        value = answers.get(field["id"], "")
        display = "、".join(value) if isinstance(value, list) else value
        if display:
            lines.append(f"{field['label']}：{display}")
    return "\n".join(lines)
