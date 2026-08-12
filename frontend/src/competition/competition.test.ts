import { describe, expect, it, vi } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  CompetitionApp,
  CompetitionDemoController,
  loadCompetitionManifest,
  resolveCompetitionEntry
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
});

describe("competition manifest and controller", () => {
  it("loads Task 1 JSON and rejects an unknown schema version", () => {
    expect(loadCompetitionManifest("main").schemaVersion).toBe(1);
    expect(() => loadCompetitionManifest("main", { schemaVersion: 2 })).toThrow("不支持的竞赛清单版本");
  });

  it("single-step and automatic execution reach the same completed state", async () => {
    const makeController = () => new CompetitionDemoController("safety", false, vi.fn(async () => new Response(JSON.stringify(payload({ currentTime: 700 })), { status: 200 })));
    const stepped = makeController();
    await stepped.prepare();
    while (stepped.snapshot.runState !== "completed") await stepped.step();
    const automatic = makeController();
    await automatic.runAutomatically();
    expect(stepped.snapshot.runState).toBe("completed");
    expect(automatic.snapshot).toMatchObject({ runState: "completed", stepIndex: stepped.snapshot.stepIndex });
  });

  it("recording automation uses the fixed accelerated narration cadence", async () => {
    const waits: number[] = [];
    const controller = new CompetitionDemoController(
      "safety",
      true,
      vi.fn(async () => new Response(JSON.stringify(payload({ currentTime: 700 })), { status: 200 })),
      async (milliseconds) => { waits.push(milliseconds); }
    );
    await controller.runAutomatically();
    expect(waits).toEqual([350, 350]);
    expect(controller.snapshot.runState).toBe("completed");
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

  it("pauses the main demo on a safety intervention and reset returns to idle", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12, safetyIntervention: { time: 12 } as never })), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload({ currentTime: 12, safetyIntervention: { time: 12 } as never, result: { ...payload().result, tasks: [{ id: "DEMO-URGENT-12" }] } as never })), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload()), { status: 200 }));
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
});
