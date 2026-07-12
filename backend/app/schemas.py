from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Cell = tuple[int, int]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
AssignmentReplanWindowInt = Annotated[int, Field(ge=0, le=120)]
RecoveryAction = Literal[
    "addCapableRobotOrReduceDemand",
    "clearBlockedCells",
    "clearBlockedCellsAndRestoreRobot",
    "clearBlockedCellsOrRestoreRobot",
    "fixMapOrTaskTarget",
    "fixTaskDefinition",
    "relaxLocksOrReplan",
    "restoreRobot",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Task(ApiModel):
    id: str
    type: Literal["inspection", "delivery", "emergency"]
    title: str
    priority: NonNegativeInt
    releaseTime: NonNegativeInt | None = None
    deadline: NonNegativeInt | None = None
    serviceTime: NonNegativeInt | None = None
    targets: list[Cell] | None = None
    pickup: Cell | None = None
    dropoff: Cell | None = None
    demand: PositiveInt | None = None
    target: Cell | None = None


class Robot(ApiModel):
    id: str
    name: str
    start: Cell
    battery: NonNegativeInt
    load: NonNegativeInt


class DynamicEvent(ApiModel):
    triggerTime: NonNegativeInt
    blockedCells: list[Cell]
    failedRobots: list[str]
    tasks: list[Task]


class Zones(ApiModel):
    warehouse: list[Cell]
    inspection: list[Cell]
    delivery: list[Cell]


class Scenario(ApiModel):
    id: str
    name: str
    description: str
    width: PositiveInt
    height: PositiveInt
    obstacles: list[Cell]
    zones: Zones
    robots: list[Robot]
    tasks: list[Task]
    dynamic: DynamicEvent


class DispatchOptions(ApiModel):
    avoidConflicts: bool = True
    includeDynamic: bool = True
    assignmentReplanWindow: AssignmentReplanWindowInt = 24


class DispatchRequest(ApiModel):
    scenario: Scenario
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class ConflictAvoidanceExperimentRequest(ApiModel):
    scenario: Scenario
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class DynamicReplanningExperimentRequest(ApiModel):
    scenario: Scenario
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class ReplanWindowExperimentRequest(ApiModel):
    scenario: Scenario
    options: DispatchOptions = Field(default_factory=DispatchOptions)
    windows: list[AssignmentReplanWindowInt]


class ScaleExperimentScenario(ApiModel):
    label: str
    scenario: Scenario


class ScaleExperimentRequest(ApiModel):
    cases: list[ScaleExperimentScenario]
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class SeededPressureExperimentRequest(ApiModel):
    caseSet: Literal["standard", "extended"] = "standard"
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class OnlinePressureExperimentRequest(ApiModel):
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class CreateSessionRequest(ApiModel):
    scenario: Scenario
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class AddTaskRequest(ApiModel):
    task: Task


class SessionTickRequest(ApiModel):
    currentTime: NonNegativeInt


class AddBlockRequest(ApiModel):
    cell: Cell
    currentTime: NonNegativeInt = 0


class RemoveBlockRequest(ApiModel):
    cell: Cell
    currentTime: NonNegativeInt = 0


class FailRobotRequest(ApiModel):
    robotId: str
    currentTime: NonNegativeInt = 0


class RestoreRobotRequest(ApiModel):
    robotId: str
    currentTime: NonNegativeInt = 0


class Assignment(ApiModel):
    robotId: str
    tasks: list[Task]


class Conflict(ApiModel):
    time: int
    type: Literal["vertex", "edge"]
    robots: list[str]
    cell: Cell


class ConflictState(ApiModel):
    time: int
    type: Literal["vertex", "edge"]
    robots: list[str]
    cell: Cell
    status: Literal["active", "resolved"]
    startedAt: int
    resolvedAt: int | None = None


class Metrics(ApiModel):
    makespan: int
    totalDistance: int
    conflictCount: int
    loadBalance: float
    assignedTaskCount: int
    deadlineMissCount: int
    averageLateness: float
    failureCount: int
    replanTimeMs: float


class MetricSnapshot(ApiModel):
    time: int
    completedTaskCount: int
    activeTaskCount: int
    pendingTaskCount: int
    travelledDistance: int
    activeConflictCount: int
    deadlineMissCount: int
    replanTimeMs: float


class EventItem(ApiModel):
    time: int
    text: str


class RobotRuntimeState(ApiModel):
    robotId: str
    name: str
    position: Cell
    status: Literal["idle", "waiting", "toPickup", "delivering", "inspecting", "failed"]
    battery: int
    load: int
    currentTaskId: str | None = None


class TaskRuntimeState(ApiModel):
    taskId: str
    status: Literal["pending", "running", "completed", "unassigned"]
    assignedRobotId: str | None = None
    releaseTime: int
    completionTime: int | None = None
    locked: bool = False
    failureReason: str | None = None
    failureCategory: Literal["temporary", "permanent"] | None = None
    recoveryAction: RecoveryAction | None = None


class TaskFailureDetail(ApiModel):
    reason: str
    category: Literal["temporary", "permanent"]
    recoveryAction: RecoveryAction
    blockingCells: list[Cell] = Field(default_factory=list)
    blockingRobotIds: list[str] = Field(default_factory=list)


class DispatchResult(ApiModel):
    scenarioId: str
    avoidConflicts: bool
    includeDynamic: bool
    dynamicTriggerTime: int | None
    extraBlocked: list[Cell]
    unavailableRobotIds: list[str]
    assignments: list[Assignment]
    paths: dict[str, list[Cell]]
    conflicts: list[Conflict]
    conflictStates: list[ConflictState] = Field(default_factory=list)
    metrics: Metrics
    failureReasons: dict[str, str] = Field(default_factory=dict)
    failureDetails: dict[str, TaskFailureDetail] = Field(default_factory=dict)
    eventLog: list[EventItem]
    tasks: list[Task]


class ExperimentCaseResult(ApiModel):
    label: str
    options: DispatchOptions
    result: DispatchResult


class ConflictAvoidanceExperimentResult(ApiModel):
    scenarioId: str
    cases: list[ExperimentCaseResult]


class DynamicReplanningExperimentResult(ApiModel):
    scenarioId: str
    cases: list[ExperimentCaseResult]


class ReplanWindowExperimentResult(ApiModel):
    scenarioId: str
    cases: list[ExperimentCaseResult]


class ScaleExperimentCaseResult(ApiModel):
    label: str
    scenarioId: str
    options: DispatchOptions
    result: DispatchResult


class ScaleExperimentResult(ApiModel):
    cases: list[ScaleExperimentCaseResult]


class SeededPressureExperimentCaseResult(ApiModel):
    label: str
    seed: int
    scenarioId: str
    options: DispatchOptions
    robotCount: int
    taskCount: int
    dynamicTaskCount: int
    obstacleCount: int
    assignedTaskCount: int
    stable: bool
    completionRatePercent: float
    conflictCount: int
    deadlineMissCount: int
    failureCount: int
    totalDistance: int
    averageDistancePerTask: float
    makespan: int
    replanTimeMs: float
    withinPlanningTimeBudget: bool


class SeededPressureExperimentSummary(ApiModel):
    caseCount: int
    largestRobotCount: int
    largestTaskCount: int
    totalTaskCount: int
    totalAssignedTaskCount: int
    stableCaseCount: int
    stableRatePercent: float
    completionRatePercent: float
    planningTimeBudgetMs: int
    withinPlanningTimeBudgetCount: int
    withinPlanningTimeBudgetRatePercent: float
    maxConflictCount: int
    totalDeadlineMissCount: int
    totalFailureCount: int
    totalDistance: int
    averageDistancePerTask: float
    maxMakespan: int
    averageReplanTimeMs: float
    maxReplanTimeMs: float


class SeededPressureExperimentResult(ApiModel):
    cases: list[SeededPressureExperimentCaseResult]
    summary: SeededPressureExperimentSummary


class OnlinePressureExperimentCaseResult(ApiModel):
    label: str
    seed: int
    scenarioId: str
    options: DispatchOptions
    robotCount: int
    baseTaskCount: int
    scenarioDynamicTaskCount: int
    manualTaskCount: int
    streamTaskCount: int
    runtimeEventCount: int
    runtimeEventEvidence: list[str]
    tickCount: int
    taskCount: int
    coveredTaskCount: int
    completedTaskCount: int
    assignedTaskCount: int
    stable: bool
    completionRatePercent: float
    conflictCount: int
    deadlineMissCount: int
    failureCount: int
    totalDistance: int
    averageDistancePerTask: float
    makespan: int
    replanTimeMs: float
    metricsHistoryCount: int
    eventLogCount: int


class OnlinePressureExperimentSummary(ApiModel):
    caseCount: int
    totalTaskCount: int
    totalCoveredTaskCount: int
    totalCompletedTaskCount: int
    totalAssignedTaskCount: int
    stableCaseCount: int
    stableRatePercent: float
    completionRatePercent: float
    maxConflictCount: int
    totalDeadlineMissCount: int
    totalFailureCount: int
    totalRuntimeEventCount: int
    totalManualTaskCount: int
    totalStreamTaskCount: int
    totalDistance: int
    averageDistancePerTask: float
    maxMakespan: int
    averageReplanTimeMs: float
    maxReplanTimeMs: float
    maxMetricsHistoryCount: int
    maxEventLogCount: int


class OnlinePressureExperimentResult(ApiModel):
    cases: list[OnlinePressureExperimentCaseResult]
    summary: OnlinePressureExperimentSummary


class SessionResult(ApiModel):
    sessionId: str
    scenarioId: str
    options: DispatchOptions
    createdAt: float
    updatedAt: float
    lastAccessedAt: float
    currentTime: int
    manualTaskCount: int
    streamTaskCount: int
    runtimeEventCount: int
    robotStates: list[RobotRuntimeState]
    taskStates: list[TaskRuntimeState]
    metricsHistory: list[MetricSnapshot]
    completedTaskCount: int
    result: DispatchResult


class SessionSummary(ApiModel):
    sessionId: str
    scenarioId: str
    createdAt: float
    updatedAt: float
    lastAccessedAt: float
    currentTime: int
    manualTaskCount: int
    streamTaskCount: int
    runtimeEventCount: int
    completedTaskCount: int


class DeleteSessionResult(ApiModel):
    sessionId: str
    deleted: bool
