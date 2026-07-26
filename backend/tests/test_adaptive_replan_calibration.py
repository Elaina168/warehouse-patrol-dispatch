import csv
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.dispatch import astar, task_waypoints
from backend.app.replan_window import (
    DEFAULT_ADAPTIVE_REPLAN_POLICY,
    ReplanObservation,
)
from backend.app.schemas import Scenario
from backend.app.sessions import list_sessions
from backend.app.validation import validate_scenario
from backend.benchmarks import adaptive_runner as adaptive_runner_module
from backend.benchmarks import adaptive_reporting as adaptive_reporting_module
from backend.benchmarks import (
    adaptive_replan_calibration as cli_module,
)
from backend.benchmarks.adaptive_reporting import (
    REPLAN_OBSERVATION_FIELD_NAMES,
    RUN_FIELD_NAMES,
    VARIANT_SUMMARY_FIELD_NAMES,
    write_final_report,
    write_partial_report,
)
from backend.benchmarks.adaptive_replan_calibration import main, parse_args
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationReport,
    AdaptiveCalibrationRun,
    AdaptiveReplanRecord,
    nearest_rank,
)
from backend.benchmarks.adaptive_runner import (
    execute_adaptive_calibration_case,
    run_adaptive_calibration_cases,
    run_isolated_adaptive_calibration,
)
from backend.benchmarks import adaptive_scenarios as adaptive_scenarios_module
from backend.benchmarks.adaptive_scenarios import (
    PRESSURE_CALIBRATION_BATTERY_BUDGET,
    PRESSURE_CALIBRATION_DEADLINE,
    RUNTIME_TASK_TICKS,
    adaptive_calibration_cases,
    build_adaptive_calibration_scenario,
    build_calibration_runtime_task,
)
from backend.benchmarks.adaptive_variants import adaptive_calibration_variants
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    IsolatedExecution,
)
from backend.benchmarks.scenarios import (
    benchmark_cases,
    build_benchmark_scenario,
)


def _public_robot_positions(
    session,
) -> dict[str, tuple[int, int]]:
    return {
        state.robotId: state.position
        for state in session.robotStates
    }


def _assert_public_robot_snapshots_are_collision_free(
    snapshots: list[dict[str, tuple[int, int]]],
) -> None:
    assert snapshots
    for positions in snapshots:
        assert len(positions) == len(set(positions.values()))
    for previous, current in zip(snapshots, snapshots[1:]):
        assert set(previous) == set(current)
        robot_ids = sorted(previous)
        for first_index, first_robot_id in enumerate(robot_ids):
            for second_robot_id in robot_ids[first_index + 1:]:
                assert not (
                    previous[first_robot_id] == current[second_robot_id]
                    and previous[second_robot_id] == current[first_robot_id]
                )


def _observation(time: int, window: int, reason: str = "固定窗口") -> ReplanObservation:
    return ReplanObservation(
        time=time,
        configured_window=window,
        effective_window=window,
        reason=reason,
        released_task_count=2,
        future_task_count=3,
        active_robot_count=4,
        task_pressure_ratio=0.5,
        latency_samples_before_ms=(),
        latency_median_before_ms=None,
        latency_slow_before=False,
        replan_time_ms=10,
        latency_slow_after=False,
        path_candidate_count=1,
        selected_path_candidate_index=0,
        failed_path_candidate_count=0,
        timed_astar_call_count=4,
        timed_astar_expanded_state_count=20,
        max_timed_astar_expanded_state_count=8,
        timed_astar_exhausted_search_count=0,
        timed_astar_goal_fully_reserved_reject_count=0,
    )


def _completed_run(
    case_id: str,
    variant_id: str,
    run_index: int,
    *,
    replan_times: list[float] | None = None,
    pressure_ratios: list[float] | None = None,
    correctness_stable: bool = True,
) -> AdaptiveCalibrationRun:
    case = next(
        item for item in adaptive_calibration_cases()
        if item.case_id == case_id
    )
    variant = next(
        item for item in adaptive_calibration_variants()
        if item.variant_id == variant_id
    )
    times = replan_times or [10]
    ratios = pressure_ratios or [0.5] * len(times)
    assert len(times) == len(ratios)
    observations = tuple(
        AdaptiveReplanRecord.from_observation(
            case_id,
            variant_id,
            run_index,
            index,
            replace(
                _observation(
                    time=index * 10,
                    window=variant.assignment_replan_window,
                ),
                replan_time_ms=replan_time,
                task_pressure_ratio=pressure_ratio,
            ),
        )
        for index, (replan_time, pressure_ratio) in enumerate(
            zip(times, ratios, strict=True),
            start=1,
        )
    )
    return AdaptiveCalibrationRun(
        case_id=case_id,
        variant_id=variant_id,
        run_index=run_index,
        robot_count=case.robot_count,
        task_count=case.task_count,
        tick_target=case.tick_target,
        outcome="completed",
        error_type=None,
        error_message=None,
        correctness_stable=correctness_stable,
        released_task_count=case.task_count,
        covered_task_count=case.task_count,
        completed_task_count=case.task_count,
        coverage_rate_percent=100,
        actual_completion_rate_percent=100,
        predicted_conflict_count=0,
        active_conflict_count=0,
        safety_intervention_count=0,
        safety_stall_reached=False,
        max_consecutive_safety_intervention_count=0,
        deadline_miss_count=0,
        failure_count=0,
        total_distance=10,
        makespan=10,
        wall_clock_ms=10,
        replan_count=len(observations),
        window_change_count=0,
        replan_observations=observations,
    )


def test_adaptive_replan_record_adds_run_identity() -> None:
    record = AdaptiveReplanRecord.from_observation(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        2,
        3,
        _observation(20, 24),
    )
    payload = record.to_record()
    assert payload["caseId"] == "adaptive-low-load-r4-t17"
    assert payload["variantId"] == "fixed-24"
    assert payload["runIndex"] == 2
    assert payload["observationIndex"] == 3
    assert payload["effectiveWindow"] == 24


