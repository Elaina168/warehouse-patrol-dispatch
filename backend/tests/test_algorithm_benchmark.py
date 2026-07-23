import csv
import datetime as datetime_module
import json
import multiprocessing
import re
import time

import pytest

from backend.benchmarks import algorithm_boundary as algorithm_boundary_module
from backend.benchmarks import runner as runner_module
from backend.benchmarks.algorithm_boundary import main, parse_args
from backend.benchmarks.reporting import write_final_report, write_partial_report
from backend.benchmarks.scenarios import BenchmarkCase, benchmark_cases, benchmark_options, build_benchmark_scenario
from backend.benchmarks.results import BenchmarkReport, BenchmarkRun, nearest_rank_p95, percent, summarize_runs
from backend.benchmarks.runner import execute_benchmark_case, run_benchmark_cases, run_isolated_case
from backend.app.dispatch import astar, run_dispatch
from backend.app.planning_diagnostics import PlanningDiagnostics
from backend.app.schemas import SessionTickRequest
from backend.app import sessions as sessions_module


def _sleeping_benchmark_worker(case_id: str, run_index: int):
    time.sleep(2)


def _failing_benchmark_worker(case_id: str, run_index: int):
    raise RuntimeError("benchmark worker failed")


def _multiline_failing_benchmark_worker(case_id: str, run_index: int):
    raise RuntimeError("first\r\nsecond" + "x" * 600)


def _large_failing_benchmark_worker(case_id: str, run_index: int):
    raise RuntimeError("large-error-marker-" + "x" * 2_000_000)


class _UnpicklableBenchmarkWorker:
    def __call__(self, case_id: str, run_index: int):
        raise AssertionError("不可序列化 worker 不应进入子进程")

    def __reduce__(self):
        raise RuntimeError("worker serialization failed")


def _active_child_pids() -> set[int]:
    return {
        process.pid
        for process in multiprocessing.active_children()
        if process.pid is not None
    }


def _benchmark_run(case_id: str, run_index: int, **updates) -> BenchmarkRun:
    values = {
        "case_id": case_id,
        "family": "scale",
        "mode": "direct",
        "seed": None,
        "run_index": run_index,
        "robot_count": 4,
        "task_count": 15,
        "dynamic_task_count": 3,
        "obstacle_count": 0,
        "tick_target": None,
        "outcome": "completed",
        "error_type": None,
        "error_message": None,
        "correctness_stable": True,
        "released_task_count": None,
        "covered_task_count": None,
        "assigned_task_count": 15,
        "completed_task_count": None,
        "assignment_rate_percent": 100,
        "coverage_rate_percent": None,
        "actual_completion_rate_percent": None,
        "predicted_conflict_count": 0,
        "active_conflict_count": None,
        "execution_safety_evaluated": False,
        "safety_intervention_count": 0,
        "deadline_miss_count": 0,
        "failure_count": 0,
        "total_distance": 10,
        "makespan": 10,
        "replan_time_ms": 4,
        "max_snapshot_replan_time_ms": None,
        "wall_clock_ms": 10,
    }
    values.update(updates)
    return BenchmarkRun(**values)


def test_isolated_algorithm_benchmark_records_timeout() -> None:
    case = benchmark_cases(("scale",))[0]
    children_before = _active_child_pids()
    run = run_isolated_case(case, 1, 0.05, worker_callable=_sleeping_benchmark_worker)

    assert run.outcome == "timeout"
    assert run.correctness_stable is False
    assert run.error_type == "TimeoutError"
    assert _active_child_pids() <= children_before


def test_isolated_algorithm_benchmark_records_worker_error(capfd) -> None:
    case = benchmark_cases(("scale",))[0]
    run = run_isolated_case(case, 1, 5, worker_callable=_failing_benchmark_worker)
    captured = capfd.readouterr()

    assert run.outcome == "error"
    assert run.error_type == "RuntimeError"
    assert run.error_message == "benchmark worker failed"
    assert "Traceback (most recent call last)" in captured.err
    assert "benchmark worker failed" in captured.err
    assert captured.out == ""


