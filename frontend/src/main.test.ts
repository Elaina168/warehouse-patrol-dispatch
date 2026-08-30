import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
// Vitest 在 Node.js 中运行；项目构建不包含 Node 类型，仅在样式回归中读取源码。
// @ts-expect-error 测试运行时提供 node:fs。
import { readFileSync } from "node:fs";
import {
  buildLiveMetrics,
  buildDispatchOptions,
  buildReplanStatus,
  assignmentReplanWindowLabel,
  assignmentReplanWindowStatusLabel,
  buildRecoveryTargets,
  recoveryActionLabel,
  buildTaskSnapshots,
  buildTaskQueueMetricRows,
  buildActiveRouteArrows,
  filterActiveRouteArrows,
  canJumpToEvent,
  visibleRuntimeEvents,
  isSelectedCell,
  isSelectedRobotCell,
  mapContextAction,
  robotContextAction,
  getVisibleMetricsHistory,
  getRuntimeActionTime,
  shouldAdvancePlaybackLocally,
  shouldContinueOnlinePlayback,
  shouldGenerateRandomTaskAtTime,
  shouldUseRuntimeRobotSnapshot,
  shouldRequestSessionTick,
  shouldReusePrecreatedScenario,
  parsePlaybackSpeed,
  parsePositiveIntegerInput,
  normalizeManualTaskDeadline,
  normalizeTaskPriority,
  buildRandomGeneratedTask,
  robotCapabilityLabels,
  supportedTaskTypes,
  nextMapPickTarget,
  applyMapPickToManualTask,
  applySessionRequestFailure,
  selectLatestConflictAlert,
  selectMapConflictMarkers,
  shouldPauseForSafetyIntervention,
  safetyInterventionLabel,
  safetyStallLabel,
  mergeSafetyInterventionMarker,
  isSafetyInterventionRobot,
  filterInitialScenarioTaskLabels,
  shouldDisplayConflictMarker,
  robotColorForIndex,
  robotMoveDurationLabel,
  routeHintsAfterSessionUpdate,
  parseScenario,
  parseCoordinateInput,
  buildManualTask,
  clearSessionViewState,
  createManualTaskForm,
  buildCreateSessionRequest,
  createSessionRequestCoordinator,
  dispatchSynchronizationLabel,
  MapBoard,
  LiveMetricsPanel,
  OperationalErrorNotice,
  resetSessionConflictState,
  RIGHTBAR_EVENT_LOG_CLASS,
  RIGHTBAR_TASK_QUEUE_CLASS,
  sortTaskSnapshotsForDisplay,
  splitTaskSnapshotsForDisplay,
  taskTimingFields,
  App,
  RootApplication
} from "./main";
import { scenarios } from "./domain/scenarios";
import { ApiRequestError } from "./domain/apiError";
import { settleCreatedSession } from "./domain/sessionApi";
import { runOnlineMutation } from "./domain/sessionRequestState";
import type { DispatchResult, RobotRuntimeStatus, Scenario, SessionResult, ShelfRuntimeState, Task, TaskType } from "./domain/types";

describe("project branding", () => {
  it("renders the official project name as the dashboard heading", () => {
    const markup = renderToStaticMarkup(createElement(App));

    expect(markup).toContain(
      "<h1>仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统</h1>"
    );
  });

  it("uses the official project name as the browser document title", () => {
    const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

    expect(html).toContain(
      "<title>仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统</title>"
    );
  });

  it("routes legacy competition query URLs to the full online dashboard", () => {
    const mainMarkup = renderToStaticMarkup(createElement(RootApplication, {
      search: "?competition=3s&demo=main&record=1"
    }));
    const safetyMarkup = renderToStaticMarkup(createElement(RootApplication, {
      search: "?competition=3s&demo=safety"
    }));

    for (const markup of [mainMarkup, safetyMarkup]) {
      expect(markup).toContain('<p class="eyebrow">在线调度控制台</p>');
      expect(markup).toContain("仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统");
      expect(markup).not.toContain("3S 杯主演示模式");
      expect(markup).not.toContain("3S 杯安全门演示模式");
      expect(markup).not.toContain("竞赛演示控制");
    }
  });
});

describe("session request coordination", () => {
  it("renders the runtime robot onboarding panel in the main dashboard", () => {
    const markup = renderToStaticMarkup(createElement(App));
    expect(markup).toContain("机器人接入");
    expect(markup).toContain("当前机器人管理");
    expect(markup).toContain("当前会话建立后可管理机器人。");
    expect(markup).toContain("接入位置");
    expect(markup).toContain("能力");
  });

  type RequestFailureTestState = {
    apiStatus: "checking" | "online" | "offline" | "error";
    dispatchStatus: "loading" | "ready" | "error";
    dispatchError: string | null;
    operationError: string | null;
  };

  function createRequestFailureState(): RequestFailureTestState {
    return {
      apiStatus: "checking",
      dispatchStatus: "loading",
      dispatchError: null,
      operationError: null
    };
  }

  function requestFailureSetters(state: RequestFailureTestState) {
    return {
      setApiStatus: (status: RequestFailureTestState["apiStatus"]) => {
        state.apiStatus = status;
      },
      setDispatchStatus: (status: RequestFailureTestState["dispatchStatus"]) => {
        state.dispatchStatus = status;
      },
      setDispatchError: (message: string) => {
        state.dispatchError = message;
      },
      setOperationError: (message: string) => {
        state.operationError = message;
      }
    };
  }

  it("reuses only the exact precreated scenario object", () => {
    const scenario = structuredClone(scenarios[0]);
    const sameIdClone = structuredClone(scenario);

    expect(shouldReusePrecreatedScenario(scenario, scenario)).toBe(true);
    expect(shouldReusePrecreatedScenario(scenario, sameIdClone)).toBe(false);
    expect(shouldReusePrecreatedScenario(null, scenario)).toBe(false);
  });

  it("delays only the built-in scenario even when an import reuses its ID", () => {
    const builtInScenario = scenarios[0];
    const options = buildDispatchOptions(true, true, 24, false);
    const importedScenario = structuredClone(builtInScenario);

    expect(
      buildCreateSessionRequest(builtInScenario, options, null).delayInitialPlanning
    ).toBe(true);
    expect(
      buildCreateSessionRequest(importedScenario, options, importedScenario).delayInitialPlanning
    ).toBe(false);
  });

  it("keeps create HTTP 4xx online without marking a missing session ready", () => {
    const state = createRequestFailureState();

    applySessionRequestFailure(
      new ApiRequestError(422, "session failed: invalid request"),
      { hasUsableSession: false },
      requestFailureSetters(state)
    );

    expect(state).toEqual({
      apiStatus: "online",
      dispatchStatus: "error",
      dispatchError: "session failed: invalid request",
      operationError: "session failed: invalid request"
    });
  });

  it("keeps a retained session ready after reset HTTP 4xx", () => {
    const state = createRequestFailureState();

    applySessionRequestFailure(
      new ApiRequestError(409, "session reset failed: invalid snapshot"),
      { hasUsableSession: true },
      requestFailureSetters(state)
    );

    expect(state).toEqual({
      apiStatus: "online",
      dispatchStatus: "ready",
      dispatchError: "session reset failed: invalid snapshot",
      operationError: "session reset failed: invalid snapshot"
    });

    expect(renderToStaticMarkup(createElement(OperationalErrorNotice, { message: state.operationError })))
      .toContain("session reset failed: invalid snapshot");
  });

  it("does not retain a missing session after reset HTTP 404", () => {
    const state = createRequestFailureState();

    applySessionRequestFailure(
      new ApiRequestError(404, "session reset failed: missing"),
      { hasUsableSession: true },
      requestFailureSetters(state)
    );

    expect(state).toEqual({
      apiStatus: "online",
      dispatchStatus: "error",
      dispatchError: "session reset failed: missing",
      operationError: "session reset failed: missing"
    });
  });

  it("clears both session and result before creating a replacement session", () => {
    let session = {} as SessionResult | null;
    let result = {} as DispatchResult | null;

    clearSessionViewState({
      setSession: (value) => {
        session = value;
      },
      setResult: (value) => {
        result = value;
      }
    });

    expect(session).toBeNull();
    expect(result).toBeNull();
  });

  it("keeps the synchronization label stable while a tick is in flight", () => {
    expect(dispatchSynchronizationLabel(false, "ready")).toBe("调度结果已同步");
    expect(dispatchSynchronizationLabel(true, "ready")).toBe("调度结果已同步");
    expect(dispatchSynchronizationLabel(false, "loading")).toBe("等待重规划结果");
    expect(dispatchSynchronizationLabel(false, "error")).toBe("调度同步失败");
    expect(dispatchSynchronizationLabel(true, "error")).toBe("调度同步失败");
  });

  it("does not call a blocked online mutation request", async () => {
    let calls = 0;

    await runOnlineMutation(false, async () => {
      calls += 1;
    });

    expect(calls).toBe(0);
  });

  it("calls an enabled online mutation request exactly once", async () => {
    let calls = 0;

    await runOnlineMutation(true, async () => {
      calls += 1;
    });

    expect(calls).toBe(1);
  });

  it("runs reset after older mutations and rejects their stale responses", async () => {
    const coordinator = createSessionRequestCoordinator();
    let releaseOlderMutation: (() => void) | undefined;
    const olderMutationBlocked = new Promise<void>((resolve) => {
      releaseOlderMutation = resolve;
    });
    const executionOrder: string[] = [];
    const appliedPayloads: string[] = [];
    const olderGeneration = coordinator.currentGeneration();

    const olderMutation = coordinator.enqueue(async () => {
      executionOrder.push("older-start");
      await olderMutationBlocked;
      executionOrder.push("older-finish");
      return "older-payload";
    }).then((payload) => {
      if (coordinator.isCurrent(olderGeneration)) appliedPayloads.push(payload);
    });

    const resetGeneration = coordinator.invalidate();
    const reset = coordinator.enqueue(async () => {
      executionOrder.push("reset");
      return "reset-payload";
    }).then((payload) => {
      if (coordinator.isCurrent(resetGeneration)) appliedPayloads.push(payload);
    });

    await Promise.resolve();
    expect(executionOrder).toEqual(["older-start"]);
    releaseOlderMutation?.();
    await Promise.all([olderMutation, reset]);

    expect(executionOrder).toEqual(["older-start", "older-finish", "reset"]);
    expect(appliedPayloads).toEqual(["reset-payload"]);
  });

  it("applies the newer create generation and deletes the older late response", async () => {
    const coordinator = createSessionRequestCoordinator();
    const applied: string[] = [];
    const deleted: string[] = [];
    const olderGeneration = coordinator.invalidate();
    const newerGeneration = coordinator.invalidate();
    const newerPayload = { sessionId: "session-new" } as SessionResult;
    const olderPayload = { sessionId: "session-old" } as SessionResult;
    let resolveOlder!: (payload: SessionResult) => void;
    const olderResponse = new Promise<SessionResult>((resolve) => {
      resolveOlder = resolve;
    });

    const olderSettlement = olderResponse.then((payload) =>
      settleCreatedSession(
        payload,
        coordinator.isCurrent(olderGeneration),
        (current) => applied.push(current.sessionId),
        async (sessionId) => {
          deleted.push(sessionId);
        }
      )
    );
    const newerSettlement = settleCreatedSession(
      newerPayload,
      coordinator.isCurrent(newerGeneration),
      (payload) => applied.push(payload.sessionId),
      async (sessionId) => {
        deleted.push(sessionId);
      }
    );

    await newerSettlement;
    resolveOlder(olderPayload);
    await olderSettlement;

    expect(applied).toEqual(["session-new"]);
    expect(deleted).toEqual(["session-old"]);
  });
});