def test_adaptive_calibration_case_catalog_is_exact() -> None:
    cases = adaptive_calibration_cases()
    assert [
        (case.case_id, case.source_case_id, case.robot_count, case.task_count, case.tick_target)
        for case in cases
    ] == [
        ("adaptive-low-load-r4-t17", "scale-r4-t15", 4, 17, 120),
        ("adaptive-pressure-r8-t45", "density-r8-t43", 8, 45, 120),
        ("adaptive-transition-r4-t6", "bottleneck-r4-t4", 4, 6, 120),
    ]


def test_adaptive_calibration_variant_catalog_is_exact() -> None:
    variants = adaptive_calibration_variants()
    assert [
        (
            item.variant_id,
            item.assignment_replan_window,
            item.adaptive_replan_window,
        )
        for item in variants
    ] == [
        ("fixed-4", 4, False),
        ("fixed-24", 24, False),
        ("fixed-48", 48, False),
        ("adaptive-current-24", 24, True),
    ]


@pytest.mark.parametrize(
    ("variant_id", "expected_options"),
    [
        (
            "fixed-4",
            {
                "avoidConflicts": True,
                "includeDynamic": True,
                "assignmentReplanWindow": 4,
                "adaptiveReplanWindow": False,
            },
        ),
        (
            "fixed-24",
            {
                "avoidConflicts": True,
                "includeDynamic": True,
                "assignmentReplanWindow": 24,
                "adaptiveReplanWindow": False,
            },
        ),
        (
            "fixed-48",
            {
                "avoidConflicts": True,
                "includeDynamic": True,
                "assignmentReplanWindow": 48,
                "adaptiveReplanWindow": False,
            },
        ),
        (
            "adaptive-current-24",
            {
                "avoidConflicts": True,
                "includeDynamic": True,
                "assignmentReplanWindow": 24,
                "adaptiveReplanWindow": True,
            },
        ),
    ],
)
def test_adaptive_calibration_variant_options_are_exact(
    variant_id: str,
    expected_options: dict[str, object],
) -> None:
    variant = next(
        item
        for item in adaptive_calibration_variants()
        if item.variant_id == variant_id
    )

    assert variant.options().model_dump(mode="json") == expected_options


@pytest.mark.parametrize(
    "case_id",
    [
        "adaptive-low-load-r4-t17",
        "adaptive-pressure-r8-t45",
        "adaptive-transition-r4-t6",
    ],
)
def test_adaptive_calibration_scenarios_are_valid_and_deterministic(case_id: str) -> None:
    first = build_adaptive_calibration_scenario(case_id)
    second = build_adaptive_calibration_scenario(case_id)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert Scenario.model_validate(
        first.model_dump(mode="json")
    ).model_dump(mode="json") == first.model_dump(mode="json")
    assert validate_scenario(
        first,
        adaptive_calibration_variants()[0].options(),
    ) == []
    tasks = first.tasks + first.dynamic.tasks
    case = next(
        item
        for item in adaptive_calibration_cases()
        if item.case_id == case_id
    )
    assert len(tasks) == case.task_count - len(RUNTIME_TASK_TICKS)
    assert len({task.id for task in tasks}) == len(tasks)
    assert first.zones.inspection or first.zones.delivery
    for task in tasks:
        if task.deadline is not None:
            assert task.releaseTime is not None
            assert task.releaseTime <= task.deadline
        for x, y in task_waypoints(task):
            assert 0 <= x < first.width
            assert 0 <= y < first.height
            assert (x, y) not in first.obstacles


def test_low_load_case_has_no_released_task_at_t0() -> None:
    scenario = build_adaptive_calibration_scenario("adaptive-low-load-r4-t17")
    assert [task.releaseTime for task in scenario.tasks] == [
        48 + index % 3 for index in range(12)
    ]
    assert scenario.dynamic.triggerTime == 72


def test_pressure_case_normalizes_resources_without_changing_workload() -> None:
    source = build_benchmark_scenario("density-r8-t43")
    source_before = source.model_dump(mode="json")
    scenario = build_adaptive_calibration_scenario(
        "adaptive-pressure-r8-t45"
    )

    assert PRESSURE_CALIBRATION_BATTERY_BUDGET == 150
    assert PRESSURE_CALIBRATION_DEADLINE == 120
    assert [
        robot.model_dump(mode="json")
        for robot in scenario.robots
    ] == [
        robot.model_copy(
            update={
                "battery": PRESSURE_CALIBRATION_BATTERY_BUDGET,
                "batteryCapacity": (
                    PRESSURE_CALIBRATION_BATTERY_BUDGET
                ),
            }
        ).model_dump(mode="json")
        for robot in source.robots
    ]
    assert [
        task.model_dump(mode="json")
        for task in scenario.tasks
    ] == [
        task.model_copy(
            update={
                "deadline": (
                    PRESSURE_CALIBRATION_DEADLINE
                )
                if task.deadline is not None
                else None
            }
        ).model_dump(mode="json")
        for task in source.tasks
    ]
    assert scenario.dynamic.model_dump(mode="json") == (
        source.dynamic.model_dump(mode="json")
    )
    assert scenario.width == source.width
    assert scenario.height == source.height
    assert scenario.obstacles == source.obstacles
    assert scenario.zones == source.zones
    assert all(
        task.deadline == PRESSURE_CALIBRATION_DEADLINE
        for task in scenario.tasks
    )
    assert [task.releaseTime for task in scenario.tasks[:7]] == [
        0,
        1,
        2,
        3,
        4,
        5,
        0,
    ]
    assert len(scenario.tasks) == 40
    assert len(scenario.dynamic.tasks) == 3
    assert build_benchmark_scenario(
        "density-r8-t43"
    ).model_dump(mode="json") == source_before


