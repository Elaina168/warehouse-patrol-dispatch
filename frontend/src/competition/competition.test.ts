import { describe, expect, it, vi } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  CompetitionApp,
  CompetitionDemoController,
  loadCompetitionManifest,
  resolveCompetitionEntry,
  validateMainAcceptance
} from "./competition";
import { RootApplication } from "../main";
import type { SessionResult } from "../domain/types";

const payload = (overrides: Partial<SessionResult> = {}) => ({
  sessionId: "demo-session",
  currentTime: 0,
  safetyIntervention: null,
  completedTaskCount: 0,
  result: {
    extraBlocked: [],
    unavailableRobotIds: [],
    tasks: [],
    metrics: { conflictCount: 0, failureCount: 0, deadlineMissCount: 0 }
  },
  ...overrides
}) as SessionResult;

const safetyPayload = (
  currentTime: number,
  consecutiveCount: number,
  positions: [[number, number], [number, number]] = [[0, 0], [2, 0]]
) => payload({
  currentTime,
  safetyIntervention: {
    time: currentTime,
    type: "edge",
    robots: ["R1", "R2"],
    cell: [1, 0]
  },
  safetyStall: consecutiveCount >= 3 ? {
    conflict: { time: currentTime, type: "edge", robots: ["R1", "R2"], cell: [1, 0] },
    consecutiveCount,
    firstInterventionTime: 2,
    latestInterventionTime: currentTime
  } : null,
  robotStates: [
    { robotId: "R1", name: "R1", position: positions[0], status: "waiting", battery: 90, load: 1, moveTicks: 1, currentTaskId: "T1" },
    { robotId: "R2", name: "R2", position: positions[1], status: "waiting", battery: 90, load: 1, moveTicks: 1, currentTaskId: "T2" }
  ],
  metricsHistory: [{
    time: currentTime,
    completedTaskCount: 0,
    activeTaskCount: 2,
    pendingTaskCount: 0,
    travelledDistance: 0,
    activeConflictCount: 0,
    deadlineMissCount: 0,
    replanTimeMs: 1
  }],
  result: { ...payload().result, metrics: { ...payload().result.metrics, conflictCount: 1 } } as never
});

const safetyFetcher = () => vi.fn()
  .mockResolvedValueOnce(new Response(JSON.stringify(payload()), { status: 200 }))
  .mockResolvedValueOnce(new Response(JSON.stringify(safetyPayload(2, 1)), { status: 200 }))
  .mockResolvedValueOnce(new Response(JSON.stringify(safetyPayload(3, 2)), { status: 200 }))
  .mockResolvedValueOnce(new Response(JSON.stringify(safetyPayload(4, 3)), { status: 200 }));

describe("competition query entry", () => {
  it("keeps the default dashboard for ordinary URLs and selects both 3S demos explicitly", () => {
    expect(resolveCompetitionEntry("")).toBeNull();
    expect(resolveCompetitionEntry("?competition=3s&demo=main")).toEqual({ demo: "main", record: false });
    expect(resolveCompetitionEntry("?competition=3s&demo=safety&record=1")).toEqual({ demo: "safety", record: true });
  });

  it("renders visible competition controls and hides development controls when recording", () => {
    const normal = renderToStaticMarkup(React.createElement(CompetitionApp, { entry: { demo: "main", record: false } }));
    const recorded = renderToStaticMarkup(React.createElement(CompetitionApp, { entry: { demo: "main", record: true } }));
    expect(normal).toContain("3S 杯主演示模式");
    expect(normal).toContain("当前步骤");
    expect(normal).toContain("后置条件");
    expect(normal).toContain("单步执行");
    expect(normal).toContain("开发控件");
    expect(recorded).toContain("录制节奏");
    expect(recorded).not.toContain("开发控件");
  });

  it("routes the public application entry to the visible competition screen", () => {
    const html = renderToStaticMarkup(React.createElement(RootApplication, { search: "?competition=3s&demo=safety" }));
    expect(html).toContain("3S 杯安全门演示模式");
    expect(html).toContain("竞赛演示控制");
  });

  it("states the observable safety-wait boundary without claiming full trajectory proof", () => {
    const html = renderToStaticMarkup(React.createElement(CompetitionApp, { entry: { demo: "safety", record: false } }));
    expect(html).toContain("每次返回状态证明危险移动未提交");
    expect(html).not.toContain("轨迹全程证明");
  });
});

