import axios from "axios";

import type { AccountProfile, StudentAuth, StudentProfile, TeacherAuth } from "../types";
import { normalizeSchoolStage, schoolStageLabel } from "./schoolStages";


export const TEACHER_TOKEN_KEY = "coderai_teacher_token";
export const TEACHER_REFRESH_TOKEN_KEY = "coderai_teacher_refresh_token";
export const TEACHER_ACCESS_EXPIRES_KEY = "coderai_teacher_access_expires_at";
export const TEACHER_PASSWORD_CHANGE_REQUIRED_KEY = "coderai_teacher_password_change_required";
export const TEACHER_PROFILE_KEY = "coderai_teacher_profile";
export const TEACHER_WEAK_PASSWORD_SESSION_KEY = "coderai_teacher_weak_password_session";
export const STUDENT_TOKEN_KEY = "coderai_student_token";
export const STUDENT_PROFILE_KEY = "coderai_student_profile";
export const STUDENT_PASSWORD_CHANGE_REQUIRED_KEY = "coderai_student_password_change_required";

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || "" });

api.interceptors.request.use((config) => {
  const teacherToken = localStorage.getItem(TEACHER_TOKEN_KEY);
  const studentToken = localStorage.getItem(STUDENT_TOKEN_KEY);
  if (teacherToken) config.headers.set("X-CoderAI-Teacher-Token", teacherToken);
  if (studentToken) config.headers.set("X-CoderAI-Student-Token", studentToken);
  return config;
});

export function storeTeacherAuth(auth: TeacherAuth) {
  localStorage.setItem(TEACHER_TOKEN_KEY, auth.token);
  localStorage.setItem(TEACHER_REFRESH_TOKEN_KEY, auth.refresh_token);
  localStorage.setItem(TEACHER_ACCESS_EXPIRES_KEY, auth.access_expires_at);
  localStorage.setItem(TEACHER_PASSWORD_CHANGE_REQUIRED_KEY, auth.password_change_required ? "true" : "false");
  if (auth.user) localStorage.setItem(TEACHER_PROFILE_KEY, JSON.stringify(auth.user));
}

export function clearTeacherAuth() {
  localStorage.removeItem(TEACHER_TOKEN_KEY);
  localStorage.removeItem(TEACHER_REFRESH_TOKEN_KEY);
  localStorage.removeItem(TEACHER_ACCESS_EXPIRES_KEY);
  localStorage.removeItem(TEACHER_PASSWORD_CHANGE_REQUIRED_KEY);
  localStorage.removeItem(TEACHER_PROFILE_KEY);
  sessionStorage.removeItem(TEACHER_WEAK_PASSWORD_SESSION_KEY);
}

export function storeStudentAuth(auth: StudentAuth) {
  localStorage.setItem(STUDENT_TOKEN_KEY, auth.token);
  localStorage.setItem(STUDENT_PROFILE_KEY, JSON.stringify(auth.student));
  localStorage.setItem(STUDENT_PASSWORD_CHANGE_REQUIRED_KEY, auth.password_change_required ? "true" : "false");
}

export function loadStudentProfile(): StudentProfile | null {
  try {
    const value = localStorage.getItem(STUDENT_PROFILE_KEY);
    if (!value) return null;
    const parsed = JSON.parse(value) as StudentProfile & { age_level?: string };
    const ageLevel = normalizeSchoolStage(parsed.age_level);
    const profile: StudentProfile = {
      ...parsed,
      age_level: ageLevel,
      school_stage_label: schoolStageLabel(ageLevel),
    };
    if (parsed.age_level !== ageLevel || parsed.school_stage_label !== profile.school_stage_label) {
      localStorage.setItem(STUDENT_PROFILE_KEY, JSON.stringify(profile));
    }
    return profile;
  } catch {
    return null;
  }
}

export function clearStudentAuth() {
  localStorage.removeItem(STUDENT_TOKEN_KEY);
  localStorage.removeItem(STUDENT_PROFILE_KEY);
  localStorage.removeItem(STUDENT_PASSWORD_CHANGE_REQUIRED_KEY);
}

export function loadTeacherProfile(): AccountProfile | null {
  try {
    const value = localStorage.getItem(TEACHER_PROFILE_KEY);
    return value ? JSON.parse(value) as AccountProfile : null;
  } catch {
    return null;
  }
}