def test_pressure_normalization_does_not_change_other_calibration_cases() -> None:
    low_load_before = build_adaptive_calibration_scenario(
        "adaptive-low-load-r4-t17"
    ).model_dump(mode="json")
    transition_before = build_adaptive_calibration_scenario(
        "adaptive-transition-r4-t6"
    ).model_dump(mode="json")

    build_adaptive_calibration_scenario("adaptive-pressure-r8-t45")

    assert build_adaptive_calibration_scenario(
        "adaptive-low-load-r4-t17"
    ).model_dump(mode="json") == low_load_before
    assert build_adaptive_calibration_scenario(
        "adaptive-transition-r4-t6"
    ).model_dump(mode="json") == transition_before


def test_transition_case_has_two_current_and_two_future_tasks() -> None:
    scenario = build_adaptive_calibration_scenario("adaptive-transition-r4-t6")
    assert {task.id: task.releaseTime for task in scenario.tasks} == {
        "D1": 0,
        "D2": 0,
        "D3": 48,
        "D4": 48,
    }


@pytest.mark.parametrize(
    ("case_id", "target_zone"),
    [
        ("adaptive-low-load-r4-t17", "inspection"),
        ("adaptive-pressure-r8-t45", "inspection"),
        ("adaptive-transition-r4-t6", "delivery"),
    ],
)
@pytest.mark.parametrize("current_time", [20, 40])
def test_calibration_runtime_task_is_exact_and_reachable(
    case_id: str,
    target_zone: str,
    current_time: int,
) -> None:
    scenario = build_adaptive_calibration_scenario(case_id)
    task = build_calibration_runtime_task(case_id, scenario, current_time)
    expected_target = getattr(scenario.zones, target_zone)[0]

    assert task.id == f"{case_id}-runtime-{current_time}"
    assert task.type == "emergency"
    assert task.releaseTime == current_time
    assert task.deadline == current_time + 40
    assert task.priority == 5
    assert task.target == expected_target
    assert expected_target not in scenario.obstacles
    assert expected_target not in {robot.start for robot in scenario.robots}
    assert task_waypoints(task) == [expected_target]
    assert any(
        astar(scenario, robot.start, expected_target)
        for robot in scenario.robots
    )


def test_calibration_runtime_task_rejects_unknown_case() -> None:
    scenario = build_adaptive_calibration_scenario("adaptive-transition-r4-t6")

    with pytest.raises(KeyError):
        build_calibration_runtime_task("unknown", scenario, 20)


@pytest.mark.parametrize("current_time", [19, 21, 39, 41])
def test_calibration_runtime_task_rejects_unapproved_tick(current_time: int) -> None:
    case_id = "adaptive-transition-r4-t6"
    scenario = build_adaptive_calibration_scenario(case_id)

    with pytest.raises(ValueError):
        build_calibration_runtime_task(case_id, scenario, current_time)


def test_calibration_catalog_filters_in_catalog_order() -> None:
    assert [
        case.case_id
        for case in adaptive_calibration_cases(
            ("adaptive-transition-r4-t6", "adaptive-low-load-r4-t17")
        )
    ] == [
        "adaptive-low-load-r4-t17",
        "adaptive-transition-r4-t6",
    ]
    assert [
        variant.variant_id
        for variant in adaptive_calibration_variants(("fixed-48", "fixed-4"))
    ] == ["fixed-4", "fixed-48"]


@pytest.mark.parametrize(
    ("function", "values"),
    [
        (adaptive_calibration_cases, ()),
        (adaptive_calibration_cases, ("unknown",)),
        (adaptive_calibration_variants, ()),
        (adaptive_calibration_variants, ("unknown",)),
    ],
)
def test_calibration_catalog_rejects_empty_or_unknown_filters(function, values) -> None:
    with pytest.raises(ValueError):
        function(values)


