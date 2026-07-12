export type Cell = [number, number];

export type TaskType = "inspection" | "delivery" | "emergency";
export type ConflictType = "vertex" | "edge";
export type ConflictStatus = "active" | "resolved";
export type RobotRuntimeStatus = "idle" | "waiting" | "toPickup" | "delivering" | "inspecting" | "failed";
export type TaskRuntimeStatus = "pending" | "running" | "completed" | "unassigned";
export type TaskFailureCategory = "temporary" | "permanent";

export type PatrolTask = {
  id: string;
  type: "inspection";
  title: string;
  priority: number;
  releaseTime?: number | null;
  deadline?: number | null;
  serviceTime?: number | null;
  targets: Cell[];
  pickup?: Cell | null;
  dropoff?: Cell | null;
  demand?: number | null;
  target?: Cell | null;
};

export type DeliveryTask = {
  id: string;
  type: "delivery";
  title: string;
  priority: number;
  releaseTime?: number | null;
  deadline?: number | null;
  serviceTime?: number | null;
  targets?: Cell[] | null;
  pickup: Cell;
  dropoff: Cell;
  demand: number;
  target?: Cell | null;
};

export type EmergencyTask = {
  id: string;
  type: "emergency";
  title: string;
  priority: number;
  releaseTime?: number | null;
  deadline?: number | null;
  serviceTime?: number | null;
  targets?: Cell[] | null;
  pickup?: Cell | null;
  dropoff?: Cell | null;
  demand?: number | null;
  target: Cell;
};

export type Task = PatrolTask | DeliveryTask | EmergencyTask;

export type Robot = {
  id: string;
  name: string;
  start: Cell;
  battery: number;
  load: number;
  moveTicks?: number;
};

export type DynamicEvent = {
  triggerTime: number;
  blockedCells: Cell[];
  failedRobots: string[];
  tasks: Task[];
};

export type Zones = {
  warehouse: Cell[];
  inspection: Cell[];
  delivery: Cell[];
};

export type Scenario = {
  id: string;
  name: string;
  description: string;
  width: number;
  height: number;
  obstacles: Cell[];
  zones: Zones;
  robots: Robot[];
  tasks: Task[];
  dynamic: DynamicEvent;
};

export type Assignment = {
  robotId: string;
  tasks: Task[];
};

export type Conflict = {
  time: number;
  type: ConflictType;
  robots: string[];
  cell: Cell;
};

export type ConflictState = {
  time: number;
  type: ConflictType;
  robots: string[];
  cell: Cell;
  status: ConflictStatus;
  startedAt: number;
  resolvedAt: number | null;
};

export type Metrics = {
  makespan: number;
  totalDistance: number;
  conflictCount: number;
  loadBalance: number;
  assignedTaskCount: number;
  deadlineMissCount: number;
  averageLateness: number;
  failureCount: number;
  replanTimeMs: number;
};

export type MetricSnapshot = {
  time: number;
  completedTaskCount: number;
  activeTaskCount: number;
  pendingTaskCount: number;
  travelledDistance: number;
  activeConflictCount: number;
  deadlineMissCount: number;
  replanTimeMs: number;
};

export type EventItem = {
  time: number;
  text: string;
};

export type RecoveryAction =
  | "addCapableRobotOrReduceDemand"
  | "clearBlockedCells"
  | "clearBlockedCellsAndRestoreRobot"
  | "clearBlockedCellsOrRestoreRobot"
  | "fixMapOrTaskTarget"
  | "fixTaskDefinition"
  | "relaxLocksOrReplan"
  | "restoreRobot";

export type RobotRuntimeState = {
  robotId: string;
  name: string;
  position: Cell;
  status: RobotRuntimeStatus;
  battery: number;
  load: number;
  moveTicks: number;
  currentTaskId: string | null;
};

export type TaskRuntimeState = {
  taskId: string;
  status: TaskRuntimeStatus;
  assignedRobotId: string | null;
  releaseTime: number;
  completionTime: number | null;
  locked: boolean;
  failureReason: string | null;
  failureCategory: TaskFailureCategory | null;
  recoveryAction: RecoveryAction | null;
};

export type TaskFailureDetail = {
  reason: string;
  category: TaskFailureCategory;
  recoveryAction: RecoveryAction;
  blockingCells: Cell[];
  blockingRobotIds: string[];
};

export type DispatchResult = {
  scenarioId: string;
  avoidConflicts: boolean;
  includeDynamic: boolean;
  dynamicTriggerTime: number | null;
  extraBlocked: Cell[];
  unavailableRobotIds: string[];
  assignments: Assignment[];
  paths: Record<string, Cell[]>;
  conflicts: Conflict[];
  conflictStates?: ConflictState[];
  metrics: Metrics;
  failureReasons: Record<string, string>;
  failureDetails: Record<string, TaskFailureDetail>;
  eventLog: EventItem[];
  tasks: Task[];
};

export type DispatchOptions = {
  avoidConflicts: boolean;
  includeDynamic: boolean;
  assignmentReplanWindow: number;
};

export type DispatchRequest = {
  scenario: Scenario;
  options?: DispatchOptions;
};

export type ConflictAvoidanceExperimentRequest = {
  scenario: Scenario;
  options?: DispatchOptions;
};

export type DynamicReplanningExperimentRequest = {
  scenario: Scenario;
  options?: DispatchOptions;
};

export type ReplanWindowExperimentRequest = {
  scenario: Scenario;
  options?: DispatchOptions;
  windows: number[];
};

export type ScaleExperimentScenario = {
  label: string;
  scenario: Scenario;
};

