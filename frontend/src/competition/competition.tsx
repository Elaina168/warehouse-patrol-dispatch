import React, { useMemo, useState } from "react";

import mainManifestJson from "../../../competition/3s/manifests/main-demo.json";
import safetyManifestJson from "../../../competition/3s/manifests/safety-demo.json";
import { scenarios } from "../domain/scenarios";
import type { Cell, DispatchOptions, Scenario, SessionResult, Task } from "../domain/types";

export type CompetitionRunState = "idle" | "preparing" | "running" | "paused" | "completed" | "failed";
export type CompetitionDemoKind = "main" | "safety";

export type CompetitionDemoStep =
  | { time: number; action: "addTask"; task: Task }
  | { time: number; action: "addBlockedCell" | "removeBlockedCell"; cell: Cell }
  | { time: number; action: "failRobot" | "restoreRobot"; robotId: string }
  | { time: number; action: "accept" };

type CompetitionScenario = Omit<Scenario, "shelves"> & { shelves?: Scenario["shelves"] };

export type CompetitionDemoManifest = {
  schemaVersion: 1;
  kind: "main-demo" | "safety-demo";
  scenarioId?: string;
  scenario?: CompetitionScenario;
  options: DispatchOptions;
  steps?: CompetitionDemoStep[];
};

export type CompetitionEntry = { demo: CompetitionDemoKind; record: boolean };

