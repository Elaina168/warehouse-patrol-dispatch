import multiprocessing
import pickle
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

_PROCESS_STOP_TIMEOUT_SECONDS = 1.0


class BenchmarkInfrastructureError(RuntimeError):
    """父进程无法可靠管理基准子进程时抛出。"""


def execute_benchmark_case(case_id: str, run_index: int) -> BenchmarkRun:
    case = next((item for item in benchmark_cases() if item.case_id == case_id), None)
    if case is None:
        raise KeyError(f"未知基准场景: {case_id}")
    if case.mode == "direct":
        return _execute_direct(case, run_index)
    return _execute_online(case, run_index)


def _sanitize_error_text(text: str) -> str:
    return text.replace("\r", "").replace("\n", "")[:500]


def _write_traceback(traceback_text: str) -> None:
    sys.stderr.write(traceback_text)
    sys.stderr.flush()


def _cleanup_isolated_resources(
    process,
    connections: tuple[object | None, ...],
    *,
    process_started: bool,
    force_stop: bool,
) -> None:
    resource_errors: list[str] = []
    fatal_resource_errors: list[str] = []

    def record_resource_error(label: str, *, fatal: bool = False) -> None:
        traceback_text = traceback.format_exc()
        _write_traceback(traceback_text)
        detail = f"{label}: {traceback_text.splitlines()[-1]}"
        resource_errors.append(detail)
        if fatal:
            fatal_resource_errors.append(detail)

    def process_is_alive() -> bool | None:
        try:
            return process.is_alive()
        except Exception:
            record_resource_error("检查子进程状态失败")
            return None

    def bounded_join() -> None:
        try:
            process.join(_PROCESS_STOP_TIMEOUT_SECONDS)
        except Exception:
            record_resource_error("等待子进程停止失败")

    stopped = process is None
    if process is not None:
        if not process_started:
            stopped = True
        else:
            alive = process_is_alive()
            stopped = alive is False
            if alive is True and not force_stop:
                bounded_join()
                alive = process_is_alive()
                stopped = alive is False
            if not stopped:
                try:
                    process.terminate()
                except Exception:
                    record_resource_error("终止子进程失败")
                bounded_join()
                alive = process_is_alive()
                stopped = alive is False
            if not stopped:
                try:
                    process.kill()
                except Exception:
                    record_resource_error("强制终止子进程失败")
                bounded_join()
                alive = process_is_alive()
                stopped = alive is False

        if stopped:
            try:
                process.close()
            except Exception:
                record_resource_error("关闭子进程资源失败", fatal=True)

    for connection in connections:
        if connection is None:
            continue
        try:
            connection.close()
        except Exception:
            record_resource_error("关闭进程通信端点失败", fatal=True)

    if process is not None and not stopped:
        details = "; ".join(resource_errors)
        suffix = f": {details}" if details else ""
        raise BenchmarkInfrastructureError(f"无法确认基准子进程已停止{suffix}")
    if fatal_resource_errors:
        raise BenchmarkInfrastructureError(
            "基准子进程资源清理失败: " + "; ".join(fatal_resource_errors)
        )


def _guarded_worker_entry(
    result_connection,
    worker_callable: Callable[[str, int], BenchmarkRun],
    case_id: str,
    run_index: int,
) -> None:
    try:
        result_connection.send(("completed", worker_callable(case_id, run_index)))
    except Exception as exc:
        traceback_text = traceback.format_exc()
        _write_traceback(traceback_text)
        result_connection.send(
            (
                "error",
                _sanitize_error_text(type(exc).__name__),
                _sanitize_error_text(str(exc)),
            )
        )
    finally:
        result_connection.close()


def run_isolated_case(
    case: BenchmarkCase,
    run_index: int,
    timeout_seconds: float,
    worker_callable: Callable[[str, int], BenchmarkRun] = execute_benchmark_case,
) -> BenchmarkRun:
    started_at = perf_counter()
    parent_connection = None
    child_connection = None
    process = None
    process_started = False
    run: BenchmarkRun | None = None
    parent_error: tuple[str, str, str] | None = None
    try:
        pickle.dumps(worker_callable)
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_guarded_worker_entry,
            args=(child_connection, worker_callable, case.case_id, run_index),
        )
        process.start()
        process_started = True
        child_connection.close()
        child_connection = None
        payload_available = parent_connection.poll(timeout_seconds)
        wall_clock_ms = round((perf_counter() - started_at) * 1000, 2)
        if not payload_available:
            run = BenchmarkRun.timeout(case, run_index, wall_clock_ms)
        else:
            payload = parent_connection.recv()
            if not isinstance(payload, tuple) or not payload:
                raise ValueError("子进程返回了无效的基准结果载荷")
            if payload[0] == "error" and len(payload) == 3:
                run = BenchmarkRun.error(
                    case,
                    run_index,
                    payload[1],
                    _sanitize_error_text(payload[2]),
                    wall_clock_ms,
                )
            elif payload[0] == "completed" and len(payload) == 2:
                run = replace(payload[1], wall_clock_ms=wall_clock_ms)
            else:
                raise ValueError("子进程返回了未知的基准结果载荷")
    except Exception as exc:
        parent_error = (type(exc).__name__, str(exc), traceback.format_exc())

    try:
        _cleanup_isolated_resources(
            process,
            (parent_connection, child_connection),
            process_started=process_started,
            force_stop=run is None or run.outcome == "timeout",
        )
    except BenchmarkInfrastructureError:
        if parent_error is not None:
            _write_traceback(parent_error[2])
        raise
    if parent_error is not None:
        _write_traceback(parent_error[2])
        return BenchmarkRun.error(
            case,
            run_index,
            parent_error[0],
            _sanitize_error_text(parent_error[1]),
            round((perf_counter() - started_at) * 1000, 2),
        )
    if run is None:
        return BenchmarkRun.error(
            case,
            run_index,
            "ChildProcessError",
            "子进程未返回基准结果",
            round((perf_counter() - started_at) * 1000, 2),
        )
    return run


def run_benchmark_cases(
    cases: tuple[BenchmarkCase, ...],
    repetitions: int,
    timeout_seconds: float,
    on_result: Callable[[list[BenchmarkRun]], None] | None = None,
) -> list[BenchmarkRun]:
    runs: list[BenchmarkRun] = []
    for case in cases:
        for run_index in range(1, repetitions + 1):
            started_at = perf_counter()
            try:
                run = run_isolated_case(case, run_index, timeout_seconds)
            except BenchmarkInfrastructureError:
                raise
            except Exception as exc:
                traceback_text = traceback.format_exc()
                _write_traceback(traceback_text)
                run = BenchmarkRun.error(
                    case,
                    run_index,
                    type(exc).__name__,
                    _sanitize_error_text(str(exc)),
                    round((perf_counter() - started_at) * 1000, 2),
                )
            runs.append(run)
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