def test_isolated_algorithm_benchmark_sanitizes_worker_error() -> None:
    case = benchmark_cases(("scale",))[0]
    run = run_isolated_case(case, 1, 5, worker_callable=_multiline_failing_benchmark_worker)

    assert run.error_message is not None
    assert len(run.error_message) == 500
    assert "\r" not in run.error_message
    assert "\n" not in run.error_message
    assert run.error_message.startswith("firstsecond")


def test_isolated_algorithm_benchmark_large_error_payload_is_not_misreported_as_timeout(
    capfd,
) -> None:
    case = benchmark_cases(("scale",))[0]
    run = run_isolated_case(case, 1, 5, worker_callable=_large_failing_benchmark_worker)
    captured = capfd.readouterr()

    assert run.outcome == "error"
    assert run.error_type == "RuntimeError"
    assert run.error_message is not None
    assert len(run.error_message) == 500
    assert run.error_message.startswith("large-error-marker-")
    assert "Traceback (most recent call last)" in captured.err
    assert "large-error-marker-" in captured.err


def test_isolated_algorithm_benchmark_records_unpicklable_worker_error(capsys) -> None:
    case = benchmark_cases(("scale",))[0]
    children_before = _active_child_pids()
    run = run_isolated_case(case, 1, 5, worker_callable=_UnpicklableBenchmarkWorker())
    captured = capsys.readouterr()

    assert run.outcome == "error"
    assert run.error_type == "RuntimeError"
    assert run.error_message == "worker serialization failed"
    assert "Traceback (most recent call last)" in captured.err
    assert "worker serialization failed" in captured.err
    assert captured.out == ""
    assert _active_child_pids() <= children_before


def test_isolated_algorithm_benchmark_cleans_resources_after_start_error(
    monkeypatch,
) -> None:
    class FailingConnection:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FailingProcess:
        def __init__(self) -> None:
            self.closed = False

        def start(self) -> None:
            raise RuntimeError("process start failed")

        def is_alive(self) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    class FailingContext:
        def __init__(self) -> None:
            self.parent_connection = FailingConnection()
            self.child_connection = FailingConnection()
            self.process = FailingProcess()

        def Pipe(self, duplex: bool):
            assert duplex is False
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            assert target is runner_module._guarded_worker_entry
            assert args[0] is self.child_connection
            return self.process

    context = FailingContext()
    monkeypatch.setattr(runner_module.multiprocessing, "get_context", lambda method: context)

    case = benchmark_cases(("scale",))[0]
    run = run_isolated_case(case, 1, 5)

    assert run.outcome == "error"
    assert run.error_type == "RuntimeError"
    assert run.error_message == "process start failed"
    assert context.process.closed is True
    assert context.parent_connection.closed is True
    assert context.child_connection.closed is True


def test_isolated_algorithm_benchmark_uses_kill_fallback_after_terminate_error(
    monkeypatch,
) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def poll(self, timeout: float) -> bool:
            assert timeout == 0.01
            return False

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True
            self.join_timeouts: list[float] = []
            self.kill_called = False
            self.closed = False

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            raise OSError("terminate failed")

        def join(self, timeout: float) -> None:
            self.join_timeouts.append(timeout)

        def kill(self) -> None:
            self.kill_called = True
            self.alive = False

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FakeConnection()
            self.child_connection = FakeConnection()
            self.process = FakeProcess()

        def Pipe(self, duplex: bool):
            assert duplex is False
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            return self.process

    context = FakeContext()
    monkeypatch.setattr(runner_module.multiprocessing, "get_context", lambda method: context)

    case = benchmark_cases(("scale",))[0]
    run = run_isolated_case(case, 1, 0.01)

    assert run.outcome == "timeout"
    assert context.process.kill_called is True
    assert context.process.join_timeouts
    assert all(timeout > 0 for timeout in context.process.join_timeouts)
    assert context.process.closed is True
    assert context.parent_connection.closed is True
    assert context.child_connection.closed is True


