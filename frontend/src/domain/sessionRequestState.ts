import { ApiRequestError } from "./apiError";
import type { Cell, RobotRuntimeState, ShelfRuntimeState } from "./types";

export type SessionOperation = "tick" | "mutation";
export type ApiStatus = "checking" | "online" | "offline" | "error";
export type DispatchStatus = "loading" | "ready" | "error";

export type SessionFailureDecision = {
  apiStatus: ApiStatus;
  dispatchStatus: DispatchStatus;
  pausePlayback: boolean;
  invalidateSession: boolean;
};

export type MutationAvailabilityInput = {
  hasSession: boolean;
  dispatchStatus: DispatchStatus;
  tickInFlight: boolean;
  displayTime: number;
  sessionCurrentTime: number | null;
};

export type PlaybackAvailabilityInput = {
  hasResult: boolean;
  dispatchStatus: DispatchStatus;
  tickInFlight: boolean;
};

export type RuntimeOverlay = {
  robotStates: RobotRuntimeState[];
  shelfStates: ShelfRuntimeState[];
  extraBlocked: Cell[];
  unavailableRobotIds: string[];
};

export function isHistoricalPlayback(
  displayTime: number,
  sessionCurrentTime: number | null
): boolean {
  return sessionCurrentTime !== null && displayTime < sessionCurrentTime;
}

export function historicalRuntimeOverlay(
  current: RuntimeOverlay,
  historicalPlayback: boolean
): RuntimeOverlay {
  if (!historicalPlayback) return current;
  return {
    robotStates: [],
    shelfStates: [],
    extraBlocked: [],
    unavailableRobotIds: []
  };
}

export function historicalPlaybackNotice(historicalPlayback: boolean): string | null {
  return historicalPlayback
    ? "历史回放仅提供路径、事件和指标；返回最新 T 查看实时状态。"
    : null;
}

export function canMutateOnlineSession(input: MutationAvailabilityInput): boolean {
  return input.hasSession
    && input.dispatchStatus === "ready"
    && !input.tickInFlight
    && !isHistoricalPlayback(input.displayTime, input.sessionCurrentTime);
}

export function canAddRuntimeRobot(input: MutationAvailabilityInput): boolean {
  return canMutateOnlineSession(input);
}

export function canControlOnlinePlayback(input: PlaybackAvailabilityInput): boolean {
  return input.hasResult
    && input.dispatchStatus === "ready"
    && !input.tickInFlight;
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
  if (error instanceof ApiRequestError && error.status === 404) {
    return {
      apiStatus: "online",
      dispatchStatus: "error",
      pausePlayback: true,
      invalidateSession: true
    };
  }

  if (error instanceof ApiRequestError && error.status >= 400 && error.status <= 499) {
    return {
      apiStatus: "online",
      dispatchStatus: "ready",
      pausePlayback: operation === "tick",
      invalidateSession: false
    };
  }

  if (error instanceof ApiRequestError && error.status >= 500 && error.status <= 599) {
    return {
      apiStatus: "error",
      dispatchStatus: "error",
      pausePlayback: true,
      invalidateSession: false
    };
  }

  return {
    apiStatus: "offline",
    dispatchStatus: "error",
    pausePlayback: true,
    invalidateSession: false
  };
}
