import { describe, expect, it, vi } from "vitest";

import {
  addRobot,
  createSession,
  deleteSession,
  removeRobot,
  resetSession,
  settleCreatedSession
} from "./sessionApi";
import type {
  AddRobotRequest,
  CreateSessionRequest,
  DeleteSessionResult,
  SessionResult
} from "./types";

describe("session API helpers", () => {
  it("posts an encoded runtime robot removal request with the exact body", async () => {
    const request = { robotId: "R5", currentTime: 12 };
    const payload = { sessionId: "session-removed" } as SessionResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await removeRobot("/api", "session/1", request, fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      "/api/sessions/session%2F1/robots/remove",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request)
      }
    );
    expect(result).toEqual(payload);
  });

  it("preserves backend removal detail when the request is rejected", async () => {
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify({ detail: "机器人正在执行任务：R5" }), {
        status: 409,
        headers: { "Content-Type": "application/json" }
      })
    ));

    await expect(removeRobot(
      "http://127.0.0.1:8011",
      "session-1",
      { robotId: "R5" },
      fetcher
    )).rejects.toThrow("session robot removal failed: 机器人正在执行任务：R5");
  });

  it("posts an encoded runtime robot onboarding request with the exact body", async () => {
    const request = {
      robot: {
        id: "R5",
        name: "运行时巡检车",
        start: [2, 1],
        battery: 80,
        batteryCapacity: 100,
        load: 1,
        moveTicks: 1,
        capabilities: ["inspection"]
      },
      currentTime: 12
    } satisfies AddRobotRequest;
    const payload = { sessionId: "session-joined" } as SessionResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await addRobot("/api", "session/1", request, fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      "/api/sessions/session%2F1/robots",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request)
      }
    );
    expect(result).toEqual(payload);
  });

  it("preserves backend onboarding detail when the request is rejected", async () => {
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify({ detail: "机器人 ID 已存在：R5" }), {
        status: 409,
        headers: { "Content-Type": "application/json" }
      })
    ));

    await expect(addRobot(
      "http://127.0.0.1:8011",
      "session-1",
      { robot: { id: "R5" } } as AddRobotRequest,
      fetcher
    )).rejects.toThrow("session robot onboarding failed: 机器人 ID 已存在：R5");
  });

  it("posts a create-session request and returns the session payload", async () => {
    const request = {
      scenario: { id: "candidate" },
      options: { avoidConflicts: true, includeDynamic: true }
    } as CreateSessionRequest;
    const payload = { sessionId: "session-created" } as SessionResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await createSession("/api", request, fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      "/api/sessions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request)
      }
    );
    expect(result).toEqual(payload);
  });

  it("includes backend create error detail in thrown errors", async () => {
    const request = { scenario: { id: "candidate" } } as CreateSessionRequest;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify({ detail: "动态故障机器人不存在：MISSING" }), {
        status: 422,
        headers: { "Content-Type": "application/json" }
      })
    ));

    await expect(createSession("http://127.0.0.1:8011", request, fetcher))
      .rejects.toThrow("session failed: 动态故障机器人不存在：MISSING");
  });

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

    const result = await resetSession("/api", "session-1", fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      "/api/sessions/session-1/reset",
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

    const result = await deleteSession("/api", "session-1", fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      "/api/sessions/session-1",
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
