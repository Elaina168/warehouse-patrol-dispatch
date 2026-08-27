import type {
  AddRobotRequest,
  CreateSessionRequest,
  DeleteSessionResult,
  RemoveRobotRequest,
  SessionResult
} from "./types";
import { apiErrorFromResponse } from "./apiError";

export type FetchLike = typeof fetch;

export async function addRobot(
  apiBase: string,
  sessionId: string,
  request: AddRobotRequest,
  fetcher: FetchLike = fetch
): Promise<SessionResult> {
  const response = await fetcher(`${apiBase}/sessions/${encodeURIComponent(sessionId)}/robots`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "session robot onboarding failed");
  }
  return await response.json() as SessionResult;
}

export async function removeRobot(
  apiBase: string,
  sessionId: string,
  request: RemoveRobotRequest,
  fetcher: FetchLike = fetch
): Promise<SessionResult> {
  const response = await fetcher(`${apiBase}/sessions/${encodeURIComponent(sessionId)}/robots/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "session robot removal failed");
  }
  return await response.json() as SessionResult;
}

export async function createSession(
  apiBase: string,
  request: CreateSessionRequest,
  fetcher: FetchLike = fetch
): Promise<SessionResult> {
  const response = await fetcher(`${apiBase}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "session failed");
  }
  return await response.json() as SessionResult;
}

export async function settleCreatedSession(
  payload: SessionResult,
  current: boolean,
  apply: (payload: SessionResult) => void,
  remove: (sessionId: string) => Promise<void>
): Promise<"applied" | "deleted"> {
  if (current) {
    apply(payload);
    return "applied";
  }

  try {
    await remove(payload.sessionId);
  } catch {
    // 过期会话不能重新写入界面；删除失败由服务端清理机制兜底。
  }
  return "deleted";
}

export async function resetSession(
  apiBase: string,
  sessionId: string,
  fetcher: FetchLike = fetch
): Promise<SessionResult> {
  const response = await fetcher(`${apiBase}/sessions/${encodeURIComponent(sessionId)}/reset`, {
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
  const response = await fetcher(`${apiBase}/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "session delete failed");
  }
  return await response.json() as DeleteSessionResult;
}