describe("robot charging runtime status contract", () => {
  it("declares toCharge and charging runtime statuses", () => {
    const statuses: RobotRuntimeStatus[] = ["toCharge", "charging"];

    expect(statuses).toEqual(["toCharge", "charging"]);
  });
});

describe("execution safety intervention", () => {
  const intervention = {
    time: 7,
    type: "vertex" as const,
    robots: ["R1", "R2"],
    cell: [3, 2] as [number, number]
  };

  it("pauses only for a structured safety intervention", () => {
    expect(shouldPauseForSafetyIntervention(intervention)).toBe(true);
    expect(shouldPauseForSafetyIntervention(null)).toBe(false);
  });

  it("formats the status without parsing event text", () => {
    expect(safetyInterventionLabel(intervention)).toBe("T=7 安全门已拦截顶点冲突：R1 / R2");
    expect(safetyInterventionLabel({ ...intervention, type: "edge" })).toBe(
      "T=7 安全门已拦截边交换冲突：R1 / R2"
    );
    expect(safetyInterventionLabel(null)).toBeNull();
  });

  it("formats a structured consecutive stall without parsing event text", () => {
    const stall = {
      conflict: intervention,
      consecutiveCount: 3,
      firstInterventionTime: 7,
      latestInterventionTime: 9
    };

    expect(safetyStallLabel(stall)).toBe("连续拦截 3 次，当前调度停滞");
    expect(safetyStallLabel(null)).toBeNull();
  });

  it("adds the intercepted cell and robots only at the intervention tick", () => {
    expect(mergeSafetyInterventionMarker([], intervention, 7)).toEqual([intervention]);
    expect(mergeSafetyInterventionMarker([intervention], intervention, 7)).toEqual([intervention]);
    expect(mergeSafetyInterventionMarker([], intervention, 8)).toEqual([]);
    expect(isSafetyInterventionRobot("R1", intervention, 7)).toBe(true);
    expect(isSafetyInterventionRobot("R3", intervention, 7)).toBe(false);
    expect(isSafetyInterventionRobot("R1", intervention, 8)).toBe(false);
  });

  it("does not apply a safety intervention from an invalidated request generation", async () => {
    const coordinator = createSessionRequestCoordinator();
    const staleGeneration = coordinator.currentGeneration();
    const stalePayload = Promise.resolve(intervention);
    coordinator.invalidate();
    let playing = true;

    const payload = await stalePayload;
    if (coordinator.isCurrent(staleGeneration) && shouldPauseForSafetyIntervention(payload)) {
      playing = false;
    }

    expect(playing).toBe(true);
  });
});

