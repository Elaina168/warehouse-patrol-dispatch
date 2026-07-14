from backend.app.dispatch import run_dispatch
from backend.app.seeded_scenarios import seeded_pressure_scenario
from backend.app.schemas import (
    AddBlockRequest,
    AddTaskRequest,
    ConflictAvoidanceExperimentRequest,
    ConflictAvoidanceExperimentResult,
    CreateSessionRequest,
    DispatchOptions,
    DynamicReplanningExperimentRequest,
    DynamicReplanningExperimentResult,
    EventItem,
    ExperimentCaseResult,
    FailRobotRequest,
    OnlinePressureExperimentCaseResult,
    OnlinePressureExperimentRequest,
    OnlinePressureExperimentResult,
    OnlinePressureExperimentSummary,
    ReplanWindowExperimentRequest,
    ReplanWindowExperimentResult,
    RemoveBlockRequest,
    RestoreRobotRequest,
    ScaleExperimentRequest,
    ScaleExperimentResult,
    ScaleExperimentCaseResult,
    SeededPressureExperimentCaseResult,
    SeededPressureExperimentRequest,
    SeededPressureExperimentResult,
    SeededPressureExperimentSummary,
    SessionTickRequest,
    Task,
)
from backend.app.sessions import (
    add_blocked_cell,
    add_task,
    create_session,
    delete_session,
    fail_robot,
    remove_blocked_cell,
    restore_robot,
    tick_session,
)


SEEDED_PRESSURE_CASES = (
    ("seed-17", 17, 4, 12),
    ("seed-29", 29, 6, 20),
    ("seed-31", 31, 8, 24),
)

EXTENDED_SEEDED_PRESSURE_CASES = SEEDED_PRESSURE_CASES + (
    ("seed-37", 37, 8, 26),
    ("seed-43", 43, 8, 28),
    ("seed-53", 53, 8, 28),
    ("seed-67", 67, 8, 30),
)
SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS = 2000
ONLINE_PRESSURE_FLOW = ("seed-17-online-flow", 17, 4, 12)
ONLINE_RUNTIME_EVENT_EVIDENCE = (
    ("manualTask", "手动录入任务"),
    ("blockedCell", "手动封锁单元"),
    ("failedRobot", "手动标记故障机器人"),
    ("restoredRobot", "手动恢复机器人"),
    ("clearedBlockedCell", "手动解除封锁单元"),
    ("generatedTask", "手动录入任务：G"),
)


def compare_conflict_avoidance(request: ConflictAvoidanceExperimentRequest) -> ConflictAvoidanceExperimentResult:
    without_options = request.options.model_copy(update={"avoidConflicts": False})
    with_options = request.options.model_copy(update={"avoidConflicts": True})

    return ConflictAvoidanceExperimentResult(
        scenarioId=request.scenario.id,
        cases=[
            _run_case("withoutConflictAvoidance", request, without_options),
            _run_case("withConflictAvoidance", request, with_options),
        ],
    )


def compare_dynamic_replanning(request: DynamicReplanningExperimentRequest) -> DynamicReplanningExperimentResult:
    without_options = request.options.model_copy(update={"includeDynamic": False})
    with_options = request.options.model_copy(update={"includeDynamic": True})

    return DynamicReplanningExperimentResult(
        scenarioId=request.scenario.id,
        cases=[
            _run_case("withoutDynamicReplanning", request, without_options),
            _run_case("withDynamicReplanning", request, with_options),
        ],
    )


def compare_replan_windows(request: ReplanWindowExperimentRequest) -> ReplanWindowExperimentResult:
    return ReplanWindowExperimentResult(
        scenarioId=request.scenario.id,
        cases=[
            _run_case(
                f"window-{window}",
                request,
                request.options.model_copy(update={"assignmentReplanWindow": window}),
            )
            for window in request.windows
        ],
    )


def compare_scale_cases(request: ScaleExperimentRequest) -> ScaleExperimentResult:
    return ScaleExperimentResult(
        cases=[
            ScaleExperimentCaseResult(
                label=case.label,
                scenarioId=case.scenario.id,
                options=request.options,
                result=run_dispatch(case.scenario, request.options),
            )
            for case in request.cases
        ],
    )