def test_original_algorithm_boundary_catalog_is_unchanged() -> None:
    assert [case.case_id for case in benchmark_cases()] == [
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


@pytest.mark.parametrize(
    ("calibration_case_id", "source_case_id"),
    [
        ("adaptive-low-load-r4-t17", "scale-r4-t15"),
        ("adaptive-pressure-r8-t45", "density-r8-t43"),
        ("adaptive-transition-r4-t6", "bottleneck-r4-t4"),
    ],
)
def test_calibration_transform_does_not_mutate_source_scenario(
    calibration_case_id: str,
    source_case_id: str,
) -> None:
    before = build_benchmark_scenario(source_case_id).model_dump(
        mode="json"
    )
    build_adaptive_calibration_scenario(calibration_case_id)
    after = build_benchmark_scenario(source_case_id).model_dump(
        mode="json"
    )
    assert after == before


@pytest.mark.parametrize(
    ("calibration_case_id", "source_case_id"),
    [
        ("adaptive-low-load-r4-t17", "scale-r4-t15"),
        ("adaptive-pressure-r8-t45", "density-r8-t43"),
        ("adaptive-transition-r4-t6", "bottleneck-r4-t4"),
    ],
)
def test_calibration_transform_deep_copies_persistent_source(
    monkeypatch,
    calibration_case_id: str,
    source_case_id: str,
) -> None:
    source = build_benchmark_scenario(source_case_id)
    source_before = source.model_dump(mode="json")
    monkeypatch.setattr(
        adaptive_scenarios_module,
        "build_benchmark_scenario",
        lambda _case_id: source,
    )

    first = build_adaptive_calibration_scenario(calibration_case_id)
    first.tasks[0].title = "mutated-first-task"
    first.robots[0].name = "mutated-first-robot"
    first.dynamic.triggerTime += 1
    if first.dynamic.tasks:
        first.dynamic.tasks[0].title = "mutated-first-dynamic-task"
    second = build_adaptive_calibration_scenario(calibration_case_id)

    assert source.model_dump(mode="json") == source_before
    assert first is not source
    assert second is not source
    assert first is not second
    assert first.tasks is not source.tasks
    assert second.tasks is not source.tasks
    assert first.tasks is not second.tasks
    assert first.tasks[0] is not source.tasks[0]
    assert second.tasks[0] is not source.tasks[0]
    assert first.tasks[0] is not second.tasks[0]
    assert first.robots is not source.robots
    assert second.robots is not source.robots
    assert first.robots is not second.robots
    assert first.robots[0] is not source.robots[0]
    assert second.robots[0] is not source.robots[0]
    assert first.robots[0] is not second.robots[0]
    assert first.dynamic is not source.dynamic
    assert second.dynamic is not source.dynamic
    assert first.dynamic is not second.dynamic
    assert first.dynamic.tasks is not source.dynamic.tasks
    assert second.dynamic.tasks is not source.dynamic.tasks
    assert first.dynamic.tasks is not second.dynamic.tasks
    if source.dynamic.tasks:
        assert first.dynamic.tasks[0] is not source.dynamic.tasks[0]
        assert second.dynamic.tasks[0] is not source.dynamic.tasks[0]
        assert first.dynamic.tasks[0] is not second.dynamic.tasks[0]


def test_adaptive_calibration_executes_real_online_flow() -> None:
    run = execute_adaptive_calibration_case(
        "adaptive-transition-r4-t6",
        "fixed-24",
        1,
    )
    assert run.outcome == "completed"
    assert run.tick_target == 120
    assert run.task_count == 6
    assert run.released_task_count == 6
    assert run.covered_task_count == 6
    assert run.active_conflict_count == 0
    assert run.total_distance is not None
    assert run.total_distance > 0
    assert run.replan_count == len(run.replan_observations)
    assert run.replan_count >= 3
    assert {
        record.time for record in run.replan_observations
    }.issuperset({0, 20, 40})
    assert all(
        record.reason == "固定窗口"
        and record.effective_window == 24
        for record in run.replan_observations
    )


def test_pressure_calibration_completes_by_tick_target() -> None:
    run = execute_adaptive_calibration_case(
        "adaptive-pressure-r8-t45",
        "fixed-24",
        1,
    )

    assert run.outcome == "completed"
    assert run.tick_target == 120
    assert run.released_task_count == 45
    assert run.covered_task_count == 45
    assert run.completed_task_count == 45
    assert run.coverage_rate_percent == 100
    assert run.actual_completion_rate_percent == 100
    assert run.correctness_stable is True
    assert run.predicted_conflict_count == 0
    assert run.active_conflict_count == 0
    assert run.safety_intervention_count == 0
    assert run.deadline_miss_count == 0
    assert run.failure_count == 0
    assert run.total_distance is not None
    assert run.total_distance > 0


def test_adaptive_run_uses_terminal_metric_snapshot_distance(
    monkeypatch,
) -> None:
    real_tick_session = adaptive_runner_module.tick_session

    def tick_with_distance_sentinel(session_id, request):
        result = real_tick_session(session_id, request)
        if request.currentTime != 120:
            return result
        terminal_snapshot = result.metricsHistory[-1].model_copy(
            update={"travelledDistance": 777}
        )
        plan_metrics = result.result.metrics.model_copy(
            update={"totalDistance": 0}
        )
        return result.model_copy(
            update={
                "metricsHistory": [
                    *result.metricsHistory[:-1],
                    terminal_snapshot,
                ],
                "result": result.result.model_copy(
                    update={"metrics": plan_metrics}
                ),
            }
        )

    monkeypatch.setattr(
        adaptive_runner_module,
        "tick_session",
        tick_with_distance_sentinel,
    )

    run = execute_adaptive_calibration_case(
        "adaptive-transition-r4-t6",
        "fixed-24",
        1,
    )

    assert run.total_distance == 777


def test_adaptive_run_rejects_missing_metric_history_and_cleans_session(
    monkeypatch,
) -> None:
    session_ids_before = {
        item.sessionId for item in list_sessions()
    }
    real_tick_session = adaptive_runner_module.tick_session

    def tick_without_terminal_history(session_id, request):
        result = real_tick_session(session_id, request)
        if request.currentTime == 120:
            return result.model_copy(update={"metricsHistory": []})
        return result

    monkeypatch.setattr(
        adaptive_runner_module,
        "tick_session",
        tick_without_terminal_history,
    )

    with pytest.raises(
        RuntimeError,
        match="^自适应窗口校准完成但缺少指标历史$",
    ):
        execute_adaptive_calibration_case(
            "adaptive-transition-r4-t6",
            "fixed-24",
            1,
        )

    assert {
        item.sessionId for item in list_sessions()
    } == session_ids_before


def test_adaptive_worker_enforces_safety_and_exact_online_sequence(
    monkeypatch,
) -> None:
    session_ids_before = {
        item.sessionId for item in list_sessions()
    }
    calls: list[tuple[str, int]] = []
    snapshots: list[dict[str, tuple[int, int]]] = []
    create_kwargs: list[dict[str, object]] = []
    real_create_session = adaptive_runner_module.create_session
    real_tick_session = adaptive_runner_module.tick_session
    real_add_task = adaptive_runner_module.add_task

    def record(
        call: tuple[str, int],
        result,
    ):
        calls.append(call)
        snapshots.append(_public_robot_positions(result))
        return result

    def observed_create_session(request, **kwargs):
        create_kwargs.append(kwargs)
        result = real_create_session(request, **kwargs)
        return record(("create", result.currentTime), result)

    def observed_tick_session(session_id, request):
        result = real_tick_session(session_id, request)
        assert result.currentTime == request.currentTime
        return record(("tick", request.currentTime), result)

    def observed_add_task(session_id, request):
        result = real_add_task(session_id, request)
        return record(("add", request.task.releaseTime), result)

    monkeypatch.setattr(
        adaptive_runner_module,
        "create_session",
        observed_create_session,
    )
    monkeypatch.setattr(
        adaptive_runner_module,
        "tick_session",
        observed_tick_session,
    )
    monkeypatch.setattr(
        adaptive_runner_module,
        "add_task",
        observed_add_task,
    )

    run = execute_adaptive_calibration_case(
        "adaptive-transition-r4-t6",
        "fixed-24",
        1,
    )

    expected_calls = [("create", 0)]
    for current_time in range(1, 121):
        expected_calls.append(("tick", current_time))
        if current_time in (20, 40):
            expected_calls.append(("add", current_time))
    assert calls == expected_calls
    assert len(snapshots) == len(expected_calls)
    _assert_public_robot_snapshots_are_collision_free(snapshots)
    assert len(create_kwargs) == 1
    assert create_kwargs[0]["enforce_execution_safety"] is True
    assert (
        create_kwargs[0]["adaptive_replan_policy"]
        is DEFAULT_ADAPTIVE_REPLAN_POLICY
    )
    assert run.tick_target == 120
    assert {
        item.sessionId for item in list_sessions()
    } == session_ids_before


@pytest.mark.parametrize("failure_point", ["tick", "add"])
def test_adaptive_worker_cleans_session_when_online_operation_raises(
    monkeypatch,
    failure_point: str,
) -> None:
    session_ids_before = {
        item.sessionId for item in list_sessions()
    }

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{failure_point} failed")

    monkeypatch.setattr(
        adaptive_runner_module,
        f"{failure_point}_session" if failure_point == "tick" else "add_task",
        fail,
    )

    with pytest.raises(
        RuntimeError,
        match=f"{failure_point} failed",
    ):
        execute_adaptive_calibration_case(
            "adaptive-transition-r4-t6",
            "fixed-24",
            1,
        )

    assert {
        item.sessionId for item in list_sessions()
    } == session_ids_before


@pytest.mark.parametrize(
    "snapshots",
    [
        [
            {"R1": (0, 0), "R2": (2, 0)},
            {"R1": (1, 0), "R2": (1, 0)},
        ],
        [
            {"R1": (0, 0), "R2": (1, 0)},
            {"R1": (1, 0), "R2": (0, 0)},
        ],
    ],
    ids=["vertex", "reverse-edge"],
)
def test_public_robot_snapshot_safety_rejects_controlled_conflicts(
    snapshots,
) -> None:
    with pytest.raises(AssertionError):
        _assert_public_robot_snapshots_are_collision_free(snapshots)


def test_adaptive_calibration_current_variant_uses_adaptive_reasons() -> None:
    run = execute_adaptive_calibration_case(
        "adaptive-low-load-r4-t17",
        "adaptive-current-24",
        1,
    )
    assert run.outcome == "completed"
    assert run.replan_observations[0].effective_window == 48
    assert run.replan_observations[0].reason == "当前负载较低且存在远期任务，扩大窗口"


def test_adaptive_batch_uses_case_variant_run_order_and_copies_snapshots(
    monkeypatch,
) -> None:
    cases = adaptive_calibration_cases()[:2]
    variants = adaptive_calibration_variants()[:2]
    calls = []
    snapshots = []

    def fake_isolated(case, variant, run_index, timeout_seconds):
        calls.append((case.case_id, variant.variant_id, run_index, timeout_seconds))
        return _completed_run(
            case.case_id,
            variant.variant_id,
            run_index,
        )

    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_adaptive_calibration",
        fake_isolated,
    )
    runs = run_adaptive_calibration_cases(
        cases,
        variants,
        repetitions=2,
        timeout_seconds=3.5,
        on_result=snapshots.append,
    )
    assert calls == [
        (cases[0].case_id, variants[0].variant_id, 1, 3.5),
        (cases[0].case_id, variants[0].variant_id, 2, 3.5),
        (cases[0].case_id, variants[1].variant_id, 1, 3.5),
        (cases[0].case_id, variants[1].variant_id, 2, 3.5),
        (cases[1].case_id, variants[0].variant_id, 1, 3.5),
        (cases[1].case_id, variants[0].variant_id, 2, 3.5),
        (cases[1].case_id, variants[1].variant_id, 1, 3.5),
        (cases[1].case_id, variants[1].variant_id, 2, 3.5),
    ]
    assert len(runs) == 8
    assert [len(snapshot) for snapshot in snapshots] == list(range(1, 9))
    assert len({id(snapshot) for snapshot in snapshots}) == 8