def test_isolated_algorithm_benchmark_propagates_unstoppable_child_cleanup_failure(
    monkeypatch,
) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def poll(self, timeout: float) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.join_timeouts: list[float] = []
            self.closed = False

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            return None

        def join(self, timeout: float) -> None:
            self.join_timeouts.append(timeout)

        def kill(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FakeConnection()
            self.child_connection = FakeConnection()
            self.process = FakeProcess()

        def Pipe(self, duplex: bool):
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            return self.process

    context = FakeContext()
    monkeypatch.setattr(runner_module.multiprocessing, "get_context", lambda method: context)

    case = benchmark_cases(("scale",))[0]
    with pytest.raises(RuntimeError, match="无法确认基准子进程已停止"):
        run_isolated_case(case, 1, 0.01)

    assert len(context.process.join_timeouts) == 2
    assert all(timeout > 0 for timeout in context.process.join_timeouts)
    assert context.process.closed is False
    assert context.parent_connection.closed is True
    assert context.child_connection.closed is True


def test_algorithm_benchmark_batch_continues_and_reports_cumulative_copies(
    monkeypatch,
    capsys,
) -> None:
    cases = benchmark_cases(("scale",))[:2]
    calls: list[tuple[str, int, float]] = []
    snapshots: list[list[BenchmarkRun]] = []

    def fake_run_isolated_case(
        case: BenchmarkCase,
        run_index: int,
        timeout_seconds: float,
    ) -> BenchmarkRun:
        calls.append((case.case_id, run_index, timeout_seconds))
        if len(calls) == 1:
            raise RuntimeError("isolated runner failed")
        return _benchmark_run(case.case_id, run_index)

    monkeypatch.setattr(runner_module, "run_isolated_case", fake_run_isolated_case)
    runs = run_benchmark_cases(cases, 2, 3.5, on_result=snapshots.append)
    captured = capsys.readouterr()

    assert calls == [
        (cases[0].case_id, 1, 3.5),
        (cases[0].case_id, 2, 3.5),
        (cases[1].case_id, 1, 3.5),
        (cases[1].case_id, 2, 3.5),
    ]
    assert [run.outcome for run in runs] == ["error", "completed", "completed", "completed"]
    assert [len(snapshot) for snapshot in snapshots] == [1, 2, 3, 4]
    assert len({id(snapshot) for snapshot in snapshots}) == 4
    assert snapshots[0] == [runs[0]]
    assert runs[0].error_type == "RuntimeError"
    assert runs[0].error_message == "isolated runner failed"
    assert "Traceback (most recent call last)" in captured.err
    assert "isolated runner failed" in captured.err
    assert captured.out == ""


def test_algorithm_benchmark_batch_propagates_parent_infrastructure_failure(
    monkeypatch,
) -> None:
    case = benchmark_cases(("scale",))[0]

    def fail_isolated_case(
        case: BenchmarkCase,
        run_index: int,
        timeout_seconds: float,
    ) -> BenchmarkRun:
        raise runner_module.BenchmarkInfrastructureError("无法确认基准子进程已停止")

    monkeypatch.setattr(runner_module, "run_isolated_case", fail_isolated_case)

    with pytest.raises(
        runner_module.BenchmarkInfrastructureError,
        match="无法确认基准子进程已停止",
    ):
        run_benchmark_cases((case,), 1, 5)


def test_algorithm_benchmark_statistics_use_completed_runs_only() -> None:
    runs = [
        _benchmark_run("scale-r4-t15", 1, wall_clock_ms=10, replan_time_ms=4),
        _benchmark_run("scale-r4-t15", 2, wall_clock_ms=20, replan_time_ms=8),
        _benchmark_run("scale-r4-t15", 3, wall_clock_ms=30, replan_time_ms=12),
        _benchmark_run(
            "scale-r4-t15",
            4,
            outcome="timeout",
            correctness_stable=False,
            error_type="TimeoutError",
            error_message="运行超时",
            replan_time_ms=None,
            wall_clock_ms=30,
        ),
    ]
    summary = summarize_runs(runs)[0]
    assert percent(1, 3) == 33.3
    assert nearest_rank_p95([10, 20, 30]) == 30
    assert summary.completed_run_count == 3
    assert summary.timeout_count == 1
    assert summary.median_wall_clock_ms == 20
    assert summary.p95_wall_clock_ms == 30
    assert summary.median_replan_time_ms == 8
    assert summary.p95_replan_time_ms == 12


def test_algorithm_benchmark_summary_uses_null_timings_without_completed_runs() -> None:
    summary = summarize_runs([
        _benchmark_run(
            "scale-r4-t15",
            1,
            outcome="timeout",
            correctness_stable=False,
            error_type="TimeoutError",
            error_message="运行超时",
            replan_time_ms=None,
            wall_clock_ms=30,
        )
    ])[0]
    assert summary.median_wall_clock_ms is None
    assert summary.p95_wall_clock_ms is None
    assert summary.median_replan_time_ms is None
    assert summary.p95_replan_time_ms is None


def test_algorithm_benchmark_failed_run_factories_preserve_case_metadata() -> None:
    case = BenchmarkCase("bottleneck-r4-t4", "bottleneck", "online", None, 4, 4, 0, 120)
    timeout = BenchmarkRun.timeout(case, 1, 30)
    error = BenchmarkRun.error(case, 2, "ValueError", "无效输入", 20)

    assert timeout.to_record() == {
        "caseId": "bottleneck-r4-t4",
        "family": "bottleneck",
        "mode": "online",
        "seed": None,
        "runIndex": 1,
        "robotCount": 4,
        "taskCount": 4,
        "dynamicTaskCount": 0,
        "obstacleCount": None,
        "tickTarget": 120,
        "outcome": "timeout",
        "errorType": "TimeoutError",
        "errorMessage": None,
        "correctnessStable": False,
        "releasedTaskCount": None,
        "coveredTaskCount": None,
        "assignedTaskCount": None,
        "completedTaskCount": None,
        "assignmentRatePercent": None,
        "coverageRatePercent": None,
        "actualCompletionRatePercent": None,
        "predictedConflictCount": None,
        "activeConflictCount": None,
        "executionSafetyEvaluated": True,
        "safetyInterventionCount": 0,
        "deadlineMissCount": None,
        "failureCount": None,
        "totalDistance": None,
        "makespan": None,
        "replanTimeMs": None,
        "maxSnapshotReplanTimeMs": None,
        "wallClockMs": 30,
    }
    assert error.error_type == "ValueError"
    assert error.error_message == "无效输入"
    assert error.outcome == "error"


def test_algorithm_benchmark_report_serializes_runs_and_summaries() -> None:
    report = BenchmarkReport.create({"repetitions": 5}, [_benchmark_run("scale-r4-t15", 1)])
    record = report.to_record()

    assert record["schemaVersion"] == 1
    assert record["generatedAt"].endswith("Z")
    assert record["config"] == {"repetitions": 5}
    assert record["runs"][0]["caseId"] == "scale-r4-t15"
    assert record["caseSummaries"][0]["completedRunCount"] == 1


def test_algorithm_benchmark_catalog_has_exact_cases_and_options() -> None:
    cases = benchmark_cases()
    assert [case.case_id for case in cases] == [
        "scale-r4-t15",
        "scale-r8-t27",
        "scale-r12-t39",
        "density-r8-t31",
        "density-r8-t43",
        "density-r8-t55",
        "bottleneck-r4-t4",
        "bottleneck-r6-t6",
        "bottleneck-r8-t8",
    ]
    assert [(case.family, case.mode) for case in cases] == [
        *(("scale", "direct") for _ in range(3)),
        *(("density", "direct") for _ in range(3)),
        *(("bottleneck", "online") for _ in range(3)),
    ]
    assert benchmark_options().model_dump(mode="json") == {
        "avoidConflicts": True,
        "includeDynamic": True,
        "assignmentReplanWindow": 120,
        "adaptiveReplanWindow": False,
    }


def test_algorithm_benchmark_catalog_filters_scale_family_exactly() -> None:
    assert [case.case_id for case in benchmark_cases(("scale",))] == [
        "scale-r4-t15",
        "scale-r8-t27",
        "scale-r12-t39",
    ]


def test_algorithm_benchmark_catalog_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="^未知基准场景族: unknown$"):
        benchmark_cases(("unknown",))