def run_seeded_pressure_experiment(request: SeededPressureExperimentRequest) -> SeededPressureExperimentResult:
    cases = []
    pressure_cases = EXTENDED_SEEDED_PRESSURE_CASES if request.caseSet == "extended" else SEEDED_PRESSURE_CASES
    for label, seed, robot_count, task_count in pressure_cases:
        scenario = seeded_pressure_scenario(label, seed, robot_count, task_count)
        result = run_dispatch(scenario, request.options)
        result_task_count = len(result.tasks)
        stable = (
            result.metrics.assignedTaskCount == result_task_count
            and result.metrics.conflictCount == 0
            and result.metrics.deadlineMissCount == 0
            and result.metrics.failureCount == 0
        )
        cases.append(
            SeededPressureExperimentCaseResult(
                label=label,
                seed=seed,
                scenarioId=scenario.id,
                options=request.options,
                robotCount=len(scenario.robots),
                taskCount=len(result.tasks),
                dynamicTaskCount=len(scenario.dynamic.tasks),
                obstacleCount=len(scenario.obstacles),
                assignedTaskCount=result.metrics.assignedTaskCount,
                stable=stable,
                assignmentRatePercent=_percent(result.metrics.assignedTaskCount, result_task_count),
                conflictCount=result.metrics.conflictCount,
                deadlineMissCount=result.metrics.deadlineMissCount,
                failureCount=result.metrics.failureCount,
                totalDistance=result.metrics.totalDistance,
                averageDistancePerTask=round(result.metrics.totalDistance / result_task_count, 1)
                if result_task_count
                else 0,
                makespan=result.metrics.makespan,
                replanTimeMs=result.metrics.replanTimeMs,
                withinPlanningTimeBudget=result.metrics.replanTimeMs < SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS,
            )
        )
    stable_case_count = sum(1 for case in cases if case.stable)
    within_budget_count = sum(1 for case in cases if case.withinPlanningTimeBudget)
    total_task_count = sum(case.taskCount for case in cases)
    total_assigned_task_count = sum(case.assignedTaskCount for case in cases)
    total_distance = sum(case.totalDistance for case in cases)
    return SeededPressureExperimentResult(
        cases=cases,
        summary=SeededPressureExperimentSummary(
            caseCount=len(cases),
            largestRobotCount=max((case.robotCount for case in cases), default=0),
            largestTaskCount=max((case.taskCount for case in cases), default=0),
            totalTaskCount=total_task_count,
            totalAssignedTaskCount=total_assigned_task_count,
            stableCaseCount=stable_case_count,
            stableRatePercent=_percent(stable_case_count, len(cases)),
            assignmentRatePercent=_percent(total_assigned_task_count, total_task_count),
            planningTimeBudgetMs=SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS,
            withinPlanningTimeBudgetCount=within_budget_count,
            withinPlanningTimeBudgetRatePercent=_percent(within_budget_count, len(cases)),
            maxConflictCount=max((case.conflictCount for case in cases), default=0),
            totalDeadlineMissCount=sum(case.deadlineMissCount for case in cases),
            totalFailureCount=sum(case.failureCount for case in cases),
            totalDistance=total_distance,
            averageDistancePerTask=round(total_distance / total_task_count, 1) if total_task_count else 0,
            maxMakespan=max((case.makespan for case in cases), default=0),
            averageReplanTimeMs=round(
                sum(case.replanTimeMs for case in cases) / len(cases),
                2,
            )
            if cases
            else 0,
            maxReplanTimeMs=max((case.replanTimeMs for case in cases), default=0),
        ),
    )


def run_online_pressure_experiment(request: OnlinePressureExperimentRequest) -> OnlinePressureExperimentResult:
    label, seed, robot_count, task_count = ONLINE_PRESSURE_FLOW
    scenario = seeded_pressure_scenario("seed-17", seed, robot_count, task_count)
    session_id: str | None = None

    try:
        session = create_session(CreateSessionRequest(scenario=scenario, options=request.options))
        session_id = session.sessionId
        for current_time in range(1, 9):
            session = tick_session(session_id, SessionTickRequest(currentTime=current_time))

        session = add_task(
            session_id,
            AddTaskRequest(
                task=Task(
                    id="RUNTIME-SEED-17",
                    type="emergency",
                    title="固定种子运行时复核",
                    priority=5,
                    releaseTime=8,
                    deadline=28,
                    target=(2, 9),
                )
            ),
        )
        session = add_blocked_cell(session_id, AddBlockRequest(cell=(3, 9), currentTime=8))
        session = fail_robot(session_id, FailRobotRequest(robotId="R4", currentTime=8))
        session = restore_robot(session_id, RestoreRobotRequest(robotId="R4", currentTime=8))
        session = remove_blocked_cell(session_id, RemoveBlockRequest(cell=(3, 9), currentTime=8))

        for current_time in range(9, 19):
            session = tick_session(session_id, SessionTickRequest(currentTime=current_time))
        session = add_task(
            session_id,
            AddTaskRequest(
                task=Task(
                    id="G-SEED-17",
                    type="inspection",
                    title="随机生成巡检",
                    priority=0,
                    releaseTime=18,
                    deadline=42,
                    targets=[scenario.zones.inspection[0] if scenario.zones.inspection else scenario.robots[0].start],
                )
            ),
        )
        for current_time in range(19, 21):
            session = tick_session(session_id, SessionTickRequest(currentTime=current_time))
    finally:
        if session_id is not None:
            delete_session(session_id)

    metrics = session.result.metrics
    task_count = len(session.result.tasks)
    released_task_count = sum(
        1 for state in session.taskStates if state.releaseTime <= session.currentTime
    )
    covered_task_count = sum(1 for state in session.taskStates if state.status != "unassigned")
    runtime_event_evidence = _online_runtime_event_evidence(session.result.eventLog)
    stable = (
        covered_task_count == task_count
        and metrics.conflictCount == 0
        and metrics.deadlineMissCount == 0
        and metrics.failureCount == 0
    )
    case = OnlinePressureExperimentCaseResult(
        label=label,
        seed=seed,
        scenarioId=scenario.id,
        options=request.options,
        robotCount=len(scenario.robots),
        baseTaskCount=len(scenario.tasks),
        scenarioDynamicTaskCount=len(scenario.dynamic.tasks),
        releasedTaskCount=released_task_count,
        runtimeTaskCount=session.runtimeTaskCount,
        runtimeEventCount=len(runtime_event_evidence),
        runtimeEventEvidence=runtime_event_evidence,
        tickCount=session.currentTime,
        taskCount=task_count,
        coveredTaskCount=covered_task_count,
        completedTaskCount=session.completedTaskCount,
        assignedTaskCount=metrics.assignedTaskCount,
        stable=stable,
        coverageRatePercent=_percent(covered_task_count, task_count),
        actualCompletionRatePercent=_percent(session.completedTaskCount, released_task_count),
        conflictCount=metrics.conflictCount,
        deadlineMissCount=metrics.deadlineMissCount,
        failureCount=metrics.failureCount,
        totalDistance=metrics.totalDistance,
        averageDistancePerTask=round(metrics.totalDistance / task_count, 1) if task_count else 0,
        makespan=metrics.makespan,
        replanTimeMs=metrics.replanTimeMs,
        metricsHistoryCount=len(session.metricsHistory),
        eventLogCount=len(session.result.eventLog),
    )
    return OnlinePressureExperimentResult(
        cases=[case],
        summary=_online_pressure_summary([case]),
    )