export type CompetitionControllerSnapshot = {
  runState: CompetitionRunState;
  stepIndex: number;
  pauseReason: string | null;
  postcondition: string;
  session: SessionResult | null;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export function resolveCompetitionEntry(search: string): CompetitionEntry | null {
  const query = new URLSearchParams(search);
  if (query.get("competition") !== "3s") return null;
  const demo = query.get("demo");
  if (demo !== "main" && demo !== "safety") return null;
  return { demo, record: query.get("record") === "1" };
}

export function loadCompetitionManifest(
  demo: CompetitionDemoKind,
  candidate: unknown = demo === "main" ? mainManifestJson : safetyManifestJson
): CompetitionDemoManifest {
  if (!candidate || typeof candidate !== "object" || (candidate as { schemaVersion?: unknown }).schemaVersion !== 1) {
    throw new Error("不支持的竞赛清单版本");
  }
  return candidate as CompetitionDemoManifest;
}

function sameCell(left: Cell, right: Cell): boolean {
  return left[0] === right[0] && left[1] === right[1];
}

function responseError(response: Response): Error {
  return new Error(`HTTP ${response.status}`);
}

export class CompetitionDemoController {
  private manifest: CompetitionDemoManifest;
  private fetcher: typeof fetch;
  private state: CompetitionControllerSnapshot = {
    runState: "idle",
    stepIndex: 0,
    pauseReason: null,
    postcondition: "等待准备",
    session: null
  };

  constructor(
    demo: CompetitionDemoKind,
    readonly record: boolean,
    fetcher: typeof fetch = fetch,
    private readonly wait: (milliseconds: number) => Promise<void> = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))
  ) {
    this.manifest = loadCompetitionManifest(demo);
    this.fetcher = fetcher;
  }

  get snapshot(): CompetitionControllerSnapshot {
    return this.state;
  }

  async prepare(): Promise<void> {
    this.state = { ...this.state, runState: "preparing", pauseReason: null, postcondition: "创建固定演示会话" };
    const scenario = this.manifest.scenario
      ?? scenarios.find((candidate) => candidate.id === this.manifest.scenarioId);
    if (!scenario) {
      this.fail("清单场景不存在");
      return;
    }
    try {
      const response = await this.fetcher(`${API_BASE}/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario, options: this.manifest.options })
      });
      if (!response.ok) throw responseError(response);
      const session = await response.json() as SessionResult;
      this.state = { ...this.state, runState: "paused", session, postcondition: "会话已准备，可单步或自动运行" };
    } catch (error) {
      this.fail(error instanceof Error ? error.message : "创建会话失败");
    }
  }

  async step(): Promise<void> {
    if (this.state.runState === "idle" && !this.state.session) await this.prepare();
    const activeSession = this.state.session;
    if (!activeSession || this.state.runState === "failed" || this.state.runState === "completed") return;
    const steps = this.steps();
    const step = steps[this.state.stepIndex];
    if (!step) {
      this.state = { ...this.state, runState: "completed", postcondition: "演示完成" };
      return;
    }
    this.state = { ...this.state, runState: "running", pauseReason: null, postcondition: `执行 T=${step.time} ${step.action}` };
    try {
      let session = activeSession;
      if (session.currentTime < step.time) {
        session = await this.writeWithConfirmation(
          `${API_BASE}/sessions/${encodeURIComponent(session.sessionId)}/tick`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ currentTime: step.time }) },
          (candidate) => candidate.currentTime >= step.time
        );
      }
      if (this.manifest.kind === "main-demo" && session.safetyIntervention) {
        this.state = { ...this.state, runState: "paused", session, pauseReason: "主演示触发 safetyIntervention", postcondition: "安全门已暂停主演示" };
        return;
      }
      session = await this.applyStep(step, session);
      if (this.manifest.kind === "main-demo" && session.safetyIntervention) {
        this.state = { ...this.state, runState: "paused", session, pauseReason: "主演示触发 safetyIntervention", postcondition: "安全门已暂停主演示" };
        return;
      }
      const nextIndex = this.state.stepIndex + 1;
      this.state = {
        ...this.state,
        session,
        stepIndex: nextIndex,
        runState: nextIndex === steps.length ? "completed" : "paused",
        postcondition: nextIndex === steps.length ? "全部后置条件已满足" : "当前步骤后置条件已满足"
      };
    } catch (error) {
      this.fail(error instanceof Error ? error.message : "步骤执行失败");
    }
  }

  async runAutomatically(): Promise<void> {
    if (this.state.runState === "idle") await this.prepare();
    while (this.state.runState !== "completed" && this.state.runState !== "failed" && this.state.pauseReason === null) {
      await this.step();
      if (this.record) await this.wait(350);
    }
  }

  async reset(): Promise<void> {
    if (this.state.session) {
      const response = await this.fetcher(`${API_BASE}/sessions/${encodeURIComponent(this.state.session.sessionId)}/reset`, { method: "POST" });
      if (!response.ok) {
        this.fail(`HTTP ${response.status}`);
        return;
      }
      const session = await response.json() as SessionResult;
      this.state = { runState: "idle", stepIndex: 0, pauseReason: null, postcondition: "等待准备", session };
      return;
    }
    this.state = { runState: "idle", stepIndex: 0, pauseReason: null, postcondition: "等待准备", session: null };
  }

  private steps(): CompetitionDemoStep[] {
    if (this.manifest.steps) return this.manifest.steps;
    return [
      { time: 2, action: "accept" },
      { time: 4, action: "accept" }
    ];
  }

  private async applyStep(step: CompetitionDemoStep, session: SessionResult): Promise<SessionResult> {
    if (step.action === "accept") {
      if (this.manifest.kind === "main-demo") {
        const metrics = session.result.metrics;
        if (metrics.conflictCount || metrics.failureCount || metrics.deadlineMissCount) throw new Error("主演示验收后置条件失败");
      }
      return session;
    }
    const sessionUrl = `${API_BASE}/sessions/${encodeURIComponent(session.sessionId)}`;
    if (step.action === "addTask") {
      return this.writeWithConfirmation(`${sessionUrl}/tasks`, jsonPost({ task: step.task }), (candidate) => candidate.result.tasks.some((task) => task.id === step.task.id));
    }
    if (step.action === "addBlockedCell") {
      return this.writeWithConfirmation(`${sessionUrl}/blocked-cells`, jsonPost({ cell: step.cell, currentTime: step.time }), (candidate) => candidate.result.extraBlocked.some((cell) => sameCell(cell, step.cell)));
    }
    if (step.action === "removeBlockedCell") {
      return this.writeWithConfirmation(`${sessionUrl}/blocked-cells/remove`, jsonPost({ cell: step.cell, currentTime: step.time }), (candidate) => !candidate.result.extraBlocked.some((cell) => sameCell(cell, step.cell)));
    }
    if (step.action === "failRobot") {
      return this.writeWithConfirmation(`${sessionUrl}/failed-robots`, jsonPost({ robotId: step.robotId, currentTime: step.time }), (candidate) => candidate.result.unavailableRobotIds.includes(step.robotId));
    }
    if (step.action === "restoreRobot") {
      return this.writeWithConfirmation(`${sessionUrl}/failed-robots/restore`, jsonPost({ robotId: step.robotId, currentTime: step.time }), (candidate) => !candidate.result.unavailableRobotIds.includes(step.robotId));
    }
    throw new Error("不支持的竞赛步骤");
  }

  private async writeWithConfirmation(url: string, init: RequestInit, applied: (candidate: SessionResult) => boolean): Promise<SessionResult> {
    try {
      const response = await this.fetcher(url, init);
      if (!response.ok) throw responseError(response);
      const candidate = await response.json() as SessionResult;
      if (!applied(candidate)) throw new Error("步骤后置条件失败");
      return candidate;
    } catch (error) {
      if (!(error instanceof TypeError) || !this.state.session) throw error;
      const check = await this.fetcher(`${API_BASE}/sessions/${encodeURIComponent(this.state.session.sessionId)}`);
      if (!check.ok) throw responseError(check);
      const checked = await check.json() as SessionResult;
      if (applied(checked)) return checked;
      const retry = await this.fetcher(url, init);
      if (!retry.ok) throw responseError(retry);
      const retried = await retry.json() as SessionResult;
      if (!applied(retried)) throw new Error("步骤后置条件失败");
      return retried;
    }
  }

  private fail(reason: string): void {
    this.state = { ...this.state, runState: "failed", pauseReason: reason, postcondition: "演示已停止" };
  }
}

function jsonPost(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function CompetitionApp({ entry }: { entry: CompetitionEntry }) {
  const controller = useMemo(() => new CompetitionDemoController(entry.demo, entry.record), [entry.demo, entry.record]);
  const [snapshot, setSnapshot] = useState(controller.snapshot);
  const operate = async (action: () => Promise<void>) => {
    await action();
    setSnapshot({ ...controller.snapshot });
  };
  const title = entry.demo === "main" ? "3S 杯主演示模式" : "3S 杯安全门演示模式";
  return <main className={`competition-shell${entry.record ? " competition-recording" : ""}`}>
    <header><p>仓巡智调</p><h1>{title}</h1></header>
    <section className="competition-status" aria-live="polite">
      <p>状态：{snapshot.runState}</p>
      <p>当前步骤：{snapshot.stepIndex + 1}</p>
      <p>后置条件：{snapshot.postcondition}</p>
      <p>暂停原因：{snapshot.pauseReason ?? "无"}</p>
    </section>
    <nav aria-label="竞赛演示控制">
      <button onClick={() => void operate(() => controller.reset())}>重置</button>
      <button onClick={() => void operate(() => controller.step())}>单步执行</button>
      <button onClick={() => void operate(() => controller.runAutomatically())}>自动运行</button>
    </nav>
    {entry.record ? <p>录制节奏：固定讲解停顿 · 加速执行</p> : <aside>开发控件：步骤诊断已显示</aside>}
  </main>;
}
