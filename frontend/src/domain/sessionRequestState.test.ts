import { describe, expect, it } from "vitest";

import { ApiRequestError } from "./apiError";
import {
  canControlOnlinePlayback,
  canAddRuntimeRobot,
  canRemoveRuntimeRobot,
  canMutateOnlineSession,
  classifySessionRequestFailure,
  historicalPlaybackNotice,
  historicalRuntimeOverlay,
  isHistoricalPlayback
} from "./sessionRequestState";

describe("session request failure classification", () => {
  it("keeps mutation business errors online without pausing playback", () => {
    expect(classifySessionRequestFailure(new ApiRequestError(409, "conflict"), "mutation"))
      .toEqual({
        apiStatus: "online",
        dispatchStatus: "ready",
        pausePlayback: false,
        invalidateSession: false
      });
  });

  it("keeps tick business errors online but pauses playback", () => {
    expect(classifySessionRequestFailure(new ApiRequestError(422, "past"), "tick"))
      .toEqual({
        apiStatus: "online",
        dispatchStatus: "ready",
        pausePlayback: true,
        invalidateSession: false
      });
  });

  it.each(["mutation", "tick"] as const)("invalidates an expired session for %s HTTP 404", (operation) => {
    expect(classifySessionRequestFailure(new ApiRequestError(404, "missing"), operation))
      .toEqual({
        apiStatus: "online",
        dispatchStatus: "error",
        pausePlayback: true,
        invalidateSession: true
      });
  });

  it("classifies backend failures as reachable operational errors", () => {
    expect(classifySessionRequestFailure(new ApiRequestError(503, "down"), "mutation"))
      .toEqual({
        apiStatus: "error",
        dispatchStatus: "error",
        pausePlayback: true,
        invalidateSession: false
      });
  });

  it("classifies non-HTTP failures as offline", () => {
    expect(classifySessionRequestFailure(new TypeError("fetch failed"), "mutation"))
      .toEqual({
        apiStatus: "offline",
        dispatchStatus: "error",
        pausePlayback: true,
        invalidateSession: false
      });
  });

  it.each([400, 499])("keeps the HTTP %i boundary reachable", (status) => {
    expect(classifySessionRequestFailure(new ApiRequestError(status, "business"), "mutation"))
      .toMatchObject({
        apiStatus: "online",
        dispatchStatus: "ready",
        pausePlayback: false,
        invalidateSession: false
      });
  });

  it.each([500, 599])("classifies the HTTP %i boundary as a backend error", (status) => {
    expect(classifySessionRequestFailure(new ApiRequestError(status, "backend"), "mutation"))
      .toEqual({
        apiStatus: "error",
        dispatchStatus: "error",
        pausePlayback: true,
        invalidateSession: false
      });
  });
});

describe("online playback availability", () => {
  const available = {
    hasResult: true,
    dispatchStatus: "ready" as const,
    tickInFlight: false
  };

  it("allows playback only for a synchronized ready result", () => {
    expect(canControlOnlinePlayback(available)).toBe(true);
  });

  it.each([
    { name: "no result", input: { ...available, hasResult: false } },
    { name: "dispatch loading", input: { ...available, dispatchStatus: "loading" as const } },
    { name: "dispatch error", input: { ...available, dispatchStatus: "error" as const } },
    { name: "tick in flight", input: { ...available, tickInFlight: true } }
  ])("blocks playback controls for $name", ({ input }) => {
    expect(canControlOnlinePlayback(input)).toBe(false);
  });
});

describe("online mutation availability", () => {
  const available = {
    hasSession: true,
    dispatchStatus: "ready" as const,
    tickInFlight: false,
    displayTime: 5,
    sessionCurrentTime: 5
  };

  it("allows mutations only at the latest ready session tick", () => {
    expect(canMutateOnlineSession(available)).toBe(true);
  });

  it.each([
    { name: "no session", input: { ...available, hasSession: false } },
    { name: "dispatch loading", input: { ...available, dispatchStatus: "loading" as const } },
    { name: "dispatch error", input: { ...available, dispatchStatus: "error" as const } },
    { name: "tick in flight", input: { ...available, tickInFlight: true } },
    { name: "historical display", input: { ...available, displayTime: 4 } }
  ])("blocks mutations for $name", ({ input }) => {
    expect(canMutateOnlineSession(input)).toBe(false);
  });

  it("treats only an earlier display tick as historical", () => {
    expect(isHistoricalPlayback(4, 5)).toBe(true);
    expect(isHistoricalPlayback(5, 5)).toBe(false);
    expect(isHistoricalPlayback(6, 5)).toBe(false);
    expect(isHistoricalPlayback(0, null)).toBe(false);
  });

  it.each([
    { name: "no session", input: { ...available, hasSession: false } },
    { name: "dispatch not ready", input: { ...available, dispatchStatus: "loading" as const } },
    { name: "tick in flight", input: { ...available, tickInFlight: true } },
    { name: "historical playback", input: { ...available, displayTime: 4 } }
  ])("disables runtime robot onboarding for $name", ({ input }) => {
    expect(canAddRuntimeRobot(input)).toBe(false);
  });

  it("uses the same latest-ready-session gate for permanent robot removal", () => {
    expect(canRemoveRuntimeRobot(available)).toBe(true);
    expect(canRemoveRuntimeRobot({ ...available, displayTime: 4 })).toBe(false);
  });
});

describe("historical playback state", () => {
  const currentOverlay = {
    robotStates: [{
      robotId: "R1",
      name: "机器人 1",
      position: [2, 1] as [number, number],
      status: "failed" as const,
      battery: 80,
      load: 1,
      moveTicks: 1,
      currentTaskId: null
    }],
    shelfStates: [{
      shelfId: "S1",
      cell: [3, 3] as [number, number],
      serviceCell: [3, 2] as [number, number],
      status: "occupied" as const
    }],
    extraBlocked: [[4, 4] as [number, number]],
    unavailableRobotIds: ["R1"]
  };

  it("removes current-only runtime state during historical playback", () => {
    expect(historicalRuntimeOverlay(currentOverlay, true)).toEqual({
      robotStates: [],
      shelfStates: [],
      extraBlocked: [],
      unavailableRobotIds: []
    });
  });

  it("preserves current runtime state at the latest tick", () => {
    expect(historicalRuntimeOverlay(currentOverlay, false)).toEqual(currentOverlay);
  });

  it("provides the exact task-queue notice only for historical playback", () => {
    expect(historicalPlaybackNotice(true)).toBe(
      "历史回放仅提供路径、事件和指标；返回最新 T 查看实时状态。"
    );
    expect(historicalPlaybackNotice(false)).toBeNull();
  });
});
