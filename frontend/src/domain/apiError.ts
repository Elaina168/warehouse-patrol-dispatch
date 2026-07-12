export async function apiErrorFromResponse(response: Response, prefix: string): Promise<Error> {
  let detail = "";
  try {
    const payload = await response.json() as { detail?: unknown };
    detail = formatApiErrorDetail(payload.detail);
  } catch {
    detail = "";
  }
  return new Error(detail ? `${prefix}: ${detail}` : `${prefix}: ${response.status}`);
}

export function formatApiErrorDetail(detail: unknown): string {
  if (Array.isArray(detail)) {
    return detail.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join("；");
  }
  if (typeof detail === "string") return detail;
  if (detail === undefined || detail === null) return "";
  return JSON.stringify(detail);
}
