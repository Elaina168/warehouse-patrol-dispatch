from dataclasses import dataclass
from typing import Literal

from backend.app.schemas import DispatchOptions, Scenario
from backend.app.seeded_scenarios import seeded_pressure_scenario

BenchmarkFamily = Literal["scale", "density", "bottleneck"]
BenchmarkMode = Literal["direct", "online"]


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    family: BenchmarkFamily
    mode: BenchmarkMode
    seed: int | None
    robot_count: int
    task_count: int
    dynamic_task_count: int
    tick_target: int | None


_CASES = (
    BenchmarkCase("scale-r4-t15", "scale", "direct", None, 4, 15, 3, None),
    BenchmarkCase("scale-r8-t27", "scale", "direct", None, 8, 27, 3, None),
    BenchmarkCase("scale-r12-t39", "scale", "direct", None, 12, 39, 3, None),
    BenchmarkCase("density-r8-t31", "density", "direct", 43, 8, 31, 3, None),
    BenchmarkCase("density-r8-t43", "density", "direct", 43, 8, 43, 3, None),
    BenchmarkCase("density-r8-t55", "density", "direct", 43, 8, 55, 3, None),
    BenchmarkCase("bottleneck-r4-t4", "bottleneck", "online", None, 4, 4, 0, 120),
    BenchmarkCase("bottleneck-r6-t6", "bottleneck", "online", None, 6, 6, 0, 120),
    BenchmarkCase("bottleneck-r8-t8", "bottleneck", "online", None, 8, 8, 0, 120),
)


def benchmark_options() -> DispatchOptions:
    return DispatchOptions(
        avoidConflicts=True,
        includeDynamic=True,
        assignmentReplanWindow=120,
        adaptiveReplanWindow=False,
    )


def benchmark_cases(families: tuple[str, ...] | None = None) -> tuple[BenchmarkCase, ...]:
    if families is None:
        return _CASES
    unknown = sorted(set(families) - {"scale", "density", "bottleneck"})
    if unknown:
        raise ValueError(f"未知基准场景族: {', '.join(unknown)}")
    return tuple(case for case in _CASES if case.family in families)


def _scale_scenario(case: BenchmarkCase) -> Scenario:
    robots = [
        {
            "id": f"R{row + 1}",
            "name": f"R{row + 1}",
            "start": [0, row],
            "battery": 100,
            "load": 2,
        }
        for row in range(case.robot_count)
    ]
    tasks = []
    inspection_cells = []
    for row in range(case.robot_count):
        for column_index, x in enumerate((4, 7, 10), start=1):
            task_id = f"I{row * 3 + column_index}"
            target = [x, row]
            inspection_cells.append(target)
            tasks.append(
                {
                    "id": task_id,
                    "type": "inspection",
                    "title": task_id,
                    "priority": 1 + (row + column_index) % 5,
                    "releaseTime": column_index - 1,
                    "deadline": 120 + row,
                    "targets": [target],
                }
            )
    dynamic_tasks = [
        {
            "id": f"E{row + 1}",
            "type": "emergency",
            "title": f"E{row + 1}",
            "priority": 5,
            "target": [12, row],
        }
        for row in range(3)
    ]
    inspection_cells.extend(task["target"] for task in dynamic_tasks)
    return Scenario.model_validate(
        {
            "id": case.case_id,
            "name": case.case_id,
            "description": "确定性规模基准场景",
            "width": 14,
            "height": case.robot_count,
            "obstacles": [],
            "zones": {
                "warehouse": [robot["start"] for robot in robots],
                "inspection": inspection_cells,
                "delivery": [],
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 8,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": dynamic_tasks,
            },
        }
    )


def _bottleneck_scenario(case: BenchmarkCase) -> Scenario:
    row_order = (1, 7, 2, 6, 3, 5, 0, 8)
    robots = []
    tasks = []
    pickup_cells = []
    dropoff_cells = []
    for index, y in enumerate(row_order[: case.robot_count]):
        starts_left = index % 2 == 0
        start = [0 if starts_left else 12, y]
        pickup = [1 if starts_left else 11, y]
        dropoff = [11 if starts_left else 1, y]
        robot_id = f"R{index + 1}"
        task_id = f"D{index + 1}"
        robots.append({"id": robot_id, "name": robot_id, "start": start, "battery": 100, "load": 2})
        tasks.append(
            {
                "id": task_id,
                "type": "delivery",
                "title": task_id,
                "priority": 2 + index % 4,
                "releaseTime": index % 4,
                "deadline": 200,
                "pickup": pickup,
                "dropoff": dropoff,
                "demand": 1,
            }
        )
        pickup_cells.append(pickup)
        dropoff_cells.append(dropoff)
    return Scenario.model_validate(
        {
            "id": case.case_id,
            "name": case.case_id,
            "description": "确定性瓶颈基准场景",
            "width": 13,
            "height": 9,
            "obstacles": [[6, y] for y in range(9) if y not in {3, 4, 5}],
            "zones": {
                "warehouse": pickup_cells,
                "inspection": [],
                "delivery": dropoff_cells,
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 8,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )


def build_benchmark_scenario(case_id: str) -> Scenario:
    case = next((item for item in _CASES if item.case_id == case_id), None)
    if case is None:
        raise KeyError(f"未知基准场景: {case_id}")
    if case.family == "scale":
        return _scale_scenario(case)
    if case.family == "density":
        base_task_count = {
            "density-r8-t31": 28,
            "density-r8-t43": 40,
            "density-r8-t55": 52,
        }[case.case_id]
        return seeded_pressure_scenario(case.case_id, 43, 8, base_task_count)
    return _bottleneck_scenario(case)
