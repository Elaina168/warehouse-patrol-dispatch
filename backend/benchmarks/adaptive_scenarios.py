from dataclasses import dataclass

from backend.app.schemas import Scenario, Task
from backend.benchmarks.scenarios import build_benchmark_scenario


RUNTIME_TASK_TICKS = (20, 40)
PRESSURE_CALIBRATION_BATTERY_BUDGET = 150
PRESSURE_CALIBRATION_DEADLINE = 120


@dataclass(frozen=True, slots=True)
class AdaptiveCalibrationCase:
    case_id: str
    source_case_id: str
    robot_count: int
    task_count: int
    tick_target: int


_CASES = (
    AdaptiveCalibrationCase(
        "adaptive-low-load-r4-t17",
        "scale-r4-t15",
        4,
        17,
        120,
    ),
    AdaptiveCalibrationCase(
        "adaptive-pressure-r8-t45",
        "density-r8-t43",
        8,
        45,
        120,
    ),
    AdaptiveCalibrationCase(
        "adaptive-transition-r4-t6",
        "bottleneck-r4-t4",
        4,
        6,
        120,
    ),
)


def adaptive_calibration_cases(
    case_ids: tuple[str, ...] | None = None,
) -> tuple[AdaptiveCalibrationCase, ...]:
    if case_ids is None:
        return _CASES
    if not case_ids:
        raise ValueError("校准案例必须至少包含一项")
    known = {case.case_id for case in _CASES}
    unknown = sorted(set(case_ids) - known)
    if unknown:
        raise ValueError(f"未知校准案例: {', '.join(unknown)}")
    return tuple(case for case in _CASES if case.case_id in case_ids)


def build_adaptive_calibration_scenario(case_id: str) -> Scenario:
    case = next(
        (item for item in _CASES if item.case_id == case_id),
        None,
    )
    if case is None:
        raise KeyError(f"未知校准案例: {case_id}")
    scenario = build_benchmark_scenario(
        case.source_case_id
    ).model_copy(deep=True)
    scenario.id = case.case_id
    scenario.name = case.case_id
    scenario.description = f"自适应重规划窗口校准场景：{case.case_id}"

    if case.case_id == "adaptive-low-load-r4-t17":
        for index, task in enumerate(scenario.tasks):
            task.releaseTime = 48 + index % 3
        scenario.dynamic.triggerTime = 72
    elif case.case_id == "adaptive-pressure-r8-t45":
        for robot in scenario.robots:
            robot.battery = PRESSURE_CALIBRATION_BATTERY_BUDGET
            robot.batteryCapacity = (
                PRESSURE_CALIBRATION_BATTERY_BUDGET
            )
        for task in scenario.tasks:
            if task.deadline is not None:
                task.deadline = (
                    PRESSURE_CALIBRATION_DEADLINE
                )
    elif case.case_id == "adaptive-transition-r4-t6":
        release_times = {
            "D1": 0,
            "D2": 0,
            "D3": 48,
            "D4": 48,
        }
        for task in scenario.tasks:
            task.releaseTime = release_times[task.id]

    return Scenario.model_validate(
        scenario.model_dump(mode="json")
    )


def build_calibration_runtime_task(
    case_id: str,
    scenario: Scenario,
    current_time: int,
) -> Task:
    if case_id not in {case.case_id for case in _CASES}:
        raise KeyError(f"未知校准案例: {case_id}")
    if current_time not in RUNTIME_TASK_TICKS:
        raise ValueError(
            f"校准运行时任务只允许在 {RUNTIME_TASK_TICKS} 插入"
        )
    if scenario.zones.inspection:
        target = scenario.zones.inspection[0]
    elif scenario.zones.delivery:
        target = scenario.zones.delivery[0]
    else:
        raise ValueError(f"校准案例缺少运行时任务目标: {case_id}")
    task_id = f"{case_id}-runtime-{current_time}"
    return Task(
        id=task_id,
        type="emergency",
        title=task_id,
        priority=5,
        releaseTime=current_time,
        deadline=current_time + 40,
        target=target,
    )
