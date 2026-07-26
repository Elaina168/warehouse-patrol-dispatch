from dataclasses import replace

import pytest

from backend.app.dispatch import astar, task_waypoints
from backend.app.replan_window import ReplanObservation
from backend.app.schemas import Scenario
from backend.app.validation import validate_scenario
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationRun,
    AdaptiveReplanRecord,
)
from backend.benchmarks.adaptive_runner import (
    execute_adaptive_calibration_case,
    run_adaptive_calibration_cases,
    run_isolated_adaptive_calibration,
)
from backend.benchmarks import adaptive_scenarios as adaptive_scenarios_module
from backend.benchmarks.adaptive_scenarios import (
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


def test_pressure_case_preserves_seeded_release_schedule() -> None:
    scenario = build_adaptive_calibration_scenario("adaptive-pressure-r8-t45")
    assert len(scenario.tasks) == 40
    assert len(scenario.dynamic.tasks) == 3
    assert [task.releaseTime for task in scenario.tasks[:7]] == [0, 1, 2, 3, 4, 5, 0]


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