export type ScaleExperimentRequest = {
  cases: ScaleExperimentScenario[];
  options?: DispatchOptions;
};

export type SeededPressureExperimentRequest = {
  caseSet?: "standard" | "extended";
  options?: DispatchOptions;
};

export type OnlinePressureExperimentRequest = {
  options?: DispatchOptions;
};

export type CreateSessionRequest = {
  scenario: Scenario;
  options?: DispatchOptions;
};

export type AddTaskRequest = {
  task: Task;
};

export type SessionTickRequest = {
  currentTime: number;
};

export type AddBlockRequest = {
  cell: Cell;
  currentTime?: number;
};

export type RemoveBlockRequest = {
  cell: Cell;
  currentTime?: number;
};

export type FailRobotRequest = {
  robotId: string;
  currentTime?: number;
};

export type RestoreRobotRequest = {
  robotId: string;
  currentTime?: number;
};

export type SessionResult = {
  sessionId: string;
  scenarioId: string;
  options: DispatchOptions;
  createdAt: number;
  updatedAt: number;
  lastAccessedAt: number;
  currentTime: number;
  manualTaskCount: number;
  streamTaskCount: number;
  runtimeEventCount: number;
  robotStates: RobotRuntimeState[];
  taskStates: TaskRuntimeState[];
  metricsHistory: MetricSnapshot[];
  completedTaskCount: number;
  result: DispatchResult;
};

export type SessionSummary = {
  sessionId: string;
  scenarioId: string;
  createdAt: number;
  updatedAt: number;
  lastAccessedAt: number;
  currentTime: number;
  manualTaskCount: number;
  streamTaskCount: number;
  runtimeEventCount: number;
  completedTaskCount: number;
};

export type DeleteSessionResult = {
  sessionId: string;
  deleted: boolean;
};

export type ExperimentCaseResult = {
  label: string;
  options: DispatchOptions;
  result: DispatchResult;
};

export type ConflictAvoidanceExperimentResult = {
  scenarioId: string;
  cases: ExperimentCaseResult[];
};

export type DynamicReplanningExperimentResult = {
  scenarioId: string;
  cases: ExperimentCaseResult[];
};

export type ReplanWindowExperimentResult = {
  scenarioId: string;
  cases: ExperimentCaseResult[];
};

export type ScaleExperimentCaseResult = {
  label: string;
  scenarioId: string;
  options: DispatchOptions;
  result: DispatchResult;
};

export type ScaleExperimentResult = {
  cases: ScaleExperimentCaseResult[];
};

export type SeededPressureExperimentCaseResult = {
  label: string;
  seed: number;
  scenarioId: string;
  options: DispatchOptions;
  robotCount: number;
  taskCount: number;
  dynamicTaskCount: number;
  obstacleCount: number;
  assignedTaskCount: number;
  stable: boolean;
  completionRatePercent: number;
  conflictCount: number;
  deadlineMissCount: number;
  failureCount: number;
  totalDistance: number;
  averageDistancePerTask: number;
  makespan: number;
  replanTimeMs: number;
  withinPlanningTimeBudget: boolean;
};

export type SeededPressureExperimentSummary = {
  caseCount: number;
  largestRobotCount: number;
  largestTaskCount: number;
  totalTaskCount: number;
  totalAssignedTaskCount: number;
  stableCaseCount: number;
  stableRatePercent: number;
  completionRatePercent: number;
  planningTimeBudgetMs: number;
  withinPlanningTimeBudgetCount: number;
  withinPlanningTimeBudgetRatePercent: number;
  maxConflictCount: number;
  totalDeadlineMissCount: number;
  totalFailureCount: number;
  totalDistance: number;
  averageDistancePerTask: number;
  maxMakespan: number;
  averageReplanTimeMs: number;
  maxReplanTimeMs: number;
};

export type SeededPressureExperimentResult = {
  cases: SeededPressureExperimentCaseResult[];
  summary: SeededPressureExperimentSummary;
};

export type OnlinePressureExperimentCaseResult = {
  label: string;
  seed: number;
  scenarioId: string;
  options: DispatchOptions;
  robotCount: number;
  baseTaskCount: number;
  scenarioDynamicTaskCount: number;
  manualTaskCount: number;
  streamTaskCount: number;
  runtimeEventCount: number;
  runtimeEventEvidence: string[];
  tickCount: number;
  taskCount: number;
  coveredTaskCount: number;
  completedTaskCount: number;
  assignedTaskCount: number;
  stable: boolean;
  completionRatePercent: number;
  conflictCount: number;
  deadlineMissCount: number;
  failureCount: number;
  totalDistance: number;
  averageDistancePerTask: number;
  makespan: number;
  replanTimeMs: number;
  metricsHistoryCount: number;
  eventLogCount: number;
};

export type OnlinePressureExperimentSummary = {
  caseCount: number;
  totalTaskCount: number;
  totalCoveredTaskCount: number;
  totalCompletedTaskCount: number;
  totalAssignedTaskCount: number;
  stableCaseCount: number;
  stableRatePercent: number;
  completionRatePercent: number;
  maxConflictCount: number;
  totalDeadlineMissCount: number;
  totalFailureCount: number;
  totalRuntimeEventCount: number;
  totalManualTaskCount: number;
  totalStreamTaskCount: number;
  totalDistance: number;
  averageDistancePerTask: number;
  maxMakespan: number;
  averageReplanTimeMs: number;
  maxReplanTimeMs: number;
  maxMetricsHistoryCount: number;
  maxEventLogCount: number;
};

export type OnlinePressureExperimentResult = {
  cases: OnlinePressureExperimentCaseResult[];
  summary: OnlinePressureExperimentSummary;
};