describe("warehouse shelf map", () => {
  it("places the simulation startup panel below the random event generator", () => {
    const source = readFileSync(new URL("./main.tsx", import.meta.url), "utf8");
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    const controlPanelStackRule = styles.match(
      /(?:^|\r?\n)\.control-panel-stack\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;

    expect(source).toMatch(
      /<div className="control-panel-stack">\s*<Panel title="随机事件生成器">[\s\S]*?<Panel title="仿真启动">/
    );
    expect(controlPanelStackRule).toBeDefined();
    expect(controlPanelStackRule).toMatch(/display:\s*grid;/);
    expect(controlPanelStackRule).toMatch(/gap:\s*12px;/);
  });

  it("keeps robot markers circular and bounded by responsive map cells", () => {
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    const robotMarkerRule = styles.match(/(?:^|\r?\n)\.robot-marker\s*\{(?<rule>[^}]*)\}/)?.groups?.rule;

    expect(robotMarkerRule).toBeDefined();
    expect(robotMarkerRule).toMatch(/width:\s*min\(28px,\s*100%\);/);
    expect(robotMarkerRule).toMatch(/max-height:\s*100%;/);
    expect(robotMarkerRule).toMatch(/aspect-ratio:\s*1;/);
    expect(robotMarkerRule).toMatch(/border-radius:\s*50%;/);
    expect(robotMarkerRule).not.toMatch(/height:\s*28px;/);
  });

  it("keeps the runtime robot form visually stable while tick controls are disabled", () => {
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    const disabledRule = styles.match(
      /(?:^|\r?\n)\.runtime-robot-form input:disabled,\s*\.runtime-robot-form button:disabled\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;

    expect(disabledRule).toBeDefined();
    expect(disabledRule).toMatch(/opacity:\s*1;/);
  });

  it("uses a stable custom glyph when tick requests toggle capability checkbox disabled state", () => {
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    const checkboxRule = styles.match(
      /(?:^|\r?\n)\.capability-option input\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;
    const checkedGlyphRule = styles.match(
      /\.capability-option input:checked\s*\+\s*span::before\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;

    expect(checkboxRule).toBeDefined();
    expect(checkboxRule).toMatch(/appearance:\s*none;/);
    expect(checkboxRule).toMatch(/position:\s*absolute;/);
    expect(checkedGlyphRule).toBeDefined();
  });

  it("keeps runtime robot control cursors stable while tick disabled state changes", () => {
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    const textInputRule = styles.match(
      /(?:^|\r?\n)\.runtime-robot-form input:not\(\[type=\"checkbox\"\]\)\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;
    const buttonRule = styles.match(
      /(?:^|\r?\n)\.runtime-robot-form button\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;
    const disabledRule = styles.match(
      /(?:^|\r?\n)\.runtime-robot-form input:disabled,\s*\.runtime-robot-form button:disabled\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;
    const disabledCapabilityRule = styles.match(
      /(?:^|\r?\n)\.capability-option input:disabled\s*\+\s*span\s*\{(?<rule>[^}]*)\}/
    )?.groups?.rule;

    expect(textInputRule).toBeDefined();
    expect(textInputRule).toMatch(/cursor:\s*text;/);
    expect(buttonRule).toBeDefined();
    expect(buttonRule).toMatch(/cursor:\s*pointer;/);
    expect(disabledRule).toBeDefined();
    expect(disabledRule).not.toMatch(/cursor:\s*not-allowed;/);
    expect(disabledCapabilityRule).toBeUndefined();
  });

  it("pauses playback when an editable runtime robot field receives focus", () => {
    const source = readFileSync(new URL("./main.tsx", import.meta.url), "utf8");

    expect(source).toMatch(
      /<form className="task-form runtime-robot-form"[\s\S]*onFocusCapture=\{\(\) => \{\s*if \(playing\) setPlaying\(false\);\s*\}\}/
    );
  });

  it("renders shelf stock classes and labels from the session shelf states", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const result = buildMapTestResult(scenario);

    const markup = renderToStaticMarkup(createElement(MapBoard, {
      scenario,
      result,
      robotStates: [],
      shelfStates: [
        { shelfId: "S02", cell: [3, 3], serviceCell: [3, 2], status: "occupied" }
      ],
      sessionCurrentTime: 0,
      time: 0,
      routeHintsEnabled: false,
      selectedRobotId: "",
      onSelectRobot: () => undefined,
      mapPickTarget: null,
      onPickCell: () => undefined,
      unresolvedConflictAlert: null,
      safetyIntervention: null,
      contextMenu: null,
      historicalPlayback: false,
      canManageBlocks: false,
      onOpenContextMenu: () => undefined,
      onCloseContextMenu: () => undefined,
      onRunContextAction: () => undefined
    }));

    expect(markup).toContain('class="cell shelf-cell shelf-stocked"');
    expect(markup).toContain('title="货架 S02 · 已有货物"');
  });

  it("lets blocked and conflict styles fully override stocked shelf highlights", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const result: DispatchResult = {
      ...buildMapTestResult(scenario),
      extraBlocked: [[3, 3]],
      conflicts: [{ time: 0, type: "vertex", robots: ["R1", "R2"], cell: [3, 3] }]
    };
    const markup = renderToStaticMarkup(createElement(MapBoard, {
      scenario,
      result,
      robotStates: [],
      shelfStates: [
        { shelfId: "S02", cell: [3, 3], serviceCell: [3, 2], status: "occupied" }
      ],
      sessionCurrentTime: 0,
      time: 0,
      routeHintsEnabled: false,
      selectedRobotId: "",
      onSelectRobot: () => undefined,
      mapPickTarget: null,
      onPickCell: () => undefined,
      unresolvedConflictAlert: null,
      safetyIntervention: null,
      contextMenu: null,
      historicalPlayback: false,
      canManageBlocks: false,
      onOpenContextMenu: () => undefined,
      onCloseContextMenu: () => undefined,
      onRunContextAction: () => undefined
    }));

    expect(markup).toContain('class="cell blocked shelf-cell shelf-stocked conflict-cell"');
  });

  it("marks a failed runtime robot cell with the failure style", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const result = buildMapTestResult(scenario);
    const robot = scenario.robots[0];
    const markup = renderToStaticMarkup(createElement(MapBoard, {
      scenario,
      result,
      robotStates: [{
        robotId: robot.id,
        name: robot.name,
        position: robot.start,
        status: "failed",
        battery: robot.battery,
        load: robot.load,
        moveTicks: robot.moveTicks ?? 1,
        currentTaskId: null
      }],
      shelfStates: [],
      sessionCurrentTime: 0,
      time: 0,
      routeHintsEnabled: false,
      selectedRobotId: "",
      onSelectRobot: () => undefined,
      mapPickTarget: null,
      onPickCell: () => undefined,
      unresolvedConflictAlert: null,
      safetyIntervention: null,
      contextMenu: null,
      historicalPlayback: false,
      canManageBlocks: false,
      onOpenContextMenu: () => undefined,
      onCloseContextMenu: () => undefined,
      onRunContextAction: () => undefined
    }));

    expect(markup).toContain("cell robot-cell failed-robot-cell");
  });

  it("hides a removed robot from the current map but keeps its pre-removal history visible", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const robot = scenario.robots[0];
    const result: DispatchResult = {
      ...buildMapTestResult(scenario),
      paths: { [robot.id]: [robot.start, [1, 0], [2, 0]] },
      pathStartTimes: { [robot.id]: 0 }
    };
    const removedState: SessionResult["robotStates"][number] = {
      robotId: robot.id,
      name: robot.name,
      position: [2, 0],
      status: "removed",
      battery: robot.battery,
      load: robot.load,
      moveTicks: robot.moveTicks ?? 1,
      joinedAt: 0,
      removedAt: 2,
      currentTaskId: null
    };
    const renderMap = (time: number, historicalPlayback: boolean) => renderToStaticMarkup(createElement(MapBoard, {
      scenario,
      result,
      robotStates: [removedState],
      shelfStates: [],
      sessionCurrentTime: 2,
      time,
      routeHintsEnabled: false,
      selectedRobotId: "",
      onSelectRobot: () => undefined,
      mapPickTarget: null,
      onPickCell: () => undefined,
      unresolvedConflictAlert: null,
      safetyIntervention: null,
      contextMenu: null,
      historicalPlayback,
      canManageBlocks: false,
      onOpenContextMenu: () => undefined,
      onCloseContextMenu: () => undefined,
      onRunContextAction: () => undefined
    }));

    expect(renderMap(2, false)).not.toContain("robot-marker");
    expect(renderMap(1, true)).toContain("robot-marker");
    expect(renderMap(1, true)).toContain(robot.id);
  });

  it("does not render current runtime overlays during historical playback", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const robot = scenario.robots[0];
    const result: DispatchResult = {
      ...buildMapTestResult(scenario),
      extraBlocked: [[3, 3]],
      unavailableRobotIds: [robot.id]
    };
    const markup = renderToStaticMarkup(createElement(MapBoard, {
      scenario,
      result,
      robotStates: [{
        robotId: robot.id,
        name: robot.name,
        position: robot.start,
        status: "failed",
        battery: robot.battery,
        load: robot.load,
        moveTicks: robot.moveTicks ?? 1,
        currentTaskId: null
      }],
      shelfStates: [
        { shelfId: "S02", cell: [3, 3], serviceCell: [3, 2], status: "occupied" }
      ],
      sessionCurrentTime: 1,
      time: 0,
      routeHintsEnabled: false,
      selectedRobotId: "",
      onSelectRobot: () => undefined,
      mapPickTarget: null,
      onPickCell: () => undefined,
      unresolvedConflictAlert: null,
      safetyIntervention: null,
      contextMenu: null,
      historicalPlayback: true,
      canManageBlocks: false,
      onOpenContextMenu: () => undefined,
      onCloseContextMenu: () => undefined,
      onRunContextAction: () => undefined
    }));

    expect(markup).not.toContain("blocked shelf-cell");
    expect(markup).not.toContain("shelf-stocked");
    expect(markup).not.toContain("failed-robot-cell");
  });
});

describe("robot task capabilities", () => {
  it("treats omitted capabilities as all task types for the robot display", () => {
    const robot = { id: "R1", name: "默认能力", start: [0, 0] as [number, number], battery: 90, load: 2 };

    expect(robotCapabilityLabels(robot)).toEqual(["巡检", "取送", "突发"]);
  });

  it("shows only explicit robot capability labels", () => {
    const robot = {
      id: "R1",
      name: "配送突发能力",
      start: [0, 0] as [number, number],
      battery: 90,
      load: 2,
      capabilities: ["delivery", "emergency"] as TaskType[]
    };

    expect(robotCapabilityLabels(robot)).toEqual(["取送", "突发"]);
  });

  it("falls back to a supported non-delivery task for a delivery-incapable warehouse fleet", () => {
    const scenario = {
      ...buildWarehouseGeneratorScenario(),
      robots: [{
        id: "R1",
        name: "非配送机器人",
        start: [0, 0] as [number, number],
        battery: 90,
        load: 2,
        capabilities: ["inspection", "emergency"] as TaskType[]
      }]
    };

    expect(supportedTaskTypes(scenario.robots)).toEqual(new Set(["inspection", "emergency"]));
    for (let sequence = 1; sequence <= 6; sequence += 1) {
      const task = buildRandomGeneratedTask([], 8, scenario, sequence, buildWarehouseShelfStates());

      expect(task).not.toBeNull();
      if (task) expect(supportedTaskTypes(scenario.robots)).toContain(task.type);
    }
  });

  it("returns null for a delivery-only fleet when no warehouse delivery is legal", () => {
    const scenario = {
      ...buildWarehouseGeneratorScenario(),
      robots: [{
        id: "R1",
        name: "配送机器人",
        start: [0, 0] as [number, number],
        battery: 90,
        load: 2,
        capabilities: ["delivery"] as TaskType[]
      }]
    };
    const reservedStates = buildWarehouseShelfStates().map((state, index): ShelfRuntimeState => ({
      ...state,
      status: index % 2 === 0 ? "inboundReserved" : "outboundReserved"
    }));

    expect(buildRandomGeneratedTask([], 8, scenario, 1, reservedStates)).toBeNull();
  });

  it("returns null for a delivery-only fleet without capacity for generated demand", () => {
    const scenario = {
      ...buildWarehouseGeneratorScenario(),
      robots: [{
        id: "R1",
        name: "零载重配送机器人",
        start: [0, 0] as [number, number],
        battery: 90,
        load: 0,
        capabilities: ["delivery"] as TaskType[]
      }]
    };

    expect(supportedTaskTypes(scenario.robots)).toEqual(new Set());
    expect(buildRandomGeneratedTask([], 8, scenario, 1, buildWarehouseShelfStates())).toBeNull();
  });

  it("enables random delivery only when a mixed fleet has a delivery robot with sufficient load", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const zeroLoadFleet = [
      {
        id: "R-DELIVERY",
        name: "零载重配送机器人",
        start: [0, 0] as [number, number],
        battery: 90,
        load: 0,
        capabilities: ["delivery"] as TaskType[]
      },
      {
        id: "R-INSPECTION",
        name: "巡检机器人",
        start: [1, 0] as [number, number],
        battery: 90,
        load: 0,
        capabilities: ["inspection"] as TaskType[]
      }
    ];
    const capableFleet = zeroLoadFleet.map((robot) => (
      robot.id === "R-DELIVERY" ? { ...robot, load: 1 } : robot
    ));

    expect(supportedTaskTypes(zeroLoadFleet)).toEqual(new Set(["inspection"]));
    expect(buildRandomGeneratedTask(
      [],
      8,
      { ...scenario, robots: zeroLoadFleet },
      1,
      buildWarehouseShelfStates()
    )?.type).toBe("inspection");
    expect(supportedTaskTypes(capableFleet)).toEqual(new Set(["delivery", "inspection"]));
    expect(buildRandomGeneratedTask(
      [],
      8,
      { ...scenario, robots: capableFleet },
      1,
      buildWarehouseShelfStates()
    )?.type).toBe("delivery");
  });

  it("generates only inspection for an inspection-only fleet", () => {
    const scenario = {
      ...buildWarehouseGeneratorScenario(),
      robots: [{
        id: "R1",
        name: "巡检机器人",
        start: [0, 0] as [number, number],
        battery: 90,
        load: 2,
        capabilities: ["inspection"] as TaskType[]
      }]
    };

    for (let sequence = 1; sequence <= 6; sequence += 1) {
      expect(buildRandomGeneratedTask([], 8, scenario, sequence, buildWarehouseShelfStates())?.type).toBe("inspection");
    }
  });

  it("excludes removed robots from random task capability candidates", () => {
    const scenario: Scenario = {
      id: "removed-robot-task-capability",
      name: "removed-robot-task-capability",
      description: "",
      width: 4,
      height: 2,
      obstacles: [],
      zones: {
        warehouse: [[0, 0]],
        inspection: [[2, 0]],
        delivery: []
      },
      shelves: [],
      robots: [
        {
          id: "R-REMOVED",
          name: "已移除突发机器人",
          start: [0, 0],
          battery: 90,
          load: 1,
          capabilities: ["emergency"]
        },
        {
          id: "R-ACTIVE",
          name: "当前巡检机器人",
          start: [1, 0],
          battery: 90,
          load: 1,
          capabilities: ["inspection"]
        }
      ],
      tasks: [],
      dynamic: { triggerTime: 20, blockedCells: [], failedRobots: [], tasks: [] }
    };
    const robotStates: SessionResult["robotStates"] = [
      {
        robotId: "R-REMOVED",
        name: "已移除突发机器人",
        start: [0, 0],
        position: [0, 0],
        status: "removed",
        battery: 90,
        load: 1,
        moveTicks: 1,
        capabilities: ["emergency"],
        joinedAt: 0,
        removedAt: 0,
        currentTaskId: null
      },
      {
        robotId: "R-ACTIVE",
        name: "当前巡检机器人",
        start: [1, 0],
        position: [1, 0],
        status: "idle",
        battery: 90,
        load: 1,
        moveTicks: 1,
        capabilities: ["inspection"],
        joinedAt: 0,
        removedAt: null,
        currentTaskId: null
      }
    ];
    const generate = buildRandomGeneratedTask as unknown as (
      tasks: Task[],
      currentTime: number,
      scenario: Scenario,
      sequence: number,
      shelfStates: ShelfRuntimeState[],
      robotStates: SessionResult["robotStates"]
    ) => Task | null;

    expect(generate([], 0, scenario, 1, [], robotStates)?.type).toBe("inspection");
  });
});

function buildMapTestResult(scenario: Scenario): DispatchResult {
  return {
    scenarioId: scenario.id,
    avoidConflicts: true,
    includeDynamic: true,
    dynamicTriggerTime: null,
    extraBlocked: [],
    unavailableRobotIds: [],
    assignments: [],
    paths: {},
    conflicts: [],
    metrics: {
      makespan: 0,
      totalDistance: 0,
      conflictCount: 0,
      loadBalance: 0,
      assignedTaskCount: 0,
      deadlineMissCount: 0,
      averageLateness: 0,
      failureCount: 0,
      replanTimeMs: 0
    },
    failureReasons: {},
    failureDetails: {},
    eventLog: [],
    tasks: []
  };
}

describe("charging scenario import", () => {
  it("accepts legacy scenarios without charging fields", () => {
    const scenario = structuredClone(scenarios[0]);
    delete scenario.zones.charging;
    delete scenario.chargeTime;

    const parsed = parseScenario(scenario);
    expect(parsed.id).toBe(scenarios[0].id);
    expect(parsed.zones.charging).toBeUndefined();
    expect(parsed.chargeTime).toBeUndefined();
  });

  it("rejects invalid explicit charging fields", () => {
    const invalidChargingShape = structuredClone(scenarios[0]) as unknown as { zones: { charging: unknown } };
    invalidChargingShape.zones.charging = ["invalid"];
    expect(() => parseScenario(invalidChargingShape)).toThrow("JSON 必须是 Scenario 对象");

    const invalidCharging = structuredClone(scenarios[0]);
    invalidCharging.zones.charging = [[invalidCharging.width, 0]];
    expect(() => parseScenario(invalidCharging)).toThrow("坐标超出地图范围");

    const invalidChargeTime = structuredClone(scenarios[0]);
    invalidChargeTime.chargeTime = 0;
    expect(() => parseScenario(invalidChargeTime)).toThrow("JSON 必须是 Scenario 对象");

    const invalidBatteryCapacity = structuredClone(scenarios[0]);
    invalidBatteryCapacity.robots[0].batteryCapacity = 1;
    expect(() => parseScenario(invalidBatteryCapacity)).toThrow("JSON 必须是 Scenario 对象");
  });
});

describe("robot task capability scenario import", () => {
  it("accepts robots without capabilities", () => {
    const scenario = structuredClone(scenarios[0]);
    delete scenario.robots[0].capabilities;

    expect(parseScenario(scenario).robots[0].capabilities).toBeUndefined();
  });

  it("accepts robots with a unique nonempty task-type subset", () => {
    const scenario = structuredClone(scenarios[0]);
    scenario.robots[0].capabilities = ["inspection", "emergency"];

    expect(parseScenario(scenario).robots[0].capabilities).toEqual(["inspection", "emergency"]);
  });

  it("rejects robots with an empty capability list", () => {
    const scenario = structuredClone(scenarios[0]) as unknown as { robots: Array<{ capabilities?: unknown }> };
    scenario.robots[0].capabilities = [];

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects robots with duplicate capabilities", () => {
    const scenario = structuredClone(scenarios[0]) as unknown as { robots: Array<{ capabilities?: unknown }> };
    scenario.robots[0].capabilities = ["inspection", "inspection"];

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects robots with unknown capabilities", () => {
    const scenario = structuredClone(scenarios[0]) as unknown as { robots: Array<{ capabilities?: unknown }> };
    scenario.robots[0].capabilities = ["unknown"];

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });
});

describe("session reset state", () => {
  it("clears the transient conflict alert and its resolved state", () => {
    let alert: ReturnType<typeof selectLatestConflictAlert> = {
      time: 12,
      type: "vertex",
      robots: ["R1", "R2"],
      cell: [4, 5]
    };
    let resolved = true;

    resetSessionConflictState(
      (value) => {
        alert = value;
      },
      (value) => {
        resolved = value;
      }
    );

    expect(alert).toBeNull();
    expect(resolved).toBe(false);
  });
});

describe("robot movement duration", () => {
  it("formats the configured ticks per grid cell", () => {
    expect(robotMoveDurationLabel(3)).toBe("每格耗时 3 tick");
  });
});

describe("live metrics", () => {
  it("does not count pending tasks as deadline misses", () => {
    const result = {
      scenarioId: "deadline-pending",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: 5,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [],
      paths: { R1: [[0, 0]] },
      conflicts: [],
      metrics: {
        makespan: 0,
        totalDistance: 0,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 0,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 0,
        replanTimeMs: 0
      },
      failureReasons: {},
      failureDetails: {},
      eventLog: [],
      tasks: [
        {
          id: "PENDING",
          type: "inspection",
          title: "PENDING",
          priority: 3,
          releaseTime: 5,
          deadline: 1,
          targets: [[2, 0]]
        }
      ]
    } satisfies DispatchResult;
    const runtimeStates: SessionResult["taskStates"] = [
      {
        taskId: "PENDING",
        status: "pending",
        assignedRobotId: null,
        releaseTime: 5,
        completionTime: null,
        locked: false,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      }
    ];

    const metrics = buildLiveMetrics(result, 2, runtimeStates);
    if (!metrics) throw new Error("当前指标不应缺失");
    expect(metrics.liveDeadlineMissCount).toBe(0);
  });

  it("keeps completed session deadline misses visible from backend history", () => {
    const task: DispatchResult["tasks"][number] = {
      id: "LATE",
      type: "inspection",
      title: "LATE",
      priority: 1,
      releaseTime: 0,
      deadline: 0,
      targets: [[1, 0]]
    };
    const result = {
      scenarioId: "completed-deadline-miss",
      avoidConflicts: true,
      includeDynamic: false,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [],
      paths: { R1: [[0, 0]] },
      conflicts: [],
      metrics: {
        makespan: 0,
        totalDistance: 0,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 0,
        deadlineMissCount: 1,
        averageLateness: 1,
        failureCount: 0,
        replanTimeMs: 0
      },
      failureReasons: {},
      failureDetails: {},
      eventLog: [],
      tasks: [task]
    } satisfies DispatchResult;
    const runtimeStates: SessionResult["taskStates"] = [
      {
        taskId: "LATE",
        status: "completed",
        assignedRobotId: "R1",
        releaseTime: 0,
        completionTime: 1,
        locked: false,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      }
    ];
    const metricsHistory: SessionResult["metricsHistory"] = [
      {
        time: 2,
        completedTaskCount: 1,
        activeTaskCount: 0,
        pendingTaskCount: 0,
        travelledDistance: 1,
        activeConflictCount: 0,
        deadlineMissCount: 1,
        replanTimeMs: 0
      }
    ];

    const metrics = buildLiveMetrics(result, 2, runtimeStates, metricsHistory);
    if (!metrics) throw new Error("当前指标不应缺失");
    expect(metrics.liveDeadlineMissCount).toBe(1);
  });

  it("uses the selected snapshot and marks expired metric history unavailable without leaking current task state", () => {
    const task: DispatchResult["tasks"][number] = {
      id: "RUNNING",
      type: "inspection",
      title: "RUNNING",
      priority: 3,
      releaseTime: 0,
      deadline: 20,
      targets: [[4, 0]]
    };
    const result = {
      scenarioId: "timeline-status",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [{ robotId: "R1", tasks: [task] }],
      paths: { R1: [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]] },
      conflicts: [],
      metrics: {
        makespan: 4,
        totalDistance: 4,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 1,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 0,
        replanTimeMs: 0
      },
      failureReasons: {},
      failureDetails: {},
      eventLog: [],
      tasks: [task]
    } satisfies DispatchResult;
    const runtimeStates: SessionResult["taskStates"] = [
      {
        taskId: "RUNNING",
        status: "completed",
        assignedRobotId: "R1",
        releaseTime: 0,
        completionTime: 4,
        locked: false,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      }
    ];
    const metricsHistory: SessionResult["metricsHistory"] = [
      {
        time: 2,
        completedTaskCount: 1,
        activeTaskCount: 0,
        pendingTaskCount: 0,
        travelledDistance: 2,
        activeConflictCount: 0,
        deadlineMissCount: 1,
        replanTimeMs: 17
      }
    ];

    const metrics = buildLiveMetrics(result, 2, runtimeStates, metricsHistory);

    expect(metrics).toEqual({
      completedTaskCount: 1,
      activeTaskCount: 0,
      pendingTaskCount: 0,
      travelledDistance: 2,
      activeConflictCount: 0,
      liveDeadlineMissCount: 1,
      replanTimeMs: 17
    });

    const unavailableMetrics = buildLiveMetrics(result, 1, runtimeStates, metricsHistory, false);
    expect(unavailableMetrics).toBeNull();

    const snapshotMarkup = renderToStaticMarkup(createElement(LiveMetricsPanel, {
      time: 2,
      metrics,
      historicalMetricsUnavailable: false
    }));
    expect(snapshotMarkup).toContain("已完成任务");
    expect(snapshotMarkup).toContain("1 个");

    const unavailableMarkup = renderToStaticMarkup(createElement(LiveMetricsPanel, {
      time: 1,
      metrics: unavailableMetrics,
      historicalMetricsUnavailable: true
    }));
    expect(unavailableMarkup).toContain("历史指标不可用");
    expect(unavailableMarkup).not.toContain("已完成任务");
    expect(unavailableMarkup).not.toContain("0 个");
  });

  it("does not show future failure details while a replayed task is still active", () => {
    const task: DispatchResult["tasks"][number] = {
      id: "ACTIVE",
      type: "inspection",
      title: "ACTIVE",
      priority: 3,
      releaseTime: 0,
      deadline: 20,
      targets: [[4, 0]]
    };
    const result = {
      scenarioId: "timeline-failure",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [{ robotId: "R1", tasks: [task] }],
      paths: { R1: [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]] },
      conflicts: [],
      metrics: {
        makespan: 4,
        totalDistance: 4,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 1,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 1,
        replanTimeMs: 0
      },
      failureReasons: { ACTIVE: "未来调度失败" },
      failureDetails: {
        ACTIVE: {
          reason: "未来调度失败",
          category: "temporary",
          recoveryAction: "clearBlockedCells",
          blockingCells: [[3, 0]],
          blockingRobotIds: []
        }
      },
      eventLog: [],
      tasks: [task]
    } satisfies DispatchResult;
    const runtimeStates: SessionResult["taskStates"] = [
      {
        taskId: "ACTIVE",
        status: "completed",
        assignedRobotId: "R1",
        releaseTime: 0,
        completionTime: 4,
        locked: false,
        failureReason: "未来调度失败",
        failureCategory: "temporary",
        recoveryAction: "clearBlockedCells"
      }
    ];

    const [snapshot] = buildTaskSnapshots(result, 2, runtimeStates);

    expect(snapshot.status).toBe("active");
    expect(snapshot.failureReason).toBeNull();
    expect(snapshot.failureDetail).toBeNull();
  });

  it("honors backend unassigned task state even when a failed assignment has a robot id", () => {
    const task: DispatchResult["tasks"][number] = {
      id: "PATH-FAILED",
      type: "delivery",
      title: "PATH-FAILED",
      priority: 3,
      releaseTime: 0,
      deadline: 20,
      pickup: [0, 0],
      dropoff: [4, 0],
      demand: 1
    };
    const result = {
      scenarioId: "path-failed",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [{ robotId: "R1", tasks: [task] }],
      paths: { R1: [[0, 0]] },
      conflicts: [],
      metrics: {
        makespan: 0,
        totalDistance: 0,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 1,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 1,
        replanTimeMs: 0
      },
      failureReasons: { "PATH-FAILED": "机器人 R1 未生成可执行路径" },
      failureDetails: {
        "PATH-FAILED": {
          reason: "机器人 R1 未生成可执行路径",
          category: "temporary",
          recoveryAction: "relaxLocksOrReplan",
          blockingCells: [],
          blockingRobotIds: []
        }
      },
      eventLog: [],
      tasks: [task]
    } satisfies DispatchResult;
    const runtimeStates: SessionResult["taskStates"] = [
      {
        taskId: "PATH-FAILED",
        status: "unassigned",
        assignedRobotId: "R1",
        releaseTime: 0,
        completionTime: null,
        locked: false,
        failureReason: "机器人 R1 未生成可执行路径",
        failureCategory: "temporary",
        recoveryAction: "relaxLocksOrReplan"
      }
    ];

    const [snapshot] = buildTaskSnapshots(result, 10, runtimeStates);

    expect(snapshot.status).toBe("unassigned");
    expect(snapshot.failureReason).toBe("机器人 R1 未生成可执行路径");
    expect(snapshot.failureDetail?.recoveryAction).toBe("relaxLocksOrReplan");
  });
});

