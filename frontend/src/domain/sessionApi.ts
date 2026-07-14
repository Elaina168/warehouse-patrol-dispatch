import type {
  DeleteSessionResult,
  SessionResult
} from "./types";
import { apiErrorFromResponse } from "./apiError";

export type FetchLike = typeof fetch;

export async function resetSession(
  apiBase: string,
  sessionId: string,
  fetcher: FetchLike = fetch
): Promise<SessionResult> {
  const response = await fetcher(`${apiBase}/api/sessions/${encodeURIComponent(sessionId)}/reset`, {
    method: "POST"
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "session reset failed");
  }
  return await response.json() as SessionResult;
}

export async function deleteSession(
  apiBase: string,
  sessionId: string,
  fetcher: FetchLike = fetch
): Promise<DeleteSessionResult> {
  const response = await fetcher(`${apiBase}/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "session delete failed");
  }
  return await response.json() as DeleteSessionResult;
}
