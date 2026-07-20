from __future__ import annotations

import json
import re
from collections.abc import Iterable


SCHOOL_STAGES = ("primary_lower", "primary_upper", "secondary")
SCHOOL_STAGE_LABELS = {
    "primary_lower": "小学低龄",
    "primary_upper": "小学高龄",
    "secondary": "初中高中",
}
SCHOOL_STAGE_ALIASES = {
    "primary_lower": "primary_lower",
    "小学低龄": "primary_lower",
    "junior": "primary_lower",
    "低龄": "primary_lower",
    "低龄引导": "primary_lower",
    "低阶": "primary_lower",
    "primary_upper": "primary_upper",
    "小学高龄": "primary_upper",
    "senior": "primary_upper",
    "高龄": "primary_upper",
    "高阶": "primary_upper",
    "高阶创作": "primary_upper",
    "secondary": "secondary",
    "初中高中": "secondary",
    "初高中": "secondary",
    "中学": "secondary",
}


def parse_school_stage(value: object) -> str | None:
    return SCHOOL_STAGE_ALIASES.get(str(value or "").strip().lower())


def normalize_school_stage(value: object, default: str = "primary_lower") -> str:
    return parse_school_stage(value) or default


def school_stage_label(value: object) -> str:
    normalized = parse_school_stage(value)
    return SCHOOL_STAGE_LABELS.get(normalized or "", str(value or ""))


def normalize_school_stages(values: Iterable[object] | None) -> list[str]:
    selected = {stage for value in values or [] if (stage := parse_school_stage(value))}
    return [stage for stage in SCHOOL_STAGES if stage in selected] or list(SCHOOL_STAGES)


def infer_school_stages(age_range: object) -> list[str]:
    raw = str(age_range or "").strip()
    lowered = raw.lower()
    if not lowered or any(token in lowered for token in ("全年龄", "全学龄", "all", "mixed", "混合")):
        return list(SCHOOL_STAGES)

    direct = parse_school_stage(lowered)
    if direct:
        return [direct]

    matched = [stage for stage, label in SCHOOL_STAGE_LABELS.items() if label in raw]
    if matched:
        return normalize_school_stages(matched)

    ages = [int(item) for item in re.findall(r"\d{1,2}", raw)]
    if ages:
        start, end = min(ages), max(ages)
        inferred: list[str] = []
        if start <= 8 and end >= 6:
            inferred.append("primary_lower")
        if start <= 11 and end >= 9:
            inferred.append("primary_upper")
        if end >= 12:
            inferred.append("secondary")
        return normalize_school_stages(inferred)
    return list(SCHOOL_STAGES)


def school_stages_from_json(value: object, legacy_age_range: object = "") -> list[str]:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, list) and parsed:
        return normalize_school_stages(parsed)
    return infer_school_stages(legacy_age_range)


def school_stages_label(values: Iterable[object] | None) -> str:
    return "、".join(SCHOOL_STAGE_LABELS[stage] for stage in normalize_school_stages(values))
