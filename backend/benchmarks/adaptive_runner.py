import traceback
from collections.abc import Callable
from dataclasses import replace
from time import perf_counter

from backend.app.replan_window import (
    DEFAULT_ADAPTIVE_REPLAN_POLICY,
    ReplanObservation,
)
from backend.app.schemas import (
    AddTaskRequest,
    CreateSessionRequest,
    SessionTickRequest,
)
from backend.app.sessions import (
    add_task,
    create_session,
    delete_session,
    tick_session,
)
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationRun,
    AdaptiveReplanRecord,
)
from backend.benchmarks.adaptive_scenarios import (
    RUNTIME_TASK_TICKS,
    AdaptiveCalibrationCase,
    adaptive_calibration_cases,
    build_adaptive_calibration_scenario,
    build_calibration_runtime_task,
)
from backend.benchmarks.adaptive_variants import (
    AdaptiveCalibrationVariant,
    adaptive_calibration_variants,
)
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
    sanitize_error_text,
)
from backend.benchmarks.results import percent


def execute_adaptive_calibration_case(
    case_id: str,
    variant_id: str,
    run_index: int,
) -> AdaptiveCalibrationRun:
    case = adaptive_calibration_cases((case_id,))[0]
    variant = adaptive_calibration_variants((variant_id,))[0]
    scenario = build_adaptive_calibration_scenario(case.case_id)
    observations: list[ReplanObservation] = []
    session_id: str | None = None
    safety_intervention_count = 0
    safety_stall_reached = False
    max_consecutive_safety_intervention_count = 0
    inserted_ticks: set[int] = set()
    started_at = perf_counter()
    try:
        session = create_session(
            CreateSessionRequest(
                scenario=scenario,
                options=variant.options(),
            ),
            enforce_execution_safety=True,
            adaptive_replan_policy=DEFAULT_ADAPTIVE_REPLAN_POLICY,
            replan_observer=observations.append,
        )
        session_id = session.sessionId
        while session.currentTime < case.tick_target:
            session = tick_session(
                session_id,
                SessionTickRequest(
                    currentTime=session.currentTime + 1
                ),
            )
            if session.safetyIntervention is not None:
                safety_intervention_count += 1
            if session.safetyStall is not None:
                safety_stall_reached = True
                max_consecutive_safety_intervention_count = max(
                    max_consecutive_safety_intervention_count,
                    session.safetyStall.consecutiveCount,
                )
            if (
                session.currentTime in RUNTIME_TASK_TICKS
                and session.currentTime not in inserted_ticks
            ):
                inserted_ticks.add(session.currentTime)
                session = add_task(
                    session_id,
                    AddTaskRequest(
                        task=build_calibration_runtime_task(
                            case.case_id,
                            scenario,
                            session.currentTime,
                        )
                    ),
                )
                if session.safetyStall is not None:
                    safety_stall_reached = True
                    max_consecutive_safety_intervention_count = max(
                        max_consecutive_safety_intervention_count,
                        session.safetyStall.consecutiveCount,
                    )
    finally:
        if session_id is not None:
            delete_session(session_id)
    wall_clock_ms = (perf_counter() - started_at) * 1000

    records = tuple(
        AdaptiveReplanRecord.from_observation(
            case.case_id,
            variant.variant_id,
            run_index,
            observation_index,
            observation,
        )
        for observation_index, observation in enumerate(
            observations,
            start=1,
        )
    )
    window_change_count = sum(
        previous.effective_window != current.effective_window
        for previous, current in zip(records, records[1:])
    )
    metrics = session.result.metrics
    released_task_count = sum(
        state.releaseTime <= session.currentTime
        for state in session.taskStates
    )
    covered_task_count = sum(
        state.status != "unassigned" for state in session.taskStates
    )
    active_conflict_count = (
        session.metricsHistory[-1].activeConflictCount
        if session.metricsHistory
        else 0
    )
    correctness_stable = (
        covered_task_count == case.task_count
        and active_conflict_count == 0
        and metrics.deadlineMissCount == 0
        and metrics.failureCount == 0
    )
    return AdaptiveCalibrationRun(
        case_id=case.case_id,
        variant_id=variant.variant_id,
        run_index=run_index,
        robot_count=case.robot_count,
        task_count=case.task_count,
        tick_target=case.tick_target,
        outcome="completed",
        error_type=None,
        error_message=None,
        correctness_stable=correctness_stable,
        released_task_count=released_task_count,
        covered_task_count=covered_task_count,
        completed_task_count=session.completedTaskCount,
        coverage_rate_percent=percent(
            covered_task_count,
            case.task_count,
        ),
        actual_completion_rate_percent=percent(
            session.completedTaskCount,
            released_task_count,
        ),
        predicted_conflict_count=metrics.conflictCount,
        active_conflict_count=active_conflict_count,
        safety_intervention_count=safety_intervention_count,
        safety_stall_reached=safety_stall_reached,
        max_consecutive_safety_intervention_count=(
            max_consecutive_safety_intervention_count
        ),
        deadline_miss_count=metrics.deadlineMissCount,
        failure_count=metrics.failureCount,
        total_distance=metrics.totalDistance,
        makespan=metrics.makespan,
        wall_clock_ms=wall_clock_ms,
        replan_count=len(records),
        window_change_count=window_change_count,
        replan_observations=records,
    )


def run_isolated_adaptive_calibration(
    case: AdaptiveCalibrationCase,
    variant: AdaptiveCalibrationVariant,
    run_index: int,
    timeout_seconds: float,
) -> AdaptiveCalibrationRun:
    execution = run_isolated_process(
        execute_adaptive_calibration_case,
        (case.case_id, variant.variant_id, run_index),
        timeout_seconds,
    )
    if execution.outcome == "timeout":
        return AdaptiveCalibrationRun.timeout(
            case,
            variant,
            run_index,
            execution.wall_clock_ms,
        )
    if execution.outcome == "error":
        return AdaptiveCalibrationRun.error(
            case,
            variant,
            run_index,
            execution.error_type or "ChildProcessError",
            execution.error_message or "子进程未返回错误信息",
            execution.wall_clock_ms,
        )
    if not isinstance(execution.value, AdaptiveCalibrationRun):
        return AdaptiveCalibrationRun.error(
            case,
            variant,
            run_index,
            "ChildProcessError",
            "子进程返回了无效的自适应窗口校准结果载荷",
            execution.wall_clock_ms,
        )
    return replace(
        execution.value,
        wall_clock_ms=execution.wall_clock_ms,
    )


def run_adaptive_calibration_cases(
    cases: tuple[AdaptiveCalibrationCase, ...],
    variants: tuple[AdaptiveCalibrationVariant, ...],
    repetitions: int,
    timeout_seconds: float,
    on_result: Callable[[list[AdaptiveCalibrationRun]], None] | None = None,
) -> list[AdaptiveCalibrationRun]:
    runs: list[AdaptiveCalibrationRun] = []
    for case in cases:
        for variant in variants:
            for run_index in range(1, repetitions + 1):
                started_at = perf_counter()
                try:
                    run = run_isolated_adaptive_calibration(
                        case,
                        variant,
                        run_index,
                        timeout_seconds,
                    )
                except BenchmarkInfrastructureError:
                    raise
                except Exception as exc:
                    traceback.print_exc()
                    run = AdaptiveCalibrationRun.error(
                        case,
                        variant,
                        run_index,
                        type(exc).__name__,
                        sanitize_error_text(str(exc)),
                        round(
                            (perf_counter() - started_at) * 1000,
                            2,
                        ),
                    )
                runs.append(run)
                if on_result is not None:
                    on_result(list(runs))
    return runs
