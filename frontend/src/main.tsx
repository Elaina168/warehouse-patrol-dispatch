import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ArrowUp, Crosshair, Download, Lock, Pause, Play, Plus, RefreshCcw, Server, ShieldX, Square, TriangleAlert, Unlock, Upload } from "lucide-react";
import { apiErrorFromResponse } from "./domain/apiError";
import {
  deleteSession,
  resetSession
} from "./domain/sessionApi";
import { buildShelfCellPresentations, buildWarehouseDeliveryCandidates } from "./domain/inventory";
import { buildZoneCellPresentations, cellKey, getRobotStateAt } from "./domain/view";
import { scenarios } from "./domain/scenarios";
import type { Cell, Conflict, ConflictState, DispatchOptions, DispatchResult, RecoveryAction, Scenario, SessionResult, ShelfRuntimeState, Task, TaskFailureDetail, TaskType } from "./domain/types";
import "./styles.css";

const API_BASE = "http://127.0.0.1:8011";
const DEFAULT_ASSIGNMENT_REPLAN_WINDOW = 24;
const MAX_ASSIGNMENT_REPLAN_WINDOW = 120;
const DEFAULT_PLAYBACK_RATE = 2.4;
const MIN_PLAYBACK_RATE = 0.2;
const MAX_PLAYBACK_RATE = 10;
const DEFAULT_RANDOM_TASK_INTERVAL = 8;
const MIN_RANDOM_TASK_INTERVAL = 2;
const MAX_RANDOM_TASK_INTERVAL = 60;
export const ROBOT_COLORS = ["#1c6dd0", "#117a8b", "#5e8b2f", "#c23b22"] as const;
export const RIGHTBAR_EVENT_LOG_CLASS = "rightbar-event-log";
export const RIGHTBAR_TASK_QUEUE_CLASS = "rightbar-task-queue";

declare global {
  interface Window {
    __warehousePatrolRoot?: Root;
  }
}

type ManualTaskForm = {
  type: TaskType;
  title: string;
  priority: number;
  deadline: number;
  serviceTime: number;
  target: string;
  pickup: string;
  dropoff: string;
};

export type MapPickTarget = "target" | "pickup" | "dropoff";
export type ConflictAlert = Conflict;

type TaskRuntimeStatus = "pending" | "active" | "done" | "unassigned";

export type TaskSnapshot = {
  task: Task;
  assignedRobotId: string | null;
  releaseTime: number;
  isReleased: boolean;
  completionTime: number | null;
  status: TaskRuntimeStatus;
  locked: boolean;
  progress: number;
  failureReason: string | null;
  failureDetail: TaskFailureDetail | null;
};

export type ActiveRouteArrow = {
  robotId: string;
  cell: Cell;
  angle: number;
  lane: number;
};

type RecoveryTarget =
  | { kind: "cell"; cell: Cell; label: string }
  | { kind: "robot"; robotId: string; label: string };

type LiveMetrics = {
  completedTaskCount: number;
  activeTaskCount: number;
  pendingTaskCount: number;
  travelledDistance: number;
  activeConflictCount: number;
  liveDeadlineMissCount: number;
};

type ReplanStatus = {
  lockedTaskCount: number;
  unassignedTaskCount: number;
  failedRobotCount: number;
};

export type MapContextAction = "block" | "unblock" | "failRobot" | "restoreRobot";

type MapContextMenuState = {
  cell: Cell;
  action: MapContextAction;
  robotId?: string;
  x: number;
  y: number;
};

type CellKeyLookup = Pick<ReadonlySet<string>, "has">;

