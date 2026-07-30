import { ApiRequestError } from "./apiError";

export type SessionOperation = "tick" | "mutation";
export type ApiStatus = "checking" | "online" | "offline" | "error";
export type DispatchStatus = "loading" | "ready" | "error";

export type SessionFailureDecision = {
  apiStatus: ApiStatus;
  dispatchStatus: DispatchStatus;
  pausePlayback: boolean;
};

export type MutationAvailabilityInput = {
  hasSession: boolean;
  dispatchStatus: DispatchStatus;
  tickInFlight: boolean;
  displayTime: number;
  sessionCurrentTime: number | null;
};

export function isHistoricalPlayback(
  displayTime: number,
  sessionCurrentTime: number | null
): boolean {
  return sessionCurrentTime !== null && displayTime < sessionCurrentTime;
}

export function canMutateOnlineSession(input: MutationAvailabilityInput): boolean {
  return input.hasSession
    && input.dispatchStatus === "ready"
    && !input.tickInFlight
    && !isHistoricalPlayback(input.displayTime, input.sessionCurrentTime);
}

export async function runOnlineMutation<T>(
  enabled: boolean,
  request: () => Promise<T>
): Promise<T | undefined> {
  if (!enabled) return undefined;
  return request();
}

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
