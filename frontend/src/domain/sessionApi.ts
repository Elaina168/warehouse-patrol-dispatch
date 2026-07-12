import type {
  ConflictAvoidanceExperimentRequest,
  ConflictAvoidanceExperimentResult,
  DeleteSessionResult,
  DynamicReplanningExperimentRequest,
  DynamicReplanningExperimentResult,
  OnlinePressureExperimentRequest,
  OnlinePressureExperimentResult,
  ReplanWindowExperimentRequest,
  ReplanWindowExperimentResult,
  ScaleExperimentRequest,
  ScaleExperimentResult,
  SeededPressureExperimentRequest,
  SeededPressureExperimentResult,
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

export async function compareConflictAvoidance(
  apiBase: string,
  request: ConflictAvoidanceExperimentRequest,
  fetcher: FetchLike = fetch
): Promise<ConflictAvoidanceExperimentResult> {
  const response = await fetcher(`${apiBase}/api/experiments/conflict-avoidance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "conflict avoidance experiment failed");
  }
  return await response.json() as ConflictAvoidanceExperimentResult;
}

export async function compareDynamicReplanning(
  apiBase: string,
  request: DynamicReplanningExperimentRequest,
  fetcher: FetchLike = fetch
): Promise<DynamicReplanningExperimentResult> {
  const response = await fetcher(`${apiBase}/api/experiments/dynamic-replanning`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "dynamic replanning experiment failed");
  }
  return await response.json() as DynamicReplanningExperimentResult;
}

export async function compareReplanWindows(
  apiBase: string,
  request: ReplanWindowExperimentRequest,
  fetcher: FetchLike = fetch
): Promise<ReplanWindowExperimentResult> {
  const response = await fetcher(`${apiBase}/api/experiments/replan-window`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "replan window experiment failed");
  }
  return await response.json() as ReplanWindowExperimentResult;
}

export async function compareScaleCases(
  apiBase: string,
  request: ScaleExperimentRequest,
  fetcher: FetchLike = fetch
): Promise<ScaleExperimentResult> {
  const response = await fetcher(`${apiBase}/api/experiments/scale`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "scale experiment failed");
  }
  return await response.json() as ScaleExperimentResult;
}

export async function runSeededPressureExperiment(
  apiBase: string,
  request: SeededPressureExperimentRequest,
  fetcher: FetchLike = fetch
): Promise<SeededPressureExperimentResult> {
  const response = await fetcher(`${apiBase}/api/experiments/seeded-pressure`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "seeded pressure experiment failed");
  }
  return await response.json() as SeededPressureExperimentResult;
}

export async function runOnlinePressureExperiment(
  apiBase: string,
  request: OnlinePressureExperimentRequest,
  fetcher: FetchLike = fetch
): Promise<OnlinePressureExperimentResult> {
  const response = await fetcher(`${apiBase}/api/experiments/online-pressure`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    throw await apiErrorFromResponse(response, "online pressure experiment failed");
  }
  return await response.json() as OnlinePressureExperimentResult;
}
