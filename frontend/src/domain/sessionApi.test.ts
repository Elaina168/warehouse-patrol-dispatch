import { describe, expect, it, vi } from "vitest";

import {
  deleteSession,
  resetSession,
  settleCreatedSession
} from "./sessionApi";
import type {
  DeleteSessionResult,
  SessionResult
} from "./types";

describe("session API helpers", () => {
  it("applies a current created session without deleting it", async () => {
    const payload = { sessionId: "session-current" } as SessionResult;
    const applied: string[] = [];
    const deleted: string[] = [];

    const outcome = await settleCreatedSession(
      payload,
      true,
      (current) => applied.push(current.sessionId),
      async (sessionId) => {
        deleted.push(sessionId);
      }
    );

    expect(outcome).toBe("applied");
    expect(applied).toEqual(["session-current"]);
    expect(deleted).toEqual([]);
  });

  it("deletes a stale created session without applying it", async () => {
    const payload = { sessionId: "session-stale" } as SessionResult;
    const applied: string[] = [];
    const deleted: string[] = [];

    const outcome = await settleCreatedSession(
      payload,
      false,
      (current) => applied.push(current.sessionId),
      async (sessionId) => {
        deleted.push(sessionId);
      }
    );

    expect(outcome).toBe("deleted");
    expect(applied).toEqual([]);
    expect(deleted).toEqual(["session-stale"]);
  });

  it("keeps stale state unapplied when cleanup deletion fails", async () => {
    const payload = { sessionId: "session-stale" } as SessionResult;
    const applied: string[] = [];
    let deleteAttempted = false;

    const outcome = await settleCreatedSession(
      payload,
      false,
      (current) => applied.push(current.sessionId),
      async () => {
        deleteAttempted = true;
        throw new Error("delete failed");
      }
    );

    expect(outcome).toBe("deleted");
    expect(deleteAttempted).toBe(true);
    expect(applied).toEqual([]);
  });

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
