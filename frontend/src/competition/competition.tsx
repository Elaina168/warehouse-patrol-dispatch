import React, { useEffect, useMemo, useState } from "react";

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

type SafetyCompetitionStep = { time: number; action: "observeSafetyWait"; expectedConsecutiveCount: number };

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
  currentStep: string;
  message: string;
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
  private listeners = new Set<(snapshot: CompetitionControllerSnapshot) => void>();
  private state: CompetitionControllerSnapshot = {
    runState: "idle",
    stepIndex: 0,
    currentStep: "等待开始",
    message: "等待操作",
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

  subscribe(listener: (snapshot: CompetitionControllerSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => this.listeners.delete(listener);
  }

  private update(next: CompetitionControllerSnapshot): void {
    this.state = next;
    for (const listener of this.listeners) listener(this.snapshot);
  }

  async prepare(): Promise<void> {
    this.update({ ...this.state, runState: "preparing", currentStep: "准备演示", message: "正在创建固定演示会话", pauseReason: null, postcondition: "创建固定演示会话" });
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
      this.update({ ...this.state, runState: "paused", currentStep: this.stepLabel(0), message: "演示已准备", session, postcondition: "会话已准备，可单步或自动运行" });
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
      this.update({ ...this.state, runState: "completed", currentStep: "演示完成", message: "全部步骤已完成", postcondition: "演示完成" });
      return;
    }
    this.update({ ...this.state, runState: "running", currentStep: `T=${step.time} ${step.action}`, message: "正在执行安全演示步骤", pauseReason: null, postcondition: `执行 T=${step.time} ${step.action}` });
    try {
      let session = activeSession;
      if (session.currentTime < step.time) {
        session = await this.writeWithConfirmation(
          `${API_BASE}/sessions/${encodeURIComponent(session.sessionId)}/tick`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ currentTime: step.time }) },
          (candidate) => candidate.currentTime === step.time
        );
      }
      if (this.manifest.kind === "main-demo" && session.safetyIntervention) {
        this.update({ ...this.state, runState: "paused", session, pauseReason: "主演示触发 safetyIntervention", postcondition: "安全门已暂停主演示" });
        return;
      }
      session = await this.applyStep(step, session);
      if (this.manifest.kind === "main-demo" && session.safetyIntervention) {
        this.update({ ...this.state, runState: "paused", session, pauseReason: "主演示触发 safetyIntervention", postcondition: "安全门已暂停主演示" });
        return;
      }
      const nextIndex = this.state.stepIndex + 1;
      this.update({
        ...this.state,
        session,
        stepIndex: nextIndex,
        currentStep: nextIndex === steps.length ? "演示完成" : this.stepLabel(nextIndex),
        message: nextIndex === steps.length ? "全部步骤已完成" : "当前步骤已完成",
        runState: nextIndex === steps.length ? "completed" : "paused",
        postcondition: nextIndex === steps.length ? "全部后置条件已满足" : "当前步骤后置条件已满足"
      });
    } catch (error) {
      this.fail(error instanceof Error ? error.message : "步骤执行失败");
    }
  }

  async runAutomatically(): Promise<void> {
    try {
      if (this.state.runState === "idle") await this.prepare();
      while (this.state.runState !== "completed" && this.state.runState !== "failed" && this.state.pauseReason === null) {
        await this.step();
        if (this.record) {
          this.update({ ...this.state, message: "录制讲解停顿中", postcondition: "录制讲解停顿 350ms" });
          await this.wait(350);
        }
      }
    } catch (error) {
      this.fail(error instanceof Error ? error.message : "自动演示失败");
    }
  }

  async reset(): Promise<void> {
    if (this.state.session) {
      try {
        const sessionUrl = `${API_BASE}/sessions/${encodeURIComponent(this.state.session.sessionId)}`;
        const session = await this.writeWithConfirmation(
          `${sessionUrl}/reset`,
          { method: "POST" },
          resetPostcondition
        );
        this.update({ runState: "idle", stepIndex: 0, currentStep: "等待开始", message: "重置完成", pauseReason: null, postcondition: "等待准备", session });
      } catch (error) {
        this.fail(error instanceof Error ? error.message : "重置失败");
      }
      return;
    }
    this.update({ runState: "idle", stepIndex: 0, currentStep: "等待开始", message: "等待操作", pauseReason: null, postcondition: "等待准备", session: null });
  }

  private steps(): Array<CompetitionDemoStep | SafetyCompetitionStep> {
    if (this.manifest.steps) return this.manifest.steps;
    return [
      { time: 2, action: "observeSafetyWait", expectedConsecutiveCount: 1 },
      { time: 3, action: "observeSafetyWait", expectedConsecutiveCount: 2 },
      { time: 4, action: "observeSafetyWait", expectedConsecutiveCount: 3 }
    ];
  }

  private stepLabel(index: number): string {
    const step = this.steps()[index];
    return step ? `T=${step.time} ${step.action}` : "演示完成";
  }

  private async applyStep(step: CompetitionDemoStep | SafetyCompetitionStep, session: SessionResult): Promise<SessionResult> {
    if (step.action === "observeSafetyWait") {
      validateSafetyWait(session, step.time, step.expectedConsecutiveCount);
      return session;
    }
    if (step.action === "accept") {
      if (this.manifest.kind === "main-demo") {
        validateMainAcceptance(session);
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
    this.update({ ...this.state, runState: "failed", message: reason, pauseReason: reason, postcondition: "演示已停止" });
  }
}

function resetPostcondition(session: SessionResult): boolean {
  return session.currentTime === 0
    && session.runtimeTaskCount === 0
    && session.runtimeEventCount === 0;
}

export function validateMainAcceptance(session: SessionResult): void {
  const metrics = session.result.metrics;
  if (
    session.completedTaskCount !== 7
    || session.metricsHistory.at(-1)?.activeConflictCount !== 0
    || metrics.conflictCount !== 0
    || metrics.failureCount !== 0
    || metrics.deadlineMissCount !== 0
  ) {
    throw new Error("主演示验收后置条件失败");
  }
}

function validateSafetyWait(session: SessionResult, time: number, expectedConsecutiveCount: number): void {
  if (session.currentTime !== time || session.safetyIntervention?.time !== time) {
    throw new Error(`安全门未在 T=${time} 产生预期拦截`);
  }
  if (new Set(session.robotStates.map((state) => `${state.position[0]},${state.position[1]}`)).size !== session.robotStates.length) {
    throw new Error("安全等待返回位置不唯一");
  }
  if (session.metricsHistory.at(-1)?.activeConflictCount !== 0) {
    throw new Error("安全等待仍存在活动冲突");
  }
  if (expectedConsecutiveCount >= 3 && session.safetyStall?.consecutiveCount !== expectedConsecutiveCount) {
    throw new Error("安全停滞计数未达到三次");
  }
}

function jsonPost(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function CompetitionApp({ entry }: { entry: CompetitionEntry }) {
  const controller = useMemo(() => new CompetitionDemoController(entry.demo, entry.record), [entry.demo, entry.record]);
  const [snapshot, setSnapshot] = useState(controller.snapshot);
  useEffect(() => controller.subscribe((next) => setSnapshot({ ...next })), [controller]);
  const operate = async (action: () => Promise<void>) => {
    await action();
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
    {entry.demo === "safety" ? <p>专项边界：每次返回状态证明危险移动未提交，并观察机器人安全等待；前端不声称拥有后端内部轨迹。</p> : null}
    {entry.record ? <p>录制节奏：固定讲解停顿 · 加速执行</p> : <aside>开发控件：步骤诊断已显示</aside>}
  </main>;
}