@pytest.mark.parametrize(
    ("execution", "expected_outcome", "expected_error_type"),
    [
        (
            IsolatedExecution(
                outcome="timeout",
                value=None,
                error_type="TimeoutError",
                error_message=None,
                wall_clock_ms=50,
            ),
            "timeout",
            "TimeoutError",
        ),
        (
            IsolatedExecution(
                outcome="error",
                value=None,
                error_type="RuntimeError",
                error_message="worker failed",
                wall_clock_ms=20,
            ),
            "error",
            "RuntimeError",
        ),
        (
            IsolatedExecution(
                outcome="completed",
                value="invalid",
                error_type=None,
                error_message=None,
                wall_clock_ms=10,
            ),
            "error",
            "ChildProcessError",
        ),
    ],
)
def test_isolated_adaptive_run_translates_process_outcomes(
    monkeypatch,
    execution,
    expected_outcome,
    expected_error_type,
) -> None:
    case = adaptive_calibration_cases()[0]
    variant = adaptive_calibration_variants()[0]
    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_process",
        lambda worker, worker_args, timeout_seconds: execution,
    )
    run = run_isolated_adaptive_calibration(case, variant, 1, 5)
    assert run.outcome == expected_outcome
    assert run.error_type == expected_error_type
    assert run.correctness_stable is False
    assert run.released_task_count is None
    assert run.replan_observations == ()


def test_adaptive_batch_continues_after_per_run_error(monkeypatch) -> None:
    case = adaptive_calibration_cases()[0]
    variant = adaptive_calibration_variants()[0]
    calls = 0

    def fake_isolated(case, variant, run_index, timeout_seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AdaptiveCalibrationRun.error(
                case,
                variant,
                run_index,
                "RuntimeError",
                "first failed",
                1,
            )
        return _completed_run(
            case.case_id,
            variant.variant_id,
            run_index,
        )

    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_adaptive_calibration",
        fake_isolated,
    )
    runs = run_adaptive_calibration_cases(
        (case,),
        (variant,),
        repetitions=2,
        timeout_seconds=5,
    )
    assert [run.outcome for run in runs] == ["error", "completed"]


