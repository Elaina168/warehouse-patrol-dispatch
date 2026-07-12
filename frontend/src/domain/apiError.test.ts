import { describe, expect, it } from "vitest";

import { apiErrorFromResponse } from "./apiError";

describe("API error formatting", () => {
  it("uses backend string detail before falling back to status", async () => {
    const response = new Response(JSON.stringify({ detail: "currentTime must be <= max session time" }), {
      status: 422,
      headers: { "Content-Type": "application/json" }
    });

    await expect(apiErrorFromResponse(response, "session tick failed"))
      .resolves.toEqual(new Error("session tick failed: currentTime must be <= max session time"));
  });

  it("joins backend detail arrays for operational display", async () => {
    const response = new Response(JSON.stringify({ detail: ["任务数已达上限", { loc: ["body", "scenario"] }] }), {
      status: 409,
      headers: { "Content-Type": "application/json" }
    });

    await expect(apiErrorFromResponse(response, "session update failed"))
      .resolves.toEqual(new Error('session update failed: 任务数已达上限；{"loc":["body","scenario"]}'));
  });

  it("falls back to status when the response body is not JSON", async () => {
    const response = new Response("Service unavailable", { status: 503 });

    await expect(apiErrorFromResponse(response, "session failed"))
      .resolves.toEqual(new Error("session failed: 503"));
  });
});
