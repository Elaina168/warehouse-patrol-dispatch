from __future__ import annotations

from math import isfinite

from backend.app.dispatch import astar, cell_key, is_inside, task_waypoints
from backend.app.schemas import Cell, DispatchOptions, Robot, Scenario, Task


def validate_scenario(scenario: Scenario, options: DispatchOptions) -> list[str]:
    errors: list[str] = []
    blocked_cells = [*scenario.obstacles]
    if options.includeDynamic:
        blocked_cells.extend(scenario.dynamic.blockedCells)

    errors.extend(_duplicate_errors("机器人 ID", [robot.id for robot in scenario.robots]))
    errors.extend(_duplicate_errors("动态故障机器人 ID", scenario.dynamic.failedRobots))
    errors.extend(_dynamic_failed_robot_errors(scenario))
    errors.extend(_duplicate_errors("任务 ID", [task.id for task in _all_tasks(scenario)]))
    errors.extend(_duplicate_errors("障碍/封锁坐标", [cell_key(cell) for cell in blocked_cells]))
    errors.extend(_cell_bounds_errors(scenario))
    errors.extend(_blocked_point_errors(scenario, options))
    errors.extend(_reachability_errors(scenario, options))

    return errors


def _duplicate_errors(label: str, values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return [f"{label} 重复：{value}" for value in duplicates]


def _dynamic_failed_robot_errors(scenario: Scenario) -> list[str]:
    robot_ids = {robot.id for robot in scenario.robots}
    return [
        f"动态故障机器人不存在：{robot_id}"
        for robot_id in scenario.dynamic.failedRobots
        if robot_id not in robot_ids
    ]


def _cell_bounds_errors(scenario: Scenario) -> list[str]:
    errors: list[str] = []
    for label, cell in _named_cells(scenario):
        if not is_inside(cell, scenario):
            errors.append(f"{label} 坐标超出地图范围：({cell[0]}, {cell[1]})")
    return errors


def _blocked_point_errors(scenario: Scenario, options: DispatchOptions) -> list[str]:
    errors: list[str] = []
    base_blocked = {cell_key(cell) for cell in scenario.obstacles}
    if options.includeDynamic and scenario.dynamic.triggerTime == 0:
        base_blocked |= {cell_key(cell) for cell in scenario.dynamic.blockedCells}
    dynamic_blocked = {cell_key(cell) for cell in scenario.obstacles} | (
        {cell_key(cell) for cell in scenario.dynamic.blockedCells}
        if options.includeDynamic
        else set()
    )
    for label, cell in _base_required_walkable_cells(scenario):
        if cell_key(cell) in base_blocked:
            errors.append(f"{label} 位于障碍或封锁单元：({cell[0]}, {cell[1]})")
    for label, cell in _dynamic_required_walkable_cells(scenario):
        if cell_key(cell) in dynamic_blocked:
            errors.append(f"{label} 位于障碍或封锁单元：({cell[0]}, {cell[1]})")
    return errors


def _reachability_errors(scenario: Scenario, options: DispatchOptions) -> list[str]:
    errors: list[str] = []

    base_extra_blocked = scenario.dynamic.blockedCells if options.includeDynamic and scenario.dynamic.triggerTime == 0 else []
    base_unavailable_robot_ids = (
        set(scenario.dynamic.failedRobots)
        if options.includeDynamic and scenario.dynamic.triggerTime == 0
        else set()
    )
    errors.extend(_task_reachability_errors(scenario, scenario.tasks, base_extra_blocked, base_unavailable_robot_ids))
    if options.includeDynamic:
        errors.extend(
            _task_reachability_errors(
                scenario,
                scenario.dynamic.tasks,
                scenario.dynamic.blockedCells,
                set(scenario.dynamic.failedRobots),
            )
        )

    return errors


def _task_reachability_errors(
    scenario: Scenario,
    tasks: list[Task],
    extra_blocked: list[Cell],
    unavailable_robot_ids: set[str],
) -> list[str]:
    active_robots = [robot for robot in scenario.robots if robot.id not in unavailable_robot_ids]
    if tasks and not active_robots:
        return ["没有可用机器人执行任务"]

    errors: list[str] = []
    for task in tasks:
        if not _has_reachable_robot(scenario, task, active_robots, extra_blocked):
            errors.append(f"任务不可达：{task.id} {task.title}")
    return errors


def _has_reachable_robot(
    scenario: Scenario,
    task: Task,
    robots: list[Robot],
    extra_blocked: list[Cell],
) -> bool:
    waypoints = task_waypoints(task)
    if not waypoints:
        return False

    for robot in robots:
        if task.type == "delivery" and robot.load < (task.demand or 1):
            continue
        if _path_distance_for_task(scenario, robot.start, waypoints, extra_blocked) is not None:
            return True

    return False


def _path_distance_for_task(
    scenario: Scenario,
    start: Cell,
    waypoints: list[Cell],
    extra_blocked: list[Cell],
) -> int | None:
    cursor = start
    total = 0
    for waypoint in waypoints:
        path = astar(scenario, cursor, waypoint, extra_blocked)
        if not path:
            return None
        total += len(path) - 1
        cursor = waypoint
    return total if isfinite(total) else None


def _named_cells(scenario: Scenario) -> list[tuple[str, Cell]]:
    cells: list[tuple[str, Cell]] = []
    cells.extend((f"障碍 {index + 1}", cell) for index, cell in enumerate(scenario.obstacles))
    cells.extend((f"仓储区 {index + 1}", cell) for index, cell in enumerate(scenario.zones.warehouse))
    cells.extend((f"巡检区 {index + 1}", cell) for index, cell in enumerate(scenario.zones.inspection))
    cells.extend((f"投递区 {index + 1}", cell) for index, cell in enumerate(scenario.zones.delivery))
    cells.extend((f"充电区 {index + 1}", cell) for index, cell in enumerate(scenario.zones.charging))
    cells.extend((f"机器人 {robot.id} 起点", robot.start) for robot in scenario.robots)
    cells.extend(
        (f"动态封锁 {index + 1}", cell)
        for index, cell in enumerate(scenario.dynamic.blockedCells)
    )
    for task in _all_tasks(scenario):
        cells.extend((f"任务 {task.id} 目标 {index + 1}", cell) for index, cell in enumerate(task_waypoints(task)))
    return cells


def _base_required_walkable_cells(scenario: Scenario) -> list[tuple[str, Cell]]:
    cells: list[tuple[str, Cell]] = []
    cells.extend((f"充电区 {index + 1}", cell) for index, cell in enumerate(scenario.zones.charging))
    cells.extend((f"机器人 {robot.id} 起点", robot.start) for robot in scenario.robots)
    for task in scenario.tasks:
        cells.extend((f"任务 {task.id} 目标 {index + 1}", cell) for index, cell in enumerate(task_waypoints(task)))
    return cells


def _dynamic_required_walkable_cells(scenario: Scenario) -> list[tuple[str, Cell]]:
    cells: list[tuple[str, Cell]] = []
    for task in scenario.dynamic.tasks:
        cells.extend((f"任务 {task.id} 目标 {index + 1}", cell) for index, cell in enumerate(task_waypoints(task)))
    return cells


def _all_tasks(scenario: Scenario) -> list[Task]:
    return [*scenario.tasks, *scenario.dynamic.tasks]
