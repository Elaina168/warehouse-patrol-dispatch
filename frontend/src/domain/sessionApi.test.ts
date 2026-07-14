import { describe, expect, it, vi } from "vitest";

import {
  deleteSession,
  resetSession
} from "./sessionApi";
import type {
  DeleteSessionResult,
  SessionResult
} from "./types";

describe("session API helpers", () => {
  it("posts to the current session reset endpoint and returns the session payload", async () => {
    const payload = { sessionId: "session-1" } as SessionResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await resetSession("http://127.0.0.1:8011", "session-1", fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/sessions/session-1/reset",
      { method: "POST" }
    );
    expect(result).toEqual(payload);
  });

  it("deletes the requested session and returns the delete payload", async () => {
    const payload = { sessionId: "session-1", deleted: true } as DeleteSessionResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await deleteSession("http://127.0.0.1:8011", "session-1", fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/sessions/session-1",
      { method: "DELETE" }
    );
    expect(result).toEqual(payload);
  });


  it("includes backend reset error detail in thrown errors", async () => {
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify({ detail: "调度会话不存在：session-404" }), {
        status: 404,
        headers: { "Content-Type": "application/json" }
      })
    ));

    await expect(resetSession("http://127.0.0.1:8011", "session-404", fetcher))
      .rejects.toThrow("session reset failed: 调度会话不存在：session-404");
  });

  it("includes backend delete error detail in thrown errors", async () => {
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify({ detail: ["任务数已达上限", "请删除旧会话"] }), {
        status: 409,
        headers: { "Content-Type": "application/json" }
      })
    ));

    await expect(deleteSession("http://127.0.0.1:8011", "session-1", fetcher))
      .rejects.toThrow("session delete failed: 任务数已达上限；请删除旧会话");
  });
});
