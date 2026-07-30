import axios from "axios";

import type { CoderAIAxiosError } from "./api";


export function explainError(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    const connectionIssue = (error as CoderAIAxiosError).coderaiConnectionIssue;
    if (connectionIssue === "offline") return "当前设备未连接网络，请恢复网络后重试。";
    if (connectionIssue === "timeout") return "连接 CoderAI 云端服务超时，请稍后重试。";
    if (connectionIssue === "unavailable" || !error.response) {
      return "暂时无法连接 CoderAI 云端服务，已自动重试。请检查网络，或稍后再试。";
    }
    if ([502, 503, 504].includes(error.response.status)) {
      return "CoderAI 云端服务正在启动或暂时不可用，请稍后重试。";
    }
    return error.message;
  }
  return String(error);
}

export async function explainBlobError(error: unknown) {
  if (axios.isAxiosError(error) && error.response?.data instanceof Blob) {
    try {
      const payload = JSON.parse(await error.response.data.text()) as {
        detail?: string | { message?: string };
      };
      if (typeof payload.detail === "string") return payload.detail;
      if (payload.detail?.message) return payload.detail.message;
    } catch {
      // Fall through to the standard HTTP or network error description.
    }
  }
  return explainError(error);
}

export function errorCode(error: unknown) {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail?.code || (error.response ? `HTTP_${error.response.status}` : "NETWORK_ERROR");
  }
  return "UNKNOWN_ERROR";
}

export function isTransientConnectionError(error: unknown) {
  return axios.isAxiosError(error)
    && (!error.response || [502, 503, 504].includes(error.response.status));
}
