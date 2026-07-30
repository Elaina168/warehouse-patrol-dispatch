import { describe, expect, it } from "vitest";

import { ApiRequestError, apiErrorFromResponse } from "./apiError";

describe("API error formatting", () => {
  it("uses backend string detail before falling back to status", async () => {
    const response = new Response(JSON.stringify({ detail: "currentTime must be <= max session time" }), {
      status: 422,
      headers: { "Content-Type": "application/json" }
    });

    const error = await apiErrorFromResponse(response, "session tick failed");

    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error.status).toBe(422);
    expect(error.message).toBe("session tick failed: currentTime must be <= max session time");
  });

  it("joins backend detail arrays for operational display", async () => {
    const response = new Response(JSON.stringify({ detail: ["任务数已达上限", { loc: ["body", "scenario"] }] }), {
      status: 422,
      headers: { "Content-Type": "application/json" }
    });

    const error = await apiErrorFromResponse(response, "session update failed");

    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error.status).toBe(422);
    expect(error.message).toBe('session update failed: 任务数已达上限；{"loc":["body","scenario"]}');
  });

  it("falls back to status when the response body is not JSON", async () => {
    const response = new Response("Service unavailable", { status: 503 });

    const error = await apiErrorFromResponse(response, "session failed");

    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error.status).toBe(503);
    expect(error.message).toBe("session failed: 503");
  });
});