def test_adaptive_batch_propagates_infrastructure_failure(monkeypatch) -> None:
    case = adaptive_calibration_cases()[0]
    variant = adaptive_calibration_variants()[0]

    def fail(*args, **kwargs):
        raise BenchmarkInfrastructureError("worker cleanup failed")

    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_adaptive_calibration",
        fail,
    )
    with pytest.raises(
        BenchmarkInfrastructureError,
        match="worker cleanup failed",
    ):
        run_adaptive_calibration_cases(
            (case,),
            (variant,),
            repetitions=1,
            timeout_seconds=5,
        )


def test_adaptive_percentiles_use_nearest_rank() -> None:
    values = [10, 20, 30, 40]
    assert nearest_rank(values, 0.50) == 20
    assert nearest_rank(values, 0.75) == 30
    assert nearest_rank(values, 0.95) == 40
    assert nearest_rank([], 0.50) is None


def test_variant_replan_summary_weights_each_run_once() -> None:
    first = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=1,
        replan_times=[10, 20, 30],
    )
    second = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=2,
        replan_times=[100],
    )
    report = AdaptiveCalibrationReport.create({}, [first, second])
    summary = report.variant_summaries[0]
    assert summary.median_run_replan_time_ms == 60
    assert summary.p95_run_replan_time_ms == 100


def test_candidate_envelope_uses_only_stable_fixed_24_observations() -> None:
    runs = []
    for index, case in enumerate(adaptive_calibration_cases(), start=1):
        runs.append(
            _completed_run(
                case_id=case.case_id,
                variant_id="fixed-24",
                run_index=1,
                replan_times=[10 * index, 20 * index, 30 * index],
                pressure_ratios=[0.5 * index, 1.0 * index, 1.5 * index],
                correctness_stable=True,
            )
        )
    runs.append(
        _completed_run(
            case_id=adaptive_calibration_cases()[0].case_id,
            variant_id="fixed-4",
            run_index=1,
            replan_times=[777, 777, 777],
            pressure_ratios=[77, 77, 77],
            correctness_stable=True,
        )
    )
    runs.append(
        _completed_run(
            case_id=adaptive_calibration_cases()[0].case_id,
            variant_id="fixed-48",
            run_index=1,
            replan_times=[666, 666, 666],
            pressure_ratios=[66, 66, 66],
            correctness_stable=True,
        )
    )
    runs.append(
        _completed_run(
            case_id=adaptive_calibration_cases()[0].case_id,
            variant_id="adaptive-current-24",
            run_index=1,
            replan_times=[999, 999, 999],
            pressure_ratios=[99, 99, 99],
            correctness_stable=True,
        )
    )
    runs.append(
        _completed_run(
            case_id=adaptive_calibration_cases()[0].case_id,
            variant_id="fixed-24",
            run_index=2,
            replan_times=[888, 888, 888],
            pressure_ratios=[88, 88, 88],
            correctness_stable=False,
        )
    )
    case = adaptive_calibration_cases()[0]
    variant = next(
        item
        for item in adaptive_calibration_variants()
        if item.variant_id == "fixed-24"
    )
    timeout_observations = _completed_run(
        case.case_id,
        variant.variant_id,
        3,
        replan_times=[555, 555, 555],
        pressure_ratios=[55, 55, 55],
    ).replan_observations
    error_observations = _completed_run(
        case.case_id,
        variant.variant_id,
        4,
        replan_times=[444, 444, 444],
        pressure_ratios=[44, 44, 44],
    ).replan_observations
    runs.append(
        replace(
            AdaptiveCalibrationRun.timeout(case, variant, 3, 1),
            replan_observations=timeout_observations,
        )
    )
    runs.append(
        replace(
            AdaptiveCalibrationRun.error(
                case,
                variant,
                4,
                "RuntimeError",
                "failed",
                1,
            ),
            replan_observations=error_observations,
        )
    )
    report = AdaptiveCalibrationReport.create({}, runs)
    assert report.candidate_envelope_available is True
    assert report.observation_distribution.latency_ms.sample_count == 9
    assert report.observation_distribution.latency_ms.p50 == 30
    assert report.observation_distribution.latency_ms.p75 == 60
    assert report.observation_distribution.latency_ms.p95 == 90
    pressure = report.observation_distribution.task_pressure_ratio
    assert (pressure.p50, pressure.p75, pressure.p95) == (1.5, 3, 4.5)
    assert report.candidate_envelope is not None
    assert report.candidate_envelope.to_record() == {
        "slowExitThresholdMs": {"min": 30, "max": 60},
        "slowEnterThresholdMs": {"min": 60, "max": 90},
        "taskPressureMultiplier": {"min": 1.5, "max": 3},
    }


def test_candidate_envelope_is_unavailable_when_one_case_has_fewer_than_three_samples() -> None:
    cases = adaptive_calibration_cases()
    runs = [
        _completed_run(
            case_id=case.case_id,
            variant_id="fixed-24",
            run_index=1,
            replan_times=(
                [10, 20, 30]
                if case.case_id != cases[-1].case_id
                else [10, 20]
            ),
            pressure_ratios=(
                [1, 2, 3]
                if case.case_id != cases[-1].case_id
                else [1, 2]
            ),
            correctness_stable=True,
        )
        for case in cases
    ]
    report = AdaptiveCalibrationReport.create({}, runs)
    assert report.candidate_envelope_available is False
    assert report.candidate_envelope is None
    assert report.observation_distribution.latency_ms.sample_count == 8


