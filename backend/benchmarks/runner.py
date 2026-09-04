import traceback
from collections.abc import Callable
from dataclasses import replace
from time import perf_counter

from backend.app.dispatch import run_dispatch
from backend.app.planning_diagnostics import PlanningDiagnostics
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
    sanitize_error_text,
)
from backend.benchmarks.online_flow import execute_online_flow
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


def run_isolated_case(
    case: BenchmarkCase,
    run_index: int,
    timeout_seconds: float,
    worker_callable: Callable[[str, int], BenchmarkRun] = execute_benchmark_case,
) -> BenchmarkRun:
    execution = run_isolated_process(
        worker_callable,
        (case.case_id, run_index),
        timeout_seconds,
    )
    if execution.outcome == "timeout":
        return BenchmarkRun.timeout(case, run_index, execution.wall_clock_ms)
    if execution.outcome == "error":
        return BenchmarkRun.error(
            case,
            run_index,
            execution.error_type or "ChildProcessError",
            execution.error_message or "子进程未返回错误信息",
            execution.wall_clock_ms,
        )
    if not isinstance(execution.value, BenchmarkRun):
        return BenchmarkRun.error(
            case,
            run_index,
            "ChildProcessError",
            "子进程返回了无效的基准结果载荷",
            execution.wall_clock_ms,
        )
    return replace(execution.value, wall_clock_ms=execution.wall_clock_ms)


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
                traceback.print_exc()
                run = BenchmarkRun.error(
                    case,
                    run_index,
                    type(exc).__name__,
                    sanitize_error_text(str(exc)),
                    round((perf_counter() - started_at) * 1000, 2),
                )
            runs.append(run)
            if on_result is not None:
                on_result(list(runs))
    return runs


def _execute_direct(case: BenchmarkCase, run_index: int) -> BenchmarkRun:
    scenario = build_benchmark_scenario(case.case_id)
    planning_diagnostics = PlanningDiagnostics()
    started_at = perf_counter()
    result = run_dispatch(
        scenario,
        benchmark_options(),
        planning_diagnostics=planning_diagnostics,
    )
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
        planning_diagnostics_evaluated=True,
        path_candidate_count=planning_diagnostics.path_candidate_count,
        selected_path_candidate_index=(
            planning_diagnostics.selected_path_candidate_index
        ),
        failed_path_candidate_count=(
            planning_diagnostics.failed_path_candidate_count
        ),
        timed_astar_call_count=planning_diagnostics.timed_astar_call_count,
        timed_astar_expanded_state_count=(
            planning_diagnostics.timed_astar_expanded_state_count
        ),
        max_timed_astar_expanded_state_count=(
            planning_diagnostics.max_timed_astar_expanded_state_count
        ),
        timed_astar_exhausted_search_count=(
            planning_diagnostics.timed_astar_exhausted_search_count
        ),
        timed_astar_goal_fully_reserved_reject_count=(
            planning_diagnostics.timed_astar_goal_fully_reserved_reject_count
        ),
        runtime_task_count=case.runtime_task_count,
        runtime_mutation_count=0,
        replan_observation_count=0,
        assignment_candidate_expansion_count=(
            planning_diagnostics.assignment_candidate_expansion_count
        ),
        assignment_robot_state_copy_count=(
            planning_diagnostics.assignment_robot_state_copy_count
        ),
        assignment_beam_peak_width=planning_diagnostics.assignment_beam_peak_width,
    )


def _execute_online(case: BenchmarkCase, run_index: int) -> BenchmarkRun:
    scenario = build_benchmark_scenario(case.case_id)
    started_at = perf_counter()
    execution = execute_online_flow(case, scenario, benchmark_options())
    wall_clock_ms = (perf_counter() - started_at) * 1000
    session = execution.session
    observations = execution.observations

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
    diagnostics_evaluated = bool(observations)
    if diagnostics_evaluated:
        path_candidate_count = sum(
            item.path_candidate_count for item in observations
        )
        failed_path_candidate_count = sum(
            item.failed_path_candidate_count for item in observations
        )
        timed_astar_call_count = sum(
            item.timed_astar_call_count for item in observations
        )
        timed_astar_expanded_state_count = sum(
            item.timed_astar_expanded_state_count for item in observations
        )
        max_timed_astar_expanded_state_count = max(
            item.max_timed_astar_expanded_state_count for item in observations
        )
        timed_astar_exhausted_search_count = sum(
            item.timed_astar_exhausted_search_count for item in observations
        )
        timed_astar_goal_fully_reserved_reject_count = sum(
            item.timed_astar_goal_fully_reserved_reject_count
            for item in observations
        )
        assignment_candidate_expansion_count = sum(
            item.assignment_candidate_expansion_count for item in observations
        )
        assignment_robot_state_copy_count = sum(
            item.assignment_robot_state_copy_count for item in observations
        )
        assignment_beam_peak_width = max(
            item.assignment_beam_peak_width for item in observations
        )
    else:
        path_candidate_count = None
        failed_path_candidate_count = None
        timed_astar_call_count = None
        timed_astar_expanded_state_count = None
        max_timed_astar_expanded_state_count = None
        timed_astar_exhausted_search_count = None
        timed_astar_goal_fully_reserved_reject_count = None
        assignment_candidate_expansion_count = None
        assignment_robot_state_copy_count = None
        assignment_beam_peak_width = None
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
        safety_intervention_count=execution.safety_intervention_count,
        deadline_miss_count=metrics.deadlineMissCount,
        failure_count=metrics.failureCount,
        total_distance=metrics.totalDistance,
        makespan=metrics.makespan,
        replan_time_ms=metrics.replanTimeMs,
        max_snapshot_replan_time_ms=max_snapshot_replan_time_ms,
        wall_clock_ms=wall_clock_ms,
        planning_diagnostics_evaluated=diagnostics_evaluated,
        path_candidate_count=path_candidate_count,
        selected_path_candidate_index=None,
        failed_path_candidate_count=failed_path_candidate_count,
        timed_astar_call_count=timed_astar_call_count,
        timed_astar_expanded_state_count=timed_astar_expanded_state_count,
        max_timed_astar_expanded_state_count=max_timed_astar_expanded_state_count,
        timed_astar_exhausted_search_count=timed_astar_exhausted_search_count,
        timed_astar_goal_fully_reserved_reject_count=(
            timed_astar_goal_fully_reserved_reject_count
        ),
        runtime_task_count=case.runtime_task_count,
        runtime_mutation_count=execution.runtime_mutation_count,
        replan_observation_count=len(observations),
        assignment_candidate_expansion_count=assignment_candidate_expansion_count,
        assignment_robot_state_copy_count=assignment_robot_state_copy_count,
        assignment_beam_peak_width=assignment_beam_peak_width,
    )
