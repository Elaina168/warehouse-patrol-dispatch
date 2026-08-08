export type Cell = [number, number];

export type TaskType = "inspection" | "delivery" | "emergency";
export type ConflictType = "vertex" | "edge";
export type ConflictStatus = "active" | "resolved";
export type RobotRuntimeStatus = "idle" | "waiting" | "toPickup" | "delivering" | "inspecting" | "toCharge" | "charging" | "failed";
export type TaskRuntimeStatus = "pending" | "running" | "completed" | "unassigned";
export type TaskFailureCategory = "temporary" | "permanent";
export type ShelfStatus = "empty" | "inboundReserved" | "occupied" | "outboundReserved";

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
  batteryCapacity?: number;
  load: number;
  moveTicks?: number;
  capabilities?: TaskType[];
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
  charging?: Cell[];
};

export type Shelf = {
  id: string;
  cell: Cell;
  serviceCell: Cell;
  initialOccupied: boolean;
};

export type ShelfRuntimeState = {
  shelfId: string;
  cell: Cell;
  serviceCell: Cell;
  status: ShelfStatus;
};

export type Scenario = {
  id: string;
  name: string;
  description: string;
  width: number;
  height: number;
  obstacles: Cell[];
  zones: Zones;
  shelves: Shelf[];
  robots: Robot[];
  tasks: Task[];
  dynamic: DynamicEvent;
  chargeTime?: number;
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

export type SafetyStall = {
  conflict: Conflict;
  consecutiveCount: number;
  firstInterventionTime: number;
  latestInterventionTime: number;
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

export type ChargingVisit = {
  robotId: string;
  station: Cell;
  departureTime: number;
  arrivalTime: number;
  completionTime: number;
};

export type RecoveryAction =
  | "addCapableRobotOrChangeTaskType"
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
  batteryCapacity?: number;
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
  effectiveAssignmentReplanWindow?: number;
  replanWindowReason?: string;
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
  chargingVisits?: ChargingVisit[];
  eventLog: EventItem[];
  tasks: Task[];
};

export type DispatchOptions = {
  avoidConflicts: boolean;
  includeDynamic: boolean;
  assignmentReplanWindow: number;
  adaptiveReplanWindow?: boolean;
};

export type DispatchRequest = {
  scenario: Scenario;
  options?: DispatchOptions;
};

export type CreateSessionRequest = {
  scenario: Scenario;
  options?: DispatchOptions;
  delayInitialPlanning?: boolean;
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
  runtimeTaskCount: number;
  runtimeEventCount: number;
  robotStates: RobotRuntimeState[];
  shelfStates: ShelfRuntimeState[];
  taskStates: TaskRuntimeState[];
  metricsHistory: MetricSnapshot[];
  completedTaskCount: number;
  safetyIntervention: Conflict | null;
  safetyStall: SafetyStall | null;
  result: DispatchResult;
};

export type SessionSummary = {
  sessionId: string;
  scenarioId: string;
  createdAt: number;
  updatedAt: number;
  lastAccessedAt: number;
  currentTime: number;
  runtimeTaskCount: number;
  runtimeEventCount: number;
  completedTaskCount: number;
};

export type DeleteSessionResult = {
  sessionId: string;
  deleted: boolean;
};
