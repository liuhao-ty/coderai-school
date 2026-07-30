import { save } from "@tauri-apps/plugin-dialog";
import { writeFile } from "@tauri-apps/plugin-fs";

import { isTauriRuntime } from "./secureAuthStorage";


const INVALID_FILENAME_CHARACTERS = /[<>:"/\\|?*\u0000-\u001f]/g;

export function safeDownloadName(filename: string) {
  const normalized = filename.replace(INVALID_FILENAME_CHARACTERS, "_").trim();
  return normalized || "CoderAI-download";
}

function extensionFilter(filename: string) {
  const extension = filename.match(/\.([a-z0-9]{1,10})$/i)?.[1];
  return extension
    ? [{ name: `${extension.toUpperCase()} 文件`, extensions: [extension] }]
    : undefined;
}

function browserDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function saveBlobFile(blob: Blob, filename: string) {
  const safeName = safeDownloadName(filename);
  if (!isTauriRuntime()) {
    browserDownload(blob, safeName);
    return true;
  }

  const targetPath = await save({
    defaultPath: safeName,
    filters: extensionFilter(safeName),
  });
  if (!targetPath) return false;
  await writeFile(targetPath, new Uint8Array(await blob.arrayBuffer()));
  return true;
}

export function saveTextFile(content: string, filename: string, mimeType = "text/plain;charset=utf-8") {
  return saveBlobFile(new Blob([content], { type: mimeType }), filename);
}
