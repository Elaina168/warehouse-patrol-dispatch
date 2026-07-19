from time import perf_counter

from backend.app.dispatch import run_dispatch
from backend.app.schemas import CreateSessionRequest, SessionTickRequest
from backend.app.sessions import create_session, delete_session, tick_session
from backend.benchmarks.results import BenchmarkRun, percent
from backend.benchmarks.scenarios import (
    BenchmarkCase,
    benchmark_cases,
    benchmark_options,
    build_benchmark_scenario,
)


def execute_benchmark_case(case_id: str, run_index: int) -> BenchmarkRun:
    case = next((item for item in benchmark_cases() if item.case_id == case_id), None)
    if case is None:
        raise KeyError(f"未知基准场景: {case_id}")
    if case.mode == "direct":
        return _execute_direct(case, run_index)
    return _execute_online(case, run_index)


def _execute_direct(case: BenchmarkCase, run_index: int) -> BenchmarkRun:
    scenario = build_benchmark_scenario(case.case_id)
    started_at = perf_counter()
    result = run_dispatch(scenario, benchmark_options())
    wall_clock_ms = (perf_counter() - started_at) * 1000
    metrics = result.metrics
    correctness_stable = (
        metrics.assignedTaskCount == case.task_count
        and metrics.conflictCount == 0
        and metrics.deadlineMissCount == 0
        and metrics.failureCount == 0
    )
    return BenchmarkRun(
        case_id=case.case_id,
        family=case.family,
        mode=case.mode,
        seed=case.seed,
        run_index=run_index,
        robot_count=case.robot_count,
        task_count=case.task_count,
        dynamic_task_count=case.dynamic_task_count,
        obstacle_count=len(scenario.obstacles),
        tick_target=case.tick_target,
        outcome="completed",
        error_type=None,
        error_message=None,
        correctness_stable=correctness_stable,
        released_task_count=None,
        covered_task_count=None,
        assigned_task_count=metrics.assignedTaskCount,
        completed_task_count=None,
        assignment_rate_percent=percent(metrics.assignedTaskCount, case.task_count),
        coverage_rate_percent=None,
        actual_completion_rate_percent=None,
        predicted_conflict_count=metrics.conflictCount,
        active_conflict_count=None,
        execution_safety_evaluated=False,
        safety_intervention_count=0,
        deadline_miss_count=metrics.deadlineMissCount,
        failure_count=metrics.failureCount,
        total_distance=metrics.totalDistance,
        makespan=metrics.makespan,
        replan_time_ms=metrics.replanTimeMs,
        max_snapshot_replan_time_ms=None,
        wall_clock_ms=wall_clock_ms,
    )


def _execute_online(case: BenchmarkCase, run_index: int) -> BenchmarkRun:
    scenario = build_benchmark_scenario(case.case_id)
    session_id: str | None = None
    safety_count = 0
    started_at = perf_counter()
    try:
        session = create_session(CreateSessionRequest(scenario=scenario, options=benchmark_options()))
        session_id = session.sessionId
        while case.tick_target is not None and session.currentTime < case.tick_target:
            session = tick_session(
                session_id,
                SessionTickRequest(currentTime=session.currentTime + 1),
            )
            if session.safetyIntervention is not None:
                safety_count += 1
    finally:
        if session_id is not None:
            delete_session(session_id)
    wall_clock_ms = (perf_counter() - started_at) * 1000

    metrics = session.result.metrics
    released_task_count = sum(
        1 for state in session.taskStates if state.releaseTime <= session.currentTime
    )
    covered_task_count = sum(1 for state in session.taskStates if state.status != "unassigned")
    active_conflict_count = (
        session.metricsHistory[-1].activeConflictCount if session.metricsHistory else 0
    )
    max_snapshot_replan_time_ms = max(
        (snapshot.replanTimeMs for snapshot in session.metricsHistory),
        default=0,
    )
    correctness_stable = (
        covered_task_count == case.task_count
        and active_conflict_count == 0
        and metrics.deadlineMissCount == 0
        and metrics.failureCount == 0
    )
    return BenchmarkRun(
        case_id=case.case_id,
        family=case.family,
        mode=case.mode,
        seed=case.seed,
        run_index=run_index,
        robot_count=case.robot_count,
        task_count=case.task_count,
        dynamic_task_count=case.dynamic_task_count,
        obstacle_count=len(scenario.obstacles),
        tick_target=case.tick_target,
        outcome="completed",
        error_type=None,
        error_message=None,
        correctness_stable=correctness_stable,
        released_task_count=released_task_count,
        covered_task_count=covered_task_count,
        assigned_task_count=metrics.assignedTaskCount,
        completed_task_count=session.completedTaskCount,
        assignment_rate_percent=percent(metrics.assignedTaskCount, case.task_count),
        coverage_rate_percent=percent(covered_task_count, case.task_count),
        actual_completion_rate_percent=percent(session.completedTaskCount, released_task_count),
        predicted_conflict_count=metrics.conflictCount,
        active_conflict_count=active_conflict_count,
        execution_safety_evaluated=True,
        safety_intervention_count=safety_count,
        deadline_miss_count=metrics.deadlineMissCount,
        failure_count=metrics.failureCount,
        total_distance=metrics.totalDistance,
        makespan=metrics.makespan,
        replan_time_ms=metrics.replanTimeMs,
        max_snapshot_replan_time_ms=max_snapshot_replan_time_ms,
        wall_clock_ms=wall_clock_ms,
    )
