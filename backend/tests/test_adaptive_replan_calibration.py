import pytest

from backend.app.dispatch import astar, task_waypoints
from backend.app.schemas import Scenario
from backend.app.validation import validate_scenario
from backend.benchmarks import adaptive_scenarios as adaptive_scenarios_module
from backend.benchmarks.adaptive_scenarios import (
    RUNTIME_TASK_TICKS,
    adaptive_calibration_cases,
    build_adaptive_calibration_scenario,
    build_calibration_runtime_task,
)
from backend.benchmarks.adaptive_variants import adaptive_calibration_variants
from backend.benchmarks.scenarios import (
    benchmark_cases,
    build_benchmark_scenario,
)


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