describe("recovery actions", () => {
  it("labels the task type capability recovery action", () => {
    expect(recoveryActionLabel("addCapableRobotOrChangeTaskType")).toBe(
      "恢复：增加兼容机器人或修改任务类型"
    );
  });

  it("builds actionable recovery targets from blocked cells and failed robots", () => {
    expect(buildRecoveryTargets({
      reason: "blocked and failed",
      category: "temporary",
      recoveryAction: "clearBlockedCellsOrRestoreRobot",
      blockingCells: [[1, 0], [2, 0]],
      blockingRobotIds: ["R2"]
    })).toEqual([
      { kind: "cell", cell: [1, 0], label: "解除封锁 (1, 0)" },
      { kind: "cell", cell: [2, 0], label: "解除封锁 (2, 0)" },
      { kind: "robot", robotId: "R2", label: "恢复机器人 R2" }
    ]);
  });

  it("does not create runtime buttons for permanent definition fixes", () => {
    expect(buildRecoveryTargets({
      reason: "target outside map",
      category: "permanent",
      recoveryAction: "fixMapOrTaskTarget",
      blockingCells: [[1, 0]],
      blockingRobotIds: ["R2"]
    })).toEqual([]);
  });
});

describe("map cell selection", () => {
  it("matches the selected runtime cell by coordinates", () => {
    expect(isSelectedCell([3, 2], [3, 2])).toBe(true);
    expect(isSelectedCell([3, 2], [2, 3])).toBe(false);
    expect(isSelectedCell([3, 2], null)).toBe(false);
  });

  it("matches the selected robot only when the cell contains that robot", () => {
    expect(isSelectedRobotCell("R2", "R2")).toBe(true);
    expect(isSelectedRobotCell("R1", "R2")).toBe(false);
    expect(isSelectedRobotCell(null, "R2")).toBe(false);
    expect(isSelectedRobotCell("R2", "")).toBe(false);
  });

  it("only exposes a context action for an unoccupied non-task cell", () => {
    expect(mapContextAction([2, 2], new Set(), new Set(), new Set(), new Set(), new Set())).toBe("block");
    expect(mapContextAction([2, 2], new Set(["2,2"]), new Set(), new Set(), new Set(), new Set())).toBe("unblock");
    expect(mapContextAction([2, 2], new Set(), new Set(["2,2"]), new Set(), new Set(), new Set())).toBeNull();
    expect(mapContextAction([2, 2], new Set(), new Set(), new Set(["2,2"]), new Set(), new Set())).toBeNull();
    expect(mapContextAction([2, 2], new Set(), new Set(), new Set(), new Set(["2,2"]), new Set())).toBeNull();
    expect(mapContextAction([2, 2], new Set(), new Set(), new Set(), new Set(), new Set(["2,2"]))).toBeNull();
  });

  it("uses robot context actions for failure and recovery", () => {
    expect(robotContextAction("R1", new Set())).toBe("failRobot");
    expect(robotContextAction("R1", new Set(["R1"]))).toBe("restoreRobot");
    expect(robotContextAction(null, new Set(["R1"]))).toBeNull();
  });
});

