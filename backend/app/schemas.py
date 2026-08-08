from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.functional_validators import AfterValidator

from backend.app.limits import (
    MAX_DESCRIPTION_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_EXPERIMENT_CASES,
    MAX_IDENTIFIER_LENGTH,
    MAX_SAFE_INTEGER,
    MAX_SCENARIO_CHARGE_TIME,
    MAX_SCENARIO_AXIS_LENGTH,
    MAX_SCENARIO_CELL_COUNT,
    MAX_SCENARIO_ROBOTS,
    MAX_SCENARIO_TASKS,
    MAX_SESSION_CURRENT_TIME,
    MAX_TASK_SERVICE_TIME,
    MAX_TASK_TARGETS,
)

Cell = tuple[int, int]


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


IdentifierStr = Annotated[
    str,
    Field(max_length=MAX_IDENTIFIER_LENGTH),
    AfterValidator(_require_non_blank),
]
DisplayNameStr = Annotated[
    str,
    Field(max_length=MAX_DISPLAY_NAME_LENGTH),
    AfterValidator(_require_non_blank),
]
DescriptionStr = Annotated[str, Field(max_length=MAX_DESCRIPTION_LENGTH)]
NonNegativeInt = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
PositiveInt = Annotated[int, Field(gt=0, le=MAX_SAFE_INTEGER)]
SessionTimeInt = Annotated[int, Field(ge=0, le=MAX_SESSION_CURRENT_TIME)]
TaskServiceTimeInt = Annotated[int, Field(ge=0, le=MAX_TASK_SERVICE_TIME)]
ChargeTimeInt = Annotated[int, Field(ge=1, le=MAX_SCENARIO_CHARGE_TIME)]
ScenarioAxisInt = Annotated[int, Field(gt=0, le=MAX_SCENARIO_AXIS_LENGTH)]
PriorityInt = Annotated[int, Field(ge=0, le=5)]
AssignmentReplanWindowInt = Annotated[int, Field(ge=0, le=120)]
TaskType = Literal["inspection", "delivery", "emergency"]
ALL_TASK_TYPES: tuple[TaskType, ...] = ("inspection", "delivery", "emergency")
RecoveryAction = Literal[
    "addCapableRobotOrChangeTaskType",
    "addCapableRobotOrReduceDemand",
    "clearBlockedCells",
    "clearBlockedCellsAndRestoreRobot",
    "clearBlockedCellsOrRestoreRobot",
    "fixMapOrTaskTarget",
    "fixTaskDefinition",
    "relaxLocksOrReplan",
    "restoreRobot",
]
ShelfStatus = Literal["empty", "inboundReserved", "occupied", "outboundReserved"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Task(ApiModel):
    id: IdentifierStr
    type: TaskType
    title: DisplayNameStr
    priority: PriorityInt
    releaseTime: SessionTimeInt | None = None
    deadline: NonNegativeInt | None = None
    serviceTime: TaskServiceTimeInt | None = None
    targets: list[Cell] | None = Field(default=None, max_length=MAX_TASK_TARGETS)
    pickup: Cell | None = None
    dropoff: Cell | None = None
    demand: PositiveInt | None = None
    target: Cell | None = None

    @model_validator(mode="after")
    def validate_task_shape(self) -> "Task":
        required_fields = {
            "inspection": ("targets",),
            "delivery": ("pickup", "dropoff", "demand"),
            "emergency": ("target",),
        }[self.type]
        irrelevant_fields = {
            "inspection": ("pickup", "dropoff", "demand", "target"),
            "delivery": ("targets", "target"),
            "emergency": ("targets", "pickup", "dropoff", "demand"),
        }[self.type]

        for field_name in required_fields:
            if getattr(self, field_name) is None:
                raise ValueError(f"{self.type} task requires {field_name}")
        for field_name in irrelevant_fields:
            if getattr(self, field_name) is not None:
                raise ValueError(f"{self.type} task does not allow {field_name}")
        return self


class Robot(ApiModel):
    id: IdentifierStr
    name: DisplayNameStr
    start: Cell
    battery: NonNegativeInt
    batteryCapacity: PositiveInt = 100
    load: NonNegativeInt
    moveTicks: int = Field(default=1, ge=1, le=4)
    capabilities: list[TaskType] = Field(default_factory=lambda: list(ALL_TASK_TYPES), min_length=1)

    @model_validator(mode="after")
    def validate_battery_capacity(self) -> "Robot":
        if self.battery > self.batteryCapacity:
            raise ValueError("battery must be <= batteryCapacity")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("robot capabilities must be unique")
        return self


class DynamicEvent(ApiModel):
    triggerTime: SessionTimeInt
    blockedCells: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    failedRobots: list[IdentifierStr] = Field(max_length=MAX_SCENARIO_ROBOTS)
    tasks: list[Task] = Field(max_length=MAX_SCENARIO_TASKS)


class Zones(ApiModel):
    warehouse: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    inspection: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    delivery: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    charging: list[Cell] = Field(default_factory=list, max_length=MAX_SCENARIO_CELL_COUNT)


class Shelf(ApiModel):
    id: IdentifierStr
    cell: Cell
    serviceCell: Cell
    initialOccupied: bool = False


class ShelfRuntimeState(ApiModel):
    shelfId: str
    cell: Cell
    serviceCell: Cell
    status: ShelfStatus


class Scenario(ApiModel):
    id: IdentifierStr
    name: DisplayNameStr
    description: DescriptionStr
    width: ScenarioAxisInt
    height: ScenarioAxisInt
    obstacles: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    zones: Zones
    shelves: list[Shelf] = Field(default_factory=list, max_length=MAX_SCENARIO_CELL_COUNT)
    robots: list[Robot] = Field(max_length=MAX_SCENARIO_ROBOTS)
    tasks: list[Task] = Field(max_length=MAX_SCENARIO_TASKS)
    dynamic: DynamicEvent
    chargeTime: ChargeTimeInt = 4

    @model_validator(mode="after")
    def validate_size_limits(self) -> "Scenario":
        cell_count = self.width * self.height
        if cell_count > MAX_SCENARIO_CELL_COUNT:
            raise ValueError(
                f"map cell count must be <= {MAX_SCENARIO_CELL_COUNT}: {cell_count}"
            )
        if len(self.tasks) + len(self.dynamic.tasks) > MAX_SCENARIO_TASKS:
            raise ValueError(
                "initial and dynamic task count must be "
                f"<= {MAX_SCENARIO_TASKS}: "
                f"{len(self.tasks) + len(self.dynamic.tasks)}"
            )
        cell_lists = {
            "obstacles": self.obstacles,
            "shelves": self.shelves,
            "zones.warehouse": self.zones.warehouse,
            "zones.inspection": self.zones.inspection,
            "zones.delivery": self.zones.delivery,
            "zones.charging": self.zones.charging,
            "dynamic.blockedCells": self.dynamic.blockedCells,
        }
        for field_name, values in cell_lists.items():
            if len(values) > cell_count:
                raise ValueError(
                    f"{field_name} count must be <= map cell count: "
                    f"{len(values)} > {cell_count}"
                )
        return self


class DispatchOptions(ApiModel):
    avoidConflicts: bool = True
    includeDynamic: bool = True
    assignmentReplanWindow: AssignmentReplanWindowInt = 24
    adaptiveReplanWindow: bool = False


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
    windows: list[AssignmentReplanWindowInt] = Field(
        min_length=1,
        max_length=MAX_EXPERIMENT_CASES,
    )
    includeAdaptive: bool = False

    @model_validator(mode="after")
    def validate_unique_windows(self) -> "ReplanWindowExperimentRequest":
        if len(set(self.windows)) != len(self.windows):
            raise ValueError("windows must be unique")
        if len(self.windows) + int(self.includeAdaptive) > MAX_EXPERIMENT_CASES:
            raise ValueError(
                f"total experiment case count must be <= {MAX_EXPERIMENT_CASES}"
            )
        return self


class ScaleExperimentScenario(ApiModel):
    label: DisplayNameStr
    scenario: Scenario


class ScaleExperimentRequest(ApiModel):
    cases: list[ScaleExperimentScenario] = Field(
        min_length=1,
        max_length=MAX_EXPERIMENT_CASES,
    )
    options: DispatchOptions = Field(default_factory=DispatchOptions)

    @model_validator(mode="after")
    def validate_unique_case_labels(self) -> "ScaleExperimentRequest":
        labels = [case.label for case in self.cases]
        if len(set(labels)) != len(labels):
            raise ValueError("case labels must be unique")
        return self


class SeededPressureExperimentRequest(ApiModel):
    caseSet: Literal["standard", "extended"] = "standard"
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class OnlinePressureExperimentRequest(ApiModel):
    options: DispatchOptions = Field(default_factory=DispatchOptions)


class CreateSessionRequest(ApiModel):
    scenario: Scenario
    options: DispatchOptions = Field(default_factory=DispatchOptions)
    delayInitialPlanning: bool = False


class AddTaskRequest(ApiModel):
    task: Task


class SessionTickRequest(ApiModel):
    currentTime: SessionTimeInt


class AddBlockRequest(ApiModel):
    cell: Cell
    currentTime: SessionTimeInt = 0


class RemoveBlockRequest(ApiModel):
    cell: Cell
    currentTime: SessionTimeInt = 0


class FailRobotRequest(ApiModel):
    robotId: IdentifierStr
    currentTime: SessionTimeInt = 0


class RestoreRobotRequest(ApiModel):
    robotId: IdentifierStr
    currentTime: SessionTimeInt = 0


class Assignment(ApiModel):
    robotId: str
    tasks: list[Task]


class Conflict(ApiModel):
    time: int
    type: Literal["vertex", "edge"]
    robots: list[str]
    cell: Cell


class SafetyStall(ApiModel):
    conflict: Conflict
    consecutiveCount: PositiveInt
    firstInterventionTime: NonNegativeInt
    latestInterventionTime: NonNegativeInt


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


class ChargingVisit(ApiModel):
    robotId: str
    station: Cell
    departureTime: int
    arrivalTime: int
    completionTime: int


class RobotRuntimeState(ApiModel):
    robotId: str
    name: str
    position: Cell
    status: Literal["idle", "waiting", "toPickup", "delivering", "inspecting", "toCharge", "charging", "failed"]
    battery: int
    batteryCapacity: PositiveInt = 100
    load: int
    moveTicks: int
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
    effectiveAssignmentReplanWindow: AssignmentReplanWindowInt = 24
    replanWindowReason: str = "固定窗口"
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
    chargingVisits: list[ChargingVisit] = Field(default_factory=list)
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
    assignmentRatePercent: float
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
    assignmentRatePercent: float
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
    releasedTaskCount: int
    runtimeTaskCount: int
    runtimeEventCount: int
    runtimeEventEvidence: list[str]
    tickCount: int
    taskCount: int
    coveredTaskCount: int
    completedTaskCount: int
    assignedTaskCount: int
    stable: bool
    coverageRatePercent: float
    actualCompletionRatePercent: float
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
    totalReleasedTaskCount: int
    totalCoveredTaskCount: int
    totalCompletedTaskCount: int
    totalAssignedTaskCount: int
    stableCaseCount: int
    stableRatePercent: float
    coverageRatePercent: float
    actualCompletionRatePercent: float
    maxConflictCount: int
    totalDeadlineMissCount: int
    totalFailureCount: int
    totalRuntimeEventCount: int
    totalRuntimeTaskCount: int
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
    runtimeTaskCount: int
    runtimeEventCount: int
    robotStates: list[RobotRuntimeState]
    shelfStates: list[ShelfRuntimeState] = Field(default_factory=list)
    taskStates: list[TaskRuntimeState]
    metricsHistory: list[MetricSnapshot]
    completedTaskCount: int
    safetyIntervention: Conflict | None = None
    safetyStall: SafetyStall | None = None
    result: DispatchResult


class SessionSummary(ApiModel):
    sessionId: str
    scenarioId: str
    createdAt: float
    updatedAt: float
    lastAccessedAt: float
    currentTime: int
    runtimeTaskCount: int
    runtimeEventCount: int
    completedTaskCount: int


class DeleteSessionResult(ApiModel):
    sessionId: str
    deleted: bool
