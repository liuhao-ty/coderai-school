import axios, { type AxiosError, type GenericAbortSignal, type InternalAxiosRequestConfig } from "axios";

import type { AccountProfile, StudentAuth, StudentProfile, TeacherAuth } from "../types";
import { normalizeSchoolStage, schoolStageLabel } from "./schoolStages";
import {
  getSecureAuthValue,
  hydrateSecureAuth,
  removeSecureAuthValue,
  setSecureAuthValue,
} from "./secureAuthStorage";


export const ORGANIZATION_CODE = (import.meta.env.VITE_ORGANIZATION_CODE || "coderai-pilot").trim().toLowerCase();
export const TEACHER_TOKEN_KEY = "coderai_teacher_token";
export const TEACHER_REFRESH_TOKEN_KEY = "coderai_teacher_refresh_token";
export const TEACHER_ACCESS_EXPIRES_KEY = "coderai_teacher_access_expires_at";
export const TEACHER_PASSWORD_CHANGE_REQUIRED_KEY = "coderai_teacher_password_change_required";
export const TEACHER_PROFILE_KEY = "coderai_teacher_profile";
export const TEACHER_WEAK_PASSWORD_SESSION_KEY = "coderai_teacher_weak_password_session";
export const STUDENT_TOKEN_KEY = "coderai_student_token";
export const STUDENT_PROFILE_KEY = "coderai_student_profile";
export const STUDENT_PASSWORD_CHANGE_REQUIRED_KEY = "coderai_student_password_change_required";

const API_BASE_URL = import.meta.env.VITE_API_URL || "";
const CLIENT_VERSION = import.meta.env.VITE_APP_VERSION || "dev";
const TRANSIENT_HTTP_STATUSES = new Set([502, 503, 504]);
const RECOVERY_DELAYS_MS = [300, 700, 1500, 2500, 4000];

export type ConnectionIssue = "offline" | "timeout" | "unavailable";
export type CoderAIAxiosError = AxiosError & { coderaiConnectionIssue?: ConnectionIssue };
type RecoverableRequestConfig = InternalAxiosRequestConfig & {
  coderaiRecoveryAttempted?: boolean;
  coderaiReleaseReadSlot?: () => void;
};

let recoveryProbe: Promise<boolean> | null = null;
const MAX_CONCURRENT_READS = 4;
let activeReadRequests = 0;
const readWaiters: Array<() => void> = [];

function sleep(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));
}

function deviceIsOffline() {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

function isReadMethod(method?: string) {
  return ["get", "head", "options"].includes((method || "").toLowerCase());
}

function releaseNextReadRequest() {
  const next = readWaiters.shift();
  if (next) {
    next();
    return;
  }
  activeReadRequests = Math.max(0, activeReadRequests - 1);
}

async function acquireReadSlot(signal?: GenericAbortSignal) {
  if (signal?.aborted) throw new axios.CanceledError("Request canceled");
  if (activeReadRequests >= MAX_CONCURRENT_READS) {
    await new Promise<void>((resolve, reject) => {
      const start = () => {
        signal?.removeEventListener?.("abort", cancel);
        resolve();
      };
      const cancel = () => {
        const index = readWaiters.indexOf(start);
        if (index >= 0) readWaiters.splice(index, 1);
        reject(new axios.CanceledError("Request canceled"));
      };
      readWaiters.push(start);
      signal?.addEventListener?.("abort", cancel, { once: true });
    });
  } else {
    activeReadRequests += 1;
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    releaseNextReadRequest();
  };
}

function releaseReadSlot(config?: RecoverableRequestConfig) {
  config?.coderaiReleaseReadSlot?.();
  if (config) delete config.coderaiReleaseReadSlot;
}

function markConnectionIssue(error: AxiosError, issue: ConnectionIssue) {
  (error as CoderAIAxiosError).coderaiConnectionIssue = issue;
  return error;
}

function isRecoverableRead(error: AxiosError) {
  if (!isReadMethod(error.config?.method) || error.code === "ERR_CANCELED") return false;
  return !error.response || TRANSIENT_HTTP_STATUSES.has(error.response.status);
}

async function probeApiUntilAvailable() {
  if (deviceIsOffline()) return false;
  for (const delay of RECOVERY_DELAYS_MS) {
    await sleep(delay);
    if (deviceIsOffline()) return false;
    try {
      await axios.get("/api/health/live", {
        baseURL: API_BASE_URL,
        headers: {
          "X-CoderAI-Organization-Code": ORGANIZATION_CODE,
          "X-CoderAI-Client-Version": CLIENT_VERSION,
        },
        timeout: 2500,
      });
      return true;
    } catch {
      // A single shared probe keeps concurrent workspace requests from retrying independently.
    }
  }
  return false;
}

function waitForApiRecovery() {
  if (!recoveryProbe) {
    recoveryProbe = probeApiUntilAvailable().finally(() => {
      recoveryProbe = null;
    });
  }
  return recoveryProbe;
}

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120_000,
  headers: {
    "X-CoderAI-Organization-Code": ORGANIZATION_CODE,
    "X-CoderAI-Client-Version": CLIENT_VERSION,
  },
});