def test_algorithm_benchmark_scenarios_match_catalog_and_are_deterministic() -> None:
    for case in benchmark_cases():
        first = build_benchmark_scenario(case.case_id)
        second = build_benchmark_scenario(case.case_id)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert len(first.robots) == case.robot_count
        assert len(first.tasks) + len(first.dynamic.tasks) == case.task_count
        assert len(first.dynamic.tasks) == case.dynamic_task_count
        assert len({robot.id for robot in first.robots}) == len(first.robots)
        assert len({robot.start for robot in first.robots}) == len(first.robots)
        all_tasks = [*first.tasks, *first.dynamic.tasks]
        assert len({task.id for task in all_tasks}) == len(all_tasks)


def test_planning_diagnostics_collect_path_candidates_for_density_case() -> None:
    scenario = build_benchmark_scenario("density-r8-t31")
    diagnostics = PlanningDiagnostics()

    result = run_dispatch(
        scenario,
        benchmark_options(),
        planning_diagnostics=diagnostics,
    )

    assert result.metrics.assignedTaskCount == 31
    assert result.metrics.conflictCount == 0
    assert result.metrics.failureCount == 0
    assert result.metrics.deadlineMissCount == 0
    assert diagnostics.path_candidate_count == 2
    assert diagnostics.selected_path_candidate_index == 1
    assert diagnostics.failed_path_candidate_count == 1
    assert diagnostics.timed_astar_call_count > 0
    assert diagnostics.timed_astar_expanded_state_count > 0
    assert diagnostics.max_timed_astar_expanded_state_count > 0
    assert diagnostics.timed_astar_exhausted_search_count == 1
    assert diagnostics.timed_astar_goal_fully_reserved_reject_count == 0
    assert diagnostics.path_candidates[0].failure_count == 1
    assert diagnostics.path_candidates[1].failure_count == 0


