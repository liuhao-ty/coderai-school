import { getVersion } from "@tauri-apps/api/app";
import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";

import { isTauriRuntime } from "./secureAuthStorage";


let pendingUpdate: Update | null = null;

export async function currentClientVersion() {
  return isTauriRuntime() ? getVersion() : (import.meta.env.VITE_APP_VERSION || "0.0.0-development");
}

export async function checkDesktopUpdate() {
  if (!isTauriRuntime() || import.meta.env.VITE_UPDATER_ENABLED !== "true") return null;
  pendingUpdate = await check();
  if (!pendingUpdate) return null;
  return {
    version: pendingUpdate.version,
    currentVersion: pendingUpdate.currentVersion,
    body: pendingUpdate.body || "",
    date: pendingUpdate.date || "",
  };
}

export async function installDesktopUpdate(onProgress?: (progress: number) => void) {
  if (!pendingUpdate) throw new Error("没有可安装的桌面更新。");
  let downloaded = 0;
  let total = 0;
  await pendingUpdate.downloadAndInstall((event) => {
    if (event.event === "Started") total = event.data.contentLength || 0;
    if (event.event === "Progress") downloaded += event.data.chunkLength;
    if (event.event === "Finished") downloaded = total || downloaded;
    onProgress?.(total > 0 ? Math.min(100, Math.round(downloaded / total * 100)) : 0);
  });
  await relaunch();
}

export function compareVersions(left: string, right: string) {
  const tokenize = (value: string) => value.split(/[.+-]/).map((part) => /^\d+$/.test(part) ? Number(part) : part);
  const a = tokenize(left);
  const b = tokenize(right);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const x = a[index] ?? 0;
    const y = b[index] ?? 0;
    if (x === y) continue;
    if (typeof x === "number" && typeof y === "number") return x > y ? 1 : -1;
    if (typeof x === "number") return 1;
    if (typeof y === "number") return -1;
    return String(x).localeCompare(String(y));
  }
  return 0;
}