def _online_runtime_event_evidence(event_log: list[EventItem]) -> list[str]:
    return [
        evidence_key
        for evidence_key, text_pattern in ONLINE_RUNTIME_EVENT_EVIDENCE
        if any(text_pattern in event.text for event in event_log)
    ]


def _online_pressure_summary(cases: list[OnlinePressureExperimentCaseResult]) -> OnlinePressureExperimentSummary:
    stable_case_count = sum(1 for case in cases if case.stable)
    total_task_count = sum(case.taskCount for case in cases)
    total_released_task_count = sum(case.releasedTaskCount for case in cases)
    total_covered_task_count = sum(case.coveredTaskCount for case in cases)
    total_completed_task_count = sum(case.completedTaskCount for case in cases)
    total_assigned_task_count = sum(case.assignedTaskCount for case in cases)
    total_distance = sum(case.totalDistance for case in cases)
    return OnlinePressureExperimentSummary(
        caseCount=len(cases),
        totalTaskCount=total_task_count,
        totalReleasedTaskCount=total_released_task_count,
        totalCoveredTaskCount=total_covered_task_count,
        totalCompletedTaskCount=total_completed_task_count,
        totalAssignedTaskCount=total_assigned_task_count,
        stableCaseCount=stable_case_count,
        stableRatePercent=_percent(stable_case_count, len(cases)),
        coverageRatePercent=_percent(total_covered_task_count, total_task_count),
        actualCompletionRatePercent=_percent(total_completed_task_count, total_released_task_count),
        maxConflictCount=max((case.conflictCount for case in cases), default=0),
        totalDeadlineMissCount=sum(case.deadlineMissCount for case in cases),
        totalFailureCount=sum(case.failureCount for case in cases),
        totalRuntimeEventCount=sum(case.runtimeEventCount for case in cases),
        totalRuntimeTaskCount=sum(case.runtimeTaskCount for case in cases),
        totalDistance=total_distance,
        averageDistancePerTask=round(total_distance / total_task_count, 1) if total_task_count else 0,
        maxMakespan=max((case.makespan for case in cases), default=0),
        averageReplanTimeMs=round(sum(case.replanTimeMs for case in cases) / len(cases), 2) if cases else 0,
        maxReplanTimeMs=max((case.replanTimeMs for case in cases), default=0),
        maxMetricsHistoryCount=max((case.metricsHistoryCount for case in cases), default=0),
        maxEventLogCount=max((case.eventLogCount for case in cases), default=0),
    )


def _percent(numerator: int | float, denominator: int | float) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0


def _run_case(
    label: str,
    request: ConflictAvoidanceExperimentRequest | DynamicReplanningExperimentRequest | ReplanWindowExperimentRequest,
    options: DispatchOptions,
) -> ExperimentCaseResult:
    return ExperimentCaseResult(
        label=label,
        options=options,
        result=run_dispatch(request.scenario, options),
    )