describe("event navigation", () => {
  it("only allows direct jumps to ticks already reached by the session", () => {
    expect(canJumpToEvent(4, 5)).toBe(true);
    expect(canJumpToEvent(5, 5)).toBe(true);
    expect(canJumpToEvent(6, 5)).toBe(false);
  });

  it("shows events that already happened even before playback starts", () => {
    const events = [
      { time: 0, text: "启动" },
      { time: 2, text: "实际锁定" }
    ];

    expect(visibleRuntimeEvents(events, 0)).toEqual([{ time: 0, text: "启动" }]);
    expect(visibleRuntimeEvents(events, 1)).toEqual([{ time: 0, text: "启动" }]);
    expect(visibleRuntimeEvents(events, 2)).toEqual(events);
  });
});

describe("manual task coordinates", () => {
  it("bounds manual service time after truncating fractional values", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const form = createManualTaskForm(scenario);

    expect(buildManualTask({ ...form, serviceTime: 10_001 }, [], 0, scenario)?.serviceTime).toBe(10_000);
    expect(buildManualTask({ ...form, serviceTime: -1 }, [], 0, scenario)?.serviceTime).toBe(0);
    expect(buildManualTask({ ...form, serviceTime: 7.9 }, [], 0, scenario)?.serviceTime).toBe(7);
    expect(buildManualTask({ ...form, serviceTime: 10_000.9 }, [], 0, scenario)?.serviceTime).toBe(10_000);
    expect(buildManualTask({ ...form, serviceTime: -0.9 }, [], 0, scenario)?.serviceTime).toBe(0);
  });

  it("defaults warehouse delivery dropoff to the first initially empty shelf service cell", () => {
    const scenario = buildWarehouseGeneratorScenario();

    expect(createManualTaskForm(scenario).dropoff).toBe("2, 2");
  });

  it("keeps the delivery-zone default for scenarios without shelves", () => {
    const scenario = { ...buildWarehouseGeneratorScenario(), shelves: [] };

    expect(createManualTaskForm(scenario).dropoff).toBe("2, 15");
  });

  it("parses one coordinate field only when the pair is inside the map", () => {
    const map = { width: 8, height: 6 };

    expect(parseCoordinateInput("3, 0", map)).toEqual([3, 0]);
    expect(parseCoordinateInput(" 7 , 5 ", map)).toEqual([7, 5]);
    expect(parseCoordinateInput("3 0", map)).toBeNull();
    expect(parseCoordinateInput("3.5,0", map)).toBeNull();
    expect(parseCoordinateInput("8,0", map)).toBeNull();
    expect(parseCoordinateInput("3,6", map)).toBeNull();
  });

  it("raises an expired manual-task deadline above the current simulation time", () => {
    expect(normalizeManualTaskDeadline(24, 24)).toBe(25);
    expect(normalizeManualTaskDeadline(30, 24)).toBe(30);
  });

  it("writes a picked coordinate and advances delivery selection to dropoff", () => {
    const form = {
      type: "delivery" as const,
      title: "取送",
      priority: 3,
      deadline: 24,
      serviceTime: 2,
      target: "3, 0",
      pickup: "0, 0",
      dropoff: "4, 0"
    };

    expect(applyMapPickToManualTask(form, "pickup", [3, 2]).pickup).toBe("3, 2");
    expect(nextMapPickTarget("pickup")).toBe("dropoff");
    expect(nextMapPickTarget("target")).toBeNull();
  });

  it("keeps the newest reached conflict with both robot identifiers", () => {
    expect(selectLatestConflictAlert([
      { time: 3, type: "vertex", robots: ["R1", "R2"], cell: [2, 1] },
      { time: 6, type: "edge", robots: ["R2", "R3"], cell: [4, 1] }
    ], 5)).toMatchObject({ time: 3, robots: ["R1", "R2"], cell: [2, 1] });
  });

  it("removes the map marker after replanning resolves the conflict", () => {
    const alert = { time: 3, type: "vertex" as const, robots: ["R1", "R2"], cell: [2, 1] as [number, number] };
    expect(shouldDisplayConflictMarker(alert, false)).toBe(true);
    expect(shouldDisplayConflictMarker(alert, true)).toBe(false);
  });

  it("keeps an unresolved vertex conflict only while the robots still overlap", () => {
    const conflicts = [
      { time: 5, type: "vertex" as const, robots: ["R1", "R2"], cell: [7, 4] as [number, number] }
    ];
    const paths = {
      R1: [[6, 4], [7, 4], [7, 4], [8, 4]] as [number, number][],
      R2: [[8, 4], [7, 4], [7, 4], [7, 4]] as [number, number][]
    };

    expect(selectMapConflictMarkers(conflicts, 1, conflicts[0], paths)).toEqual([conflicts[0]]);
    expect(selectMapConflictMarkers(conflicts, 2, conflicts[0], paths)).toEqual([conflicts[0]]);
    expect(selectMapConflictMarkers(conflicts, 3, conflicts[0], paths)).toEqual([]);
    expect(selectMapConflictMarkers(conflicts, 2, null, paths)).toEqual([]);
  });

  it("trusts backend resolved conflict states over local path fallback", () => {
    const conflicts = [
      { time: 5, type: "vertex" as const, robots: ["R1", "R2"], cell: [7, 4] as [number, number] }
    ];
    const paths = {
      R1: [[6, 4], [7, 4], [7, 4]] as [number, number][],
      R2: [[8, 4], [7, 4], [7, 4]] as [number, number][]
    };
    const conflictStates = [
      {
        ...conflicts[0],
        status: "resolved",
        startedAt: 5,
        resolvedAt: 6
      }
    ];

    expect(selectMapConflictMarkers(conflicts, 2, conflicts[0], paths, conflictStates as never)).toEqual([]);
  });

  it("uses backend resolvedAt to stop conflict markers during local playback", () => {
    const conflicts = [
      { time: 5, type: "vertex" as const, robots: ["R1", "R2"], cell: [7, 4] as [number, number] }
    ];
    const conflictStates = [
      {
        ...conflicts[0],
        status: "active",
        startedAt: 5,
        resolvedAt: 7
      }
    ];

    expect(selectMapConflictMarkers(conflicts, 6, conflicts[0], {}, conflictStates as never)).toEqual([conflicts[0]]);
    expect(selectMapConflictMarkers(conflicts, 7, conflicts[0], {}, conflictStates as never)).toEqual([]);
  });

  it("keeps map task labels only for tasks configured in the initial scenario", () => {
    const scenario = {
      tasks: [{ id: "T1", type: "inspection" as const, title: "初始", priority: 1, targets: [[1, 0] as [number, number]] }],
      dynamic: { tasks: [{ id: "E1", type: "emergency" as const, title: "初始动态", priority: 4, target: [2, 0] as [number, number] }] }
    };
    const tasks = [
      ...scenario.tasks,
      ...scenario.dynamic.tasks,
      { id: "M1", type: "inspection" as const, title: "手工", priority: 3, targets: [[3, 0] as [number, number]] }
    ];

    expect(filterInitialScenarioTaskLabels(tasks, scenario).map((task) => task.id)).toEqual(["T1", "E1"]);
  });

  it("builds generated tasks as ordinary queued tasks with generated ids", () => {
    const scenario: Scenario = {
      id: "generated-task-map",
      name: "generated-task-map",
      shelves: [],
      description: "",
      width: 6,
      height: 5,
      obstacles: [],
      zones: {
        warehouse: [[0, 0]],
        inspection: [[3, 1], [4, 2]],
        delivery: [[5, 4]]
      },
      robots: [{ id: "R1", name: "R1", start: [0, 0], battery: 90, load: 2 }],
      tasks: [{ id: "G1", type: "inspection", title: "已有生成任务", priority: 1, targets: [[3, 1]] }],
      dynamic: { triggerTime: 20, blockedCells: [], failedRobots: [], tasks: [] }
    };

    const task = buildRandomGeneratedTask(scenario.tasks, 8, scenario, 1, []);

    expect(task).not.toBeNull();
    expect(task?.id).toBe("G2");
    expect(task?.releaseTime).toBe(8);
    expect(task?.deadline).toBeGreaterThan(8);
    expect((task as (Task & { serviceTime?: number }) | null)?.serviceTime).toBeGreaterThanOrEqual(1);
    expect(["inspection", "delivery", "emergency"]).toContain(task?.type);
  });

  it("keeps randomly generated emergency priority within four to five", () => {
    const scenario: Scenario = {
      id: "generated-emergency-priority",
      name: "generated-emergency-priority",
      shelves: [],
      description: "",
      width: 6,
      height: 5,
      obstacles: [],
      zones: {
        warehouse: [[0, 0]],
        inspection: [[3, 1], [4, 2]],
        delivery: [[5, 4]]
      },
      robots: [{ id: "R1", name: "R1", start: [0, 0], battery: 90, load: 2 }],
      tasks: [],
      dynamic: { triggerTime: 20, blockedCells: [], failedRobots: [], tasks: [] }
    };

    const emergency = buildRandomGeneratedTask([], 8, scenario, 2, []);

    expect(emergency?.type).toBe("emergency");
    expect(emergency?.priority).toBeGreaterThanOrEqual(4);
    expect(emergency?.priority).toBeLessThanOrEqual(5);
  });

  it("avoids repeating generated task signatures while unused candidates remain", () => {
    const scenario: Scenario = {
      id: "generated-task-variety",
      name: "generated-task-variety",
      shelves: [],
      description: "",
      width: 8,
      height: 6,
      obstacles: [],
      zones: {
        warehouse: [[0, 0], [1, 0]],
        inspection: [[3, 1], [4, 2], [5, 3]],
        delivery: [[6, 4], [7, 5]]
      },
      robots: [{ id: "R1", name: "R1", start: [0, 0], battery: 90, load: 2 }],
      tasks: [],
      dynamic: { triggerTime: 20, blockedCells: [], failedRobots: [], tasks: [] }
    };
    const tasks: Scenario["tasks"] = [];
    const signatures = new Set<string>();

    for (let sequence = 1; sequence <= 6; sequence += 1) {
      const task = buildRandomGeneratedTask(tasks, sequence * 8, scenario, sequence, []);
      expect(task).not.toBeNull();
      if (!task) continue;
      const signature = generatedTaskSignature(task);
      expect(signatures.has(signature)).toBe(false);
      signatures.add(signature);
      tasks.push(task);
    }
  });

  it("generates outbound deliveries only from occupied unreserved shelves", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const task = buildRandomGeneratedTask([], 8, scenario, 1, buildWarehouseShelfStates());

    expect(task).toMatchObject({
      type: "delivery",
      pickup: [3, 2],
      dropoff: [2, 15]
    });
  });

  it("alternates twenty warehouse deliveries evenly between inbound and outbound candidates", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const shelfStates = buildWarehouseShelfStates();
    const tasks = Array.from({ length: 20 }, (_, index) =>
      buildRandomGeneratedTask([], 8, scenario, index + 1, shelfStates)
    );

    expect(tasks.every((task) => task?.type === "delivery")).toBe(true);
    const deliveries = tasks.filter((task): task is Extract<Task, { type: "delivery" }> => task?.type === "delivery");
    expect(deliveries.filter((task) => task.pickup[1] === 0)).toHaveLength(10);
    expect(deliveries.filter((task) => task.dropoff[1] === 15)).toHaveLength(10);

    const excludedOutboundPickups = new Set(["2,2", "4,2", "5,2"]);
    for (const task of deliveries.filter((item) => item.dropoff[1] === 15)) {
      expect(excludedOutboundPickups.has(task.pickup.join(","))).toBe(false);
    }
  });

  it("falls back to inspection or emergency tasks only when no warehouse delivery is legal", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const reservedStates = buildWarehouseShelfStates().map((state, index): ShelfRuntimeState => ({
      ...state,
      status: index % 2 === 0 ? "inboundReserved" : "outboundReserved"
    }));

    for (let sequence = 1; sequence <= 6; sequence += 1) {
      expect(["inspection", "emergency"]).toContain(
        buildRandomGeneratedTask([], 8, scenario, sequence, reservedStates)?.type
      );
    }
  });

  it("generates only inbound deliveries while empty is the only authoritative shelf state", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const states: ShelfRuntimeState[] = [
      { shelfId: "S01", cell: [2, 3], serviceCell: [2, 2], status: "empty" }
    ];

    for (let sequence = 1; sequence <= 8; sequence += 1) {
      expect(buildRandomGeneratedTask([], 8, scenario, sequence, states)).toMatchObject({
        type: "delivery",
        pickup: [2, 0],
        dropoff: [2, 2]
      });
    }
  });

  it("generates only outbound deliveries while occupied is the only authoritative shelf state", () => {
    const scenario = buildWarehouseGeneratorScenario();
    const states: ShelfRuntimeState[] = [
      { shelfId: "S02", cell: [3, 3], serviceCell: [3, 2], status: "occupied" }
    ];

    for (let sequence = 1; sequence <= 8; sequence += 1) {
      expect(buildRandomGeneratedTask([], 8, scenario, sequence, states)).toMatchObject({
        type: "delivery",
        pickup: [3, 2],
        dropoff: [2, 15]
      });
    }
  });
});