def test_optional_planning_diagnostics_do_not_change_dispatch_result() -> None:
    scenario = build_benchmark_scenario("scale-r4-t15")
    without_diagnostics = run_dispatch(scenario, benchmark_options())
    diagnostics = PlanningDiagnostics()
    with_diagnostics = run_dispatch(
        scenario,
        benchmark_options(),
        planning_diagnostics=diagnostics,
    )

    without_payload = without_diagnostics.model_dump(mode="json")
    with_payload = with_diagnostics.model_dump(mode="json")
    without_replan_time_ms = without_payload["metrics"].pop("replanTimeMs")
    with_replan_time_ms = with_payload["metrics"].pop("replanTimeMs")

    assert isinstance(without_replan_time_ms, float)
    assert without_replan_time_ms >= 0
    assert isinstance(with_replan_time_ms, float)
    assert with_replan_time_ms >= 0
    assert with_payload == without_payload
    assert diagnostics.path_candidate_count >= 1


def test_algorithm_benchmark_targets_are_reachable_and_bottleneck_has_bypass() -> None:
    for case in benchmark_cases():
        scenario = build_benchmark_scenario(case.case_id)
        for task in [*scenario.tasks, *scenario.dynamic.tasks]:
            if task.targets is not None:
                waypoints = task.targets
            elif task.target is not None:
                waypoints = [task.target]
            else:
                assert task.pickup is not None and task.dropoff is not None
                waypoints = [task.pickup, task.dropoff]
                assert astar(scenario, task.pickup, task.dropoff)
            for point in waypoints:
                assert any(astar(scenario, robot.start, point) for robot in scenario.robots)
    for case in benchmark_cases(("bottleneck",)):
        scenario = build_benchmark_scenario(case.case_id)
        open_rows = [y for y in range(scenario.height) if (6, y) not in set(scenario.obstacles)]
        assert open_rows == [3, 4, 5]