api.interceptors.request.use(async (config) => {
  await hydrateSecureAuth();
  const teacherToken = getSecureAuthValue(TEACHER_TOKEN_KEY);
  const studentToken = getSecureAuthValue(STUDENT_TOKEN_KEY);
  if (teacherToken) config.headers.set("X-CoderAI-Teacher-Token", teacherToken);
  if (studentToken) config.headers.set("X-CoderAI-Student-Token", studentToken);
  if (isReadMethod(config.method)) {
    (config as RecoverableRequestConfig).coderaiReleaseReadSlot = await acquireReadSlot(config.signal);
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    releaseReadSlot(response.config as RecoverableRequestConfig);
    return response;
  },
  async (unknownError: unknown) => {
    if (!axios.isAxiosError(unknownError)) return Promise.reject(unknownError);
    const error = unknownError as AxiosError;
    const config = error.config as RecoverableRequestConfig | undefined;
    releaseReadSlot(config);
    if (!config || config.coderaiRecoveryAttempted || !isRecoverableRead(error)) {
      if (!error.response) {
        const issue = deviceIsOffline()
          ? "offline"
          : error.code === "ECONNABORTED" || error.code === "ETIMEDOUT"
            ? "timeout"
            : "unavailable";
        markConnectionIssue(error, issue);
      }
      return Promise.reject(error);
    }

    config.coderaiRecoveryAttempted = true;
    const recovered = await waitForApiRecovery();
    if (!recovered) {
      markConnectionIssue(error, deviceIsOffline() ? "offline" : "unavailable");
      return Promise.reject(error);
    }
    return api.request(config);
  },
);

export function storeTeacherAuth(auth: TeacherAuth) {
  setSecureAuthValue(TEACHER_TOKEN_KEY, auth.token);
  setSecureAuthValue(TEACHER_REFRESH_TOKEN_KEY, auth.refresh_token);
  localStorage.setItem(TEACHER_ACCESS_EXPIRES_KEY, auth.access_expires_at);
  localStorage.setItem(TEACHER_PASSWORD_CHANGE_REQUIRED_KEY, auth.password_change_required ? "true" : "false");
  if (auth.user) localStorage.setItem(TEACHER_PROFILE_KEY, JSON.stringify(auth.user));
}

export function clearTeacherAuth() {
  removeSecureAuthValue(TEACHER_TOKEN_KEY);
  removeSecureAuthValue(TEACHER_REFRESH_TOKEN_KEY);
  localStorage.removeItem(TEACHER_ACCESS_EXPIRES_KEY);
  localStorage.removeItem(TEACHER_PASSWORD_CHANGE_REQUIRED_KEY);
  localStorage.removeItem(TEACHER_PROFILE_KEY);
  sessionStorage.removeItem(TEACHER_WEAK_PASSWORD_SESSION_KEY);
}

export function storeStudentAuth(auth: StudentAuth) {
  setSecureAuthValue(STUDENT_TOKEN_KEY, auth.token);
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
  removeSecureAuthValue(STUDENT_TOKEN_KEY);
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


export function getAuthValue(key: string) {
  if (key === TEACHER_TOKEN_KEY || key === TEACHER_REFRESH_TOKEN_KEY || key === STUDENT_TOKEN_KEY) {
    return getSecureAuthValue(key);
  }
  return localStorage.getItem(key);
}


export { hydrateSecureAuth };