function buildWarehouseGeneratorScenario(): Scenario {
  return {
    id: "warehouse-generated-task",
    name: "warehouse-generated-task",
    description: "",
    width: 8,
    height: 16,
    obstacles: [],
    zones: {
      warehouse: [[2, 0]],
      inspection: [[0, 1]],
      delivery: [[2, 15]]
    },
    shelves: [
      { id: "S01", cell: [2, 3], serviceCell: [2, 2], initialOccupied: false },
      { id: "S02", cell: [3, 3], serviceCell: [3, 2], initialOccupied: true },
      { id: "S03", cell: [4, 3], serviceCell: [4, 2], initialOccupied: false },
      { id: "S04", cell: [5, 3], serviceCell: [5, 2], initialOccupied: true }
    ],
    robots: [{ id: "R1", name: "R1", start: [0, 0], battery: 90, load: 2 }],
    tasks: [],
    dynamic: { triggerTime: 20, blockedCells: [], failedRobots: [], tasks: [] }
  };
}

function buildWarehouseShelfStates(): ShelfRuntimeState[] {
  return [
    { shelfId: "S01", cell: [2, 3], serviceCell: [2, 2], status: "empty" },
    { shelfId: "S02", cell: [3, 3], serviceCell: [3, 2], status: "occupied" },
    { shelfId: "S03", cell: [4, 3], serviceCell: [4, 2], status: "inboundReserved" },
    { shelfId: "S04", cell: [5, 3], serviceCell: [5, 2], status: "outboundReserved" }
  ];
}

