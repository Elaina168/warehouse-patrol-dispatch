import multiprocessing
import queue as queue_module
import sys
import traceback
from collections.abc import Callable
from dataclasses import replace
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


def _sanitize_error_text(text: str) -> str:
    return text.replace("\r", "").replace("\n", "")[:500]


def _guarded_worker_entry(
    result_queue,
    worker_callable: Callable[[str, int], BenchmarkRun],
    case_id: str,
    run_index: int,
) -> None:
    try:
        result_queue.put(("completed", worker_callable(case_id, run_index)))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, str(exc), traceback.format_exc()))


def run_isolated_case(
    case: BenchmarkCase,
    run_index: int,
    timeout_seconds: float,
    worker_callable: Callable[[str, int], BenchmarkRun] = execute_benchmark_case,
) -> BenchmarkRun:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_guarded_worker_entry,
        args=(result_queue, worker_callable, case.case_id, run_index),
    )
    process_started = False
    started_at = perf_counter()
    try:
        process.start()
        process_started = True
        process.join(timeout_seconds)
        wall_clock_ms = round((perf_counter() - started_at) * 1000, 2)
        if process.is_alive():
            process.terminate()
            process.join()
            return BenchmarkRun.timeout(case, run_index, wall_clock_ms)
        try:
            payload = result_queue.get(timeout=1)
        except queue_module.Empty:
            payload = None
        if payload is None:
            return BenchmarkRun.error(
                case,
                run_index,
                "ChildProcessError",
                f"子进程退出码: {process.exitcode}",
                wall_clock_ms,
            )
        if payload[0] == "error":
            sys.stderr.write(payload[3])
            sys.stderr.flush()
            return BenchmarkRun.error(
                case,
                run_index,
                payload[1],
                _sanitize_error_text(payload[2]),
                wall_clock_ms,
            )
        run = payload[1]
        return replace(run, wall_clock_ms=wall_clock_ms)
    finally:
        if process_started:
            if process.is_alive():
                process.terminate()
                process.join()
            process.close()
        result_queue.close()
        result_queue.join_thread()


def run_benchmark_cases(
    cases: tuple[BenchmarkCase, ...],
    repetitions: int,
    timeout_seconds: float,
    on_result: Callable[[list[BenchmarkRun]], None] | None = None,
) -> list[BenchmarkRun]:
    runs: list[BenchmarkRun] = []
    for case in cases:
        for run_index in range(1, repetitions + 1):
            runs.append(run_isolated_case(case, run_index, timeout_seconds))
            if on_result is not None:
                on_result(list(runs))
    return runs


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
