import { ApiRequestError } from "./apiError";

export type SessionOperation = "tick" | "mutation";
export type ApiStatus = "checking" | "online" | "offline" | "error";
export type DispatchStatus = "loading" | "ready" | "error";

export type SessionFailureDecision = {
  apiStatus: ApiStatus;
  dispatchStatus: DispatchStatus;
  pausePlayback: boolean;
};

export function classifySessionRequestFailure(
  error: unknown,
  operation: SessionOperation
): SessionFailureDecision {
  if (error instanceof ApiRequestError && error.status >= 400 && error.status <= 499) {
    return {
      apiStatus: "online",
      dispatchStatus: "ready",
      pausePlayback: operation === "tick"
    };
  }

  if (error instanceof ApiRequestError && error.status >= 500 && error.status <= 599) {
    return {
      apiStatus: "error",
      dispatchStatus: "error",
      pausePlayback: true
    };
  }

  return {
    apiStatus: "offline",
    dispatchStatus: "error",
    pausePlayback: true
  };
}
