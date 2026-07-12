import { describe, expect, it } from "vitest";
import {
  buildLiveMetrics,
  buildDispatchOptions,
  buildReplanStatus,
  assignmentReplanWindowLabel,
  dynamicModeLabel,
  buildRecoveryTargets,
  buildTaskSnapshots,
  buildTaskQueueMetricRows,
  buildActiveRouteArrows,
  filterActiveRouteArrows,
  canJumpToEvent,
  visibleRuntimeEvents,
  buildExperimentCsv,
  buildExperimentTableRows,
  buildExperimentChartSeries,
  buildExperimentDeltaHighlights,
  buildExperimentInsightCards,
  buildExperimentReportText,
  buildExperimentReportDisplayText,
  buildExperimentEmptyState,
  buildExperimentSectionLabels,
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
  parsePlaybackSpeed,
  parsePositiveIntegerInput,
  normalizeManualTaskDeadline,
  buildRandomGeneratedTask,
  nextMapPickTarget,
  applyMapPickToManualTask,
  selectLatestConflictAlert,
  selectMapConflictMarkers,
  filterInitialScenarioTaskLabels,
  shouldDisplayConflictMarker,
  robotColorForIndex,
  robotMoveDurationLabel,
  routeHintsAfterSessionUpdate,
  parseCoordinateInput,
  resetSessionConflictState,
  RIGHTBAR_EVENT_LOG_CLASS,
  RIGHTBAR_TASK_QUEUE_CLASS,
  sortTaskSnapshotsForDisplay,
  splitTaskSnapshotsForDisplay,
  taskTimingFields,
  buildExperimentSummaryRows,
  buildOnlinePressureInsightCards,
  buildOnlinePressureSummaryRows,
  buildOnlinePressureSummaryText,
  buildSeededPressureInsightCards,
  buildSeededPressureSummaryText,
  buildSeededPressureSummaryRows
} from "./main";
import type { DispatchResult, RobotRuntimeStatus, Scenario, SessionResult, Task } from "./domain/types";

describe("robot charging runtime status contract", () => {
  it("declares toCharge and charging runtime statuses", () => {
    const statuses: RobotRuntimeStatus[] = ["toCharge", "charging"];

    expect(statuses).toEqual(["toCharge", "charging"]);
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

    expect(buildLiveMetrics(result, 2, runtimeStates).liveDeadlineMissCount).toBe(0);
  });

  it("replays task status from the selected time instead of the latest backend status", () => {
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

    const metrics = buildLiveMetrics(result, 2, runtimeStates);

    expect(metrics.completedTaskCount).toBe(0);
    expect(metrics.activeTaskCount).toBe(1);
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
    expect(mapContextAction([2, 2], new Set(), new Set(), new Set(), new Set())).toBe("block");
    expect(mapContextAction([2, 2], new Set(["2,2"]), new Set(), new Set(), new Set())).toBe("unblock");
    expect(mapContextAction([2, 2], new Set(), new Set(["2,2"]), new Set(), new Set())).toBeNull();
    expect(mapContextAction([2, 2], new Set(), new Set(), new Set(["2,2"]), new Set())).toBeNull();
    expect(mapContextAction([2, 2], new Set(), new Set(), new Set(), new Set(["2,2"]))).toBeNull();
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

  it("hides future events until playback reaches their tick", () => {
    const events = [
      { time: 0, text: "启动" },
      { time: 2, text: "实际锁定" }
    ];

    expect(visibleRuntimeEvents(events, false, 0)).toEqual([]);
    expect(visibleRuntimeEvents(events, true, 1)).toEqual([{ time: 0, text: "启动" }]);
    expect(visibleRuntimeEvents(events, true, 2)).toEqual(events);
  });
});

describe("manual task coordinates", () => {
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

    const task = buildRandomGeneratedTask(scenario.tasks, 8, scenario, 1);

    expect(task).not.toBeNull();
    expect(task?.id).toBe("G2");
    expect(task?.releaseTime).toBe(8);
    expect(task?.deadline).toBeGreaterThan(8);
    expect((task as (Task & { serviceTime?: number }) | null)?.serviceTime).toBeGreaterThanOrEqual(1);
    expect(["inspection", "delivery", "emergency"]).toContain(task?.type);
  });

  it("avoids repeating generated task signatures while unused candidates remain", () => {
    const scenario: Scenario = {
      id: "generated-task-variety",
      name: "generated-task-variety",
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
      const task = buildRandomGeneratedTask(tasks, sequence * 8, scenario, sequence);
      expect(task).not.toBeNull();
      if (!task) continue;
      const signature = generatedTaskSignature(task);
      expect(signatures.has(signature)).toBe(false);
      signatures.add(signature);
      tasks.push(task);
    }
  });
});

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
      manualTaskCount: 0,
      streamTaskCount: 0,
      runtimeEventCount: 0,
      robotStates: [],
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

  it("uses stable robot colors and skips route arrows on task label cells", () => {
    expect(robotColorForIndex(0)).not.toBe(robotColorForIndex(1));
    expect(robotColorForIndex(4)).toBe(robotColorForIndex(0));
    expect(filterActiveRouteArrows([
      { robotId: "R1", cell: [1, 0], angle: 90, lane: 0 },
      { robotId: "R2", cell: [2, 0], angle: 90, lane: 1 }
    ], new Set(["1,0"]))).toEqual([
      { robotId: "R2", cell: [2, 0], angle: 90, lane: 1 }
    ]);
  });
});

