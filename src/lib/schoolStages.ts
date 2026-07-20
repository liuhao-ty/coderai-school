import type { SchoolStage } from "../domain-types";


export const SCHOOL_STAGE_OPTIONS: Array<{ value: SchoolStage; label: string }> = [
  { value: "primary_lower", label: "小学低龄" },
  { value: "primary_upper", label: "小学高龄" },
  { value: "secondary", label: "初中高中" },
];

export const ALL_SCHOOL_STAGES: SchoolStage[] = SCHOOL_STAGE_OPTIONS.map((item) => item.value);

export const CLASSROOM_SCHOOL_STAGE_OPTIONS: Array<{ value: SchoolStage | "mixed"; label: string }> = [
  ...SCHOOL_STAGE_OPTIONS,
  { value: "mixed", label: "混合学龄" },
];

const SCHOOL_STAGE_ALIASES: Record<string, SchoolStage> = {
  primary_lower: "primary_lower",
  junior: "primary_lower",
  "小学低龄": "primary_lower",
  "低龄": "primary_lower",
  "低龄引导": "primary_lower",
  "低阶": "primary_lower",
  primary_upper: "primary_upper",
  senior: "primary_upper",
  "小学高龄": "primary_upper",
  "高龄": "primary_upper",
  "高阶": "primary_upper",
  "高阶创作": "primary_upper",
  secondary: "secondary",
  "初中高中": "secondary",
  "初高中": "secondary",
  "中学": "secondary",
};

export function normalizeSchoolStage(value: unknown): SchoolStage {
  return SCHOOL_STAGE_ALIASES[String(value || "").trim().toLowerCase()] || "primary_lower";
}

export function schoolStageLabel(value: unknown) {
  const normalized = normalizeSchoolStage(value);
  return SCHOOL_STAGE_OPTIONS.find((item) => item.value === normalized)?.label || "小学低龄";
}

export function classroomSchoolStageLabel(value: string) {
  return value === "mixed" ? "混合学龄" : schoolStageLabel(value);
}
