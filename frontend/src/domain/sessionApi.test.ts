import { describe, expect, it, vi } from "vitest";

import {
  compareConflictAvoidance,
  compareDynamicReplanning,
  compareReplanWindows,
  compareScaleCases,
  runOnlinePressureExperiment,
  runSeededPressureExperiment,
  deleteSession,
  resetSession
} from "./sessionApi";
import type {
  ConflictAvoidanceExperimentResult,
  DeleteSessionResult,
  DynamicReplanningExperimentResult,
  OnlinePressureExperimentResult,
  ReplanWindowExperimentResult,
  ScaleExperimentResult,
  SeededPressureExperimentResult,
  Scenario,
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

  it("posts scenarios to the conflict avoidance experiment endpoint", async () => {
    const scenario = { id: "scenario-1" } as Scenario;
    const payload = {
      scenarioId: "scenario-1",
      cases: []
    } as ConflictAvoidanceExperimentResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await compareConflictAvoidance(
      "http://127.0.0.1:8011",
      { scenario, options: { avoidConflicts: true, includeDynamic: false, assignmentReplanWindow: 24 } },
      fetcher
    );

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/experiments/conflict-avoidance",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario,
          options: { avoidConflicts: true, includeDynamic: false, assignmentReplanWindow: 24 }
        })
      }
    );
    expect(result).toEqual(payload);
  });

  it("posts scenarios to the dynamic replanning experiment endpoint", async () => {
    const scenario = { id: "scenario-1" } as Scenario;
    const payload = {
      scenarioId: "scenario-1",
      cases: []
    } as DynamicReplanningExperimentResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await compareDynamicReplanning(
      "http://127.0.0.1:8011",
      { scenario, options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 24 } },
      fetcher
    );

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/experiments/dynamic-replanning",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario,
          options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 24 }
        })
      }
    );
    expect(result).toEqual(payload);
  });

  it("posts scenarios and windows to the replan window experiment endpoint", async () => {
    const scenario = { id: "scenario-1" } as Scenario;
    const payload = {
      scenarioId: "scenario-1",
      cases: []
    } as ReplanWindowExperimentResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await compareReplanWindows(
      "http://127.0.0.1:8011",
      {
        scenario,
        options: { avoidConflicts: true, includeDynamic: false, assignmentReplanWindow: 24 },
        windows: [4, 24]
      },
      fetcher
    );

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/experiments/replan-window",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario,
          options: { avoidConflicts: true, includeDynamic: false, assignmentReplanWindow: 24 },
          windows: [4, 24]
        })
      }
    );
    expect(result).toEqual(payload);
  });

  it("posts scale cases to the scale experiment endpoint", async () => {
    const scenario = { id: "scenario-1" } as Scenario;
    const payload = {
      cases: []
    } as ScaleExperimentResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await compareScaleCases(
      "http://127.0.0.1:8011",
      {
        cases: [{ label: "small", scenario }],
        options: { avoidConflicts: true, includeDynamic: false, assignmentReplanWindow: 24 }
      },
      fetcher
    );

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/experiments/scale",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cases: [{ label: "small", scenario }],
          options: { avoidConflicts: true, includeDynamic: false, assignmentReplanWindow: 24 }
        })
      }
    );
    expect(result).toEqual(payload);
  });

  it("posts options to the seeded pressure experiment endpoint", async () => {
    const payload = {
      cases: [],
      summary: {
        caseCount: 3,
        largestRobotCount: 8,
        largestTaskCount: 27,
        totalTaskCount: 65,
        totalAssignedTaskCount: 65,
        stableCaseCount: 3,
        stableRatePercent: 100,
        completionRatePercent: 100,
        planningTimeBudgetMs: 2000,
        withinPlanningTimeBudgetCount: 3,
        withinPlanningTimeBudgetRatePercent: 100,
        maxConflictCount: 0,
        totalDeadlineMissCount: 0,
        totalFailureCount: 0,
        totalDistance: 804,
        averageDistancePerTask: 12.4,
        maxMakespan: 61,
        averageReplanTimeMs: 6.2,
        maxReplanTimeMs: 18.5
      }
    } as SeededPressureExperimentResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await runSeededPressureExperiment(
      "http://127.0.0.1:8011",
      {
        caseSet: "extended",
        options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 120 }
      },
      fetcher
    );

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/experiments/seeded-pressure",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          caseSet: "extended",
          options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 120 }
        })
      }
    );
    expect(result).toEqual(payload);
    expect(result.summary.totalAssignedTaskCount).toBe(65);
  });

  it("posts options to the online pressure experiment endpoint", async () => {
    const payload = {
      cases: [],
      summary: {
        caseCount: 1,
        totalTaskCount: 17,
        totalCoveredTaskCount: 17,
        totalCompletedTaskCount: 5,
        totalAssignedTaskCount: 13,
        stableCaseCount: 1,
        stableRatePercent: 100,
        completionRatePercent: 100,
        maxConflictCount: 0,
        totalDeadlineMissCount: 0,
        totalFailureCount: 0,
        totalRuntimeEventCount: 5,
        totalManualTaskCount: 1,
        totalStreamTaskCount: 1,
        totalDistance: 120,
        averageDistancePerTask: 7.1,
        maxMakespan: 54,
        averageReplanTimeMs: 15.2,
        maxReplanTimeMs: 15.2,
        maxMetricsHistoryCount: 15,
        maxEventLogCount: 20
      }
    } as OnlinePressureExperimentResult;
    const fetcher = vi.fn(async () => (
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    ));

    const result = await runOnlinePressureExperiment(
      "http://127.0.0.1:8011",
      {
        options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 120 }
      },
      fetcher
    );

    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/experiments/online-pressure",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 120 }
        })
      }
    );
    expect(result).toEqual(payload);
    expect(result.summary.totalCoveredTaskCount).toBe(17);
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
