import pytest

from backend.app.dispatch import task_waypoints
from backend.app.schemas import Scenario
from backend.app.validation import validate_scenario
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


@pytest.mark.parametrize("current_time", [20, 40])
def test_calibration_runtime_task_uses_exact_id_and_valid_target(current_time: int) -> None:
    case_id = "adaptive-transition-r4-t6"
    scenario = build_adaptive_calibration_scenario(case_id)
    task = build_calibration_runtime_task(case_id, scenario, current_time)
    assert task.id == f"{case_id}-runtime-{current_time}"
    assert task.type == "emergency"
    assert task.releaseTime == current_time
    assert task.deadline == current_time + 40
    assert task.priority == 5
    assert task.target == scenario.zones.delivery[0]


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
