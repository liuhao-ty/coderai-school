import { invoke } from "@tauri-apps/api/core";


const cache = new Map<string, string>();
let hydration: Promise<void> | null = null;

export const SECURE_AUTH_KEYS = [
  "coderai_teacher_token",
  "coderai_teacher_refresh_token",
  "coderai_student_token",
] as const;

export function isTauriRuntime() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function hydrateSecureAuth() {
  if (hydration) return hydration;
  hydration = (async () => {
    if (!isTauriRuntime()) {
      return;
    }
    await Promise.all(SECURE_AUTH_KEYS.map(async (key) => {
      const legacy = localStorage.getItem(key) || "";
      try {
        let value = await invoke<string | null>("get_secret", { key });
        if (!value && legacy) {
          await invoke("set_secret", { key, value: legacy });
          value = legacy;
        }
        if (value) cache.set(key, value);
      } finally {
        localStorage.removeItem(key);
      }
    }));
  })();
  return hydration;
}

export function getSecureAuthValue(key: string) {
  if (isTauriRuntime()) return cache.get(key) || null;
  return localStorage.getItem(key);
}

export function setSecureAuthValue(key: string, value: string) {
  if (!isTauriRuntime()) {
    localStorage.setItem(key, value);
    return;
  }
  cache.set(key, value);
  localStorage.removeItem(key);
  void invoke("set_secret", { key, value }).catch(() => cache.delete(key));
}

export function removeSecureAuthValue(key: string) {
  cache.delete(key);
  localStorage.removeItem(key);
  if (isTauriRuntime()) void invoke("delete_secret", { key }).catch(() => undefined);
}
