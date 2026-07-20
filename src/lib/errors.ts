import axios from "axios";


export function explainError(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
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