def test_candidate_distribution_is_explicitly_empty_without_stable_fixed_24() -> None:
    run = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=1,
        replan_times=[10, 20, 30],
        correctness_stable=False,
    )
    report = AdaptiveCalibrationReport.create({}, [run])
    latency = report.observation_distribution.latency_ms
    pressure = report.observation_distribution.task_pressure_ratio
    assert (latency.p50, latency.p75, latency.p95) == (
        None,
        None,
        None,
    )
    assert (pressure.p50, pressure.p75, pressure.p95) == (
        None,
        None,
        None,
    )
    assert latency.sample_count == 0
    assert pressure.sample_count == 0
    assert report.candidate_envelope_available is False
    assert report.candidate_envelope is None


def test_adaptive_report_writes_utf8_json_and_three_csv_files(tmp_path) -> None:
    run = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=1,
        replan_times=[10, 20, 30],
        pressure_ratios=[0.5, 1, 1.5],
    )
    report = AdaptiveCalibrationReport.create({"repetitions": 1}, [run])
    write_final_report(tmp_path, report)

    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["runs"][0]["caseId"] == "adaptive-low-load-r4-t17"
    assert payload["runs"][0]["replanObservations"][0]["reason"] == "固定窗口"
    expected_headers = {
        "runs.csv": RUN_FIELD_NAMES,
        "replan-observations.csv": REPLAN_OBSERVATION_FIELD_NAMES,
        "variant-summaries.csv": VARIANT_SUMMARY_FIELD_NAMES,
    }
    for file_name, expected_header in expected_headers.items():
        with (tmp_path / file_name).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            assert tuple(reader.fieldnames or ()) == expected_header
            assert list(reader)


def test_adaptive_partial_is_replaced_and_removed_after_final(tmp_path) -> None:
    first = _completed_run("adaptive-low-load-r4-t17", "fixed-24", 1)
    second = _completed_run("adaptive-low-load-r4-t17", "fixed-24", 2)
    write_partial_report(tmp_path, AdaptiveCalibrationReport.create({}, [first]))
    write_partial_report(tmp_path, AdaptiveCalibrationReport.create({}, [first, second]))
    partial_path = tmp_path / "results.partial.json"
    payload = json.loads(partial_path.read_text(encoding="utf-8"))
    assert [item["runIndex"] for item in payload["runs"]] == [1, 2]
    assert not list(tmp_path.glob("*.tmp"))
    write_final_report(
        tmp_path,
        AdaptiveCalibrationReport.create({}, [first, second]),
    )
    assert partial_path.exists() is False
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.suffix in {".tmp", ".backup"}
    ]

    write_partial_report(
        tmp_path,
        AdaptiveCalibrationReport.create({}, [first]),
    )
    write_final_report(
        tmp_path,
        AdaptiveCalibrationReport.create({}, [first]),
    )
    payload = json.loads(
        (tmp_path / "results.json").read_text(encoding="utf-8")
    )
    assert [item["runIndex"] for item in payload["runs"]] == [1]
    assert partial_path.exists() is False
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.suffix in {".tmp", ".backup"}
    ]


def test_adaptive_final_staging_failure_creates_no_final_files(
    tmp_path,
    monkeypatch,
) -> None:
    run = _completed_run(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        1,
    )
    report = AdaptiveCalibrationReport.create({}, [run])
    write_partial_report(tmp_path, report)
    partial_path = tmp_path / "results.partial.json"
    partial_content = partial_path.read_bytes()

    def fail_csv(*_args, **_kwargs):
        raise OSError("csv failed")

    monkeypatch.setattr(
        adaptive_reporting_module.csv,
        "DictWriter",
        fail_csv,
    )
    expected_target = (tmp_path / "runs.csv").resolve()
    with pytest.raises(
        OSError,
        match=re.escape(str(expected_target)),
    ):
        write_final_report(tmp_path, report)
    assert partial_path.read_bytes() == partial_content
    assert not any(
        (tmp_path / file_name).exists()
        for file_name in (
            "results.json",
            "runs.csv",
            "replan-observations.csv",
            "variant-summaries.csv",
        )
    )
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.suffix in {".tmp", ".backup"}
    ]


def test_adaptive_final_staging_failure_preserves_existing_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    run = _completed_run(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        1,
    )
    report = AdaptiveCalibrationReport.create({}, [run])
    write_partial_report(tmp_path, report)
    partial_path = tmp_path / "results.partial.json"
    partial_content = partial_path.read_bytes()
    original_files = {
        "results.json": b"old results",
        "runs.csv": b"old runs",
        "replan-observations.csv": b"old observations",
        "variant-summaries.csv": b"old summaries",
    }
    for file_name, content in original_files.items():
        (tmp_path / file_name).write_bytes(content)

    def fail_csv(*_args, **_kwargs):
        raise OSError("csv failed")

    monkeypatch.setattr(
        adaptive_reporting_module.csv,
        "DictWriter",
        fail_csv,
    )
    with pytest.raises(OSError, match="csv failed"):
        write_final_report(tmp_path, report)

    assert partial_path.read_bytes() == partial_content
    assert {
        file_name: (tmp_path / file_name).read_bytes()
        for file_name in original_files
    } == original_files
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.suffix in {".tmp", ".backup"}
    ]