function App() {
  const [importedScenario, setImportedScenario] = useState<Scenario | null>(null);
  const [avoidConflicts, setAvoidConflicts] = useState(true);
  const [assignmentReplanWindow, setAssignmentReplanWindow] = useState(DEFAULT_ASSIGNMENT_REPLAN_WINDOW);
  const [adaptiveReplanWindow, setAdaptiveReplanWindow] = useState(false);
  const [sessionResetKey, setSessionResetKey] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [routeHintsEnabled, setRouteHintsEnabled] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(DEFAULT_PLAYBACK_RATE);
  const [playbackRateInput, setPlaybackRateInput] = useState(String(DEFAULT_PLAYBACK_RATE));
  const [randomGeneratorEnabled, setRandomGeneratorEnabled] = useState(false);
  const [randomTaskInterval, setRandomTaskInterval] = useState(DEFAULT_RANDOM_TASK_INTERVAL);
  const [randomTaskIntervalInput, setRandomTaskIntervalInput] = useState(String(DEFAULT_RANDOM_TASK_INTERVAL));
  const [lastRandomTaskTime, setLastRandomTaskTime] = useState<number | null>(null);
  const [tickInFlight, setTickInFlight] = useState(false);
  const [apiStatus, setApiStatus] = useState("checking");
  const [session, setSession] = useState<SessionResult | null>(null);
  const [result, setResult] = useState<DispatchResult | null>(null);
  const [dispatchStatus, setDispatchStatus] = useState<"loading" | "ready" | "error">("loading");
  const [dispatchError, setDispatchError] = useState<string | null>(null);
  const [manualTask, setManualTask] = useState<ManualTaskForm>(() => createManualTaskForm(scenarios[0]));
  const [manualTaskError, setManualTaskError] = useState<string | null>(null);
  const [mapPickTarget, setMapPickTarget] = useState<MapPickTarget | null>(null);
  const [latestConflictAlert, setLatestConflictAlert] = useState<ConflictAlert | null>(null);
  const [latestConflictResolved, setLatestConflictResolved] = useState(false);
  const [failedRobotId, setFailedRobotId] = useState(scenarios[0].robots[0]?.id ?? "");
  const [importStatus, setImportStatus] = useState<"idle" | "ready" | "error">("idle");
  const [importError, setImportError] = useState<string | null>(null);
  const [mapContextMenu, setMapContextMenu] = useState<MapContextMenuState | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const randomTaskSequenceRef = useRef(0);

  const scenario = importedScenario ?? scenarios[0];

  const liveMetrics = useMemo(
    () => result ? buildLiveMetrics(result, time, session?.taskStates, session?.metricsHistory) : null,
    [result, session?.metricsHistory, session?.taskStates, time]
  );

  const taskSnapshots = useMemo(
    () => result ? sortTaskSnapshotsForDisplay(buildTaskSnapshots(result, time, session?.taskStates)) : [],
    [result, session?.taskStates, time]
  );

  const taskGroups = useMemo(
    () => splitTaskSnapshotsForDisplay(taskSnapshots),
    [taskSnapshots]
  );

  const replanStatus = useMemo(
    () => result ? buildReplanStatus(result, session) : null,
    [result, session]
  );
  const runtimeActionTime = getRuntimeActionTime(time, session?.currentTime ?? null);
  const reachedConflictAlert = useMemo(
    () => result ? selectLatestConflictAlert(result.conflicts, time) : null,
    [result, time]
  );

  function applySessionPayload(payload: SessionResult) {
    const previousSessionId = activeSessionIdRef.current;
    activeSessionIdRef.current = payload.sessionId;
    setSession(payload);
    setResult(payload.result);
    setTime(payload.currentTime);
    setApiStatus("online");
    setDispatchStatus("ready");
    if (previousSessionId && previousSessionId !== payload.sessionId) {
      void deleteSession(API_BASE, previousSessionId).catch(() => undefined);
    }
  }

  useEffect(() => {
    return () => {
      const activeSessionId = activeSessionIdRef.current;
      if (activeSessionId) {
        void deleteSession(API_BASE, activeSessionId).catch(() => undefined);
      }
    };
  }, []);

  useEffect(() => {
    setTime(0);
    setPlaying(false);
    setRouteHintsEnabled(false);
    setManualTask(createManualTaskForm(scenario));
    setManualTaskError(null);
    setMapPickTarget(null);
    resetSessionConflictState(setLatestConflictAlert, setLatestConflictResolved);
    setFailedRobotId(scenario.robots[0]?.id ?? "");
    setRandomGeneratorEnabled(false);
    setLastRandomTaskTime(null);
    randomTaskSequenceRef.current = 0;
    setTickInFlight(false);
    setMapContextMenu(null);
  }, [scenario, avoidConflicts, assignmentReplanWindow, adaptiveReplanWindow]);

  useEffect(() => {
    setManualTask((task) => {
      const deadline = normalizeManualTaskDeadline(task.deadline, runtimeActionTime);
      return deadline === task.deadline ? task : { ...task, deadline };
    });
  }, [runtimeActionTime]);

  useEffect(() => {
    if (!reachedConflictAlert) return;
    setLatestConflictAlert((previous) => sameConflictAlert(previous, reachedConflictAlert) ? previous : reachedConflictAlert);
    setLatestConflictResolved(false);
  }, [reachedConflictAlert]);

  useEffect(() => {
    if (!latestConflictAlert || !result) return;
    const conflictState = findConflictState(result.conflictStates, latestConflictAlert);
    if (conflictState && isConflictStateActiveAtTime(conflictState, time)) {
      setLatestConflictResolved(false);
      return;
    }
    if (conflictState) {
      setLatestConflictResolved(true);
      return;
    }
    const activeSameConflict = result.conflicts.some(
      (conflict) => conflict.time === time && sameConflictAlert(conflict, latestConflictAlert)
    );
    if (activeSameConflict) {
      setLatestConflictResolved(false);
      return;
    }
    if (!result.conflicts.some((conflict) => sameConflictAlert(conflict, latestConflictAlert))) {
      setLatestConflictResolved(true);
    }
  }, [latestConflictAlert, result, time]);

  useEffect(() => {
    const controller = new AbortController();
    setDispatchStatus("loading");
    setDispatchError(null);
    setSession(null);
    setTime(0);
    setPlaying(false);
    setRouteHintsEnabled(false);
    setLastRandomTaskTime(null);
    randomTaskSequenceRef.current = 0;
    setTickInFlight(false);
    setMapContextMenu(null);
    setMapPickTarget(null);
    resetSessionConflictState(setLatestConflictAlert, setLatestConflictResolved);

    fetch(`${API_BASE}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario,
        options: buildDispatchOptions(avoidConflicts, true, assignmentReplanWindow, adaptiveReplanWindow)
      }),
      signal: controller.signal
      })
      .then((response) => {
        if (!response.ok) return responseError(response, "session failed");
        return response.json() as Promise<SessionResult>;
      })
      .then((payload) => {
        applySessionPayload(payload);
      })
      .catch((error: Error) => {
        if (controller.signal.aborted) return;
        setApiStatus("offline");
        setDispatchStatus("error");
        setDispatchError(error.message);
      });

    return () => controller.abort();
  }, [scenario, avoidConflicts, assignmentReplanWindow, adaptiveReplanWindow, sessionResetKey]);

  useEffect(() => {
    if (dispatchStatus === "loading") setMapContextMenu(null);
  }, [dispatchStatus]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMapContextMenu(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  useEffect(() => {
    if (!shouldContinueOnlinePlayback(playing, tickInFlight, dispatchStatus, time, session?.currentTime ?? null)) return;
    if (!session) return;
    const timer = window.setTimeout(() => {
      const nextTime = time + 1;
      if (shouldAdvancePlaybackLocally(nextTime, session.currentTime)) {
        setTime(nextTime);
      } else {
        void tickSession(nextTime);
      }
    }, 1000 / playbackRate);
    return () => window.clearTimeout(timer);
  }, [dispatchStatus, playbackRate, playing, session, tickInFlight, time]);

  useEffect(() => {
    if (!playing || !randomGeneratorEnabled || !session || !result || dispatchStatus === "loading") return;
    if (!shouldGenerateRandomTaskAtTime(time, session.currentTime, lastRandomTaskTime, randomTaskInterval)) return;
    setLastRandomTaskTime(time);
    void pushGeneratedTask();
  }, [dispatchStatus, lastRandomTaskTime, playing, randomGeneratorEnabled, randomTaskInterval, result, session, time]);

  async function submitManualTask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !result) return;

    const task = buildManualTask(manualTask, result.tasks, runtimeActionTime, scenario);
    if (!task) {
      setManualTaskError("请输入地图范围内的整数坐标，格式例如 3, 0。");
      return;
    }

    setManualTaskError(null);
    await enqueueTask(task);
  }

  function pickManualTaskCell(cell: Cell) {
    if (!mapPickTarget) return;
    setManualTask((task) => applyMapPickToManualTask(task, mapPickTarget, cell));
    setManualTaskError(null);
    setMapPickTarget(nextMapPickTarget(mapPickTarget));
  }

  async function enqueueTask(task: Task) {
    if (!session) return;
    await updateSession(() =>
      fetch(`${API_BASE}/api/sessions/${session.sessionId}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task })
      })
    );
  }

  async function pushGeneratedTask() {
    if (!result) return;
    const task = buildRandomGeneratedTask(
      result.tasks,
      runtimeActionTime,
      scenario,
      randomTaskSequenceRef.current + 1,
      session?.shelfStates ?? []
    );
    if (!task) return;
    randomTaskSequenceRef.current += 1;
    await enqueueTask(task);
  }

  async function blockCell(cell: Cell) {
    if (!session) return;
    await updateSession(() =>
      fetch(`${API_BASE}/api/sessions/${session.sessionId}/blocked-cells`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cell, currentTime: runtimeActionTime })
      })
    );
  }

  async function unblockCell(cell: Cell) {
    if (!session) return;
    await updateSession(() =>
      fetch(`${API_BASE}/api/sessions/${session.sessionId}/blocked-cells/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cell, currentTime: runtimeActionTime })
      })
    );
  }

  async function recoverBlockedCell(cell: Cell) {
    if (!session) return;
    await updateSession(() =>
      fetch(`${API_BASE}/api/sessions/${session.sessionId}/blocked-cells/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cell, currentTime: runtimeActionTime })
      })
    );
  }

  async function failRobot(robotId: string) {
    if (!session) return;
    await updateSession(() =>
      fetch(`${API_BASE}/api/sessions/${session.sessionId}/failed-robots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ robotId, currentTime: runtimeActionTime })
      })
    );
  }

  async function restoreRobot(robotId: string) {
    if (!session) return;
    await updateSession(() =>
      fetch(`${API_BASE}/api/sessions/${session.sessionId}/failed-robots/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ robotId, currentTime: runtimeActionTime })
      })
    );
  }

  async function recoverRobot(robotId: string) {
    await restoreRobot(robotId);
  }

  async function tickSession(targetTime: number) {
    if (!session || tickInFlight) return;
    if (targetTime < session.currentTime) return;

    setTickInFlight(true);
    setDispatchError(null);
    try {
      const response = await fetch(`${API_BASE}/api/sessions/${session.sessionId}/tick`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ currentTime: targetTime })
      });
      if (!response.ok) throw await apiErrorFromResponse(response, "session tick failed");
      const payload = (await response.json()) as SessionResult;
      applySessionPayload(payload);
    } catch (error) {
      setPlaying(false);
      setApiStatus("offline");
      setDispatchStatus("error");
      setDispatchError(error instanceof Error ? error.message : "unknown error");
    } finally {
      setTickInFlight(false);
    }
  }

  async function resetCurrentSession() {
    setRouteHintsEnabled(false);
    if (!session) {
      setSessionResetKey((value) => value + 1);
      return;
    }

    setPlaying(false);
    setRandomGeneratorEnabled(false);
    setLastRandomTaskTime(null);
    randomTaskSequenceRef.current = 0;
    setTickInFlight(false);
    resetSessionConflictState(setLatestConflictAlert, setLatestConflictResolved);
    setDispatchStatus("loading");
    setDispatchError(null);
    try {
      const payload = await resetSession(API_BASE, session.sessionId);
      applySessionPayload(payload);
    } catch (error) {
      setApiStatus("offline");
      setDispatchStatus("error");
      setDispatchError(error instanceof Error ? error.message : "unknown error");
    }
  }

  async function updateSession(request: () => Promise<Response>) {
    setDispatchStatus("loading");
    setDispatchError(null);
    try {
      const response = await request();
        if (!response.ok) throw await responseError(response, "session update failed");
        const payload = (await response.json()) as SessionResult;
        applySessionPayload(payload);
        setRouteHintsEnabled(routeHintsAfterSessionUpdate);
    } catch (error) {
      setApiStatus("offline");
      setDispatchStatus("error");
      setDispatchError(error instanceof Error ? error.message : "unknown error");
    }
  }

  function togglePlayback() {
    if (!playing) setRouteHintsEnabled(true);
    setPlaying((value) => !value);
  }

  function commitPlaybackRate() {
    const value = parsePlaybackSpeed(playbackRateInput);
    if (value === null) {
      setPlaybackRateInput(String(playbackRate));
      return;
    }
    setPlaybackRate(value);
    setPlaybackRateInput(String(value));
  }

  function commitRandomTaskInterval() {
    const value = parsePositiveIntegerInput(randomTaskIntervalInput, MIN_RANDOM_TASK_INTERVAL, MAX_RANDOM_TASK_INTERVAL);
    if (value === null) {
      setRandomTaskIntervalInput(String(randomTaskInterval));
      return;
    }
    setRandomTaskInterval(value);
    setRandomTaskIntervalInput(String(value));
    setLastRandomTaskTime(null);
  }

  async function importScenario(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    try {
      const parsed = JSON.parse(await file.text()) as unknown;
      const importedScenario = parseScenario(parsed);
      setImportedScenario(importedScenario);
      setImportStatus("ready");
      setImportError(null);
    } catch (error) {
      setImportStatus("error");
      setImportError(error instanceof Error ? error.message : "无法解析导入文件");
    }
  }

  async function runMapContextAction() {
    const context = mapContextMenu;
    if (!context) return;
    setMapContextMenu(null);
    if (context.action === "block") {
      await blockCell(context.cell);
      return;
    }
    if (context.action === "unblock") {
      await unblockCell(context.cell);
      return;
    }
    if (!context.robotId) return;
    if (context.action === "failRobot") {
      await failRobot(context.robotId);
      return;
    }
    await restoreRobot(context.robotId);
  }

  function exportScenarioTemplate() {
    const blob = new Blob([`${JSON.stringify(scenario, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${safeFileName(scenario.id)}.scenario.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">竞赛演示控制台</p>
          <h1>仓库物流与园区巡检一体化调度系统</h1>
        </div>
        <div className="status-strip">
          <span>T={time}</span>
          <span>{scenario.name}</span>
          <span>{avoidConflicts ? "避碰规划" : "基线对比"}</span>
          <span title={adaptiveReplanWindow ? result?.replanWindowReason : undefined}>
            {assignmentReplanWindowStatusLabel(
              assignmentReplanWindow,
              adaptiveReplanWindow,
              result?.effectiveAssignmentReplanWindow
            )}
          </span>
          <span className={`api ${apiStatus}`}>
            <Server size={15} />
            {apiStatusLabel(apiStatus)}
          </span>
        </div>
      </header>

      <section className="layout">
        <section className="main-column">
          <section className="workspace">
            <div className="map-toolbar">
              <div className="scenario-title" aria-label="当前场景">{scenario.name}</div>
              <div className="toolbar-strategy">
                <span>策略</span>
                <div className="segmented" role="group" aria-label="规划策略">
                  <button className={avoidConflicts ? "active" : ""} type="button" onClick={() => setAvoidConflicts(true)}>
                    避碰规划
                  </button>
                  <button className={!avoidConflicts ? "active" : ""} type="button" onClick={() => setAvoidConflicts(false)}>
                    基线对比
                  </button>
                </div>
              </div>
              <label className="toolbar-field compact">
                <span>重规划窗口</span>
                <input
                  aria-label="重规划窗口"
                  max={MAX_ASSIGNMENT_REPLAN_WINDOW}
                  min={0}
                  step={1}
                  type="number"
                  value={assignmentReplanWindow}
                  onChange={(event) => setAssignmentReplanWindow(normalizeAssignmentReplanWindow(Number(event.target.value)))}
                />
              </label>
              <label className="toolbar-toggle">
                <input
                  aria-label="启用自适应重规划窗口"
                  checked={adaptiveReplanWindow}
                  type="checkbox"
                  onChange={(event) => setAdaptiveReplanWindow(event.target.checked)}
                />
                <span>自适应</span>
              </label>
              <button type="button" onClick={resetCurrentSession}>
                <RefreshCcw size={16} />
                重新规划
              </button>
              <button type="button" onClick={exportScenarioTemplate}>
                <Download size={16} />
                导出模板
              </button>
              <label className="toolbar-import">
                <input accept="application/json,.json" type="file" onChange={importScenario} />
                <span>
                  <Upload size={16} />
                  导入场景
                </span>
              </label>
            </div>
            {importStatus !== "idle" ? (
              <p className={`import-note ${importStatus}`}>
                {importStatus === "ready" ? `已导入场景：${scenario.name}` : `导入失败：${importError}`}
              </p>
            ) : null}

            <div className="map-body">
              <div className="map-main">
                <div className="board-header">
                  <div>
                    <h2>{scenario.name}</h2>
                    <p>{scenario.description}</p>
                  </div>
                  <div className="legend">
                    <span><i className="robot-dot" />机器人</span>
                    <span><i className="task-dot" />任务</span>
                    <span><i className="block-dot" />障碍/封锁</span>
                    <span><i className="conflict-dot" />冲突</span>
                  </div>
                </div>
                {result ? (
                  <MapBoard
                    scenario={scenario}
                    result={result}
                    robotStates={session?.robotStates ?? []}
                    shelfStates={session?.shelfStates ?? []}
                    sessionCurrentTime={session?.currentTime ?? null}
                    time={time}
                    routeHintsEnabled={routeHintsEnabled}
                    selectedRobotId={failedRobotId}
                    onSelectRobot={setFailedRobotId}
                    mapPickTarget={mapPickTarget}
                    onPickCell={pickManualTaskCell}
                    unresolvedConflictAlert={shouldDisplayConflictMarker(latestConflictAlert, latestConflictResolved) ? latestConflictAlert : null}
                    contextMenu={mapContextMenu}
                    canManageBlocks={session !== null && dispatchStatus === "ready"}
                    onOpenContextMenu={setMapContextMenu}
                    onCloseContextMenu={() => setMapContextMenu(null)}
                    onRunContextAction={() => void runMapContextAction()}
                  />
                ) : <MapPlaceholder status={dispatchStatus} error={dispatchError} />}
              </div>

              <aside className="map-side">
                <section className="map-side-section">
                  <h2>指标</h2>
                  {result ? (
                    <>
                      <div className="metrics-grid map-metrics-grid">
                        <Metric label="当前时间" value={simulationTimeLabel(time)} />
                        <Metric label="已完成任务" value={`${liveMetrics?.completedTaskCount ?? 0} 个`} />
                        <Metric label="执行中任务" value={`${liveMetrics?.activeTaskCount ?? 0} 个`} />
                        <Metric label="等待任务" value={`${liveMetrics?.pendingTaskCount ?? 0} 个`} />
                        <Metric label="当前路程" value={`${liveMetrics?.travelledDistance ?? 0} 格`} />
                        <Metric label="当前冲突" value={`${liveMetrics?.activeConflictCount ?? 0} 次`} />
                        <Metric label="已超期任务" value={`${liveMetrics?.liveDeadlineMissCount ?? 0} 个`} />
                        <Metric label="重规划" value={`${result.metrics.replanTimeMs} ms`} />
                      </div>
                    </>
                  ) : (
                    <EmptyState status={dispatchStatus} error={dispatchError} />
                  )}
                </section>

                <section className="map-side-section">
                  <h2>重规划解释</h2>
                  {replanStatus ? (
                    <ReplanStatusPanel
                      status={replanStatus}
                      robotStates={session?.robotStates ?? []}
                      conflictAlert={latestConflictAlert}
                      conflictResolved={latestConflictResolved}
                    />
                  ) : (
                    <EmptyState status={dispatchStatus} error={dispatchError} />
                  )}
                </section>
              </aside>
            </div>
          </section>

          <section className="control-panels">
            <Panel title="在线任务">
              <form className="task-form" onSubmit={submitManualTask}>
                <label>
                  <span>类型</span>
                  <select
                    value={manualTask.type}
                    onChange={(event) => {
                      setManualTask((task) => ({ ...task, type: event.target.value as TaskType }));
                      setManualTaskError(null);
                      setMapPickTarget(null);
                    }}
                  >
                    <option value="inspection">巡检</option>
                    <option value="delivery">取送</option>
                    <option value="emergency">突发</option>
                  </select>
                </label>
                <label>
                  <span>标题</span>
                  <input
                    value={manualTask.title}
                    onChange={(event) => setManualTask((task) => ({ ...task, title: event.target.value }))}
                  />
                </label>
                 <div className="form-grid">
                  <label>
                    <span>优先级</span>
                    <input
                      min={0}
                      max={5}
                      type="number"
                      value={manualTask.priority}
                      onChange={(event) => setManualTask((task) => ({ ...task, priority: Number(event.target.value) }))}
                    />
                  </label>
                  <label>
                    <span>截止 T</span>
                    <input
                      min={runtimeActionTime + 1}
                      type="number"
                      value={manualTask.deadline}
                      onChange={(event) => setManualTask((task) => ({ ...task, deadline: Number(event.target.value) }))}
                    />
                   </label>
                   <label>
                     <span>作业时间（tick）</span>
                     <input
                       min={0}
                       step={1}
                       type="number"
                       value={manualTask.serviceTime}
                       onChange={(event) => setManualTask((task) => ({ ...task, serviceTime: Number(event.target.value) }))}
                     />
                   </label>
                 </div>
                {manualTask.type === "delivery" ? (
                  <div className="form-grid">
                    <CoordinateInput
                      label="取货"
                      value={manualTask.pickup}
                      onChange={(pickup) => {
                        setManualTask((task) => ({ ...task, pickup }));
                        setManualTaskError(null);
                      }}
                      onPick={() => setMapPickTarget("pickup")}
                      picking={mapPickTarget === "pickup"}
                    />
                    <CoordinateInput
                      label="送达"
                      value={manualTask.dropoff}
                      onChange={(dropoff) => {
                        setManualTask((task) => ({ ...task, dropoff }));
                        setManualTaskError(null);
                      }}
                      onPick={() => setMapPickTarget("dropoff")}
                      picking={mapPickTarget === "dropoff"}
                    />
                  </div>
                ) : (
                    <CoordinateInput
                      label="目标"
                      value={manualTask.target}
                      onChange={(target) => {
                        setManualTask((task) => ({ ...task, target }));
                        setManualTaskError(null);
                      }}
                      onPick={() => setMapPickTarget("target")}
                      picking={mapPickTarget === "target"}
                    />
                  )}
                {manualTaskError ? <p className="empty-state">{manualTaskError}</p> : null}
                <button className="wide-action" type="submit" disabled={!session || dispatchStatus === "loading"}>
                  <Plus size={16} />
                  推入任务队列
                </button>
              </form>
            </Panel>

            <Panel title="随机事件生成器">
              <label className="toggle-row">
                <input
                  checked={randomGeneratorEnabled}
                  type="checkbox"
                  onChange={(event) => {
                    setRandomGeneratorEnabled(event.target.checked);
                    setLastRandomTaskTime(null);
                  }}
                />
                <span>自动生成并推入任务队列</span>
              </label>
              <label className="event-select">
                <span>生成间隔（tick）</span>
                <input
                  inputMode="numeric"
                  min={MIN_RANDOM_TASK_INTERVAL}
                  max={MAX_RANDOM_TASK_INTERVAL}
                  step={1}
                  type="number"
                  value={randomTaskIntervalInput}
                  onBlur={commitRandomTaskInterval}
                  onChange={(event) => setRandomTaskIntervalInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter") return;
                    event.preventDefault();
                    commitRandomTaskInterval();
                  }}
                />
              </label>
            </Panel>

            <Panel title="仿真启动">
              <div className="control-row">
                <button type="button" onClick={togglePlayback} disabled={!result || tickInFlight}>
                  {playing ? <Pause size={16} /> : <Play size={16} />}
                  {playing ? "暂停" : "播放"}
                </button>
                <button type="button" onClick={resetCurrentSession} disabled={!result || tickInFlight}>
                  <Square size={15} />
                  重置
                </button>
              </div>
              <label className="slider-row">
                <span>当前时间：{simulationTimeLabel(time)}</span>
                <strong className="time-readout">{simulationTimeLabel(time)}</strong>
              </label>
              <label className="slider-row">
                <span>播放速度（tick/s）</span>
                <input
                  className="speed-input"
                  inputMode="decimal"
                  max={MAX_PLAYBACK_RATE}
                  min={MIN_PLAYBACK_RATE}
                  step={0.1}
                  type="number"
                  value={playbackRateInput}
                  onBlur={commitPlaybackRate}
                  onChange={(event) => setPlaybackRateInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter") return;
                    event.preventDefault();
                    commitPlaybackRate();
                  }}
                />
              </label>
              <p className="session-note">
                {playing ? "仿真运行中" : "仿真已暂停"} · {tickInFlight ? "后端 tick 同步中" : dispatchStatus === "loading" ? "等待重规划结果" : "调度结果已同步"}
              </p>
            </Panel>
          </section>

        </section>

        <aside className="rightbar">
          <Panel title="事件日志" className={RIGHTBAR_EVENT_LOG_CLASS}>
            <div className="event-log">
              {result ? visibleRuntimeEvents(result.eventLog, routeHintsEnabled, time).map((event, index) => {
                const canJump = session ? canJumpToEvent(event.time, session.currentTime) : false;
                return (
                  <div className={`event ${event.time <= time ? "active" : ""}`} key={`${event.time}-${index}`}>
                    <div className="event-header">
                      <span>T={event.time}</span>
                      <button
                        type="button"
                        onClick={() => {
                          setPlaying(false);
                          setTime(event.time);
                        }}
                        disabled={!canJump}
                      >
                        跳转
                      </button>
                    </div>
                    <strong>{event.text}</strong>
                  </div>
                );
              }) : <EmptyState status={dispatchStatus} error={dispatchError} />}
            </div>
          </Panel>
          <Panel title="任务队列" className={RIGHTBAR_TASK_QUEUE_CLASS}>
            {result ? (
              <div className="task-queue-columns">
                <section className="task-queue-column">
                  <h3>未完成</h3>
                  <div className="entity-list compact-list">
                    {taskGroups.unfinished.map((snapshot) => (
                      <TaskQueueItem
                        key={snapshot.task.id}
                        snapshot={snapshot}
                        disabled={dispatchStatus === "loading"}
                        onRecoverBlockedCell={recoverBlockedCell}
                        onRecoverRobot={recoverRobot}
                      />
                    ))}
                  </div>
                </section>
                <section className="task-queue-column">
                  <h3>已完成</h3>
                  <div className="entity-list compact-list">
                    {taskGroups.completed.map((snapshot) => (
                      <TaskQueueItem
                        key={snapshot.task.id}
                        snapshot={snapshot}
                        disabled={dispatchStatus === "loading"}
                        onRecoverBlockedCell={recoverBlockedCell}
                        onRecoverRobot={recoverRobot}
                      />
                    ))}
                  </div>
                </section>
              </div>
            ) : <EmptyState status={dispatchStatus} error={dispatchError} />}
          </Panel>
        </aside>
      </section>
    </main>
  );
}

function Panel({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`.trim()}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReplanStatusPanel({
  status,
  robotStates,
  conflictAlert,
  conflictResolved
}: {
  status: ReplanStatus | null;
  robotStates: SessionResult["robotStates"];
  conflictAlert: ConflictAlert | null;
  conflictResolved: boolean;
}) {
  if (!status) {
    return <p className="empty-state">暂无在线调度解释。</p>;
  }

  return (
    <>
      <div className="replan-status-grid">
        <Metric label="锁定任务" value={`${status.lockedTaskCount} 个`} />
        <Metric label="未分配任务" value={`${status.unassignedTaskCount} 个`} />
        <Metric label="故障机器人" value={`${status.failedRobotCount} 台`} />
        <Metric label="充电机器人" value={`${robotStates.filter((item) => item.status === "toCharge" || item.status === "charging").length} 台`} />
      </div>
      {conflictAlert ? (
        <div className={`conflict-alert ${conflictResolved ? "resolved" : ""}`}>
          <TriangleAlert size={18} aria-hidden="true" />
          <div>
            <strong>{conflictResolved ? "冲突已解决" : "持续冲突提示"}</strong>
            <span>T={conflictAlert.time} · {conflictTypeLabel(conflictAlert.type)}</span>
            <span>{conflictAlert.robots.join(" 与 ")} · ({conflictAlert.cell[0]}, {conflictAlert.cell[1]})</span>
          </div>
        </div>
      ) : null}
    </>
  );
}

function CoordinateInput({
  label,
  value,
  onChange,
  onPick,
  picking
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onPick: () => void;
  picking: boolean;
}) {
  return (
    <fieldset className="coordinate-input">
      <legend>{label}</legend>
      <input
        aria-label={`${label}坐标`}
        inputMode="text"
        placeholder="例如 3, 0"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        aria-label={`从地图选择${label}坐标`}
        className={picking ? "coordinate-pick active" : "coordinate-pick"}
        onClick={onPick}
        title={`从地图选择${label}坐标`}
        type="button"
      >
        <Crosshair size={16} />
      </button>
    </fieldset>
  );
}

function EmptyState({ status, error }: { status: string; error: string | null }) {
  return <p className="empty-state">{status === "error" ? `调度失败：${error}` : "正在请求后端调度结果..."}</p>;
}

function MapPlaceholder({ status, error }: { status: string; error: string | null }) {
  return (
    <div className="map-placeholder">
      <EmptyState status={status} error={error} />
    </div>
  );
}

function TaskQueueItem({
  snapshot,
  disabled,
  onRecoverBlockedCell,
  onRecoverRobot
}: {
  snapshot: TaskSnapshot;
  disabled: boolean;
  onRecoverBlockedCell: (cell: Cell) => void;
  onRecoverRobot: (robotId: string) => void;
}) {
  const recoveryTargets = snapshot.failureDetail ? buildRecoveryTargets(snapshot.failureDetail) : [];
  const metricRows = buildTaskQueueMetricRows(snapshot);
  return (
    <details className={`entity task-${snapshot.status}`} open={snapshot.status !== "done"}>
      <summary>
        <strong>{snapshot.task.id} · {snapshot.task.title}</strong>
      </summary>
      <div className="task-metric-list">
        {metricRows.map((item) => (
          <div className="task-metric-row" key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
      {snapshot.failureReason ? <span className="task-failure">原因：{snapshot.failureReason}</span> : null}
      {snapshot.failureDetail ? (
        <div className="task-recovery">
          <span>{recoveryActionLabel(snapshot.failureDetail.recoveryAction)}</span>
          {snapshot.failureDetail.blockingCells.length ? <span>封锁：{formatCells(snapshot.failureDetail.blockingCells)}</span> : null}
          {snapshot.failureDetail.blockingRobotIds.length ? <span>机器人：{snapshot.failureDetail.blockingRobotIds.join(", ")}</span> : null}
          {recoveryTargets.length ? (
            <div className="task-recovery-actions">
              {recoveryTargets.map((target) => (
                <button
                  key={target.kind === "cell" ? `cell-${cellKey(target.cell)}` : `robot-${target.robotId}`}
                  type="button"
                  onClick={() => target.kind === "cell" ? onRecoverBlockedCell(target.cell) : onRecoverRobot(target.robotId)}
                  disabled={disabled}
                >
                  <RefreshCcw size={13} />
                  {target.label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="progress" aria-label={`${snapshot.task.id} 进度`}>
        <i style={{ width: `${Math.round(snapshot.progress * 100)}%` }} />
      </div>
    </details>
  );
}

export function MapBoard({
  scenario,
  result,
  robotStates,
  shelfStates,
  sessionCurrentTime,
  time,
  routeHintsEnabled,
  selectedRobotId,
  onSelectRobot,
  mapPickTarget,
  onPickCell,
  unresolvedConflictAlert,
  contextMenu,
  canManageBlocks,
  onOpenContextMenu,
  onCloseContextMenu,
  onRunContextAction
}: {
  scenario: Scenario;
  result: DispatchResult;
  robotStates: SessionResult["robotStates"];
  shelfStates: ShelfRuntimeState[];
  sessionCurrentTime: number | null;
  time: number;
  routeHintsEnabled: boolean;
  selectedRobotId: string;
  onSelectRobot: (robotId: string) => void;
  mapPickTarget: MapPickTarget | null;
  onPickCell: (cell: Cell) => void;
  unresolvedConflictAlert: ConflictAlert | null;
  contextMenu: MapContextMenuState | null;
  canManageBlocks: boolean;
  onOpenContextMenu: (context: MapContextMenuState) => void;
  onCloseContextMenu: () => void;
  onRunContextAction: () => void;
}) {
  const obstacles = useMemo(() => new Set(scenario.obstacles.map(cellKey)), [scenario.obstacles]);
  const blocked = useMemo(() => new Set(result.extraBlocked.map(cellKey)), [result.extraBlocked]);
  const unavailableRobots = useMemo(() => new Set(result.unavailableRobotIds), [result.unavailableRobotIds]);
  const taskLabelTasks = useMemo(
    () => filterInitialScenarioTaskLabels(result.tasks, scenario),
    [result.tasks, scenario]
  );
  const taskCells = useMemo(() => collectTaskCells(taskLabelTasks), [taskLabelTasks]);
  const chargingCells = useMemo(() => new Set((scenario.zones.charging ?? []).map(cellKey)), [scenario.zones.charging]);
  const zoneCellPresentations = useMemo(
    () => buildZoneCellPresentations(scenario.zones),
    [scenario.zones]
  );
  const shelfCellPresentations = useMemo(
    () => buildShelfCellPresentations(scenario.shelves ?? [], shelfStates),
    [scenario.shelves, shelfStates]
  );
  const useRuntimeRobotSnapshot = shouldUseRuntimeRobotSnapshot(time, sessionCurrentTime, robotStates);
  const occupied = useMemo(
    () => useRuntimeRobotSnapshot ? getCellsFromRobotStates(robotStates) : getCellsOnPaths(result.paths, time),
    [result.paths, robotStates, time, useRuntimeRobotSnapshot]
  );
  const occupiedKeys = useMemo(
    () => new Set([...occupied.values()].map(cellKey)),
    [occupied]
  );
  const activeConflicts = useMemo(
    () => new Map(selectMapConflictMarkers(result.conflicts, time, unresolvedConflictAlert, result.paths, result.conflictStates).map((conflict) => [cellKey(conflict.cell), conflict])),
    [result.conflicts, result.conflictStates, result.paths, time, unresolvedConflictAlert]
  );
  const routeArrows = useMemo(
    () => buildActiveRouteArrows(result, time, useRuntimeRobotSnapshot ? robotStates : [], routeHintsEnabled),
    [result, robotStates, routeHintsEnabled, time, useRuntimeRobotSnapshot]
  );
  const visibleRouteArrows = useMemo(
    () => filterActiveRouteArrows(routeArrows, taskCells),
    [routeArrows, taskCells]
  );
  const robotColors = useMemo(
    () => new Map(scenario.robots.map((robot, index) => [robot.id, robotColorForIndex(index)])),
    [scenario.robots]
  );

  const cells = [];
  for (let y = 0; y < scenario.height; y += 1) {
    for (let x = 0; x < scenario.width; x += 1) {
      const cell: Cell = [x, y];
      const key = cellKey(cell);
      const robotId = robotAtCell(occupied, key);
      const displayedConflict = activeConflicts.get(key) ?? null;
      const zonePresentation = zoneCellPresentations.get(key);
      const shelfPresentation = shelfCellPresentations.get(key);
      const robot = robotId ? scenario.robots.find((item) => item.id === robotId) : null;
      const robotColor = robotId ? robotColors.get(robotId) : undefined;
      const runtimeState = useRuntimeRobotSnapshot && robotId ? robotStates.find((item) => item.robotId === robotId) : null;
      const robotState = robot && robotId
        ? getDisplayRobotState(robot, result.paths[robotId] ?? [robot.start], time, result.unavailableRobotIds, runtimeState ?? null)
        : null;
      const currentTask = robotId ? getCurrentTaskLabel(result, robotId, time, runtimeState?.currentTaskId) : null;
      const classNames = [
        "cell",
        obstacles.has(key) ? "obstacle" : "",
        blocked.has(key) ? "blocked" : "",
        taskCells.has(key) ? "task-cell" : "",
        ...(zonePresentation?.classNames ?? []),
        ...(shelfPresentation?.classNames ?? []),
        displayedConflict ? "conflict-cell" : "",
        mapPickTarget && !obstacles.has(key) ? "map-pickable-cell" : "",
        robotId ? "robot-cell" : "",
        robotState?.status === "failed" ? "failed-robot-cell" : "",
        isSelectedRobotCell(robotId, selectedRobotId) ? "selected-robot-cell" : ""
      ]
        .filter(Boolean)
        .join(" ");

      cells.push(
        <button
          aria-label={mapPickTarget ? `选择${mapPickLabel(mapPickTarget)}坐标 ${x}, ${y}` : `选择封锁单元 ${x}, ${y}`}
          className={classNames}
          key={key}
          title={shelfPresentation?.label}
          onClick={() => {
            onCloseContextMenu();
            if (mapPickTarget && !obstacles.has(key)) {
              onPickCell(cell);
              return;
            }
            if (robotId) onSelectRobot(robotId);
          }}
          onContextMenu={(event) => {
            event.preventDefault();
            if (!canManageBlocks) {
              onCloseContextMenu();
              return;
            }
            if (robotId) {
              const action = robotContextAction(robotId, unavailableRobots);
              if (!action) {
                onCloseContextMenu();
                return;
              }
              onOpenContextMenu({ cell, action, robotId, x: event.clientX, y: event.clientY });
              return;
            }
            const action = mapContextAction(cell, blocked, obstacles, taskCells, occupiedKeys, chargingCells);
            if (!action) {
              onCloseContextMenu();
              return;
            }
            onOpenContextMenu({ cell, action, x: event.clientX, y: event.clientY });
          }}
          style={robotColor ? { "--robot-color": robotColor } as React.CSSProperties : undefined}
          type="button"
        >
          {robotId ? (
            <span className="robot-marker">
              {robotId}
              {robotState ? (
                <span className="robot-tooltip">
                  <strong>{robotState.id} · {robotState.name}</strong>
                  <span>位置 ({robotState.position[0]}, {robotState.position[1]}) · {robotState.status}</span>
                   <span>任务 {currentTask ?? "无"}</span>
                     <span>电量 {robotState.battery}/{runtimeState?.batteryCapacity ?? robot?.batteryCapacity ?? 100} · 载重 {robotState.load}</span>
                   <span>{robotMoveDurationLabel(runtimeState?.moveTicks ?? robot?.moveTicks ?? 1)}</span>
                   <span>进度 {Math.round(robotState.progress * 100)}%</span>
                </span>
              ) : null}
            </span>
          ) : (taskCells.get(key) ?? zonePresentation?.label ?? null)}
          {displayedConflict ? (
            <span className="conflict-marker active">
              <TriangleAlert size={14} aria-hidden="true" />
              <span>{displayedConflict.robots.join("/")}</span>
            </span>
          ) : null}
        </button>
      );
    }
  }

  const gridStyle = {
    gridTemplateColumns: `repeat(${scenario.width}, minmax(0, 1fr))`,
    gridTemplateRows: `repeat(${scenario.height}, minmax(0, 1fr))`
  } as React.CSSProperties;

  return (
    <div className="map-wrap" style={{ "--cols": scenario.width, "--rows": scenario.height } as React.CSSProperties}>
      <div className="active-route-layer" style={gridStyle} aria-hidden="true">
        {visibleRouteArrows.map((arrow, index) => (
          <span
            className={`active-route-arrow active-route-arrow-${arrow.lane % 6}`}
            key={`${arrow.robotId}-${arrow.cell[0]}-${arrow.cell[1]}-${arrow.angle}-${index}`}
            style={{
              gridColumn: arrow.cell[0] + 1,
              gridRow: arrow.cell[1] + 1,
              "--arrow-angle": `${arrow.angle}deg`,
              "--robot-color": robotColors.get(arrow.robotId) ?? ROBOT_COLORS[0]
            } as React.CSSProperties}
          >
            <ArrowUp size={15} strokeWidth={2.6} />
          </span>
        ))}
      </div>
      <div className="grid-map" style={gridStyle}>
        {cells}
      </div>
      {contextMenu ? (
        <div className="map-context-menu" role="menu" style={{ left: contextMenu.x, top: contextMenu.y }} onClick={(event) => event.stopPropagation()}>
          <button type="button" role="menuitem" onClick={onRunContextAction}>
            {contextMenu.action === "block" ? <Lock size={15} /> : null}
            {contextMenu.action === "unblock" ? <Unlock size={15} /> : null}
            {contextMenu.action === "failRobot" ? <ShieldX size={15} /> : null}
            {contextMenu.action === "restoreRobot" ? <RefreshCcw size={15} /> : null}
            {mapContextActionLabel(contextMenu)}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function getCellsOnPaths(paths: Record<string, Cell[]>, time: number): Map<string, Cell> {
  const cells = new Map<string, Cell>();
  for (const [robotId, path] of Object.entries(paths)) {
    const cell = path[Math.min(time, path.length - 1)];
    if (cell) cells.set(robotId, cell);
  }
  return cells;
}

function getCellsFromRobotStates(robotStates: SessionResult["robotStates"]): Map<string, Cell> {
  const cells = new Map<string, Cell>();
  for (const robotState of robotStates) {
    cells.set(robotState.robotId, robotState.position);
  }
  return cells;
}

function getDisplayRobotState(
  robot: Scenario["robots"][number],
  path: Cell[],
  time: number,
  unavailableRobotIds: string[],
  runtimeState: SessionResult["robotStates"][number] | null
) {
  const fallback = getRobotStateAt(robot, path, time, unavailableRobotIds);
  if (!runtimeState) return fallback;
  return {
    ...fallback,
    id: runtimeState.robotId,
    name: runtimeState.name,
    position: runtimeState.position,
    status: robotRuntimeStatusLabel(runtimeState.status),
    battery: runtimeState.battery,
    load: runtimeState.load
  };
}

function collectTaskCells(tasks: Task[]): Map<string, string> {
  const cells = new Map<string, string>();
  for (const task of tasks) {
    if (task.type === "inspection") {
      for (const target of task.targets) cells.set(cellKey(target), "巡");
    } else if (task.type === "delivery") {
      cells.set(cellKey(task.pickup), "取");
      cells.set(cellKey(task.dropoff), "送");
    } else {
      cells.set(cellKey(task.target), "急");
    }
  }
  return cells;
}

function robotAtCell(occupied: Map<string, Cell>, key: string): string | null {
  for (const [robotId, cell] of occupied.entries()) {
    if (cellKey(cell) === key) return robotId;
  }
  return null;
}

export function buildTaskSnapshots(result: DispatchResult, time: number, runtimeStates: SessionResult["taskStates"] | undefined): TaskSnapshot[] {
  const completions = getTaskCompletionMap(result);
  return result.tasks.map((task) => {
    const runtimeState = runtimeStates?.find((item) => item.taskId === task.id);
    const assignment = findTaskAssignment(result, task.id);
    const releaseTime = runtimeState?.releaseTime ?? task.releaseTime ?? 0;
    const completionTime = runtimeState?.completionTime ?? completions.get(task.id) ?? null;
    const assignedRobotId = runtimeState ? runtimeState.assignedRobotId : assignment?.robotId ?? null;
    const status = getTaskSnapshotStatus(task, runtimeState, assignedRobotId, releaseTime, completionTime, time);
    const locked = runtimeState?.locked ?? false;
    const failureReason = status === "unassigned" ? runtimeState?.failureReason ?? result.failureReasons[task.id] ?? null : null;
    const failureDetail = status === "unassigned" ? result.failureDetails[task.id] ?? null : null;
    const progress = getTaskProgress(releaseTime, completionTime, time, status);

    return {
      task,
      assignedRobotId,
      releaseTime,
      isReleased: time >= releaseTime,
      completionTime,
      status,
      locked,
      failureReason,
      failureDetail,
      progress
    };
  });
}

export function sortTaskSnapshotsForDisplay(snapshots: TaskSnapshot[]): TaskSnapshot[] {
  const statusRank: Record<TaskRuntimeStatus, number> = {
    unassigned: 0,
    active: 1,
    pending: 2,
    done: 3
  };
  return [...snapshots].sort((left, right) => {
    const statusDifference = statusRank[left.status] - statusRank[right.status];
    if (statusDifference !== 0) return statusDifference;
    if (left.status === "done" && right.status === "done") {
      return (left.completionTime ?? Number.POSITIVE_INFINITY) - (right.completionTime ?? Number.POSITIVE_INFINITY)
        || left.task.id.localeCompare(right.task.id);
    }
    return left.releaseTime - right.releaseTime || left.task.id.localeCompare(right.task.id);
  });
}

export function splitTaskSnapshotsForDisplay(snapshots: TaskSnapshot[]): {
  unfinished: TaskSnapshot[];
  completed: TaskSnapshot[];
} {
  const sorted = sortTaskSnapshotsForDisplay(snapshots);
  return {
    unfinished: sorted.filter((snapshot) => snapshot.status !== "done"),
    completed: sorted.filter((snapshot) => snapshot.status === "done")
  };
}

export function taskTimingFields(snapshot: TaskSnapshot): { release: string; deadline: string; completion: string } {
  const completion = snapshot.completionTime == null
    ? "完成：未完成"
    : snapshot.status === "done"
      ? `完成 T=${snapshot.completionTime}`
      : `预计完成 T=${snapshot.completionTime}`;
  return {
    release: `到达 T=${snapshot.releaseTime}`,
    deadline: snapshot.task.deadline == null ? "截止：无" : `截止 T=${snapshot.task.deadline}`,
    completion
  };
}

export function buildTaskQueueMetricRows(snapshot: TaskSnapshot): Array<{ label: string; value: string }> {
  const waitingForAssignment = snapshot.status === "pending" && snapshot.isReleased && snapshot.assignedRobotId === null;
  const completion = snapshot.completionTime == null
    ? "未完成"
    : snapshot.status === "done"
      ? `T=${snapshot.completionTime}`
      : `预计 T=${snapshot.completionTime}`;
  return [
    { label: "状态", value: waitingForAssignment ? "等待分配" : taskStatusLabel(snapshot.status) },
    { label: "执行机器人", value: snapshot.assignedRobotId ?? (waitingForAssignment ? "等待分配" : "未分配") },
    { label: "任务类型", value: taskTypeLabel(snapshot.task.type) },
    { label: "优先级", value: String(snapshot.task.priority) },
    { label: "锁定状态", value: snapshot.locked ? "已锁定" : "可重分配" },
    { label: "到达时间", value: `T=${snapshot.releaseTime}` },
    { label: "截止时间", value: snapshot.task.deadline == null ? "无" : `T=${snapshot.task.deadline}` },
    { label: "作业时间", value: `${snapshot.task.serviceTime ?? 0} tick` },
    { label: "完成时间", value: completion }
  ];
}

export function buildLiveMetrics(
  result: DispatchResult,
  time: number,
  runtimeStates: SessionResult["taskStates"] | undefined,
  metricsHistory?: SessionResult["metricsHistory"]
): LiveMetrics {
  const snapshots = buildTaskSnapshots(result, time, runtimeStates);
  const backendSnapshot = getVisibleMetricsHistory(metricsHistory, time).at(-1);
  return {
    completedTaskCount: snapshots.filter((snapshot) => snapshot.status === "done").length,
    activeTaskCount: snapshots.filter((snapshot) => snapshot.status === "active").length,
    pendingTaskCount: snapshots.filter((snapshot) => snapshot.status === "pending").length,
    travelledDistance: Object.values(result.paths).reduce((sum, path) => sum + distanceUntil(path, time), 0),
    activeConflictCount: result.conflictStates !== undefined
      ? result.conflictStates.filter((conflict) => isConflictStateActiveAtTime(conflict, time)).length
      : result.conflicts.filter((conflict) => conflict.time === time).length,
    liveDeadlineMissCount: backendSnapshot?.deadlineMissCount ?? snapshots.filter((snapshot) => {
      const deadline = snapshot.task.deadline;
      return deadline != null && snapshot.status !== "done" && snapshot.status !== "pending" && time > deadline;
    }).length
  };
}

export function buildReplanStatus(
  result: DispatchResult,
  session: SessionResult | null
): ReplanStatus {
  const taskStates = session?.taskStates ?? [];
  return {
    lockedTaskCount: taskStates.filter((state) => state.locked).length,
    unassignedTaskCount: taskStates.filter((state) => state.status === "unassigned").length,
    failedRobotCount: result.unavailableRobotIds.length
  };
}

function getTaskRuntimeStatus(
  task: Task,
  assignedRobotId: string | null,
  releaseTime: number,
  completionTime: number | null,
  time: number
): TaskRuntimeStatus {
  if (time < releaseTime) return "pending";
  if (completionTime !== null && time >= completionTime) return "done";
  if (!assignedRobotId) return "unassigned";
  return "active";
}

function getTaskSnapshotStatus(
  task: Task,
  runtimeState: SessionResult["taskStates"][number] | undefined,
  assignedRobotId: string | null,
  releaseTime: number,
  completionTime: number | null,
  time: number
): TaskRuntimeStatus {
  if (time < releaseTime) return "pending";
  if (completionTime !== null && time >= completionTime) return "done";
  if (runtimeState?.status === "pending") return "pending";
  if (runtimeState?.status === "unassigned") return "unassigned";
  return getTaskRuntimeStatus(task, assignedRobotId, releaseTime, completionTime, time);
}

function getTaskProgress(
  releaseTime: number,
  completionTime: number | null,
  time: number,
  status: TaskRuntimeStatus
): number {
  if (status === "done") return 1;
  if (status === "pending" || status === "unassigned" || completionTime === null) return 0;
  const duration = Math.max(1, completionTime - releaseTime);
  return clamp((time - releaseTime) / duration, 0, 1);
}

function getTaskCompletionMap(result: DispatchResult): Map<string, number> {
  const completions = new Map<string, number>();
  for (const assignment of result.assignments) {
    const path = result.paths[assignment.robotId] ?? [];
    let cursorIndex = 0;
    for (const task of assignment.tasks) {
      let completionIndex: number | null = null;
      for (const waypoint of taskWaypoints(task)) {
        const foundIndex = findNextVisit(path, waypoint, cursorIndex);
        if (foundIndex === null) {
          completionIndex = null;
          break;
        }
        completionIndex = foundIndex;
        cursorIndex = foundIndex;
      }
      if (completionIndex !== null) completions.set(task.id, completionIndex);
    }
  }
  return completions;
}

function getCurrentTaskLabel(
  result: DispatchResult,
  robotId: string,
  time: number,
  runtimeTaskId?: string | null
): string | null {
  if (runtimeTaskId) {
    const task = result.tasks.find((item) => item.id === runtimeTaskId);
    return task ? `${task.id} · ${task.title}` : runtimeTaskId;
  }
  const assignment = result.assignments.find((item) => item.robotId === robotId);
  if (!assignment) return null;
  const completions = getTaskCompletionMap(result);
  for (const task of assignment.tasks) {
    const releaseTime = task.releaseTime ?? 0;
    const completionTime = completions.get(task.id) ?? null;
    const status = getTaskRuntimeStatus(task, robotId, releaseTime, completionTime, time);
    if (status === "active") return `${task.id} · ${task.title}`;
  }
  const nextTask = assignment.tasks.find((task) => (task.releaseTime ?? 0) > time);
  return nextTask ? `等待 ${nextTask.id}` : null;
}

export function buildActiveRouteArrows(
  result: DispatchResult,
  time: number,
  runtimeStates: SessionResult["robotStates"],
  enabled: boolean
): ActiveRouteArrow[] {
  if (!enabled) return [];

  const completions = getTaskCompletionMap(result);
  const arrows: ActiveRouteArrow[] = [];
  for (const [lane, assignment] of result.assignments.entries()) {
    if (result.unavailableRobotIds.includes(assignment.robotId)) continue;

    const path = result.paths[assignment.robotId] ?? [];
    if (path.length < 2) continue;

    const pathIndex = clamp(Math.trunc(time), 0, path.length - 1);
    const runtimeTaskId = runtimeStates.find((state) => state.robotId === assignment.robotId)?.currentTaskId;
    const task = findActiveRouteTask(assignment.tasks, assignment.robotId, runtimeTaskId, completions, time);
    if (!task) continue;

    const endIndex = findRemainingTaskEndIndex(path, task, pathIndex);
    if (endIndex === null) continue;

    for (let index = pathIndex; index < endIndex; index += 1) {
      const from = path[index];
      const to = path[index + 1];
      if (!to || cellKey(from) === cellKey(to)) continue;
      arrows.push({
        robotId: assignment.robotId,
        cell: to,
        angle: Math.round(Math.atan2(to[0] - from[0], -(to[1] - from[1])) * 180 / Math.PI),
        lane
      });
    }
  }
  return arrows;
}

function findActiveRouteTask(
  tasks: Task[],
  robotId: string,
  runtimeTaskId: string | null | undefined,
  completions: Map<string, number>,
  time: number
): Task | null {
  if (runtimeTaskId) return tasks.find((task) => task.id === runtimeTaskId) ?? null;
  for (const task of tasks) {
    const status = getTaskRuntimeStatus(task, robotId, task.releaseTime ?? 0, completions.get(task.id) ?? null, time);
    if (status === "active") return task;
  }
  return null;
}

function findRemainingTaskEndIndex(path: Cell[], task: Task, startIndex: number): number | null {
  let cursor = startIndex;
  let hasRemainingSegment = false;
  for (const waypoint of taskWaypoints(task)) {
    const nextVisit = findNextVisit(path, waypoint, cursor);
    if (nextVisit === null) continue;
    if (nextVisit > startIndex) hasRemainingSegment = true;
    cursor = nextVisit;
  }
  return hasRemainingSegment ? cursor : null;
}

function findTaskAssignment(result: DispatchResult, taskId: string) {
  return result.assignments.find((assignment) => assignment.tasks.some((task) => task.id === taskId));
}

function taskWaypoints(task: Task): Cell[] {
  if (task.type === "inspection") return task.targets;
  if (task.type === "delivery") return [task.pickup, task.dropoff];
  return [task.target];
}

function findNextVisit(path: Cell[], waypoint: Cell, startIndex: number): number | null {
  for (let index = startIndex; index < path.length; index += 1) {
    if (cellKey(path[index]) === cellKey(waypoint)) return index;
  }
  return null;
}

function distanceUntil(path: Cell[], time: number): number {
  const end = Math.min(time, path.length - 1);
  let distance = 0;
  for (let index = 1; index <= end; index += 1) {
    if (cellKey(path[index]) !== cellKey(path[index - 1])) distance += 1;
  }
  return distance;
}

function taskTypeLabel(type: Task["type"]): string {
  if (type === "inspection") return "巡检";
  if (type === "delivery") return "取送";
  return "突发";
}

function taskStatusLabel(status: TaskRuntimeStatus): string {
  if (status === "pending") return "等待释放";
  if (status === "active") return "执行中";
  if (status === "done") return "已完成";
  return "未分配";
}

function recoveryActionLabel(action: RecoveryAction): string {
  const labels: Record<RecoveryAction, string> = {
    addCapableRobotOrReduceDemand: "恢复：增加载重机器人或降低需求",
    clearBlockedCells: "恢复：解除封锁",
    clearBlockedCellsAndRestoreRobot: "恢复：解除封锁并恢复机器人",
    clearBlockedCellsOrRestoreRobot: "恢复：解除封锁或恢复机器人",
    fixMapOrTaskTarget: "恢复：修正地图或任务目标",
    fixTaskDefinition: "恢复：补全任务定义",
    relaxLocksOrReplan: "恢复：放宽锁定或重新规划",
    restoreRobot: "恢复：恢复机器人"
  };
  return labels[action];
}

export function buildRecoveryTargets(detail: TaskFailureDetail): RecoveryTarget[] {
  if (detail.category !== "temporary") return [];

  const targets: RecoveryTarget[] = [];
  if (allowsBlockedCellRecovery(detail.recoveryAction)) {
    for (const cell of detail.blockingCells) {
      targets.push({ kind: "cell", cell, label: `解除封锁 ${formatCell(cell)}` });
    }
  }
  if (allowsRobotRecovery(detail.recoveryAction)) {
    for (const robotId of detail.blockingRobotIds) {
      targets.push({ kind: "robot", robotId, label: `恢复机器人 ${robotId}` });
    }
  }
  return targets;
}

function allowsBlockedCellRecovery(action: RecoveryAction): boolean {
  return action === "clearBlockedCells"
    || action === "clearBlockedCellsAndRestoreRobot"
    || action === "clearBlockedCellsOrRestoreRobot";
}

function allowsRobotRecovery(action: RecoveryAction): boolean {
  return action === "restoreRobot"
    || action === "clearBlockedCellsAndRestoreRobot"
    || action === "clearBlockedCellsOrRestoreRobot";
}

function robotRuntimeStatusLabel(status: SessionResult["robotStates"][number]["status"]): string {
  if (status === "failed") return "故障";
  if (status === "toCharge") return "前往充电";
  if (status === "charging") return "充电中";
  if (status === "waiting") return "等待释放";
  if (status === "toPickup") return "前往取货";
  if (status === "delivering") return "配送中";
  if (status === "inspecting") return "巡检中";
  return "空闲";
}

export function robotMoveDurationLabel(moveTicks: number): string {
  return `每格耗时 ${moveTicks} tick`;
}

function formatCells(cells: Cell[]): string {
  return cells.map(formatCell).join(", ");
}

function formatCell(cell: Cell): string {
  return `(${cell[0]}, ${cell[1]})`;
}

export function isSelectedCell(cell: Cell, selectedCell: Cell | null): boolean {
  return selectedCell !== null && cell[0] === selectedCell[0] && cell[1] === selectedCell[1];
}

export function isSelectedRobotCell(robotId: string | null, selectedRobotId: string): boolean {
  return robotId !== null && selectedRobotId !== "" && robotId === selectedRobotId;
}

export function routeHintsAfterSessionUpdate(routeHintsEnabled: boolean): boolean {
  return routeHintsEnabled;
}

export function robotColorForIndex(index: number): string {
  return ROBOT_COLORS[index % ROBOT_COLORS.length];
}

export function filterActiveRouteArrows(
  arrows: ActiveRouteArrow[],
  taskCells: CellKeyLookup
): ActiveRouteArrow[] {
  return arrows.filter((arrow) => !taskCells.has(cellKey(arrow.cell)));
}

export function mapContextAction(
  cell: Cell,
  blocked: ReadonlySet<string>,
  obstacles: ReadonlySet<string>,
  taskCells: CellKeyLookup,
  occupied: ReadonlySet<string>,
  charging: ReadonlySet<string>
): MapContextAction | null {
  const key = cellKey(cell);
  if (obstacles.has(key) || taskCells.has(key) || occupied.has(key) || charging.has(key)) return null;
  return blocked.has(key) ? "unblock" : "block";
}

export function robotContextAction(
  robotId: string | null,
  unavailableRobotIds: ReadonlySet<string>
): MapContextAction | null {
  if (!robotId) return null;
  return unavailableRobotIds.has(robotId) ? "restoreRobot" : "failRobot";
}

function mapContextActionLabel(context: MapContextMenuState): string {
  if (context.action === "block") return "封锁此单元";
  if (context.action === "unblock") return "解除封锁";
  if (context.action === "failRobot") return `标记 ${context.robotId} 故障`;
  return `恢复 ${context.robotId}`;
}

export function canJumpToEvent(eventTime: number, sessionCurrentTime: number): boolean {
  return eventTime <= sessionCurrentTime;
}

export function visibleRuntimeEvents<T extends { time: number }>(events: T[], started: boolean, currentTime: number): T[] {
  if (!started) return [];
  return events.filter((event) => event.time <= currentTime);
}

export function shouldRequestSessionTick(targetTime: number, sessionCurrentTime: number): boolean {
  return targetTime > sessionCurrentTime;
}

export function shouldContinueOnlinePlayback(
  playing: boolean,
  tickInFlight: boolean,
  dispatchStatus: "loading" | "ready" | "error",
  _time: number,
  sessionCurrentTime: number | null
): boolean {
  return playing && !tickInFlight && dispatchStatus === "ready" && sessionCurrentTime !== null;
}

export function shouldAdvancePlaybackLocally(targetTime: number, sessionCurrentTime: number | null): boolean {
  return sessionCurrentTime !== null && targetTime <= sessionCurrentTime;
}

export function shouldGenerateRandomTaskAtTime(
  displayTime: number,
  sessionCurrentTime: number | null,
  lastGeneratedTime: number | null,
  interval: number
): boolean {
  return sessionCurrentTime !== null
    && displayTime === sessionCurrentTime
    && displayTime > 0
    && Number.isInteger(interval)
    && interval > 0
    && displayTime % interval === 0
    && lastGeneratedTime !== displayTime;
}

export function getRuntimeActionTime(displayTime: number, sessionCurrentTime: number | null): number {
  return sessionCurrentTime === null ? displayTime : Math.max(displayTime, sessionCurrentTime);
}

export function buildDispatchOptions(
  avoidConflicts: boolean,
  includeDynamic: boolean,
  assignmentReplanWindow: number,
  adaptiveReplanWindow = false
): DispatchOptions {
  return {
    avoidConflicts,
    includeDynamic,
    assignmentReplanWindow: normalizeAssignmentReplanWindow(assignmentReplanWindow),
    adaptiveReplanWindow
  };
}

export function normalizeTaskPriority(value: number): number {
  return clamp(Number.isFinite(value) ? Math.floor(value) : 0, 0, 5);
}

export function assignmentReplanWindowLabel(value: number): string {
  return `窗口 ${normalizeAssignmentReplanWindow(value)}T`;
}

export function assignmentReplanWindowStatusLabel(
  configuredValue: number,
  adaptive: boolean,
  effectiveValue?: number
): string {
  if (!adaptive) return assignmentReplanWindowLabel(configuredValue);
  const effectiveWindow = Number.isFinite(effectiveValue)
    ? normalizeAssignmentReplanWindow(effectiveValue as number)
    : normalizeAssignmentReplanWindow(configuredValue);
  return `自适应 ${effectiveWindow}T`;
}

export function simulationTimeLabel(time: number): string {
  return `T=${time}`;
}

export function parsePlaybackSpeed(value: string): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < MIN_PLAYBACK_RATE || parsed > MAX_PLAYBACK_RATE) return null;
  return parsed;
}

export function parsePositiveIntegerInput(value: string, min: number, max: number): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) return null;
  return clamp(parsed, min, max);
}

function normalizeAssignmentReplanWindow(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_ASSIGNMENT_REPLAN_WINDOW;
  }
  return clamp(Math.trunc(value), 0, MAX_ASSIGNMENT_REPLAN_WINDOW);
}

export function shouldUseRuntimeRobotSnapshot(
  displayTime: number,
  sessionCurrentTime: number | null,
  robotStates: SessionResult["robotStates"]
): boolean {
  return sessionCurrentTime !== null && displayTime >= sessionCurrentTime && robotStates.length > 0;
}

export function getVisibleMetricsHistory(
  history: SessionResult["metricsHistory"] | undefined,
  currentTime: number
): SessionResult["metricsHistory"] {
  return (history ?? []).filter((point) => point.time <= currentTime);
}

function assignedRobot(assignments: { robotId: string; tasks: Task[] }[], taskId: string): string {
  return assignments.find((assignment) => assignment.tasks.some((task) => task.id === taskId))?.robotId ?? "未分配";
}

function apiStatusLabel(status: string): string {
  if (status === "online") return "后端在线";
  if (status === "offline") return "后端离线";
  if (status === "error") return "后端异常";
  return "检测中";
}

export function createManualTaskForm(scenario: Scenario): ManualTaskForm {
  const target = scenario.zones.inspection[0] ?? scenario.robots[0]?.start ?? [0, 0];
  const pickup = scenario.zones.warehouse[0] ?? target;
  const dropoff = scenario.shelves.find((shelf) => !shelf.initialOccupied)?.serviceCell
    ?? scenario.zones.delivery[0]
    ?? target;
  return {
    type: "inspection",
    title: "人工追加任务",
    priority: 3,
    deadline: 24,
    serviceTime: 2,
    target: formatCoordinateInput(target),
    pickup: formatCoordinateInput(pickup),
    dropoff: formatCoordinateInput(dropoff)
  };
}

export function parseCoordinateInput(value: string, scenario: Pick<Scenario, "width" | "height">): Cell | null {
  const match = /^\s*(\d+)\s*,\s*(\d+)\s*$/.exec(value);
  if (!match) return null;

  const cell: Cell = [Number(match[1]), Number(match[2])];
  if (cell[0] >= scenario.width || cell[1] >= scenario.height) return null;
  return cell;
}

function formatCoordinateInput(cell: Cell): string {
  return `${cell[0]}, ${cell[1]}`;
}


export function applyMapPickToManualTask(
  form: ManualTaskForm,
  target: MapPickTarget,
  cell: Cell
): ManualTaskForm {
  return { ...form, [target]: formatCoordinateInput(cell) };
}


export function nextMapPickTarget(target: MapPickTarget): MapPickTarget | null {
  return target === "pickup" ? "dropoff" : null;
}


export function selectLatestConflictAlert(conflicts: Conflict[], currentTime: number): ConflictAlert | null {
  const reachedConflicts = conflicts.filter((conflict) => conflict.time <= currentTime);
  return reachedConflicts.reduce<ConflictAlert | null>(
    (latest, conflict) => !latest || conflict.time >= latest.time ? conflict : latest,
    null
  );
}

export function resetSessionConflictState(
  setLatestConflictAlert: (value: ConflictAlert | null) => void,
  setLatestConflictResolved: (value: boolean) => void
): void {
  setLatestConflictAlert(null);
  setLatestConflictResolved(false);
}


export function selectMapConflictMarkers(
  conflicts: Conflict[],
  currentTime: number,
  unresolvedAlert: ConflictAlert | null = null,
  paths: Record<string, Cell[]> = {},
  conflictStates?: ConflictState[]
): Conflict[] {
  if (conflictStates !== undefined) {
    return conflictStates
      .filter((conflict) => isConflictStateActiveAtTime(conflict, currentTime))
      .map((conflict) => ({
        time: conflict.time,
        type: conflict.type,
        robots: conflict.robots,
        cell: conflict.cell
      }));
  }
  const currentConflicts = conflicts.filter((conflict) => conflict.time === currentTime);
  if (!unresolvedAlert || currentConflicts.some((conflict) => sameConflictAlert(conflict, unresolvedAlert))) {
    return currentConflicts;
  }
  if (!isConflictActiveOnPaths(unresolvedAlert, paths, currentTime)) {
    return currentConflicts;
  }
  return [...currentConflicts, unresolvedAlert];
}


export function shouldDisplayConflictMarker(alert: ConflictAlert | null, resolved: boolean): boolean {
  return alert !== null && !resolved;
}


export function filterInitialScenarioTaskLabels(
  tasks: Task[],
  scenario: Pick<Scenario, "tasks"> & { dynamic: Pick<Scenario["dynamic"], "tasks"> }
): Task[] {
  const initialTaskIds = new Set([...scenario.tasks, ...scenario.dynamic.tasks].map((task) => task.id));
  return tasks.filter((task) => initialTaskIds.has(task.id));
}


function sameConflictAlert(first: ConflictAlert | null, second: ConflictAlert): boolean {
  return first?.time === second.time
    && first.type === second.type
    && first.robots.join(",") === second.robots.join(",")
    && cellKey(first.cell) === cellKey(second.cell);
}


function findConflictState(conflictStates: ConflictState[] | undefined, alert: ConflictAlert): ConflictState | null {
  return conflictStates?.find((conflict) => sameConflictAlert(conflict, alert)) ?? null;
}


function isConflictStateActiveAtTime(conflict: ConflictState, currentTime: number): boolean {
  return currentTime >= conflict.startedAt
    && (conflict.resolvedAt === null || currentTime < conflict.resolvedAt);
}


function isConflictActiveOnPaths(
  conflict: ConflictAlert,
  paths: Record<string, Cell[]>,
  currentTime: number
): boolean {
  const [firstRobotId, secondRobotId] = conflict.robots;
  if (!firstRobotId || !secondRobotId) return false;

  const firstPath = paths[firstRobotId] ?? [];
  const secondPath = paths[secondRobotId] ?? [];
  const firstNow = pathCellAt(firstPath, currentTime);
  const secondNow = pathCellAt(secondPath, currentTime);
  if (!firstNow || !secondNow) return false;

  if (conflict.type === "vertex") {
    return cellKey(firstNow) === cellKey(conflict.cell) && cellKey(secondNow) === cellKey(conflict.cell);
  }

  if (currentTime <= 0) return false;
  const firstPrevious = pathCellAt(firstPath, currentTime - 1);
  const secondPrevious = pathCellAt(secondPath, currentTime - 1);
  if (!firstPrevious || !secondPrevious) return false;
  return cellKey(firstPrevious) === cellKey(secondNow) && cellKey(secondPrevious) === cellKey(firstNow);
}


function pathCellAt(path: Cell[], time: number): Cell | null {
  if (path.length === 0) return null;
  return path[Math.min(Math.max(0, time), path.length - 1)];
}


function conflictTypeLabel(type: Conflict["type"]): string {
  return type === "vertex" ? "顶点冲突" : "边冲突";
}


function mapPickLabel(target: MapPickTarget): string {
  if (target === "pickup") return "取货";
  if (target === "dropoff") return "送达";
  return "目标";
}

function buildManualTask(form: ManualTaskForm, tasks: Task[], currentTime: number, scenario: Scenario): Task | null {
  const target = parseCoordinateInput(form.target, scenario);
  const pickup = parseCoordinateInput(form.pickup, scenario);
  const dropoff = parseCoordinateInput(form.dropoff, scenario);

  const base = {
    id: nextManualTaskId(tasks),
    title: form.title.trim() || "人工追加任务",
    priority: normalizeTaskPriority(form.priority),
    releaseTime: currentTime,
    deadline: normalizeManualTaskDeadline(form.deadline, currentTime),
    serviceTime: Math.max(0, Math.floor(Number.isFinite(form.serviceTime) ? form.serviceTime : 0))
  };

  if (form.type === "delivery") {
    if (!pickup || !dropoff) return null;
    return {
      ...base,
      type: "delivery",
      pickup,
      dropoff,
      demand: 1
    };
  }

  if (form.type === "emergency") {
    if (!target) return null;
    return {
      ...base,
      type: "emergency",
      priority: Math.max(base.priority, 4),
      target
    };
  }

  if (!target) return null;
  return {
    ...base,
    type: "inspection",
    targets: [target]
  };
}

export function buildRandomGeneratedTask(
  tasks: Task[],
  currentTime: number,
  scenario: Scenario,
  sequence: number,
  shelfStates: ShelfRuntimeState[]
): Task | null {
  const id = nextGeneratedTaskId(tasks);
  const seed = Math.abs(currentTime * 31 + sequence * 17);
  const candidates = scenario.shelves.length > 0
    ? buildWarehouseGeneratedTaskCandidates(scenario, shelfStates, seed)
    : buildGeneratedTaskCandidates(scenario);
  if (candidates.length === 0) return null;
  const existingSignatures = new Set(tasks.map(generatedTaskSignature));
  const availableCandidates = candidates.filter((candidate) => !existingSignatures.has(candidate.signature));
  const pool = availableCandidates.length > 0 ? availableCandidates : candidates;
  const candidate = pool[seed % pool.length];
  const releaseTime = currentTime;
  const deadline = currentTime + 18 + (seed % 18);
  const serviceTime = 1 + (seed % 3);

  if (candidate.type === "delivery") {
    return {
      id,
      type: "delivery",
      title: `随机配送 ${id}`,
      priority: seed % 4,
      releaseTime,
      deadline,
      serviceTime,
      pickup: candidate.pickup,
      dropoff: candidate.dropoff,
      demand: 1
    };
  }

  if (candidate.type === "emergency") {
    return {
      id,
      type: "emergency",
      title: `随机突发 ${id}`,
      priority: 4 + (seed % 2),
      releaseTime,
      deadline,
      serviceTime,
      target: candidate.target
    };
  }

  return {
    id,
    type: "inspection",
    title: `随机巡检 ${id}`,
    priority: seed % 4,
    releaseTime,
    deadline,
    serviceTime,
    targets: [candidate.target]
  };
}

function buildWarehouseGeneratedTaskCandidates(
  scenario: Scenario,
  shelfStates: ShelfRuntimeState[],
  seed: number
): GeneratedTaskCandidate[] {
  const warehouseCandidates = buildWarehouseDeliveryCandidates(scenario, shelfStates);
  const inboundCandidates = warehouseCandidates
    .filter((candidate) => candidate.kind === "inbound")
    .map(({ pickup, dropoff, signature }) => ({ type: "delivery" as const, pickup, dropoff, signature }));
  const outboundCandidates = warehouseCandidates
    .filter((candidate) => candidate.kind === "outbound")
    .map(({ pickup, dropoff, signature }) => ({ type: "delivery" as const, pickup, dropoff, signature }));
  if (inboundCandidates.length > 0 && outboundCandidates.length > 0) {
    return seed % 2 === 0 ? inboundCandidates : outboundCandidates;
  }
  if (inboundCandidates.length > 0) return inboundCandidates;
  if (outboundCandidates.length > 0) return outboundCandidates;
  return buildGeneratedTaskCandidates(scenario).filter((candidate) => candidate.type !== "delivery");
}

type GeneratedTaskCandidate =
  | { type: "inspection"; target: Cell; signature: string }
  | { type: "emergency"; target: Cell; signature: string }
  | { type: "delivery"; pickup: Cell; dropoff: Cell; signature: string };

function buildGeneratedTaskCandidates(scenario: Scenario): GeneratedTaskCandidate[] {
  const inspectionCells = scenario.zones.inspection.length > 0 ? scenario.zones.inspection : scenario.robots.map((robot) => robot.start);
  const warehouseCells = scenario.zones.warehouse.length > 0 ? scenario.zones.warehouse : scenario.robots.map((robot) => robot.start);
  const deliveryCells = scenario.zones.delivery.length > 0 ? scenario.zones.delivery : inspectionCells;
  const inspectionCandidates = inspectionCells.map((target) => ({
    type: "inspection" as const,
    target,
    signature: `inspection:${cellKey(target)}`
  }));
  const emergencyCandidates = inspectionCells.map((target) => ({
    type: "emergency" as const,
    target,
    signature: `emergency:${cellKey(target)}`
  }));
  const deliveryCandidates = warehouseCells.flatMap((pickup) =>
    deliveryCells.map((dropoff) => ({
      type: "delivery" as const,
      pickup,
      dropoff,
      signature: `delivery:${cellKey(pickup)}>${cellKey(dropoff)}`
    }))
  );
  return [...inspectionCandidates, ...emergencyCandidates, ...deliveryCandidates];
}

function generatedTaskSignature(task: Task): string {
  if (task.type === "delivery") return `delivery:${cellKey(task.pickup)}>${cellKey(task.dropoff)}`;
  if (task.type === "emergency") return `emergency:${cellKey(task.target)}`;
  return `inspection:${task.targets.map(cellKey).join("|")}`;
}

function nextManualTaskId(tasks: Task[]): string {
  const used = new Set(tasks.map((task) => task.id));
  let index = 1;
  while (used.has(`M${index}`)) index += 1;
  return `M${index}`;
}

function nextGeneratedTaskId(tasks: Task[]): string {
  const used = new Set(tasks.map((task) => task.id));
  let index = 1;
  while (used.has(`G${index}`)) index += 1;
  return `G${index}`;
}


export function normalizeManualTaskDeadline(deadline: number, currentTime: number): number {
  const minimumDeadline = currentTime + 1;
  return Number.isFinite(deadline) ? Math.max(deadline, minimumDeadline) : minimumDeadline;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function safeFileName(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]/g, "_") || "scenario";
}

function taskTimingLabel(task: Task): string {
  const release = task.releaseTime ?? "-";
  const deadline = task.deadline ?? "-";
  return `到达 T=${release} · 截止 T=${deadline}`;
}

export function parseScenario(value: unknown): Scenario {
  if (!isScenario(value)) {
    throw new Error("JSON 必须是 Scenario 对象，并包含 id、name、description、width、height、obstacles、zones、robots、tasks、dynamic");
  }
  const scenario = { ...value, shelves: value.shelves ?? [] };
  assertScenarioCellsInside(scenario);
  const diagnostics = diagnoseScenario(scenario);
  if (diagnostics.length > 0) {
    throw new Error(diagnostics.join("；"));
  }
  return scenario;
}

function isScenario(value: unknown): value is Scenario {
  return isRecord(value)
    && isString(value.id)
    && isString(value.name)
    && isString(value.description)
    && isNonNegativeInteger(value.width)
    && isNonNegativeInteger(value.height)
    && Array.isArray(value.obstacles)
    && value.obstacles.every(isCell)
    && (value.shelves === undefined || (
      Array.isArray(value.shelves)
      && value.shelves.every(isShelf)
    ))
    && isRecord(value.zones)
    && Array.isArray(value.zones.warehouse)
    && value.zones.warehouse.every(isCell)
    && Array.isArray(value.zones.inspection)
    && value.zones.inspection.every(isCell)
    && Array.isArray(value.zones.delivery)
    && value.zones.delivery.every(isCell)
    && (value.zones.charging === undefined || (Array.isArray(value.zones.charging) && value.zones.charging.every(isCell)))
    && Array.isArray(value.robots)
    && value.robots.every(isRobot)
    && Array.isArray(value.tasks)
    && value.tasks.every(isTask)
    && isDynamicEvent(value.dynamic)
    && (value.chargeTime === undefined || isPositiveInteger(value.chargeTime));
}

function isShelf(value: unknown): value is Scenario["shelves"][number] {
  return isRecord(value)
    && isString(value.id)
    && isCell(value.cell)
    && isCell(value.serviceCell)
    && typeof value.initialOccupied === "boolean";
}

function isRobot(value: unknown): value is Scenario["robots"][number] {
  return isRecord(value)
    && isString(value.id)
    && isString(value.name)
    && isCell(value.start)
    && isFiniteNumber(value.battery)
    && (value.batteryCapacity === undefined || (isPositiveInteger(value.batteryCapacity) && value.battery <= value.batteryCapacity))
    && isFiniteNumber(value.load)
    && (value.moveTicks === undefined || isMoveTicks(value.moveTicks));
}

function isMoveTicks(value: unknown): value is number {
  return isNonNegativeInteger(value) && value >= 1 && value <= 4;
}

function isPositiveInteger(value: unknown): value is number {
  return isNonNegativeInteger(value) && value >= 1;
}

function isDynamicEvent(value: unknown): value is Scenario["dynamic"] {
  return isRecord(value)
    && isNonNegativeInteger(value.triggerTime)
    && Array.isArray(value.blockedCells)
    && value.blockedCells.every(isCell)
    && Array.isArray(value.failedRobots)
    && value.failedRobots.every(isString)
    && Array.isArray(value.tasks)
    && value.tasks.every(isTask);
}

function isTask(value: unknown): value is Task {
  if (!isRecord(value)
    || !isString(value.id)
    || !isString(value.title)
    || !isFiniteNumber(value.priority)
    || !isOptionalFiniteNumber(value.releaseTime)
    || !isOptionalFiniteNumber(value.deadline)
  ) {
    return false;
  }

  if (value.type === "inspection") {
    return Array.isArray(value.targets) && value.targets.every(isCell);
  }
  if (value.type === "delivery") {
    return isCell(value.pickup) && isCell(value.dropoff) && isFiniteNumber(value.demand);
  }
  if (value.type === "emergency") {
    return isCell(value.target);
  }
  return false;
}

function assertScenarioCellsInside(scenario: Scenario): void {
  const cells = [
    ...scenario.obstacles,
    ...(scenario.shelves ?? []).flatMap((shelf) => [shelf.cell, shelf.serviceCell]),
    ...scenario.zones.warehouse,
    ...scenario.zones.inspection,
    ...scenario.zones.delivery,
    ...(scenario.zones.charging ?? []),
    ...scenario.robots.map((robot) => robot.start),
    ...scenario.dynamic.blockedCells,
    ...scenario.tasks.flatMap(taskWaypoints),
    ...scenario.dynamic.tasks.flatMap(taskWaypoints)
  ];
  const outOfBounds = cells.find((cell) => cell[0] < 0 || cell[0] >= scenario.width || cell[1] < 0 || cell[1] >= scenario.height);
  if (outOfBounds) {
    throw new Error(`坐标超出地图范围：(${outOfBounds[0]}, ${outOfBounds[1]})`);
  }
}

function diagnoseScenario(scenario: Scenario): string[] {
  const blocked = new Set([...scenario.obstacles, ...scenario.dynamic.blockedCells].map(cellKey));
  return [
    ...duplicateDiagnostics("机器人 ID", scenario.robots.map((robot) => robot.id)),
    ...duplicateDiagnostics("任务 ID", [...scenario.tasks, ...scenario.dynamic.tasks].map((task) => task.id)),
    ...blockedPointDiagnostics(scenario, blocked)
  ];
}

function duplicateDiagnostics(label: string, values: string[]): string[] {
  const seen = new Set<string>();
  const duplicates: string[] = [];
  for (const value of values) {
    if (seen.has(value) && !duplicates.includes(value)) duplicates.push(value);
    seen.add(value);
  }
  return duplicates.map((value) => `${label} 重复：${value}`);
}

function blockedPointDiagnostics(scenario: Scenario, blocked: Set<string>): string[] {
  const points = [
    ...scenario.robots.map((robot) => ({ label: `机器人 ${robot.id} 起点`, cell: robot.start })),
    ...[...scenario.tasks, ...scenario.dynamic.tasks].flatMap((task) =>
      taskWaypoints(task).map((cell, index) => ({ label: `任务 ${task.id} 目标 ${index + 1}`, cell }))
    )
  ];
  return points
    .filter((point) => blocked.has(cellKey(point.cell)))
    .map((point) => `${point.label} 位于障碍或封锁单元：(${point.cell[0]}, ${point.cell[1]})`);
}

function isCell(value: unknown): value is Cell {
  return Array.isArray(value)
    && value.length === 2
    && isNonNegativeInteger(value[0])
    && isNonNegativeInteger(value[1]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isOptionalFiniteNumber(value: unknown): value is number | undefined {
  return value === undefined || isFiniteNumber(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && typeof value === "number" && value >= 0;
}

async function responseError(response: Response, prefix: string): Promise<never> {
  throw await apiErrorFromResponse(response, prefix);
}

if (typeof document !== "undefined") {
  const rootElement = document.getElementById("root");
  if (!rootElement) throw new Error("root element not found");

  const root = window.__warehousePatrolRoot ?? createRoot(rootElement);
  window.__warehousePatrolRoot = root;

  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