describe("competition manifest and controller", () => {
  it("loads Task 1 JSON and rejects an unknown schema version", () => {
    expect(loadCompetitionManifest("main").schemaVersion).toBe(1);
    expect(() => loadCompetitionManifest("main", { schemaVersion: 2 })).toThrow("不支持的竞赛清单版本");
  });

  it("single-step and automatic execution reach the same completed state", async () => {
    const makeController = () => new CompetitionDemoController("safety", false, safetyFetcher());
    const stepped = makeController();
    await stepped.prepare();
    while (stepped.snapshot.runState !== "completed") await stepped.step();
    const automatic = makeController();
    await automatic.runAutomatically();
    expect(stepped.snapshot.runState).toBe("completed");
    expect(automatic.snapshot).toMatchObject({ runState: "completed", stepIndex: stepped.snapshot.stepIndex });
  });

  it("advances the safety manifest through T=2 intervention and three observable safe waits", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(safetyPayload(2, 1)), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(safetyPayload(3, 2)), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(safetyPayload(4, 3)), { status: 200 }));
    const controller = new CompetitionDemoController("safety", false, fetcher);
    await controller.prepare();
    await controller.step();
    expect(controller.snapshot).toMatchObject({ stepIndex: 1, session: { currentTime: 2 } });
    await controller.step();
    await controller.step();
    expect(controller.snapshot).toMatchObject({
      runState: "completed",
      stepIndex: 3,
      session: { safetyStall: { consecutiveCount: 3 } }
    });
    expect(fetcher.mock.calls.slice(1).map(([, init]) => JSON.parse(String(init?.body)).currentTime)).toEqual([2, 3, 4]);
  });

  it("recording automation uses the fixed accelerated narration cadence", async () => {
    const waits: number[] = [];
    const controller = new CompetitionDemoController(
      "safety",
      true,
      safetyFetcher(),
      async (milliseconds) => { waits.push(milliseconds); }
    );
    await controller.runAutomatically();
    expect(waits).toEqual([350, 350, 350]);
    expect(controller.snapshot.runState).toBe("completed");
  });

  it("publishes every running, step, postcondition, and recording-pause snapshot", async () => {
    const snapshots: Array<{ runState: string; stepIndex: number; currentStep: string; message: string; postcondition: string }> = [];
    const controller = new CompetitionDemoController(
      "safety",
      true,
      safetyFetcher(),
      async () => undefined
    );
    controller.subscribe((snapshot) => snapshots.push({
      runState: snapshot.runState,
      stepIndex: snapshot.stepIndex,
      currentStep: snapshot.currentStep,
      message: snapshot.message,
      postcondition: snapshot.postcondition
    }));
    await controller.runAutomatically();
    expect(snapshots).toContainEqual({ runState: "preparing", stepIndex: 0, currentStep: "准备演示", message: "正在创建固定演示会话", postcondition: "创建固定演示会话" });
    expect(snapshots).toContainEqual({ runState: "running", stepIndex: 0, currentStep: "T=2 observeSafetyWait", message: "正在执行安全演示步骤", postcondition: "执行 T=2 observeSafetyWait" });
    expect(snapshots).toContainEqual({ runState: "paused", stepIndex: 1, currentStep: "T=3 observeSafetyWait", message: "录制讲解停顿中", postcondition: "录制讲解停顿 350ms" });
    expect(snapshots.at(-1)).toMatchObject({ runState: "completed", stepIndex: 3 });
  });

  it("checks the session with GET before retrying an uncertain write", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12 })), { status: 200 }))
      .mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12, result: { ...payload().result, tasks: [{ id: "DEMO-URGENT-12" }] } as never })), { status: 200 }));
    const controller = new CompetitionDemoController("main", false, fetcher);
    await controller.prepare();
    await controller.step();
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/sessions",
      "/api/sessions/demo-session/tasks",
      "/api/sessions/demo-session"
    ]);
  });

  it("requires exact tick confirmation and the complete main acceptance contract", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload()), { status: 200 }))
      .mockResolvedValueOnce(new TypeError("unused") as never);
    const controller = new CompetitionDemoController("main", false, fetcher);
    await controller.prepare();
    const overrunFetcher = vi.fn()
      .mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 13 })), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 13, result: { ...payload().result, tasks: [{ id: "DEMO-URGENT-12" }] } as never })), { status: 200 }));
    (controller as unknown as { fetcher: typeof fetch }).fetcher = overrunFetcher;
    await controller.step();
    expect(controller.snapshot).toMatchObject({ runState: "failed", pauseReason: "步骤后置条件失败" });

    const incompleteFinal = payload({
      currentTime: 700,
      completedTaskCount: 6,
      metricsHistory: [{ time: 700, activeConflictCount: 0 }] as never,
      result: { ...payload().result, metrics: { conflictCount: 0, failureCount: 0, deadlineMissCount: 0 } } as never
    });
    expect(() => validateMainAcceptance(incompleteFinal)).toThrow("主演示验收后置条件失败");
    expect(() => validateMainAcceptance(payload({
      currentTime: 700,
      completedTaskCount: 7,
      metricsHistory: [{ time: 700, activeConflictCount: 1 }] as never,
      result: { ...payload().result, metrics: { conflictCount: 0, failureCount: 0, deadlineMissCount: 0 } } as never
    }))).toThrow("主演示验收后置条件失败");
  });

  it("pauses the main demo on a safety intervention and reset returns to idle", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12, safetyIntervention: { time: 12 } as never })), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ runtimeTaskCount: 0, runtimeEventCount: 0 })), { status: 200 }));
    const controller = new CompetitionDemoController("main", false, fetcher);
    await controller.prepare();
    await controller.step();
    expect(controller.snapshot).toMatchObject({ runState: "paused", pauseReason: "主演示触发 safetyIntervention" });
    await controller.reset();
    expect(controller.snapshot).toMatchObject({ runState: "idle", stepIndex: 0, pauseReason: null });
  });

  it("does not submit the scheduled write after a main-demo tick reveals a safety intervention", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12, safetyIntervention: { time: 12 } as never })), { status: 200 }));
    const controller = new CompetitionDemoController("main", false, fetcher);
    await controller.prepare();
    await controller.step();
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/sessions",
      "/api/sessions/demo-session/tick"
    ]);
    expect(controller.snapshot.runState).toBe("paused");
  });

  it("confirms an uncertain reset with GET and does not repeat an already-applied reset", async () => {
    const resetPayload = payload({ currentTime: 0, runtimeTaskCount: 0, runtimeEventCount: 0 });
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12 })), { status: 200 }))
      .mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValueOnce(new Response(JSON.stringify(resetPayload), { status: 200 }));
    const snapshots: string[] = [];
    const controller = new CompetitionDemoController("main", false, fetcher);
    controller.subscribe((snapshot) => snapshots.push(snapshot.runState));
    await controller.prepare();
    await expect(controller.reset()).resolves.toBeUndefined();
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/sessions",
      "/api/sessions/demo-session/reset",
      "/api/sessions/demo-session"
    ]);
    expect(controller.snapshot).toMatchObject({ runState: "idle", session: { currentTime: 0 } });
    expect(snapshots.at(-1)).toBe("idle");
  });

  it("retries an uncertain unapplied reset once and converts retry errors to notified failure", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12 })), { status: 200 }))
      .mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12 })), { status: 200 }))
      .mockResolvedValueOnce(new Response("reset failed", { status: 500 }));
    const snapshots: string[] = [];
    const controller = new CompetitionDemoController("main", false, fetcher);
    controller.subscribe((snapshot) => snapshots.push(snapshot.runState));
    await controller.prepare();
    await expect(controller.reset()).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(controller.snapshot).toMatchObject({ runState: "failed", pauseReason: "HTTP 500" });
    expect(snapshots.at(-1)).toBe("failed");
  });

  it("converts a recording-pause error to a notified failure instead of rejecting", async () => {
    const snapshots: string[] = [];
    const controller = new CompetitionDemoController(
      "safety",
      true,
      safetyFetcher(),
      async () => { throw new Error("pause failed"); }
    );
    controller.subscribe((snapshot) => snapshots.push(snapshot.runState));
    await expect(controller.runAutomatically()).resolves.toBeUndefined();
    expect(controller.snapshot).toMatchObject({ runState: "failed", pauseReason: "pause failed" });
    expect(snapshots.at(-1)).toBe("failed");
  });
});