def test_adaptive_final_publish_failure_rolls_back_existing_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    run = _completed_run(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        1,
    )
    report = AdaptiveCalibrationReport.create({}, [run])
    write_partial_report(tmp_path, report)
    partial_path = tmp_path / "results.partial.json"
    partial_content = partial_path.read_bytes()
    original_files = {
        "results.json": b"old results",
        "runs.csv": b"old runs",
        "replan-observations.csv": b"old observations",
        "variant-summaries.csv": b"old summaries",
    }
    for file_name, content in original_files.items():
        (tmp_path / file_name).write_bytes(content)

    real_replace = Path.replace
    failed = False

    def fail_second_csv_publish(source, target):
        nonlocal failed
        target_path = Path(target)
        if (
            not failed
            and source.suffix == ".tmp"
            and target_path.name == "replan-observations.csv"
        ):
            failed = True
            raise OSError("replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second_csv_publish)
    expected_target = (tmp_path / "replan-observations.csv").resolve()
    with pytest.raises(OSError) as exc_info:
        write_final_report(tmp_path, report)

    assert failed is True
    assert "replace failed" in str(exc_info.value)
    assert str(expected_target) in str(exc_info.value)
    assert partial_path.read_bytes() == partial_content
    assert {
        file_name: (tmp_path / file_name).read_bytes()
        for file_name in original_files
    } == original_files
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.suffix in {".tmp", ".backup"}
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["--cases", ","],
        ["--cases", "unknown"],
        ["--variants", ","],
        ["--variants", "unknown"],
        ["--repetitions", "0"],
        ["--repetitions", "-1"],
        ["--timeout-seconds", "0"],
        ["--timeout-seconds", "-1"],
        ["--timeout-seconds", "nan"],
        ["--timeout-seconds", "inf"],
        ["--timeout-seconds", "-inf"],
    ],
)
def test_adaptive_cli_rejects_invalid_config_before_output(tmp_path, args) -> None:
    assert main([*args, "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []


def test_adaptive_cli_parses_trimmed_case_and_variant_lists() -> None:
    args = parse_args(
        [
            "--cases",
            " adaptive-low-load-r4-t17, adaptive-transition-r4-t6 ",
            "--variants",
            " fixed-4, adaptive-current-24 ",
        ]
    )
    assert args.cases == (
        "adaptive-low-load-r4-t17",
        "adaptive-transition-r4-t6",
    )
    assert args.variants == ("fixed-4", "adaptive-current-24")


def test_adaptive_cli_same_second_directories_do_not_overwrite(
    tmp_path,
    monkeypatch,
) -> None:
    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return datetime(2026, 7, 26, tzinfo=timezone.utc)

    monkeypatch.setattr(cli_module, "datetime", FixedDateTime)
    first = cli_module._create_result_directory(str(tmp_path))
    second = cli_module._create_result_directory(str(tmp_path))
    assert first.name == "20260726T000000Z"
    assert second.name == "20260726T000000Z-2"


def test_adaptive_cli_success_writes_exact_config(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    result_path = (tmp_path / "20260726T000000Z").resolve()
    result_path.mkdir()
    completed = _completed_run(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        1,
    )

    def fake_run(
        cases,
        variants,
        repetitions,
        timeout_seconds,
        on_result=None,
    ):
        assert [case.case_id for case in cases] == [
            "adaptive-low-load-r4-t17"
        ]
        assert [variant.variant_id for variant in variants] == ["fixed-24"]
        assert repetitions == 1
        assert timeout_seconds == 30
        runs = [completed]
        if on_result is not None:
            on_result(list(runs))
        return runs

    monkeypatch.setattr(
        cli_module,
        "_create_result_directory",
        lambda output_dir: result_path,
    )
    monkeypatch.setattr(
        cli_module,
        "run_adaptive_calibration_cases",
        fake_run,
    )

    assert main(
        [
            "--cases",
            "adaptive-low-load-r4-t17",
            "--variants",
            "fixed-24",
            "--repetitions",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
    ) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(result_path)
    assert captured.err == ""

    payload = json.loads(
        (result_path / "results.json").read_text(encoding="utf-8")
    )
    assert payload["config"] == {
        "caseIds": ["adaptive-low-load-r4-t17"],
        "variantIds": ["fixed-24"],
        "repetitions": 1,
        "timeoutSeconds": 30,
        "tickTarget": 120,
        "runtimeTaskTicks": [20, 40],
        "outputDir": str(result_path),
        "defaultPolicy": {
            "slowEnterThresholdMs": 60,
            "slowExitThresholdMs": 40,
            "taskPressureMultiplier": 2,
        },
    }


def test_adaptive_cli_writes_final_report_for_ordinary_error_run(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    result_path = (tmp_path / "20260726T000000Z").resolve()
    result_path.mkdir()
    case = adaptive_calibration_cases()[0]
    variant = adaptive_calibration_variants()[0]
    error_run = AdaptiveCalibrationRun.error(
        case,
        variant,
        1,
        "RuntimeError",
        "ordinary failed",
        12,
    )

    def fake_run(
        cases,
        variants,
        repetitions,
        timeout_seconds,
        on_result=None,
    ):
        runs = [error_run]
        if on_result is not None:
            on_result(list(runs))
        return runs

    monkeypatch.setattr(
        cli_module,
        "_create_result_directory",
        lambda output_dir: result_path,
    )
    monkeypatch.setattr(
        cli_module,
        "run_adaptive_calibration_cases",
        fake_run,
    )

    assert main(["--output-dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(result_path)
    assert captured.err == ""
    payload = json.loads(
        (result_path / "results.json").read_text(encoding="utf-8")
    )
    assert payload["runs"][0]["outcome"] == "error"
    assert payload["runs"][0]["errorType"] == "RuntimeError"
    assert payload["runs"][0]["errorMessage"] == "ordinary failed"
    assert (result_path / "results.partial.json").exists() is False


def test_adaptive_cli_infrastructure_failure_writes_no_false_final(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    result_path = (tmp_path / "20260726T000000Z").resolve()
    result_path.mkdir()

    def fail(*_args, **_kwargs):
        raise BenchmarkInfrastructureError("worker cleanup failed")

    monkeypatch.setattr(
        cli_module,
        "_create_result_directory",
        lambda output_dir: result_path,
    )
    monkeypatch.setattr(
        cli_module,
        "run_adaptive_calibration_cases",
        fail,
    )

    assert main(["--output-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == (
        "自适应窗口校准失败: worker cleanup failed"
    )
    assert not any(
        (result_path / file_name).exists()
        for file_name in (
            "results.json",
            "runs.csv",
            "replan-observations.csv",
            "variant-summaries.csv",
        )
    )