describe("dispatch options", () => {
  it("normalizes assignment replan window for session creation", () => {
    expect(buildDispatchOptions(true, true, 18)).toEqual({
      avoidConflicts: true,
      includeDynamic: true,
      assignmentReplanWindow: 18
    });
    expect(buildDispatchOptions(true, true, 3.8).assignmentReplanWindow).toBe(3);
    expect(buildDispatchOptions(false, false, -5).assignmentReplanWindow).toBe(0);
    expect(buildDispatchOptions(true, false, 999).assignmentReplanWindow).toBe(120);
    expect(buildDispatchOptions(true, true, Number.NaN).assignmentReplanWindow).toBe(24);
  });

  it("formats the assignment replan window for status display", () => {
    expect(assignmentReplanWindowLabel(24)).toBe("窗口 24T");
    expect(assignmentReplanWindowLabel(3.8)).toBe("窗口 3T");
    expect(assignmentReplanWindowLabel(Number.NaN)).toBe("窗口 24T");
  });

  it("formats the dynamic mode for status display", () => {
    expect(dynamicModeLabel(true)).toBe("动态事件");
    expect(dynamicModeLabel(false)).toBe("静态流程");
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

describe("experiment summaries", () => {
  it("labels experiment evidence sections in display order", () => {
    expect(buildExperimentSectionLabels()).toEqual({
      report: "实验结论",
      evidence: "最佳方案依据",
      delta: "相对基线变化",
      table: "对比明细"
    });
  });

  it("describes available experiment buttons before a run", () => {
    expect(buildExperimentEmptyState()).toEqual({
      title: "选择实验类型后显示对比指标。",
      actions: [
        "避碰：对比开启/关闭冲突规避后的冲突数和路径代价。",
        "动态：对比是否处理突发任务、封锁和故障后的完成率与失败数。",
        "窗口：对比不同滚动重规划窗口对任务纳入和调度代价的影响。",
        "规模：对比固定场景集合中的机器人/任务规模表现。",
        "压力：运行固定种子压力场景，检查稳定率、完成率和规划耗时。",
        "在线：运行固定种子在线流程，检查任务插入、封锁、故障和恢复后的稳定性。"
      ]
    });
  });

  it("uses backend experiment notes when present and generated report text otherwise", () => {
    const rows = [
      {
        label: "withConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 0,
        totalDistance: 42,
        makespan: 21,
        deadlineMissCount: 0,
        failureCount: 0,
        replanTimeMs: 4
      }
    ];

    expect(buildExperimentReportDisplayText("避碰开启/关闭", rows, "后端汇总结论")).toBe("后端汇总结论");
    expect(buildExperimentReportDisplayText("避碰开启/关闭", rows, null)).toBe(
      "避碰开启/关闭：在场景 narrow-aisle 中，开启避碰 完成 3 个任务，冲突数 0，总路径长度 42，完成时间 21，截止超期 0，失败数 0，规划耗时 4ms。"
    );
  });

  it("maps experiment cases to compact metric rows", () => {
    const result = {
      scenarioId: "case-a",
      avoidConflicts: true,
      includeDynamic: false,
      dynamicTriggerTime: null,
      extraBlocked: [],
      unavailableRobotIds: [],
      assignments: [],
      paths: {},
      conflicts: [],
      failureReasons: {},
      failureDetails: {},
      eventLog: [],
      tasks: [],
      metrics: {
        makespan: 12,
        totalDistance: 30,
        conflictCount: 0,
        loadBalance: 1.2,
        assignedTaskCount: 4,
        deadlineMissCount: 1,
        averageLateness: 0.5,
        failureCount: 0,
        replanTimeMs: 2.4
      }
    } as DispatchResult;

    expect(buildExperimentSummaryRows([{ label: "withConflictAvoidance", result }])).toEqual([
      {
        label: "withConflictAvoidance",
        scenarioId: "case-a",
        assignedTaskCount: 4,
        conflictCount: 0,
        totalDistance: 30,
        makespan: 12,
        deadlineMissCount: 1,
        failureCount: 0,
        replanTimeMs: 2.4
      }
    ]);
  });

    it("exports experiment rows as CSV with stable report columns", () => {
      const rows = [
      {
        label: "withoutConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 2,
        totalDistance: 38,
        makespan: 19,
        deadlineMissCount: 1,
        failureCount: 0,
        replanTimeMs: 3.125
      },
      {
        label: "withConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 0,
        totalDistance: 42,
        makespan: 21,
        deadlineMissCount: 0,
        failureCount: 0,
        replanTimeMs: 4
      }
    ];

    expect(buildExperimentCsv(rows)).toBe([
      "组别,场景,任务数,冲突数,总路径长度,完成时间,截止超期,失败数,规划耗时(ms)",
      "关闭避碰,narrow-aisle,3,2,38,19,1,0,3.125",
      "开启避碰,narrow-aisle,3,0,42,21,0,0,4"
      ].join("\n"));
    });

    it("builds visible experiment table rows with all key metrics", () => {
      const rows = [
        {
          label: "withConflictAvoidance",
          scenarioId: "narrow-aisle",
          assignedTaskCount: 3,
          conflictCount: 0,
          totalDistance: 42,
          makespan: 21,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 4
        }
      ];

      expect(buildExperimentTableRows(rows)).toEqual([
        {
          key: "narrow-aisle-withConflictAvoidance",
          cells: ["开启避碰", "3", "0", "42", "21", "0", "0", "4ms"]
        }
      ]);
    });

  it("maps seeded pressure performance cases to experiment summary rows", () => {
    const rows = buildSeededPressureSummaryRows([
      {
        label: "seed-17",
        seed: 17,
        scenarioId: "seeded-pressure-seed-17",
        options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 120 },
        robotCount: 4,
        taskCount: 15,
        dynamicTaskCount: 3,
        obstacleCount: 8,
        assignedTaskCount: 15,
        stable: false,
        completionRatePercent: 100,
        conflictCount: 0,
        deadlineMissCount: 2,
        failureCount: 0,
        totalDistance: 120,
        averageDistancePerTask: 8,
        makespan: 42,
        replanTimeMs: 18.5,
        withinPlanningTimeBudget: true
      }
    ]);

    expect(rows).toEqual([
      {
        label: "seed-17 · 4R/15T",
        scenarioId: "seeded-pressure-seed-17",
        assignedTaskCount: 15,
        conflictCount: 0,
        totalDistance: 120,
        makespan: 42,
        deadlineMissCount: 2,
        failureCount: 0,
        replanTimeMs: 18.5
      }
    ]);
  });

  it("maps online pressure cases to experiment summary rows", () => {
    const rows = buildOnlinePressureSummaryRows([
      {
        label: "seed-17-online-flow",
        seed: 17,
        scenarioId: "seeded-pressure-seed-17",
        options: { avoidConflicts: true, includeDynamic: true, assignmentReplanWindow: 120 },
        robotCount: 4,
        baseTaskCount: 12,
        scenarioDynamicTaskCount: 3,
        manualTaskCount: 2,
        streamTaskCount: 0,
        runtimeEventCount: 6,
        runtimeEventEvidence: [
          "manualTask",
          "blockedCell",
          "failedRobot",
          "restoredRobot",
          "clearedBlockedCell",
          "generatedTask"
        ],
        tickCount: 20,
        taskCount: 17,
        coveredTaskCount: 17,
        completedTaskCount: 5,
        assignedTaskCount: 13,
        stable: true,
        completionRatePercent: 100,
        conflictCount: 0,
        deadlineMissCount: 0,
        failureCount: 0,
        totalDistance: 156,
        averageDistancePerTask: 9.2,
        makespan: 54,
        replanTimeMs: 21.5,
        metricsHistoryCount: 15,
        eventLogCount: 22
      }
    ]);

    expect(rows).toEqual([
      {
        label: "seed-17-online-flow · 4R/17T · 6事件",
        scenarioId: "seeded-pressure-seed-17",
        assignedTaskCount: 17,
        taskMetricLabel: "任务覆盖",
        conflictCount: 0,
        totalDistance: 156,
        makespan: 54,
        deadlineMissCount: 0,
        failureCount: 0,
        replanTimeMs: 21.5
      }
    ]);
    expect(buildExperimentReportText("在线压力流程", rows)).toBe(
      "在线压力流程：在场景 seeded-pressure-seed-17 中，seed-17-online-flow · 4R/17T · 6事件 任务覆盖 17 个任务，冲突数 0，总路径长度 156，完成时间 54，截止超期 0，失败数 0，规划耗时 21.5ms。"
    );
  });

  it("builds online pressure summary text and insight cards", () => {
    const summary = {
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
      totalRuntimeEventCount: 6,
      totalManualTaskCount: 1,
      totalStreamTaskCount: 1,
      totalDistance: 156,
      averageDistancePerTask: 9.2,
      maxMakespan: 54,
      averageReplanTimeMs: 21.5,
      maxReplanTimeMs: 21.5,
      maxMetricsHistoryCount: 15,
      maxEventLogCount: 22
    };

    expect(buildOnlinePressureSummaryText(summary)).toBe(
      "在线压力汇总：1 组固定种子在线流程，稳定 1/1 组（100%），覆盖 17/17 个任务（100%），已完成 5 个任务，运行时事件 6 个（手动任务 1 个，自动任务 1 个），最大冲突 0，总超期 0，总失败 0，总路径 156，平均每任务路径 9.2，最大完成时间 54，平均规划耗时 21.5ms，最大规划耗时 21.5ms，最多指标快照 15 条，最多事件日志 22 条。"
    );
    expect(buildOnlinePressureInsightCards(summary)).toEqual([
      { label: "稳定流程", value: "1/1 (100%)" },
      { label: "任务覆盖", value: "17/17 (100%)" },
      { label: "已完成", value: "5" },
      { label: "运行时事件", value: "6" },
      { label: "手动/自动任务", value: "1 / 1" },
      { label: "冲突/失败", value: "0 / 0" },
      { label: "指标/日志", value: "15 / 22" },
      { label: "规划耗时", value: "21.5 / 21.5ms" }
    ]);
  });

  it("builds a seeded pressure aggregate summary text", () => {
    expect(buildSeededPressureSummaryText({
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
      maxMakespan: 61,
      averageDistancePerTask: 12.4,
      averageReplanTimeMs: 6.2,
      maxReplanTimeMs: 18.5
    })).toBe("压力汇总：3 组固定种子场景，稳定 3/3 组（100%），最大规模 8R/27T，完成 65/65 个任务（100%），最大冲突 0，总超期 0，总失败 0，规划预算通过 3/3 组（100%，预算 2000ms），总路径 804，平均每任务路径 12.4，最大完成时间 61，平均规划耗时 6.2ms，最大规划耗时 18.5ms。");
  });

  it("builds seeded pressure insight cards for demonstration", () => {
    expect(buildSeededPressureInsightCards({
      caseCount: 7,
      largestRobotCount: 8,
      largestTaskCount: 33,
      totalTaskCount: 189,
      totalAssignedTaskCount: 189,
      stableCaseCount: 7,
      stableRatePercent: 100,
      completionRatePercent: 100,
      planningTimeBudgetMs: 2000,
      withinPlanningTimeBudgetCount: 7,
      withinPlanningTimeBudgetRatePercent: 100,
      maxConflictCount: 0,
      totalDeadlineMissCount: 0,
      totalFailureCount: 0,
      totalDistance: 2345,
      maxMakespan: 88,
      averageDistancePerTask: 12.4,
      averageReplanTimeMs: 142.4,
      maxReplanTimeMs: 1510.63
    })).toEqual([
        { label: "稳定组数", value: "7/7 (100%)" },
        { label: "任务完成", value: "189/189 (100%)" },
        { label: "预算通过", value: "7/7 (100%)" },
        { label: "截止超期", value: "0" },
        { label: "最大规模", value: "8R/33T" },
        { label: "总路径", value: "2345" },
        { label: "平均路径/任务", value: "12.4" },
        { label: "规划耗时", value: "142.4 / 1510.63ms" }
      ]);
  });

  it("builds a concise report paragraph for the best conflict case", () => {
    const rows = [
      {
        label: "withoutConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 2,
        totalDistance: 38,
        makespan: 19,
        deadlineMissCount: 1,
        failureCount: 0,
        replanTimeMs: 3.125
      },
      {
        label: "withConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 0,
        totalDistance: 42,
        makespan: 21,
        deadlineMissCount: 0,
        failureCount: 0,
        replanTimeMs: 4
      }
    ];

      expect(buildExperimentReportText("避碰开启/关闭", rows)).toBe(
        "避碰开启/关闭：在场景 narrow-aisle 中，开启避碰 相比 关闭避碰 将冲突从 2 降到 0，任务完成数保持 3，总路径长度增加 4，完成时间增加 2，截止超期减少 1，失败数保持 0，规划耗时增加 0.875ms。该结果可用于说明优先级避碰能用有限路径和时间代价换取无冲突执行。"
      );
    });

    it("builds a defense-oriented report paragraph for dynamic replanning cases", () => {
      const rows = [
        {
          label: "withoutDynamicReplanning",
          scenarioId: "campus-warehouse",
          assignedTaskCount: 4,
          conflictCount: 0,
          totalDistance: 80,
          makespan: 30,
          deadlineMissCount: 0,
          failureCount: 1,
          replanTimeMs: 5
        },
        {
          label: "withDynamicReplanning",
          scenarioId: "campus-warehouse",
          assignedTaskCount: 5,
          conflictCount: 0,
          totalDistance: 96,
          makespan: 36,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 7
        }
      ];

      expect(buildExperimentReportText("动态事件开启/关闭", rows)).toBe(
        "动态事件开启/关闭：在场景 campus-warehouse 中，开启动态 相比 关闭动态 冲突数保持 0，任务完成数增加 1，总路径长度增加 16，完成时间增加 6，截止超期保持 0，失败数减少 1，规划耗时增加 2ms。该结果可用于说明动态重规划能在突发任务或故障出现后维持任务完成和低失败。"
      );
    });

    it("keeps conflict-avoidance conclusions cautious when conflicts remain", () => {
      const rows = [
        {
          label: "withoutConflictAvoidance",
          scenarioId: "narrow-aisle",
          assignedTaskCount: 3,
          conflictCount: 3,
          totalDistance: 38,
          makespan: 19,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 3
        },
        {
          label: "withConflictAvoidance",
          scenarioId: "narrow-aisle",
          assignedTaskCount: 3,
          conflictCount: 1,
          totalDistance: 44,
          makespan: 24,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 6
        }
      ];

      expect(buildExperimentReportText("避碰开启/关闭", rows)).toBe(
        "避碰开启/关闭：在场景 narrow-aisle 中，开启避碰 相比 关闭避碰 将冲突从 3 降到 1，任务完成数保持 3，总路径长度增加 6，完成时间增加 5，截止超期保持 0，失败数保持 0，规划耗时增加 3ms。该结果可用于说明优先级避碰降低了冲突，但仍有 1 个冲突，需要继续作为压力边界优化。"
      );
    });

    it("keeps dynamic replanning conclusions cautious when failures remain", () => {
      const rows = [
        {
          label: "withoutDynamicReplanning",
          scenarioId: "robot-failure",
          assignedTaskCount: 4,
          conflictCount: 0,
          totalDistance: 72,
          makespan: 30,
          deadlineMissCount: 0,
          failureCount: 3,
          replanTimeMs: 5
        },
        {
          label: "withDynamicReplanning",
          scenarioId: "robot-failure",
          assignedTaskCount: 5,
          conflictCount: 0,
          totalDistance: 88,
          makespan: 39,
          deadlineMissCount: 0,
          failureCount: 1,
          replanTimeMs: 9
        }
      ];

      expect(buildExperimentReportText("动态事件开启/关闭", rows)).toBe(
        "动态事件开启/关闭：在场景 robot-failure 中，开启动态 相比 关闭动态 冲突数保持 0，任务完成数增加 1，总路径长度增加 16，完成时间增加 9，截止超期保持 0，失败数减少 2，规划耗时增加 4ms。该结果可用于说明动态重规划降低了失败数，但仍有 1 个失败任务，需要继续分析恢复条件。"
      );
    });

    it("builds a defense-oriented report paragraph for rolling window cases", () => {
      const rows = [
        {
          label: "window-24",
          scenarioId: "campus-warehouse",
          assignedTaskCount: 4,
          conflictCount: 0,
          totalDistance: 68,
          makespan: 32,
          deadlineMissCount: 0,
          failureCount: 1,
          replanTimeMs: 6
        },
        {
          label: "window-120",
          scenarioId: "campus-warehouse",
          assignedTaskCount: 5,
          conflictCount: 0,
          totalDistance: 75,
          makespan: 35,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 8
        }
      ];

      expect(buildExperimentReportText("滚动窗口参数", rows)).toBe(
        "滚动窗口参数：在场景 campus-warehouse 中，window-120 相比 window-24 冲突数保持 0，任务完成数增加 1，总路径长度增加 7，完成时间增加 3，截止超期保持 0，失败数减少 1，规划耗时增加 2ms。该结果可用于说明滚动窗口扩大后能纳入更多近未来任务，同时保持冲突和失败受控。"
      );
    });

    it("keeps rolling-window conclusions cautious when larger windows still leave failures", () => {
      const rows = [
        {
          label: "window-24",
          scenarioId: "campus-warehouse",
          assignedTaskCount: 4,
          conflictCount: 0,
          totalDistance: 68,
          makespan: 32,
          deadlineMissCount: 0,
          failureCount: 2,
          replanTimeMs: 6
        },
        {
          label: "window-120",
          scenarioId: "campus-warehouse",
          assignedTaskCount: 5,
          conflictCount: 0,
          totalDistance: 79,
          makespan: 35,
          deadlineMissCount: 0,
          failureCount: 1,
          replanTimeMs: 8
        }
      ];

      expect(buildExperimentReportText("滚动窗口参数", rows)).toBe(
        "滚动窗口参数：在场景 campus-warehouse 中，window-120 相比 window-24 冲突数保持 0，任务完成数增加 1，总路径长度增加 11，完成时间增加 3，截止超期保持 0，失败数减少 1，规划耗时增加 2ms。该结果可用于说明滚动窗口扩大后纳入了更多任务，但仍有 0 个冲突和 1 个失败，需要继续权衡窗口长度。"
      );
    });

    it("builds a single-case report paragraph with all key metrics", () => {
      expect(buildExperimentReportText("固定种子压力", [
        {
          label: "seed-17 · 4R/15T",
          scenarioId: "seeded-pressure-seed-17",
          assignedTaskCount: 15,
          conflictCount: 0,
          totalDistance: 120,
          makespan: 42,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 18.5
        }
      ])).toBe(
        "固定种子压力：在场景 seeded-pressure-seed-17 中，seed-17 · 4R/15T 完成 15 个任务，冲突数 0，总路径长度 120，完成时间 42，截止超期 0，失败数 0，规划耗时 18.5ms。"
      );
    });

    it("builds an aggregate report paragraph for fixed-seed pressure cases", () => {
      const rows = [
        {
          label: "seed-17 · 4R/15T",
          scenarioId: "seeded-pressure-seed-17",
          assignedTaskCount: 15,
          conflictCount: 0,
          totalDistance: 120,
          makespan: 42,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 18.5
        },
        {
          label: "seed-29 · 6R/23T",
          scenarioId: "seeded-pressure-seed-29",
          assignedTaskCount: 23,
          conflictCount: 0,
          totalDistance: 240,
          makespan: 58,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 64.2
        }
      ];

      expect(buildExperimentReportText("固定种子压力", rows)).toBe(
        "固定种子压力：覆盖 2 组固定种子压力样本，稳定 2/2 组（无冲突、无超期、无失败），累计完成 38 个任务，总路径长度 360，平均每任务路径 9.5，最大完成时间 58，平均规划耗时 41.4ms，最大规划耗时 64.2ms。该结果可用于说明算法在随机障碍和多规模压力下具备可复现实验稳定性。"
      );
    });

    it("builds an aggregate report paragraph for robot and task scale cases", () => {
      const rows = [
        {
          label: "campus-warehouse",
          scenarioId: "campus-warehouse",
          assignedTaskCount: 5,
          conflictCount: 0,
          totalDistance: 60,
          makespan: 30,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 9
        },
        {
          label: "robot-failure",
          scenarioId: "robot-failure",
          assignedTaskCount: 7,
          conflictCount: 0,
          totalDistance: 88,
          makespan: 44,
          deadlineMissCount: 0,
          failureCount: 0,
          replanTimeMs: 15
        }
      ];

      expect(buildExperimentReportText("机器人/任务规模", rows)).toBe(
        "机器人/任务规模：覆盖 2 组规模样本，稳定 2/2 组（无冲突、无超期、无失败），累计完成 12 个任务，总路径长度 148，平均每任务路径 12.3，最大完成时间 44，平均规划耗时 12ms，最大规划耗时 15ms。该结果可用于说明算法在不同机器人与任务规模场景下保持调度稳定性与可控代价。"
      );
    });

    it("builds normalized chart series for visible experiment comparison", () => {
    const rows = [
      {
        label: "withoutConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 2,
        totalDistance: 38,
        makespan: 19,
        deadlineMissCount: 1,
        failureCount: 0,
        replanTimeMs: 3.125
      },
      {
        label: "withConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 0,
        totalDistance: 42,
        makespan: 21,
        deadlineMissCount: 0,
        failureCount: 0,
        replanTimeMs: 4
      }
    ];

    expect(buildExperimentChartSeries(rows)).toEqual([
      {
        key: "conflictCount",
        label: "冲突数",
        bars: [
          { label: "关闭避碰", value: 2, widthPercent: 100 },
          { label: "开启避碰", value: 0, widthPercent: 0 }
        ]
      },
      {
        key: "totalDistance",
        label: "总路径",
        bars: [
          { label: "关闭避碰", value: 38, widthPercent: 90.5 },
          { label: "开启避碰", value: 42, widthPercent: 100 }
        ]
      },
        {
          key: "makespan",
          label: "完成时间",
          bars: [
            { label: "关闭避碰", value: 19, widthPercent: 90.5 },
            { label: "开启避碰", value: 21, widthPercent: 100 }
          ]
        },
        {
          key: "deadlineMissCount",
          label: "截止超期",
          bars: [
            { label: "关闭避碰", value: 1, widthPercent: 100 },
            { label: "开启避碰", value: 0, widthPercent: 0 }
          ]
        },
        {
          key: "replanTimeMs",
          label: "规划耗时",
          bars: [
            { label: "关闭避碰", value: 3.125, widthPercent: 78.1 },
            { label: "开启避碰", value: 4, widthPercent: 100 }
          ]
        }
      ]);
    });

  it("highlights the main differences between the baseline and best experiment case", () => {
    const rows = [
      {
        label: "withoutConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 2,
        totalDistance: 38,
        makespan: 19,
        deadlineMissCount: 1,
        failureCount: 0,
        replanTimeMs: 3.125
      },
      {
        label: "withConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 0,
        totalDistance: 42,
        makespan: 21,
        deadlineMissCount: 0,
        failureCount: 0,
        replanTimeMs: 4
      }
    ];

      expect(buildExperimentDeltaHighlights(rows)).toEqual([
        { label: "冲突变化", value: "-2" },
        { label: "任务变化", value: "0" },
        { label: "路径变化", value: "+4" },
        { label: "时间变化", value: "+2" },
        { label: "超期变化", value: "-1" },
        { label: "失败变化", value: "0" },
        { label: "规划耗时变化", value: "+0.875ms" }
      ]);
    });

  it("builds general experiment insight cards for comparison results", () => {
    const rows = [
      {
        label: "withoutConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 2,
        totalDistance: 38,
        makespan: 19,
        deadlineMissCount: 1,
        failureCount: 0,
        replanTimeMs: 3.125
      },
      {
        label: "withConflictAvoidance",
        scenarioId: "narrow-aisle",
        assignedTaskCount: 3,
        conflictCount: 0,
        totalDistance: 42,
        makespan: 21,
        deadlineMissCount: 0,
        failureCount: 0,
        replanTimeMs: 4
      }
    ];

    expect(buildExperimentInsightCards(rows)).toEqual([
      { label: "最佳方案", value: "开启避碰" },
      { label: "任务完成", value: "3" },
      { label: "冲突数", value: "0" },
      { label: "总路径", value: "42" },
      { label: "完成时间", value: "21" },
      { label: "截止超期", value: "0" },
      { label: "失败数", value: "0" },
      { label: "规划耗时", value: "4ms" }
    ]);
  });
});