def test_direct_algorithm_benchmark_maps_existing_metrics() -> None:
    run = execute_benchmark_case("scale-r4-t15", 1)
    assert run.outcome == "completed"
    assert run.mode == "direct"
    assert run.execution_safety_evaluated is False
    assert run.safety_intervention_count == 0
    assert run.active_conflict_count is None
    assert run.assigned_task_count == run.task_count
    assert run.assignment_rate_percent == 100
    assert run.coverage_rate_percent is None
    assert run.actual_completion_rate_percent is None
    assert run.predicted_conflict_count is not None
    assert run.wall_clock_ms is not None and run.wall_clock_ms >= 0


def _assert_history_collision_free(history: dict[str, list[tuple[int, int]]]) -> None:
    robot_ids = sorted(history)
    horizon = max((len(history[robot_id]) for robot_id in robot_ids), default=0)
    for time_index in range(horizon):
        positions = {
            robot_id: history[robot_id][min(time_index, len(history[robot_id]) - 1)]
            for robot_id in robot_ids
        }
        assert len(set(positions.values())) == len(positions)
        if time_index == 0:
            continue
        previous = {
            robot_id: history[robot_id][min(time_index - 1, len(history[robot_id]) - 1)]
            for robot_id in robot_ids
        }
        for first_index, first_id in enumerate(robot_ids):
            for second_id in robot_ids[first_index + 1 :]:
                assert not (
                    previous[first_id] == positions[second_id]
                    and previous[second_id] == positions[first_id]
                )


def test_online_algorithm_benchmark_uses_execution_safety(monkeypatch) -> None:
    real_delete = sessions_module.delete_session
    real_tick = sessions_module.tick_session
    captured_histories = []
    observed_safety_interventions = []

    def capture_tick(session_id: str, request: SessionTickRequest):
        session = real_tick(session_id, request)
        if session.safetyIntervention is not None:
            observed_safety_interventions.append(session.safetyIntervention)
        return session

    def capture_delete(session_id: str):
        session = sessions_module._sessions[session_id]
        captured_histories.append({key: list(value) for key, value in session.robot_path_history.items()})
        return real_delete(session_id)

    monkeypatch.setattr("backend.benchmarks.runner.tick_session", capture_tick)
    monkeypatch.setattr("backend.benchmarks.runner.delete_session", capture_delete)
    run = execute_benchmark_case("bottleneck-r4-t4", 1)
    assert run.outcome == "completed"
    assert run.mode == "online"
    assert run.execution_safety_evaluated is True
    assert run.active_conflict_count == 0
    assert run.safety_intervention_count == len(observed_safety_interventions)
    assert captured_histories
    _assert_history_collision_free(captured_histories[0])