function generatedTaskSignature(task: Scenario["tasks"][number]): string {
  if (task.type === "delivery") return `delivery:${task.pickup.join(",")}>${task.dropoff.join(",")}`;
  if (task.type === "emergency") return `emergency:${task.target.join(",")}`;
  return `inspection:${task.targets.map((cell) => cell.join(",")).join("|")}`;
}

describe("rightbar layout", () => {
  it("keeps event log above the larger task queue region", () => {
    expect(RIGHTBAR_EVENT_LOG_CLASS).toBe("rightbar-event-log");
    expect(RIGHTBAR_TASK_QUEUE_CLASS).toBe("rightbar-task-queue");
  });
});

describe("replan status", () => {
  it("summarizes current lock, assignment, and robot availability state", () => {
    const result = {
      scenarioId: "insights-replay",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: ["R2"],
      assignments: [],
      paths: {},
      conflicts: [],
      metrics: {
        makespan: 0,
        totalDistance: 0,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 0,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 0,
        replanTimeMs: 0
      },
      failureReasons: {},
      failureDetails: {},
      eventLog: [
        { time: 2, text: "释放锁定任务 T1" },
        { time: 4, text: "调度失败原因：R2 故障" },
        { time: 7, text: "释放低优先级锁定任务 T2" },
        { time: 8, text: "调度失败原因：目标不可达" }
      ],
      tasks: []
    } satisfies DispatchResult;
    const session = {
      sessionId: "S1",
      scenarioId: "insights-replay",
      options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 24 },
      createdAt: 1,
      updatedAt: 1,
      lastAccessedAt: 1,
      currentTime: 8,
      runtimeTaskCount: 0,
      runtimeEventCount: 0,
      robotStates: [],
      shelfStates: [],
      taskStates: [
        {
          taskId: "T1",
          status: "running",
          assignedRobotId: "R1",
          releaseTime: 0,
          completionTime: null,
          locked: true,
          failureReason: null,
          failureCategory: null,
          recoveryAction: null
        },
        {
          taskId: "T2",
          status: "unassigned",
          assignedRobotId: null,
          releaseTime: 0,
          completionTime: null,
          locked: false,
          failureReason: "机器人故障",
          failureCategory: "temporary",
          recoveryAction: "restoreRobot"
        }
      ],
      metricsHistory: [
        {
          time: 0,
          completedTaskCount: 0,
          activeTaskCount: 1,
          pendingTaskCount: 0,
          travelledDistance: 0,
          activeConflictCount: 0,
          deadlineMissCount: 0,
          replanTimeMs: 1
        },
        {
          time: 4,
          completedTaskCount: 1,
          activeTaskCount: 0,
          pendingTaskCount: 0,
          travelledDistance: 4,
          activeConflictCount: 0,
          deadlineMissCount: 1,
          replanTimeMs: 2
        },
        {
          time: 8,
          completedTaskCount: 2,
          activeTaskCount: 0,
          pendingTaskCount: 0,
          travelledDistance: 8,
          activeConflictCount: 0,
          deadlineMissCount: 2,
          replanTimeMs: 2
        }
      ],
      completedTaskCount: 2,
      safetyIntervention: null,
      safetyStall: null,
      result
    } satisfies SessionResult;

    expect(buildReplanStatus(result, session)).toEqual({
      lockedTaskCount: 1,
      unassignedTaskCount: 1,
      failedRobotCount: 1
    });
  });
});

describe("timeline navigation", () => {
  it("keeps online playback running beyond the current planned path horizon", () => {
    expect(shouldContinueOnlinePlayback(true, false, "ready", 37, 24)).toBe(true);
    expect(shouldContinueOnlinePlayback(false, false, "ready", 37, 24)).toBe(false);
    expect(shouldContinueOnlinePlayback(true, true, "ready", 37, 24)).toBe(false);
    expect(shouldContinueOnlinePlayback(true, false, "loading", 37, 24)).toBe(false);
  });

  it("requests a backend tick only beyond the synchronized session time", () => {
    expect(shouldRequestSessionTick(6, 5)).toBe(true);
    expect(shouldRequestSessionTick(5, 5)).toBe(false);
    expect(shouldRequestSessionTick(2, 5)).toBe(false);
  });

  it("advances playback locally while replaying already synchronized history", () => {
    expect(shouldAdvancePlaybackLocally(3, 5)).toBe(true);
    expect(shouldAdvancePlaybackLocally(5, 5)).toBe(true);
    expect(shouldAdvancePlaybackLocally(6, 5)).toBe(false);
    expect(shouldAdvancePlaybackLocally(3, null)).toBe(false);
  });

  it("generates random tasks only at the live synchronized interval tick", () => {
    expect(shouldGenerateRandomTaskAtTime(8, 8, null, 8)).toBe(true);
    expect(shouldGenerateRandomTaskAtTime(8, 10, null, 8)).toBe(false);
    expect(shouldGenerateRandomTaskAtTime(16, 16, 16, 8)).toBe(false);
    expect(shouldGenerateRandomTaskAtTime(7, 7, null, 8)).toBe(false);
    expect(shouldGenerateRandomTaskAtTime(0, 0, null, 8)).toBe(false);
    expect(shouldGenerateRandomTaskAtTime(8, null, null, 8)).toBe(false);
    expect(shouldGenerateRandomTaskAtTime(8, 8, null, 0)).toBe(false);
  });

  it("uses the synchronized session time for runtime actions during history replay", () => {
    expect(getRuntimeActionTime(2, 5)).toBe(5);
    expect(getRuntimeActionTime(5, 5)).toBe(5);
    expect(getRuntimeActionTime(6, 5)).toBe(6);
    expect(getRuntimeActionTime(2, null)).toBe(2);
  });

  it("uses runtime robot snapshots only at the synchronized session time", () => {
    const robotStates: SessionResult["robotStates"] = [
      {
        robotId: "R1",
        name: "R1",
        position: [4, 0],
        status: "idle",
        battery: 90,
        load: 0,
        moveTicks: 1,
        currentTaskId: null
      }
    ];

    expect(shouldUseRuntimeRobotSnapshot(2, 5, robotStates)).toBe(false);
    expect(shouldUseRuntimeRobotSnapshot(5, 5, robotStates)).toBe(true);
    expect(shouldUseRuntimeRobotSnapshot(6, 5, robotStates)).toBe(false);
    expect(shouldUseRuntimeRobotSnapshot(5, null, robotStates)).toBe(false);
    expect(shouldUseRuntimeRobotSnapshot(5, 5, [])).toBe(false);
  });

  it("shows metric history only up to the selected replay time", () => {
    const history: SessionResult["metricsHistory"] = [
      {
        time: 0,
        completedTaskCount: 0,
        activeTaskCount: 1,
        pendingTaskCount: 0,
        travelledDistance: 0,
        activeConflictCount: 0,
        deadlineMissCount: 0,
        replanTimeMs: 1
      },
      {
        time: 3,
        completedTaskCount: 1,
        activeTaskCount: 0,
        pendingTaskCount: 0,
        travelledDistance: 3,
        activeConflictCount: 0,
        deadlineMissCount: 0,
        replanTimeMs: 1
      },
      {
        time: 6,
        completedTaskCount: 2,
        activeTaskCount: 0,
        pendingTaskCount: 0,
        travelledDistance: 6,
        activeConflictCount: 0,
        deadlineMissCount: 0,
        replanTimeMs: 1
      }
    ];

    expect(getVisibleMetricsHistory(history, 3).map((point) => point.time)).toEqual([0, 3]);
    expect(getVisibleMetricsHistory(undefined, 3)).toEqual([]);
  });
});

