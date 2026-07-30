import { describe, expect, it } from "vitest";

import { ApiRequestError } from "./apiError";
import {
  canMutateOnlineSession,
  classifySessionRequestFailure,
  isHistoricalPlayback
} from "./sessionRequestState";

describe("session request failure classification", () => {
  it("keeps mutation business errors online without pausing playback", () => {
    expect(classifySessionRequestFailure(new ApiRequestError(409, "conflict"), "mutation"))
      .toEqual({
        apiStatus: "online",
        dispatchStatus: "ready",
        pausePlayback: false
      });
  });

  it("keeps tick business errors online but pauses playback", () => {
    expect(classifySessionRequestFailure(new ApiRequestError(422, "past"), "tick"))
      .toEqual({
        apiStatus: "online",
        dispatchStatus: "ready",
        pausePlayback: true
      });
  });

  it("classifies backend failures as reachable operational errors", () => {
    expect(classifySessionRequestFailure(new ApiRequestError(503, "down"), "mutation"))
      .toEqual({
        apiStatus: "error",
        dispatchStatus: "error",
        pausePlayback: true
      });
  });

  it("classifies non-HTTP failures as offline", () => {
    expect(classifySessionRequestFailure(new TypeError("fetch failed"), "mutation"))
      .toEqual({
        apiStatus: "offline",
        dispatchStatus: "error",
        pausePlayback: true
      });
  });

  it.each([400, 499])("keeps the HTTP %i boundary reachable", (status) => {
    expect(classifySessionRequestFailure(new ApiRequestError(status, "business"), "mutation"))
      .toMatchObject({
        apiStatus: "online",
        dispatchStatus: "ready",
        pausePlayback: false
      });
  });

  it.each([500, 599])("classifies the HTTP %i boundary as a backend error", (status) => {
    expect(classifySessionRequestFailure(new ApiRequestError(status, "backend"), "mutation"))
      .toEqual({
        apiStatus: "error",
        dispatchStatus: "error",
        pausePlayback: true
      });
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
});