def test_algorithm_benchmark_writes_utf8_json_and_csv(tmp_path) -> None:
    run = execute_benchmark_case("scale-r4-t15", 1)
    report = BenchmarkReport.create(
        config={"families": ["scale"], "repetitions": 1, "timeoutSeconds": 30},
        runs=[run],
    )

    write_final_report(tmp_path, report)

    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["runs"][0]["caseId"] == "scale-r4-t15"
    assert payload["caseSummaries"][0]["runCount"] == 1
    with (tmp_path / "runs.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["caseId"] == "scale-r4-t15"
    assert (tmp_path / "case-summaries.csv").exists()


def test_algorithm_benchmark_replaces_partial_report_and_removes_it_after_final(tmp_path) -> None:
    first_run = _benchmark_run("scale-r4-t15", 1)
    second_run = _benchmark_run("scale-r4-t15", 2)

    write_partial_report(tmp_path, BenchmarkReport.create({}, [first_run]))
    write_partial_report(tmp_path, BenchmarkReport.create({}, [first_run, second_run]))

    partial_path = tmp_path / "results.partial.json"
    partial_payload = json.loads(partial_path.read_text(encoding="utf-8"))
    assert [run["runIndex"] for run in partial_payload["runs"]] == [1, 2]
    assert not list(tmp_path.glob("*.tmp"))

    write_final_report(tmp_path, BenchmarkReport.create({}, [first_run, second_run]))
    assert partial_path.exists() is False


@pytest.mark.parametrize(
    ("writer", "file_name"),
    [
        (write_partial_report, "results.partial.json"),
        (write_final_report, "results.json"),
    ],
)
def test_algorithm_benchmark_write_failure_includes_absolute_target_path(
    tmp_path,
    writer,
    file_name,
) -> None:
    blocked_output_path = tmp_path / "blocked"
    blocked_output_path.write_text("not a directory", encoding="utf-8")
    target_path = (blocked_output_path / file_name).resolve()

    with pytest.raises(OSError, match=re.escape(str(target_path))):
        writer(blocked_output_path, BenchmarkReport.create({}, []))


def test_algorithm_benchmark_rejects_invalid_config_before_creating_output(tmp_path) -> None:
    assert main(["--families", "unknown", "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []
    assert main(["--repetitions", "0", "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []
    assert main(["--timeout-seconds", "0", "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("families", [",", "   "])
def test_algorithm_benchmark_rejects_empty_families_before_creating_output(
    tmp_path,
    families,
) -> None:
    assert main(["--families", families, "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("timeout_seconds", ["nan", "inf"])
def test_algorithm_benchmark_rejects_non_finite_timeout_before_creating_output(
    tmp_path,
    monkeypatch,
    timeout_seconds,
) -> None:
    monkeypatch.setattr(
        algorithm_boundary_module,
        "run_benchmark_cases",
        lambda cases, repetitions, timeout_seconds, on_result: [],
    )

    assert main(
        [
            "--families",
            "scale",
            "--timeout-seconds",
            timeout_seconds,
            "--output-dir",
            str(tmp_path),
        ]
    ) != 0
    assert list(tmp_path.iterdir()) == []


def test_algorithm_benchmark_parse_args_splits_and_trims_families() -> None:
    args = parse_args(["--families", " scale, density ,,bottleneck "])

    assert args.families == ("scale", "density", "bottleneck")


def test_algorithm_benchmark_main_uses_unique_timestamp_directory_and_exact_config(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    class FixedDatetime:
        @classmethod
        def now(cls, timezone_value):
            assert timezone_value is algorithm_boundary_module.timezone.utc
            return datetime_module.datetime(2026, 7, 19, 1, 2, 3, tzinfo=timezone_value)

    existing_path = tmp_path / "20260719T010203Z"
    existing_path.mkdir()
    run = _benchmark_run("scale-r4-t15", 1)

    def fake_run_benchmark_cases(cases, repetitions, timeout_seconds, on_result):
        assert [case.case_id for case in cases] == [
            "scale-r4-t15",
            "scale-r8-t27",
            "scale-r12-t39",
        ]
        assert repetitions == 1
        assert timeout_seconds == 30
        on_result([run])
        return [run]

    monkeypatch.setattr(algorithm_boundary_module, "datetime", FixedDatetime)
    monkeypatch.setattr(
        algorithm_boundary_module,
        "run_benchmark_cases",
        fake_run_benchmark_cases,
    )

    exit_code = main(
        [
            "--families",
            "scale",
            "--repetitions",
            "1",
            "--timeout-seconds",
            "30",
            "--output-dir",
            str(tmp_path),
        ]
    )

    result_path = tmp_path / "20260719T010203Z-2"
    payload = json.loads((result_path / "results.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["config"] == {
        "families": ["scale"],
        "repetitions": 1,
        "timeoutSeconds": 30,
        "outputDir": str(result_path.resolve()),
        "options": benchmark_options().model_dump(mode="json"),
    }
    assert set(path.name for path in result_path.iterdir()) == {
        "results.json",
        "runs.csv",
        "case-summaries.csv",
    }
    assert str(result_path.resolve()) in capsys.readouterr().out