describe("active route hints", () => {
  const task: DispatchResult["tasks"][number] = {
    id: "T1",
    type: "inspection",
    title: "当前巡检",
    priority: 1,
    targets: [[2, 0]]
  };
  const result = {
    scenarioId: "active-route",
    avoidConflicts: true,
    includeDynamic: true,
    dynamicTriggerTime: null,
    extraBlocked: [],
    unavailableRobotIds: [],
    assignments: [{ robotId: "R1", tasks: [task] }],
    paths: { R1: [[0, 0], [1, 0], [2, 0]] },
    conflicts: [],
    metrics: {
      makespan: 2,
      totalDistance: 2,
      conflictCount: 0,
      loadBalance: 0,
      assignedTaskCount: 1,
      deadlineMissCount: 0,
      averageLateness: 0,
      failureCount: 0,
      replanTimeMs: 1
    },
    failureReasons: {},
    failureDetails: {},
    eventLog: [],
    tasks: [task]
  } satisfies DispatchResult;

  it("does not generate route arrows before playback starts", () => {
    expect(buildActiveRouteArrows(result, 0, [], false)).toEqual([]);
  });

  it("keeps route hints disabled after a pre-start session update", () => {
    expect(routeHintsAfterSessionUpdate(false)).toBe(false);
    expect(routeHintsAfterSessionUpdate(true)).toBe(true);
  });

  it("generates only the current task's remaining direction arrows", () => {
    expect(buildActiveRouteArrows(result, 1, [], true)).toEqual([
      { robotId: "R1", cell: [2, 0], angle: 90, lane: 0 }
    ]);
  });

  it("removes route arrows after the current task completes", () => {
    expect(buildActiveRouteArrows(result, 2, [], true)).toEqual([]);
  });

  it("uses a runtime robot path start time for replay and active route hints", () => {
    const runtimeTask: DispatchResult["tasks"][number] = {
      id: "RUNTIME-TASK",
      type: "inspection",
      title: "运行时巡检",
      priority: 1,
      releaseTime: 12,
      targets: [[4, 0]]
    };
    const runtimeResult = {
      ...result,
      assignments: [{ robotId: "R5", tasks: [runtimeTask] }],
      paths: { R5: [[2, 0], [3, 0], [4, 0]] },
      pathStartTimes: { R5: 12 },
      tasks: [runtimeTask]
    } satisfies DispatchResult;

    expect(buildActiveRouteArrows(runtimeResult, 11, [], true)).toEqual([]);
    expect(buildActiveRouteArrows(runtimeResult, 12, [], true)).toEqual([
      { robotId: "R5", cell: [3, 0], angle: 90, lane: 0 },
      { robotId: "R5", cell: [4, 0], angle: 90, lane: 0 }
    ]);
  });

  it("accepts only supported playback speed input", () => {
    expect(parsePlaybackSpeed("2.4")).toBe(2.4);
    expect(parsePlaybackSpeed("0.1")).toBeNull();
    expect(parsePlaybackSpeed("10.1")).toBeNull();
    expect(parsePlaybackSpeed("invalid")).toBeNull();
  });

  it("parses positive integer input with bounds", () => {
    expect(parsePositiveIntegerInput("8", 2, 60)).toBe(8);
    expect(parsePositiveIntegerInput("1", 2, 60)).toBe(2);
    expect(parsePositiveIntegerInput("70", 2, 60)).toBe(60);
    expect(parsePositiveIntegerInput("2.5", 2, 60)).toBeNull();
    expect(parsePositiveIntegerInput("invalid", 2, 60)).toBeNull();
  });

  it("assigns unique robot colors for all supported robot slots and skips route arrows on task label cells", () => {
    const colors = Array.from({ length: 32 }, (_, index) => robotColorForIndex(index));
    expect(new Set(colors).size).toBe(colors.length);
    expect(robotColorForIndex(0)).not.toBe(robotColorForIndex(1));
    expect(robotColorForIndex(4)).not.toBe(robotColorForIndex(0));
    expect(robotColorForIndex(31)).not.toBe(robotColorForIndex(0));
    expect(filterActiveRouteArrows([
      { robotId: "R1", cell: [1, 0], angle: 90, lane: 0 },
      { robotId: "R2", cell: [2, 0], angle: 90, lane: 1 }
    ], new Set(["1,0"]))).toEqual([
      { robotId: "R2", cell: [2, 0], angle: 90, lane: 1 }
    ]);
  });
});

describe("dispatch options", () => {
  it("normalizes ordinary task priority to the inclusive zero-to-five range", () => {
    expect(normalizeTaskPriority(0)).toBe(0);
    expect(normalizeTaskPriority(6)).toBe(5);
  });

  it("normalizes assignment replan window for session creation", () => {
    expect(buildDispatchOptions(true, true, 18)).toEqual({
      avoidConflicts: true,
      includeDynamic: true,
      assignmentReplanWindow: 18,
      adaptiveReplanWindow: false
    });
    expect(buildDispatchOptions(true, true, 18, true).adaptiveReplanWindow).toBe(true);
    expect(buildDispatchOptions(true, true, 3.8).assignmentReplanWindow).toBe(3);
    expect(buildDispatchOptions(false, false, -5).assignmentReplanWindow).toBe(0);
    expect(buildDispatchOptions(true, false, 999).assignmentReplanWindow).toBe(120);
    expect(buildDispatchOptions(true, true, Number.NaN).assignmentReplanWindow).toBe(24);
  });

  it("formats the assignment replan window for status display", () => {
    expect(assignmentReplanWindowLabel(24)).toBe("窗口 24T");
    expect(assignmentReplanWindowLabel(3.8)).toBe("窗口 3T");
    expect(assignmentReplanWindowLabel(Number.NaN)).toBe("窗口 24T");
    expect(assignmentReplanWindowStatusLabel(24, false, 48)).toBe("窗口 24T");
    expect(assignmentReplanWindowStatusLabel(24, true, 48)).toBe("自适应 48T");
    expect(assignmentReplanWindowStatusLabel(24, true)).toBe("自适应 24T");
  });

});

describe("task queue display", () => {
  it("keeps capacity-deferred tasks pending instead of showing them as scheduling failures", () => {
    const task: DispatchResult["tasks"][number] = {
      id: "DEFERRED",
      type: "inspection",
      title: "等待可用机器人",
      priority: 2,
      targets: [[2, 0]]
    };
    const result = {
      scenarioId: "capacity-deferred",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [],
      paths: {},
      conflicts: [],
      metrics: {
        makespan: 0,
        totalDistance: 0,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 0,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 0,
        replanTimeMs: 1
      },
      failureReasons: {},
      failureDetails: {},
      eventLog: [],
      tasks: [task]
    } satisfies DispatchResult;
    const runtimeStates: SessionResult["taskStates"] = [
      {
        taskId: "DEFERRED",
        status: "pending",
        assignedRobotId: null,
        releaseTime: 0,
        completionTime: null,
        locked: false,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      }
    ];

    const [snapshot] = buildTaskSnapshots(result, 8, runtimeStates);

    expect(snapshot.status).toBe("pending");
    expect(buildTaskQueueMetricRows(snapshot)).toContainEqual({ label: "状态", value: "等待分配" });
    expect(buildTaskQueueMetricRows(snapshot)).toContainEqual({ label: "执行机器人", value: "等待分配" });
  });

  it("uses runtime timing fields and keeps completed task details available", () => {
    const task: DispatchResult["tasks"][number] = {
      id: "T1",
      type: "inspection",
      title: "巡检",
      priority: 2,
      targets: [[1, 0]]
    };
    (task as Task & { serviceTime?: number }).serviceTime = 2;
    const result = {
      scenarioId: "timing",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [{ robotId: "R1", tasks: [task] }],
      paths: { R1: [[0, 0], [1, 0]] },
      conflicts: [],
      metrics: {
        makespan: 1,
        totalDistance: 1,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 1,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 0,
        replanTimeMs: 1
      },
      failureReasons: {},
      failureDetails: {},
      eventLog: [],
      tasks: [task]
    } satisfies DispatchResult;
    const runtimeStates: SessionResult["taskStates"] = [
      {
        taskId: "T1",
        status: "completed",
        assignedRobotId: "R1",
        releaseTime: 4,
        completionTime: 9,
        locked: false,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      }
    ];

    const [snapshot] = buildTaskSnapshots(result, 10, runtimeStates);

    expect(taskTimingFields(snapshot)).toEqual({
      release: "到达 T=4",
      deadline: "截止：无",
      completion: "完成 T=9"
    });
    expect(buildTaskQueueMetricRows(snapshot)).toEqual([
      { label: "状态", value: "已完成" },
      { label: "执行机器人", value: "R1" },
      { label: "任务类型", value: "巡检" },
      { label: "优先级", value: "2" },
      { label: "锁定状态", value: "可重分配" },
      { label: "到达时间", value: "T=4" },
      { label: "截止时间", value: "无" },
      { label: "作业时间", value: "2 tick" },
      { label: "完成时间", value: "T=9" }
    ]);
  });

  it("orders unfinished tasks before completed tasks", () => {
    const baseTask = {
      type: "inspection",
      priority: 1,
      targets: [[0, 0]]
    } satisfies Pick<Extract<DispatchResult["tasks"][number], { type: "inspection" }>, "type" | "priority" | "targets">;
    const result = {
      scenarioId: "queue-order",
      avoidConflicts: true,
      includeDynamic: true,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [],
      paths: {},
      conflicts: [],
      metrics: {
        makespan: 0,
        totalDistance: 0,
        conflictCount: 0,
        loadBalance: 0,
        assignedTaskCount: 0,
        deadlineMissCount: 0,
        averageLateness: 0,
        failureCount: 0,
        replanTimeMs: 1
      },
      failureReasons: {},
      failureDetails: {},
      eventLog: [],
      tasks: [
        { ...baseTask, id: "DONE", title: "已完成" },
        { ...baseTask, id: "ACTIVE", title: "进行中" },
        { ...baseTask, id: "PENDING", title: "未开始", releaseTime: 12 }
      ]
    } satisfies DispatchResult;
    const snapshots = buildTaskSnapshots(result, 5, [
      {
        taskId: "DONE",
        status: "completed",
        assignedRobotId: "R1",
        releaseTime: 0,
        completionTime: 3,
        locked: false,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      },
      {
        taskId: "ACTIVE",
        status: "running",
        assignedRobotId: "R1",
        releaseTime: 0,
        completionTime: null,
        locked: true,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      },
      {
        taskId: "PENDING",
        status: "pending",
        assignedRobotId: null,
        releaseTime: 12,
        completionTime: null,
        locked: false,
        failureReason: null,
        failureCategory: null,
        recoveryAction: null
      }
    ]);

    expect(sortTaskSnapshotsForDisplay(snapshots).map((snapshot) => snapshot.task.id)).toEqual(["ACTIVE", "PENDING", "DONE"]);
    expect(splitTaskSnapshotsForDisplay(snapshots)).toMatchObject({
      unfinished: [
        { task: { id: "ACTIVE" } },
        { task: { id: "PENDING" } }
      ],
      completed: [
        { task: { id: "DONE" } }
      ]
    });
  });
});
