from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field

from backend.app.replan_window import ReplanWindowDecision, decide_replan_window
from backend.app.schemas import (
    Assignment,
    Cell,
    ChargingVisit,
    Conflict,
    ConflictState,
    DispatchOptions,
    DispatchResult,
    DynamicEvent,
    EventItem,
    Metrics,
    Robot,
    Scenario,
    Task,
    TaskFailureDetail,
)

MOVES: tuple[Cell, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
WAIT: Cell = (0, 0)
ASSIGNMENT_REPLAN_WINDOW = 24
ASSIGNMENT_SWITCH_PENALTY = 8


def has_dynamic_event(dynamic: DynamicEvent) -> bool:
    return bool(dynamic.blockedCells or dynamic.failedRobots or dynamic.tasks)


@dataclass
class Reservations:
    vertices: set[str] = field(default_factory=set)
    edges: set[str] = field(default_factory=set)


@dataclass
class RobotAssignmentState:
    robot: Robot
    cursor: Cell
    battery: int
    time: int = 0
    distance: int = 0
    penalty: float = 0
    tasks: list[Task] = field(default_factory=list)


@dataclass
class AssignmentCandidate:
    robots: list[RobotAssignmentState]


@dataclass
class PathPlanningCandidate:
    paths: dict[str, list[Cell]]
    failures: list[str]
    order: list[str]
    charging_visits: list[ChargingVisit] = field(default_factory=list)


DistanceCache = dict[tuple[Cell, Cell], float]


def cell_key(cell: Cell) -> str:
    return f"{cell[0]},{cell[1]}"


def edge_key(start: Cell, end: Cell, time_index: int) -> str:
    return f"{cell_key(start)}>{cell_key(end)}@{time_index}"


def same_cell(first: Cell, second: Cell) -> bool:
    return first[0] == second[0] and first[1] == second[1]


def manhattan(first: Cell, second: Cell) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def make_blocked_set(scenario: Scenario, extra_blocked: list[Cell] | None = None) -> set[str]:
    extra_blocked = extra_blocked or []
    return {cell_key(cell) for cell in [*scenario.obstacles, *extra_blocked]}


def merge_cells(first: list[Cell], second: list[Cell]) -> list[Cell]:
    merged: list[Cell] = []
    for cell in [*first, *second]:
        if cell not in merged:
            merged.append(cell)
    return merged


def is_inside(cell: Cell, scenario: Scenario) -> bool:
    return 0 <= cell[0] < scenario.width and 0 <= cell[1] < scenario.height


def is_walkable(cell: Cell, scenario: Scenario, blocked: set[str]) -> bool:
    return is_inside(cell, scenario) and cell_key(cell) not in blocked


def neighbors(cell: Cell, scenario: Scenario, blocked: set[str], include_wait: bool = False) -> list[Cell]:
    moves = (*MOVES, WAIT) if include_wait else MOVES
    result: list[Cell] = []
    for dx, dy in moves:
        next_cell = (cell[0] + dx, cell[1] + dy)
        if is_walkable(next_cell, scenario, blocked):
            result.append(next_cell)
    return result


def reconstruct(came_from: dict[str, str], current_key: str) -> list[Cell]:
    path: list[Cell] = []
    cursor: str | None = current_key
    while cursor:
        x_text, y_text = cursor.split(",")
        path.insert(0, (int(x_text), int(y_text)))
        cursor = came_from.get(cursor)
    return path


def astar(scenario: Scenario, start: Cell, goal: Cell, extra_blocked: list[Cell] | None = None) -> list[Cell]:
    blocked = make_blocked_set(scenario, extra_blocked)
    if not is_walkable(start, scenario, blocked) or not is_walkable(goal, scenario, blocked):
        return []

    start_key = cell_key(start)
    goal_key = cell_key(goal)
    heap: list[tuple[int, int, Cell, str]] = [(manhattan(start, goal), 0, start, start_key)]
    came_from: dict[str, str] = {}
    g_score: dict[str, int] = {start_key: 0}
    closed: set[str] = set()

    while heap:
        _, current_g, current_cell, current_key = heapq.heappop(heap)
        if current_key == goal_key:
            return reconstruct(came_from, current_key)
        if current_key in closed:
            continue
        closed.add(current_key)

        for next_cell in neighbors(current_cell, scenario, blocked):
            next_key = cell_key(next_cell)
            tentative = current_g + 1
            if tentative >= g_score.get(next_key, math.inf):
                continue
            came_from[next_key] = current_key
            g_score[next_key] = tentative
            heapq.heappush(heap, (tentative + manhattan(next_cell, goal), tentative, next_cell, next_key))

    return []


def timed_key(cell: Cell, time_index: int) -> str:
    return f"{cell_key(cell)}@{time_index}"


def is_reserved(cell: Cell, time_index: int, previous: Cell, reservations: Reservations) -> bool:
    vertex_blocked = f"{cell_key(cell)}@{time_index}" in reservations.vertices
    edge_blocked = edge_key(cell, previous, time_index - 1) in reservations.edges
    return vertex_blocked or edge_blocked


def reconstruct_timed(came_from: dict[str, str], current_key: str) -> list[Cell]:
    states: list[tuple[Cell, int]] = []
    cursor: str | None = current_key
    while cursor:
        cell_part, time_text = cursor.rsplit("@", 1)
        x_text, y_text = cell_part.split(",")
        states.insert(0, ((int(x_text), int(y_text)), int(time_text)))
        cursor = came_from.get(cursor)
    if not states:
        return []

    path = [states[0][0]]
    for (previous_cell, previous_time), (cell, time_index) in zip(states, states[1:]):
        path.extend([previous_cell] * max(0, time_index - previous_time - 1))
        path.append(cell)
    return path


def movement_is_reserved(
    start: Cell,
    goal: Cell,
    start_time: int,
    move_ticks: int,
    reservations: Reservations,
) -> bool:
    for time_index in range(start_time + 1, start_time + move_ticks):
        if f"{cell_key(start)}@{time_index}" in reservations.vertices:
            return True
    return is_reserved(goal, start_time + move_ticks, start, reservations)


def astar_timed(
    scenario: Scenario,
    start: Cell,
    goal: Cell,
    start_time: int,
    reservations: Reservations,
    extra_blocked: list[Cell] | None = None,
    move_ticks: int = 1,
) -> list[Cell]:
    blocked = make_blocked_set(scenario, extra_blocked)
    if not is_walkable(start, scenario, blocked) or not is_walkable(goal, scenario, blocked):
        return []

    max_time = start_time + scenario.width * scenario.height * 4 * move_ticks
    start_state_key = timed_key(start, start_time)
    heap: list[tuple[int, int, int, Cell, str]] = [
        (manhattan(start, goal) * move_ticks, start_time, 0, start, start_state_key)
    ]
    came_from: dict[str, str] = {}
    best: dict[str, int] = {start_state_key: 0}
    closed: set[str] = set()

    while heap:
        _, current_time, current_g, current_cell, current_key = heapq.heappop(heap)
        if same_cell(current_cell, goal):
            return reconstruct_timed(came_from, current_key)
        if current_key in closed or current_time >= max_time:
            continue
        closed.add(current_key)

        for next_cell in neighbors(current_cell, scenario, blocked, include_wait=True):
            duration = 1 if same_cell(next_cell, current_cell) else move_ticks
            next_time = current_time + duration
            reserved = (
                is_reserved(next_cell, next_time, current_cell, reservations)
                if duration == 1
                else movement_is_reserved(current_cell, next_cell, current_time, duration, reservations)
            )
            if reserved:
                continue
            next_key = timed_key(next_cell, next_time)
            tentative = current_g + duration
            if tentative >= best.get(next_key, math.inf):
                continue
            came_from[next_key] = current_key
            best[next_key] = tentative
            heapq.heappush(
                heap,
                (tentative + manhattan(next_cell, goal) * move_ticks, next_time, tentative, next_cell, next_key),
            )

    return []


def task_waypoints(task: Task) -> list[Cell]:
    if task.type == "inspection":
        return task.targets or []
    if task.type == "delivery":
        return [task.pickup, task.dropoff] if task.pickup and task.dropoff else []
    return [task.target] if task.target else []


def segment_distance(
    scenario: Scenario,
    start: Cell,
    goal: Cell,
    extra_blocked: list[Cell],
    distance_cache: DistanceCache | None = None,
) -> float:
    if distance_cache is not None:
        cache_key = (start, goal)
        cached = distance_cache.get(cache_key)
        if cached is not None:
            return cached

    path = astar(scenario, start, goal, extra_blocked)
    distance = len(path) - 1 if path else math.inf
    if distance_cache is not None:
        distance_cache[(start, goal)] = distance
    return distance


def task_distance(
    scenario: Scenario,
    start: Cell,
    task: Task,
    extra_blocked: list[Cell],
    distance_cache: DistanceCache | None = None,
) -> float:
    cursor = start
    total = 0
    for point in task_waypoints(task):
        distance = segment_distance(scenario, cursor, point, extra_blocked, distance_cache)
        if not math.isfinite(distance):
            return math.inf
        total += distance
        cursor = point
    return total


def nearest_charge_station(
    scenario: Scenario,
    start: Cell,
    extra_blocked: list[Cell],
    distance_cache: DistanceCache | None = None,
) -> tuple[Cell, float] | None:
    candidates = [
        (station, segment_distance(scenario, start, station, extra_blocked, distance_cache))
        for station in scenario.zones.charging
    ]
    reachable = [(station, distance) for station, distance in candidates if math.isfinite(distance)]
    if not reachable:
        return None
    return min(reachable, key=lambda item: (item[1], item[0]))


def task_charge_decision(
    scenario: Scenario,
    robot: Robot,
    start: Cell,
    battery: int,
    task: Task,
    extra_blocked: list[Cell],
    distance_cache: DistanceCache | None = None,
) -> tuple[Cell | None, float, int] | None:
    direct_distance = task_distance(scenario, start, task, extra_blocked, distance_cache)
    if not math.isfinite(direct_distance):
        return None
    if not scenario.zones.charging:
        if battery < direct_distance:
            return None
        return None, direct_distance, battery - int(direct_distance)

    waypoints = task_waypoints(task)
    endpoint = waypoints[-1] if waypoints else start
    direct_return = nearest_charge_station(scenario, endpoint, extra_blocked, distance_cache)
    if direct_return is not None and battery >= direct_distance + direct_return[1]:
        return None, direct_distance, battery - int(direct_distance)

    choices: list[tuple[Cell, float, float, float]] = []
    for station in scenario.zones.charging:
        to_station = segment_distance(scenario, start, station, extra_blocked, distance_cache)
        task_after_charge = task_distance(scenario, station, task, extra_blocked, distance_cache)
        return_after_charge = nearest_charge_station(scenario, endpoint, extra_blocked, distance_cache)
        if not math.isfinite(to_station) or not math.isfinite(task_after_charge) or return_after_charge is None:
            continue
        if battery >= to_station and robot.batteryCapacity >= task_after_charge + return_after_charge[1]:
            choices.append((station, to_station, task_after_charge, return_after_charge[1]))
    if not choices:
        return None
    station, to_station, task_after_charge, _ = min(choices, key=lambda item: (item[1] + item[2], item[0]))
    return station, to_station + task_after_charge, robot.batteryCapacity - int(task_after_charge)


def robot_task_travel_time(
    scenario: Scenario,
    robot: Robot,
    start: Cell,
    task: Task,
    extra_blocked: list[Cell],
    distance_cache: DistanceCache | None = None,
) -> float:
    distance = task_distance(scenario, start, task, extra_blocked, distance_cache)
    if not math.isfinite(distance):
        return math.inf
    return distance * robot.moveTicks


def task_release_time(task: Task) -> int:
    return task.releaseTime if task.releaseTime is not None else 0


def task_service_time(task: Task) -> int:
    return task.serviceTime if task.serviceTime is not None else 0


def task_deadline(task: Task) -> int:
    return task.deadline if task.deadline is not None else math.inf


def dynamic_task_for_dispatch(task: Task, trigger_time: int) -> Task:
    if task.releaseTime is not None and task.releaseTime >= trigger_time:
        return task
    return task.model_copy(update={"releaseTime": trigger_time})


def task_sort_key(task: Task) -> tuple[int, float, int, str]:
    return (-task.priority, task_deadline(task), task_release_time(task), task.id)


def split_tasks_for_planning(
    tasks: list[Task],
    locked_task_robot_ids: dict[str, str] | None = None,
    assignment_replan_window: int = ASSIGNMENT_REPLAN_WINDOW,
) -> tuple[list[Task], list[Task]]:
    locked_task_robot_ids = locked_task_robot_ids or {}
    planning_tasks: list[Task] = []
    deferred_tasks: list[Task] = []
    for task in tasks:
        if task.id in locked_task_robot_ids or task_release_time(task) <= assignment_replan_window:
            planning_tasks.append(task)
        else:
            deferred_tasks.append(task)
    return planning_tasks, deferred_tasks


def assignment_task_sort_key(
    task: Task,
    locked_task_order: dict[str, int],
    assignment_replan_window: int = ASSIGNMENT_REPLAN_WINDOW,
) -> tuple[int, int, int, int, float, int, str]:
    locked_index = locked_task_order.get(task.id)
    if locked_index is not None:
        return (0, locked_index, 0, *task_sort_key(task))
    release_bucket = 0 if task_release_time(task) <= assignment_replan_window else 1
    return (1, len(locked_task_order), release_bucket, *task_sort_key(task))


def deadline_penalty(task: Task, finish_time: int) -> int:
    if task.deadline is None:
        return 0
    return max(0, finish_time - task.deadline) * 8


def assignment_candidate_score(candidate: AssignmentCandidate) -> float:
    distances = [robot.distance for robot in candidate.robots]
    times = [robot.time for robot in candidate.robots]
    total_distance = sum(distances)
    makespan = max(times, default=0)
    mean_distance = total_distance / len(distances) if distances else 0
    balance = (
        math.sqrt(sum((distance - mean_distance) ** 2 for distance in distances) / len(distances))
        if distances
        else 0
    )
    penalty = sum(robot.penalty for robot in candidate.robots)
    assigned_task_count = sum(len(robot.tasks) for robot in candidate.robots)
    return penalty + total_distance + makespan * 2 + balance * 0.8 - assigned_task_count * 0.01


def clone_assignment_candidate(candidate: AssignmentCandidate) -> AssignmentCandidate:
    return AssignmentCandidate(
        robots=[
            RobotAssignmentState(
                robot=state.robot,
                cursor=state.cursor,
                battery=state.battery,
                time=state.time,
                distance=state.distance,
                penalty=state.penalty,
                tasks=[*state.tasks],
            )
            for state in candidate.robots
        ]
    )


def assign_tasks_beam_search(
    scenario: Scenario,
    robots: list[Robot],
    tasks: list[Task],
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    locked_task_robot_ids: dict[str, str] | None = None,
    preferred_task_robot_ids: dict[str, str] | None = None,
    delayed_unavailable_robot_ids: list[str] | None = None,
    delayed_unavailable_time: int | None = None,
    assignment_replan_window: int = ASSIGNMENT_REPLAN_WINDOW,
    task_limit_per_robot: int | None = None,
) -> list[Assignment]:
    locked_task_robot_ids = locked_task_robot_ids or {}
    preferred_task_robot_ids = preferred_task_robot_ids or {}
    delayed_unavailable = set(delayed_unavailable_robot_ids or [])
    unavailable = set(unavailable_robot_ids)
    active_robots = [robot for robot in robots if robot.id not in unavailable]
    active_robot_ids = {robot.id for robot in active_robots}
    if not active_robots:
        return []

    beam_width = max(8, min(48, len(active_robots) * 12))
    distance_cache: DistanceCache = {}
    candidates = [
        AssignmentCandidate(
            robots=[RobotAssignmentState(robot=robot, cursor=robot.start, battery=robot.battery) for robot in active_robots]
        )
    ]
    locked_task_order = {task_id: index for index, task_id in enumerate(locked_task_robot_ids)}
    sorted_tasks = sorted(
        tasks,
        key=lambda task: assignment_task_sort_key(task, locked_task_order, assignment_replan_window),
    )

    for task in sorted_tasks:
        locked_robot_id = locked_task_robot_ids.get(task.id)
        expanded: list[AssignmentCandidate] = []

        for candidate in candidates:
            for robot_index, robot_state in enumerate(candidate.robots):
                robot = robot_state.robot
                if locked_robot_id is not None and robot.id != locked_robot_id:
                    continue
                if task_limit_per_robot is not None and len(robot_state.tasks) >= task_limit_per_robot:
                    continue
                if (
                    delayed_unavailable_time is not None
                    and task_release_time(task) >= delayed_unavailable_time
                    and robot.id in delayed_unavailable
                ):
                    continue
                if not robot_can_handle_task(robot, task):
                    continue

                charge_decision = task_charge_decision(
                    scenario,
                    robot,
                    robot_state.cursor,
                    robot_state.battery,
                    task,
                    extra_blocked,
                    distance_cache,
                )
                if charge_decision is None:
                    continue
                charge_station, distance, next_battery = charge_decision
                travel_time = distance * robot.moveTicks
                if charge_station is not None:
                    travel_time += scenario.chargeTime

                current_time = robot_state.time
                start_time = max(current_time, task_release_time(task))
                finish_time = start_time + int(travel_time) + task_service_time(task)
                battery_penalty = max(0, 45 - robot.battery)
                wait_penalty = max(0, task_release_time(task) - current_time) * 0.25
                switch_penalty = assignment_switch_penalty(task, robot.id, preferred_task_robot_ids, active_robot_ids)
                waypoints = task_waypoints(task)

                next_candidate = clone_assignment_candidate(candidate)
                next_robot = next_candidate.robots[robot_index]
                next_robot.tasks.append(task)
                next_robot.time = finish_time
                next_robot.distance += int(distance)
                next_robot.battery = next_battery
                next_robot.penalty += battery_penalty + wait_penalty + switch_penalty + deadline_penalty(task, finish_time)
                if waypoints:
                    next_robot.cursor = waypoints[-1]
                expanded.append(next_candidate)

        if expanded:
            candidates = sorted(expanded, key=assignment_candidate_score)[:beam_width]

    best_candidate = min(candidates, key=assignment_candidate_score)
    return [
        Assignment(robotId=state.robot.id, tasks=state.tasks)
        for state in best_candidate.robots
    ]


def assignment_switch_penalty(
    task: Task,
    robot_id: str,
    preferred_task_robot_ids: dict[str, str],
    active_robot_ids: set[str],
) -> float:
    preferred_robot_id = preferred_task_robot_ids.get(task.id)
    if preferred_robot_id is None or preferred_robot_id == robot_id:
        return 0
    if preferred_robot_id not in active_robot_ids:
        return 0
    return ASSIGNMENT_SWITCH_PENALTY + max(0, task.priority - 1) * 1.5


def join_paths(base: list[Cell], segment: list[Cell]) -> list[Cell]:
    if not segment:
        return base
    if not base:
        return segment
    return [*base, *segment[1:]]


def expand_path_by_move_ticks(path: list[Cell], move_ticks: int) -> list[Cell]:
    if not path:
        return []
    expanded = [path[0]]
    for previous, cell in zip(path, path[1:]):
        if not same_cell(previous, cell):
            expanded.extend([previous] * (move_ticks - 1))
        expanded.append(cell)
    return expanded


def plan_robot_path(
    scenario: Scenario,
    robot: Robot,
    start: Cell,
    tasks: list[Task],
    move_ticks: int,
    avoid_conflicts: bool,
    reservations: Reservations,
    extra_blocked: list[Cell],
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    charging_visits: list[ChargingVisit] | None = None,
    active_charging_visit: ChargingVisit | None = None,
) -> tuple[list[Cell], bool]:
    path = [start]
    cursor = start
    battery = robot.battery
    charging_visits = charging_visits if charging_visits is not None else []
    distance_cache: DistanceCache = {}
    delayed_blocked = delayed_blocked or []
    if (
        active_charging_visit is not None
        and active_charging_visit.station == start
        and active_charging_visit.completionTime > 0
    ):
        for _ in range(active_charging_visit.completionTime):
            path.append(cursor)
        charging_visits.append(active_charging_visit)
        battery = robot.batteryCapacity
    for task_index, task in enumerate(tasks):
        release_time = task_release_time(task)
        while len(path) - 1 < release_time:
            path.append(cursor)
        charge_decision = task_charge_decision(
            scenario,
            robot,
            cursor,
            battery,
            task,
            extra_blocked,
            distance_cache,
        )
        if charge_decision is None:
            return path, True
        charge_station, _, next_battery = charge_decision
        if charge_station is not None:
            departure_time = len(path) - 1
            segment = (
                astar_timed(scenario, cursor, charge_station, departure_time, reservations, extra_blocked, move_ticks)
                if avoid_conflicts
                else expand_path_by_move_ticks(astar(scenario, cursor, charge_station, extra_blocked), move_ticks)
            )
            if not segment:
                return path, True
            path = join_paths(path, segment)
            cursor = charge_station
            arrival_time = len(path) - 1
            for _ in range(scenario.chargeTime):
                path.append(cursor)
            charging_visits.append(
                ChargingVisit(
                    robotId=robot.id,
                    station=charge_station,
                    departureTime=departure_time,
                    arrivalTime=arrival_time,
                    completionTime=arrival_time + scenario.chargeTime,
                )
            )
            battery = robot.batteryCapacity
        for waypoint in task_waypoints(task):
            segment_blocked = (
                merge_cells(extra_blocked, delayed_blocked)
                if delayed_block_time is not None and len(path) - 1 >= delayed_block_time
                else extra_blocked
            )
            segment = (
                astar_timed(
                    scenario,
                    cursor,
                    waypoint,
                    len(path) - 1,
                    reservations,
                    segment_blocked,
                    move_ticks,
                )
                if avoid_conflicts
                else expand_path_by_move_ticks(astar(scenario, cursor, waypoint, segment_blocked), move_ticks)
            )
            if not segment:
                return path, True
            path = join_paths(path, segment)
            cursor = waypoint
        for _ in range(task_service_time(task)):
            path.append(cursor)
        battery = next_battery
        next_tasks = tasks[task_index + 1 : task_index + 2]
        next_waypoints = task_waypoints(next_tasks[0]) if next_tasks else []
        if next_waypoints and same_cell(cursor, next_waypoints[0]):
            path.append(cursor)
    return path, False


def reserve_path(path: list[Cell], reservations: Reservations, horizon_padding: int = 12) -> None:
    for time_index, cell in enumerate(path):
        reservations.vertices.add(f"{cell_key(cell)}@{time_index}")
        if time_index > 0:
            reservations.edges.add(edge_key(path[time_index - 1], cell, time_index - 1))
    if not path:
        return
    final_cell = path[-1]
    for time_index in range(len(path), len(path) + horizon_padding):
        reservations.vertices.add(f"{cell_key(final_cell)}@{time_index}")


def blocked_cells_at_time(
    extra_blocked: list[Cell],
    delayed_blocked: list[Cell],
    delayed_block_time: int | None,
    time_index: int,
) -> list[Cell]:
    if delayed_block_time is not None and time_index >= delayed_block_time:
        return merge_cells(extra_blocked, delayed_blocked)
    return extra_blocked


def can_hold_cell(
    cell: Cell,
    start_time: int,
    reservations: Reservations,
    horizon_padding: int,
) -> bool:
    return all(
        f"{cell_key(cell)}@{time_index}" not in reservations.vertices
        for time_index in range(start_time, start_time + horizon_padding)
    )


def has_future_vertex_reservation(
    cell: Cell,
    start_time: int,
    reservations: Reservations,
    horizon_padding: int,
) -> bool:
    return any(
        f"{cell_key(cell)}@{time_index}" in reservations.vertices
        for time_index in range(start_time, start_time + horizon_padding)
    )


def append_parking_step(
    scenario: Scenario,
    path: list[Cell],
    move_ticks: int,
    reservations: Reservations,
    extra_blocked: list[Cell],
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    horizon_padding: int = 12,
) -> list[Cell]:
    if not path:
        return path

    delayed_blocked = delayed_blocked or []
    final_cell = path[-1]
    start_time = len(path) - 1
    next_time = start_time + 1
    if not has_future_vertex_reservation(final_cell, next_time, reservations, horizon_padding):
        return path

    blocked = make_blocked_set(
        scenario,
        blocked_cells_at_time(extra_blocked, delayed_blocked, delayed_block_time, next_time),
    )
    for candidate in neighbors(final_cell, scenario, blocked):
        segment = astar_timed(
            scenario,
            final_cell,
            candidate,
            start_time,
            reservations,
            blocked_cells_at_time(extra_blocked, delayed_blocked, delayed_block_time, start_time),
            move_ticks,
        )
        if not segment:
            continue
        arrival_time = start_time + len(segment) - 1
        if not can_hold_cell(candidate, arrival_time + 1, reservations, horizon_padding):
            continue
        return join_paths(path, segment)
    return path


def plan_idle_robot_parking_path(
    scenario: Scenario,
    start: Cell,
    move_ticks: int,
    reservations: Reservations,
    extra_blocked: list[Cell],
    delayed_blocked: list[Cell] | None = None,
    horizon_padding: int = 12,
) -> list[Cell]:
    blocked_cells = merge_cells(extra_blocked, delayed_blocked or [])
    blocked = make_blocked_set(scenario, blocked_cells)
    candidates = sorted(
        (
            (x, y)
            for x in range(scenario.width)
            for y in range(scenario.height)
            if is_walkable((x, y), scenario, blocked)
        ),
        key=lambda cell: (manhattan(start, cell), cell),
    )
    for candidate in candidates:
        path = astar_timed(scenario, start, candidate, 0, reservations, blocked_cells, move_ticks)
        if path and can_hold_cell(candidate, len(path), reservations, horizon_padding):
            return path
    return [start]


def path_planning_sort_key(
    robot: Robot,
    tasks: list[Task],
    locked_task_robot_ids: dict[str, str],
) -> tuple[int, int, int, float, int, str]:
    if not tasks:
        return (1, 1, 0, math.inf, math.inf, robot.id)

    has_locked_task = any(locked_task_robot_ids.get(task.id) == robot.id for task in tasks)
    highest_priority = max(task.priority for task in tasks)
    earliest_deadline = min(task_deadline(task) for task in tasks)
    earliest_release = min(task_release_time(task) for task in tasks)
    return (
        0,
        0 if has_locked_task else 1,
        -highest_priority,
        earliest_deadline,
        earliest_release,
        robot.id,
    )


def static_assignment_distance(
    scenario: Scenario,
    robot: Robot,
    tasks: list[Task],
    extra_blocked: list[Cell],
) -> float:
    cursor = robot.start
    total = 0.0
    for task in tasks:
        travel_time = robot_task_travel_time(scenario, robot, cursor, task, extra_blocked)
        if not math.isfinite(travel_time):
            return math.inf
        total += travel_time
        waypoints = task_waypoints(task)
        if waypoints:
            cursor = waypoints[-1]
    return total


def path_planning_orders(
    scenario: Scenario,
    robots: list[Robot],
    tasks_by_robot: dict[str, list[Task]],
    locked_task_robot_ids: dict[str, str],
    extra_blocked: list[Cell],
) -> list[list[Robot]]:
    keyed_orders = [
        sorted(
            robots,
            key=lambda robot: path_planning_sort_key(
                robot,
                tasks_by_robot.get(robot.id, []),
                locked_task_robot_ids,
            ),
        ),
        sorted(
            robots,
            key=lambda robot: (
                -static_assignment_distance(scenario, robot, tasks_by_robot.get(robot.id, []), extra_blocked),
                robot.id,
            ),
        ),
        sorted(
            robots,
            key=lambda robot: static_assignment_distance(
                scenario,
                robot,
                tasks_by_robot.get(robot.id, []),
                extra_blocked,
            ),
        ),
        sorted(robots, key=lambda robot: robot.id),
        sorted(robots, key=lambda robot: robot.id, reverse=True),
    ]
    unique_orders: list[list[Robot]] = []
    seen: set[tuple[str, ...]] = set()
    for order in keyed_orders:
        key = tuple(robot.id for robot in order)
        if key not in seen:
            seen.add(key)
            unique_orders.append(order)
    return unique_orders


def build_paths_for_order(
    scenario: Scenario,
    robots: list[Robot],
    assignments: list[Assignment],
    avoid_conflicts: bool,
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    planning_order: list[Robot],
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    active_charging_visits: dict[str, ChargingVisit] | None = None,
) -> PathPlanningCandidate:
    reservations = Reservations()
    paths: dict[str, list[Cell]] = {}
    failures: list[str] = []
    all_charging_visits: list[ChargingVisit] = []
    active_charging_visits = active_charging_visits or {}
    tasks_by_robot = {assignment.robotId: assignment.tasks for assignment in assignments}
    horizon_padding = max(12, scenario.width * scenario.height * 4)

    if avoid_conflicts:
        for robot in robots:
            if robot.id in unavailable_robot_ids:
                reserve_path([robot.start], reservations, horizon_padding=horizon_padding)

    for robot in planning_order:
        if robot.id in unavailable_robot_ids:
            paths[robot.id] = [robot.start]
            failures.append(f"{robot.id} 已标记为不可用")
            continue

        assigned = tasks_by_robot.get(robot.id, [])
        if avoid_conflicts and not assigned:
            path = plan_idle_robot_parking_path(
                scenario,
                robot.start,
                robot.moveTicks,
                reservations,
                extra_blocked,
                delayed_blocked,
                horizon_padding,
            )
            failed = False
        else:
            charging_visits: list[ChargingVisit] = []
            path, failed = plan_robot_path(
                scenario,
                robot,
                robot.start,
                assigned,
                robot.moveTicks,
                avoid_conflicts,
                reservations,
                extra_blocked,
                delayed_blocked,
                delayed_block_time,
                charging_visits,
                active_charging_visits.get(robot.id),
            )
        if avoid_conflicts and assigned and not failed:
            path = append_parking_step(
                scenario,
                path,
                robot.moveTicks,
                reservations,
                extra_blocked,
                delayed_blocked,
                delayed_block_time,
                horizon_padding,
            )
        paths[robot.id] = path
        all_charging_visits.extend(charging_visits if assigned else [])
        if failed:
            failures.append(f"{robot.id} 存在不可达任务")
        if avoid_conflicts:
            reserve_path(path, reservations, horizon_padding=horizon_padding)

    ordered_paths = {robot.id: paths[robot.id] for robot in robots if robot.id in paths}
    return PathPlanningCandidate(
        paths=ordered_paths,
        failures=failures,
        order=[robot.id for robot in planning_order],
        charging_visits=all_charging_visits,
    )


def path_planning_candidate_score(candidate: PathPlanningCandidate, assignments: list[Assignment]) -> tuple[float, ...]:
    conflicts = detect_conflicts(candidate.paths)
    deadline_miss_count, average_lateness = deadline_stats(assignments, candidate.paths)
    lengths = [max(0, len(path) - 1) for path in candidate.paths.values()]
    total_distance = sum(lengths)
    makespan = max(lengths, default=0)
    mean = total_distance / len(lengths) if lengths else 0
    load_balance = math.sqrt(sum((value - mean) ** 2 for value in lengths) / len(lengths)) if lengths else 0
    return (
        len(candidate.failures),
        len(conflicts),
        deadline_miss_count,
        average_lateness,
        makespan,
        total_distance,
        load_balance,
    )


def build_paths(
    scenario: Scenario,
    robots: list[Robot],
    assignments: list[Assignment],
    avoid_conflicts: bool,
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    locked_task_robot_ids: dict[str, str] | None = None,
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    include_charging_visits: bool = False,
    active_charging_visits: dict[str, ChargingVisit] | None = None,
) -> tuple[dict[str, list[Cell]], list[str]] | tuple[dict[str, list[Cell]], list[str], list[ChargingVisit]]:
    locked_task_robot_ids = locked_task_robot_ids or {}
    tasks_by_robot = {assignment.robotId: assignment.tasks for assignment in assignments}
    best_candidate: PathPlanningCandidate | None = None
    best_score: tuple[float, ...] | None = None
    for order in path_planning_orders(
        scenario,
        robots,
        tasks_by_robot,
        locked_task_robot_ids,
        extra_blocked,
    ):
        candidate = build_paths_for_order(
            scenario,
            robots,
            assignments,
            avoid_conflicts,
            extra_blocked,
            unavailable_robot_ids,
            order,
            delayed_blocked,
            delayed_block_time,
            active_charging_visits,
        )
        score = path_planning_candidate_score(candidate, assignments)
        if best_score is None or score < best_score:
            best_candidate = candidate
            best_score = score
        if avoid_conflicts and score[0] == 0 and score[1] == 0 and score[2] == 0:
            break
    if best_candidate is None:
        return ({}, [], []) if include_charging_visits else ({}, [])
    if include_charging_visits:
        return best_candidate.paths, best_candidate.failures, best_candidate.charging_visits
    return best_candidate.paths, best_candidate.failures


def path_at(path: list[Cell], time_index: int) -> Cell | None:
    if not path:
        return None
    return path[min(time_index, len(path) - 1)]


def detect_conflicts(paths: dict[str, list[Cell]]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    robot_ids = list(paths.keys())
    horizon = max((len(paths[robot_id]) for robot_id in robot_ids), default=0)

    for time_index in range(horizon):
        occupied: dict[str, str] = {}
        for robot_id in robot_ids:
            cell = path_at(paths[robot_id], time_index)
            if cell is None:
                continue
            key = cell_key(cell)
            if key in occupied:
                conflicts.append(Conflict(time=time_index, type="vertex", robots=[occupied[key], robot_id], cell=cell))
            else:
                occupied[key] = robot_id

        if time_index == 0:
            continue

        for index, first_id in enumerate(robot_ids):
            for second_id in robot_ids[index + 1 :]:
                first_prev = path_at(paths[first_id], time_index - 1)
                second_prev = path_at(paths[second_id], time_index - 1)
                first_now = path_at(paths[first_id], time_index)
                second_now = path_at(paths[second_id], time_index)
                if (
                    first_prev
                    and second_prev
                    and first_now
                    and second_now
                    and same_cell(first_prev, second_now)
                    and same_cell(second_prev, first_now)
                ):
                    conflicts.append(Conflict(time=time_index, type="edge", robots=[first_id, second_id], cell=first_now))

    return conflicts


def build_conflict_states(
    conflicts: list[Conflict],
    paths: dict[str, list[Cell]],
    current_time: int,
) -> list[ConflictState]:
    states: list[ConflictState] = []
    for conflict in conflicts:
        active = is_conflict_active(conflict, paths, current_time)
        resolved_at = conflict_resolved_time(conflict, paths)
        if conflict.time > current_time and not active:
            continue
        states.append(
            ConflictState(
                time=conflict.time,
                type=conflict.type,
                robots=conflict.robots,
                cell=conflict.cell,
                status="active" if active else "resolved",
                startedAt=conflict.time,
                resolvedAt=resolved_at,
            )
        )
    return states


def conflict_resolved_time(conflict: Conflict, paths: dict[str, list[Cell]]) -> int | None:
    horizon = max((len(path) for path in paths.values()), default=0)
    for time_index in range(conflict.time, horizon):
        if not is_conflict_active(conflict, paths, time_index):
            return time_index
    return None


def is_conflict_active(conflict: Conflict, paths: dict[str, list[Cell]], current_time: int) -> bool:
    if len(conflict.robots) < 2:
        return False
    first_robot_id, second_robot_id = conflict.robots[:2]
    first_now = path_at(paths.get(first_robot_id, []), current_time)
    second_now = path_at(paths.get(second_robot_id, []), current_time)
    if first_now is None or second_now is None:
        return False

    if conflict.type == "vertex":
        return same_cell(first_now, conflict.cell) and same_cell(second_now, conflict.cell)

    if current_time <= 0:
        return False
    first_previous = path_at(paths.get(first_robot_id, []), current_time - 1)
    second_previous = path_at(paths.get(second_robot_id, []), current_time - 1)
    if first_previous is None or second_previous is None:
        return False
    return same_cell(first_previous, second_now) and same_cell(second_previous, first_now)


def find_next_visit(path: list[Cell], waypoint: Cell, start_index: int) -> int | None:
    for index in range(start_index, len(path)):
        if same_cell(path[index], waypoint):
            return index
    return None


def task_completion_times(assignments: list[Assignment], paths: dict[str, list[Cell]]) -> dict[str, int]:
    completions: dict[str, int] = {}
    for assignment in assignments:
        path = paths.get(assignment.robotId, [])
        cursor_index = 0
        for task in assignment.tasks:
            cursor_index = max(cursor_index, task_release_time(task))
            completion_index: int | None = None
            for waypoint in task_waypoints(task):
                found_index = find_next_visit(path, waypoint, cursor_index)
                if found_index is None:
                    completion_index = None
                    break
                completion_index = found_index
                cursor_index = found_index
            if completion_index is not None:
                completion_time = completion_index + task_service_time(task)
                completions[task.id] = completion_time
                cursor_index = completion_time + 1
    return completions


def deadline_stats(assignments: list[Assignment], paths: dict[str, list[Cell]]) -> tuple[int, float]:
    completions = task_completion_times(assignments, paths)
    lateness_values: list[int] = []
    for assignment in assignments:
        for task in assignment.tasks:
            if task.deadline is None:
                continue
            completion_time = completions.get(task.id)
            if completion_time is None:
                continue
            lateness_values.append(max(0, completion_time - task.deadline))

    if not lateness_values:
        return 0, 0

    miss_count = sum(1 for value in lateness_values if value > 0)
    average_lateness = sum(lateness_values) / len(lateness_values)
    return miss_count, round(average_lateness, 2)


def calculate_metrics(
    paths: dict[str, list[Cell]],
    conflicts: list[Conflict],
    assignments: list[Assignment],
    failures: list[str],
    replan_time_ms: float,
    failure_count: int | None = None,
) -> Metrics:
    lengths = [max(0, len(path) - 1) for path in paths.values()]
    total_distance = sum(lengths)
    makespan = max(lengths, default=0)
    mean = total_distance / len(lengths) if lengths else 0
    balance = math.sqrt(sum((value - mean) ** 2 for value in lengths) / len(lengths)) if lengths else 0
    assigned_task_count = sum(len(item.tasks) for item in assignments)
    deadline_miss_count, average_lateness = deadline_stats(assignments, paths)
    return Metrics(
        makespan=makespan,
        totalDistance=total_distance,
        conflictCount=len(conflicts),
        loadBalance=round(balance, 2),
        assignedTaskCount=assigned_task_count,
        deadlineMissCount=deadline_miss_count,
        averageLateness=average_lateness,
        failureCount=len(failures) if failure_count is None else failure_count,
        replanTimeMs=replan_time_ms,
    )


def robot_supports_task_type(robot: Robot, task: Task) -> bool:
    return task.type in robot.capabilities


def robot_has_required_load(robot: Robot, task: Task) -> bool:
    return task.type != "delivery" or robot.load >= (task.demand or 1)


def robot_can_handle_task(robot: Robot, task: Task) -> bool:
    return robot_supports_task_type(robot, task) and robot_has_required_load(robot, task)


def failure_constraints_for_task(
    task: Task,
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    delayed_unavailable_robot_ids: list[str] | None = None,
    delayed_unavailable_time: int | None = None,
) -> tuple[list[Cell], list[str]]:
    task_blocked = extra_blocked
    if delayed_block_time is not None and task_release_time(task) >= delayed_block_time:
        task_blocked = merge_cells(extra_blocked, delayed_blocked or [])

    task_unavailable = list(unavailable_robot_ids)
    if delayed_unavailable_time is not None and task_release_time(task) >= delayed_unavailable_time:
        for robot_id in delayed_unavailable_robot_ids or []:
            if robot_id not in task_unavailable:
                task_unavailable.append(robot_id)
    return task_blocked, task_unavailable


def clean_invalid_task_locks(
    scenario: Scenario,
    tasks: list[Task],
    locked_task_robot_ids: dict[str, str] | None,
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    delayed_unavailable_robot_ids: list[str] | None = None,
    delayed_unavailable_time: int | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    cleaned_locks = dict(locked_task_robot_ids or {})
    released_reasons: dict[str, str] = {}
    tasks_by_id = {task.id: task for task in tasks}
    robots_by_id = {robot.id: robot for robot in scenario.robots}

    for task_id, locked_robot_id in list(cleaned_locks.items()):
        task = tasks_by_id.get(task_id)
        locked_robot = robots_by_id.get(locked_robot_id)
        if task is None or locked_robot is None or robot_can_handle_task(locked_robot, task):
            continue

        task_blocked, task_unavailable = failure_constraints_for_task(
            task,
            extra_blocked,
            unavailable_robot_ids,
            delayed_blocked,
            delayed_block_time,
            delayed_unavailable_robot_ids,
            delayed_unavailable_time,
        )
        unavailable = set(task_unavailable)
        has_executable_alternative = any(
            robot.id != locked_robot_id
            and robot.id not in unavailable
            and robot_can_handle_task(robot, task)
            and task_charge_decision(
                scenario,
                robot,
                robot.start,
                robot.battery,
                task,
                task_blocked,
            )
            is not None
            for robot in scenario.robots
        )
        if not has_executable_alternative:
            continue

        if not robot_supports_task_type(locked_robot, task):
            reason = f"锁定机器人 {locked_robot_id} 不兼容任务类型 {task.type}"
        else:
            reason = f"锁定机器人 {locked_robot_id} 不满足载重 {task.demand or 1}"
        cleaned_locks.pop(task_id, None)
        released_reasons[task_id] = reason

    return cleaned_locks, released_reasons


def reachable_unlocked_active_robot_ids(
    scenario: Scenario,
    task: Task,
    active_robots: list[Robot],
    locked_robot_id: str | None,
    extra_blocked: list[Cell],
) -> list[str]:
    if locked_robot_id is None:
        return []
    return [
        robot.id
        for robot in active_robots
        if robot.id != locked_robot_id
        and robot_can_handle_task(robot, task)
        and task_charge_decision(scenario, robot, robot.start, robot.battery, task, extra_blocked) is not None
    ]


def task_failure_reason(
    scenario: Scenario,
    task: Task,
    active_robots: list[Robot],
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    locked_task_robot_ids: dict[str, str] | None,
) -> str:
    locked_task_robot_ids = locked_task_robot_ids or {}
    locked_robot_id = locked_task_robot_ids.get(task.id)
    waypoints = task_waypoints(task)
    if not waypoints:
        return "任务缺少有效目标点"

    scoped_robots = scenario.robots
    type_compatible = [robot for robot in scoped_robots if robot_supports_task_type(robot, task)]
    if not type_compatible:
        return f"没有机器人兼容任务类型 {task.type}"

    fully_capable = [robot for robot in type_compatible if robot_has_required_load(robot, task)]
    if not fully_capable:
        demand = task.demand or 1
        return f"没有可用机器人满足载重 {demand}"

    active_robot_ids = {robot.id for robot in active_robots}
    unavailable = set(unavailable_robot_ids)
    capable_active_robots = [robot for robot in fully_capable if robot.id in active_robot_ids]
    capable_unavailable_robots = [robot for robot in fully_capable if robot.id in unavailable]

    candidate_robots = capable_active_robots
    candidate_unavailable_robots = capable_unavailable_robots
    if locked_robot_id is not None:
        locked_robot = next((robot for robot in scenario.robots if robot.id == locked_robot_id), None)
        reachable_alternatives = reachable_unlocked_active_robot_ids(
            scenario,
            task,
            capable_active_robots,
            locked_robot_id,
            extra_blocked,
        )
        locked_robot_is_fully_capable = locked_robot is not None and robot_can_handle_task(locked_robot, task)
        if not locked_robot_is_fully_capable:
            if locked_robot is not None and not robot_supports_task_type(locked_robot, task) and reachable_alternatives:
                return f"任务锁定机器人 {locked_robot_id} 不兼容任务类型 {task.type}，释放锁定后可改派"
            if locked_robot is not None and not robot_has_required_load(locked_robot, task) and reachable_alternatives:
                demand = task.demand or 1
                return f"任务锁定机器人 {locked_robot_id} 不满足载重 {demand}，释放锁定后可改派"
        elif locked_robot_id in unavailable:
            if reachable_alternatives:
                return f"任务锁定机器人 {locked_robot_id} 已不可用，释放锁定后可改派"
            if locked_robot is not None and task_charge_decision(
                scenario,
                locked_robot,
                locked_robot.start,
                locked_robot.battery,
                task,
                extra_blocked,
            ) is None:
                if math.isfinite(task_distance(scenario, locked_robot.start, task, extra_blocked)):
                    battery_reachable_without_blocked = task_charge_decision(
                        scenario,
                        locked_robot,
                        locked_robot.start,
                        locked_robot.battery,
                        task,
                        [],
                    ) is not None
                    if extra_blocked and battery_reachable_without_blocked:
                        return f"通往充电桩的路线被动态封锁，当前动态封锁 {len(extra_blocked)} 个单元"
                    if scenario.zones.charging:
                        return "电池容量不足以完成任务并到达充电桩"
                    return "剩余电量不足且无可达充电桩"
                reachable_without_blocked = math.isfinite(task_distance(scenario, locked_robot.start, task, []))
                if extra_blocked and reachable_without_blocked:
                    return f"所有候选机器人到剩余目标不可达，当前动态封锁 {len(extra_blocked)} 个单元"
                return "所有候选机器人到剩余目标不可达"
            return f"任务锁定机器人 {locked_robot_id} 已不可用"
        else:
            candidate_robots = [robot for robot in capable_active_robots if robot.id == locked_robot_id]
            candidate_unavailable_robots = [
                robot for robot in capable_unavailable_robots if robot.id == locked_robot_id
            ]
            if not candidate_robots:
                return f"任务锁定机器人 {locked_robot_id} 不在可用机器人列表"

    active_with_blocked = [
        robot.id
        for robot in candidate_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, extra_blocked) is not None
    ]
    active_without_blocked = [
        robot.id
        for robot in candidate_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, []) is not None
    ]
    unavailable_with_blocked = [
        robot.id
        for robot in candidate_unavailable_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, extra_blocked) is not None
    ]
    unavailable_without_blocked = [
        robot.id
        for robot in candidate_unavailable_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, []) is not None
    ]

    if active_with_blocked:
        return "当前锁定、排序或避碰约束下未进入可行分配"
    if locked_robot_id is not None and reachable_unlocked_active_robot_ids(
        scenario,
        task,
        active_robots,
        locked_robot_id,
        extra_blocked,
    ):
        return f"任务锁定机器人 {locked_robot_id} 不可达，释放锁定后可改派"

    active_path_with_blocked = any(
        math.isfinite(task_distance(scenario, robot.start, task, extra_blocked)) for robot in candidate_robots
    )
    if active_without_blocked and extra_blocked:
        if active_path_with_blocked:
            return f"通往充电桩的路线被动态封锁，当前动态封锁 {len(extra_blocked)} 个单元"
        return f"所有候选机器人到剩余目标不可达，当前动态封锁 {len(extra_blocked)} 个单元"
    if unavailable_with_blocked:
        return "没有可用机器人"
    if unavailable_without_blocked and extra_blocked:
        unavailable_path_with_blocked = any(
            math.isfinite(task_distance(scenario, robot.start, task, extra_blocked))
            for robot in candidate_unavailable_robots
        )
        if unavailable_path_with_blocked:
            return f"通往充电桩的路线被动态封锁，当前动态封锁 {len(extra_blocked)} 个单元"
        return f"所有候选机器人到剩余目标不可达，当前动态封锁 {len(extra_blocked)} 个单元"

    reason_robots = candidate_robots + candidate_unavailable_robots
    if any(math.isfinite(task_distance(scenario, robot.start, task, extra_blocked)) for robot in reason_robots):
        if scenario.zones.charging:
            return "电池容量不足以完成任务并到达充电桩"
        return "剩余电量不足且无可达充电桩"
    if extra_blocked and any(
        math.isfinite(task_distance(scenario, robot.start, task, [])) for robot in reason_robots
    ):
        return f"所有候选机器人到剩余目标不可达，当前动态封锁 {len(extra_blocked)} 个单元"
    if not reason_robots:
        return "没有可用机器人"
    return "所有候选机器人到剩余目标不可达"


def capacity_deferred_task_ids(
    scenario: Scenario,
    tasks: list[Task],
    assignments: list[Assignment],
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    locked_task_robot_ids: dict[str, str] | None,
) -> set[str]:
    locked_task_robot_ids = locked_task_robot_ids or {}
    assigned_task_ids = {
        task.id
        for assignment in assignments
        for task in assignment.tasks
    }
    occupied_robot_ids = {
        assignment.robotId
        for assignment in assignments
        if assignment.tasks
    }
    active_robots = [robot for robot in scenario.robots if robot.id not in set(unavailable_robot_ids)]
    deferred_task_ids: set[str] = set()
    for task in tasks:
        if task.id in assigned_task_ids or task.id in locked_task_robot_ids:
            continue
        reachable_robot_ids = {
            robot.id
            for robot in active_robots
            if robot_can_handle_task(robot, task)
            and math.isfinite(task_distance(scenario, robot.start, task, extra_blocked))
        }
        if reachable_robot_ids and reachable_robot_ids.issubset(occupied_robot_ids):
            deferred_task_ids.add(task.id)
    return deferred_task_ids


def build_failure_reasons(
    scenario: Scenario,
    tasks: list[Task],
    assignments: list[Assignment],
    paths: dict[str, list[Cell]],
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    locked_task_robot_ids: dict[str, str] | None,
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    delayed_unavailable_robot_ids: list[str] | None = None,
    delayed_unavailable_time: int | None = None,
) -> dict[str, str]:
    assigned_task_ids = {
        task.id
        for assignment in assignments
        for task in assignment.tasks
    }
    reasons: dict[str, str] = {}
    for task in tasks:
        if task.id in assigned_task_ids:
            continue
        task_blocked, task_unavailable = failure_constraints_for_task(
            task,
            extra_blocked,
            unavailable_robot_ids,
            delayed_blocked,
            delayed_block_time,
            delayed_unavailable_robot_ids,
            delayed_unavailable_time,
        )
        unavailable = set(task_unavailable)
        active_robots = [robot for robot in scenario.robots if robot.id not in unavailable]
        reasons[task.id] = task_failure_reason(
            scenario,
            task,
            active_robots,
            task_blocked,
            task_unavailable,
            locked_task_robot_ids,
        )

    completions = task_completion_times(assignments, paths)
    for assignment in assignments:
        path = paths.get(assignment.robotId, [])
        for task in assignment.tasks:
            if task.id in completions:
                continue
            if not task_waypoints(task):
                reasons[task.id] = "任务缺少有效目标点"
            elif not path or len(path) <= 1:
                reasons[task.id] = f"机器人 {assignment.robotId} 未生成可执行路径"
            else:
                reasons[task.id] = f"机器人 {assignment.robotId} 无法到达任务剩余目标"
    return reasons


def task_recovery_classification(
    scenario: Scenario,
    task: Task,
    active_robots: list[Robot],
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    locked_task_robot_ids: dict[str, str] | None,
) -> tuple[str, str, list[Cell], list[str]]:
    locked_task_robot_ids = locked_task_robot_ids or {}
    locked_robot_id = locked_task_robot_ids.get(task.id)

    waypoints = task_waypoints(task)
    if not waypoints:
        return "permanent", "fixTaskDefinition", [], []

    scoped_robots = scenario.robots
    type_compatible = [robot for robot in scoped_robots if robot_supports_task_type(robot, task)]
    if not type_compatible:
        return "permanent", "addCapableRobotOrChangeTaskType", [], []

    fully_capable = [robot for robot in type_compatible if robot_has_required_load(robot, task)]
    if not fully_capable:
        return "permanent", "addCapableRobotOrReduceDemand", [], []

    unavailable = set(unavailable_robot_ids)
    active_robot_ids = {robot.id for robot in active_robots}
    scoped_active_robots = [robot for robot in fully_capable if robot.id in active_robot_ids]
    scoped_unavailable_robots = [robot for robot in fully_capable if robot.id in unavailable]
    if locked_robot_id is not None:
        locked_robot = next((robot for robot in scenario.robots if robot.id == locked_robot_id), None)
        reachable_alternatives = reachable_unlocked_active_robot_ids(
            scenario,
            task,
            scoped_active_robots,
            locked_robot_id,
            extra_blocked,
        )
        locked_robot_is_fully_capable = locked_robot is not None and robot_can_handle_task(locked_robot, task)
        if reachable_alternatives and (not locked_robot_is_fully_capable or locked_robot_id in unavailable):
            return "temporary", "relaxLocksOrReplan", [], []
        if locked_robot_is_fully_capable:
            scoped_active_robots = [robot for robot in scoped_active_robots if robot.id == locked_robot_id]
            scoped_unavailable_robots = [robot for robot in scoped_unavailable_robots if robot.id == locked_robot_id]

    active_with_blocked = [
        robot.id
        for robot in scoped_active_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, extra_blocked) is not None
    ]
    active_without_blocked = [
        robot.id
        for robot in scoped_active_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, []) is not None
    ]
    active_recovering_blocked_cells = recovering_charge_blocked_cells(
        scenario,
        task,
        scoped_active_robots,
        extra_blocked,
    )
    unavailable_with_blocked = [
        robot.id
        for robot in scoped_unavailable_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, extra_blocked) is not None
    ]
    unavailable_without_blocked = [
        robot.id
        for robot in scoped_unavailable_robots
        if task_charge_decision(scenario, robot, robot.start, robot.battery, task, []) is not None
    ]

    if active_with_blocked:
        return "temporary", "relaxLocksOrReplan", [], []
    if locked_robot_id is not None and reachable_unlocked_active_robot_ids(
        scenario,
        task,
        active_robots,
        locked_robot_id,
        extra_blocked,
    ):
        return "temporary", "relaxLocksOrReplan", [], []

    if active_without_blocked and unavailable_with_blocked and extra_blocked:
        return "temporary", "clearBlockedCellsOrRestoreRobot", active_recovering_blocked_cells, unavailable_with_blocked
    if active_without_blocked and extra_blocked:
        return "temporary", "clearBlockedCells", active_recovering_blocked_cells, []
    if unavailable_with_blocked:
        return "temporary", "restoreRobot", [], unavailable_with_blocked
    if unavailable_without_blocked and extra_blocked:
        unavailable_recovering_blocked_cells = recovering_charge_blocked_cells(
            scenario,
            task,
            scoped_unavailable_robots,
            extra_blocked,
        )
        return "temporary", "clearBlockedCellsAndRestoreRobot", unavailable_recovering_blocked_cells, unavailable_without_blocked
    return "permanent", "fixMapOrTaskTarget", [], []


def recovering_charge_blocked_cells(
    scenario: Scenario,
    task: Task,
    robots: list[Robot],
    extra_blocked: list[Cell],
) -> list[Cell]:
    if not extra_blocked:
        return []

    cells: list[Cell] = []
    for blocked_cell in extra_blocked:
        remaining_blocked = [cell for cell in extra_blocked if cell != blocked_cell]
        if any(
            task_charge_decision(scenario, robot, robot.start, robot.battery, task, remaining_blocked) is not None
            for robot in robots
        ):
            cells.append(blocked_cell)
    if cells:
        return cells

    if not any(
        task_charge_decision(scenario, robot, robot.start, robot.battery, task, []) is not None
        for robot in robots
    ):
        return []

    joint_cells: list[Cell] = []
    for blocked_cell in extra_blocked:
        if not any(
            task_charge_decision(scenario, robot, robot.start, robot.battery, task, [blocked_cell]) is not None
            for robot in robots
        ):
            joint_cells.append(blocked_cell)
    return joint_cells or extra_blocked


def build_failure_details(
    scenario: Scenario,
    tasks: list[Task],
    assignments: list[Assignment],
    paths: dict[str, list[Cell]],
    extra_blocked: list[Cell],
    unavailable_robot_ids: list[str],
    locked_task_robot_ids: dict[str, str] | None,
    delayed_blocked: list[Cell] | None = None,
    delayed_block_time: int | None = None,
    delayed_unavailable_robot_ids: list[str] | None = None,
    delayed_unavailable_time: int | None = None,
) -> dict[str, TaskFailureDetail]:
    reasons = build_failure_reasons(
        scenario,
        tasks,
        assignments,
        paths,
        extra_blocked,
        unavailable_robot_ids,
        locked_task_robot_ids,
        delayed_blocked,
        delayed_block_time,
        delayed_unavailable_robot_ids,
        delayed_unavailable_time,
    )
    details: dict[str, TaskFailureDetail] = {}
    for task in tasks:
        reason = reasons.get(task.id)
        if reason is None:
            continue
        task_blocked, task_unavailable = failure_constraints_for_task(
            task,
            extra_blocked,
            unavailable_robot_ids,
            delayed_blocked,
            delayed_block_time,
            delayed_unavailable_robot_ids,
            delayed_unavailable_time,
        )
        unavailable = set(task_unavailable)
        active_robots = [robot for robot in scenario.robots if robot.id not in unavailable]
        category, action, blocking_cells, blocking_robot_ids = task_recovery_classification(
            scenario,
            task,
            active_robots,
            task_blocked,
            task_unavailable,
            locked_task_robot_ids,
        )
        details[task.id] = TaskFailureDetail(
            reason=reason,
            category=category,
            recoveryAction=action,
            blockingCells=blocking_cells,
            blockingRobotIds=blocking_robot_ids,
        )
    return details


def build_event_log(
    scenario: Scenario,
    include_dynamic: bool,
    avoid_conflicts: bool,
    assignments: list[Assignment],
    paths: dict[str, list[Cell]],
    conflicts: list[Conflict],
    failures: list[str],
    failure_reasons: dict[str, str] | None = None,
    deferred_task_count: int = 0,
) -> list[EventItem]:
    events = [
        EventItem(time=0, text=f"加载场景：{scenario.name}"),
        EventItem(time=0, text="启用优先级避碰规划" if avoid_conflicts else "启用基线规划，不进行时序避碰"),
    ]

    if conflicts:
        events.append(EventItem(time=0, text=f"检测到 {len(conflicts)} 次路径冲突"))

    for failure in failures:
        events.append(EventItem(time=0, text=failure))
    for task_id, reason in (failure_reasons or {}).items():
        events.append(EventItem(time=0, text=f"任务 {task_id} 调度失败原因：{reason}"))
    if deferred_task_count:
        events.append(EventItem(time=0, text=f"{deferred_task_count} 个远期任务等待滚动窗口调度"))

    return sorted(events, key=lambda item: item.time)


def run_dispatch(
    scenario: Scenario,
    options: DispatchOptions,
    locked_task_robot_ids: dict[str, str] | None = None,
    preferred_task_robot_ids: dict[str, str] | None = None,
    include_dynamic_events: bool | None = None,
    apply_dynamic_constraints_at_start: bool | None = None,
    task_limit_per_robot: int | None = None,
    replan_window_decision: ReplanWindowDecision | None = None,
    active_charging_visits: dict[str, ChargingVisit] | None = None,
) -> DispatchResult:
    avoid_conflicts = options.avoidConflicts
    include_dynamic = options.includeDynamic
    if include_dynamic_events is None:
        include_dynamic_events = include_dynamic and has_dynamic_event(scenario.dynamic)
    dynamic_active_at_start = include_dynamic and (
        apply_dynamic_constraints_at_start
        if apply_dynamic_constraints_at_start is not None
        else scenario.dynamic.triggerTime == 0
    )
    extra_blocked = scenario.dynamic.blockedCells if dynamic_active_at_start else []
    unavailable_robot_ids = scenario.dynamic.failedRobots if dynamic_active_at_start else []
    delayed_blocked = scenario.dynamic.blockedCells if include_dynamic and not dynamic_active_at_start else []
    delayed_block_time = scenario.dynamic.triggerTime if delayed_blocked else None
    delayed_unavailable_robot_ids = scenario.dynamic.failedRobots if include_dynamic and not dynamic_active_at_start else []
    delayed_unavailable_time = scenario.dynamic.triggerTime if delayed_unavailable_robot_ids else None
    dynamic_tasks = [
        dynamic_task_for_dispatch(task, scenario.dynamic.triggerTime)
        for task in scenario.dynamic.tasks
    ]
    tasks = [*scenario.tasks, *dynamic_tasks] if include_dynamic else [*scenario.tasks]
    locked_task_robot_ids, _ = clean_invalid_task_locks(
        scenario,
        tasks,
        locked_task_robot_ids,
        extra_blocked,
        unavailable_robot_ids,
        delayed_blocked,
        delayed_block_time,
        delayed_unavailable_robot_ids,
        delayed_unavailable_time,
    )
    if replan_window_decision is None:
        released_task_count = sum(
            1
            for task in tasks
            if (task.releaseTime if task.releaseTime is not None else 0) <= 0
        )
        replan_window_decision = decide_replan_window(
            configured_window=options.assignmentReplanWindow,
            adaptive=options.adaptiveReplanWindow,
            released_task_count=released_task_count,
            future_task_count=len(tasks) - released_task_count,
            active_robot_count=len(scenario.robots) - len(unavailable_robot_ids),
            recent_replan_time_ms=None,
        )
    assignment_replan_window = replan_window_decision.window
    planning_tasks, deferred_tasks = split_tasks_for_planning(
        tasks,
        locked_task_robot_ids,
        assignment_replan_window,
    )

    start_time = time.perf_counter()
    assignments = assign_tasks_beam_search(
        scenario,
        scenario.robots,
        planning_tasks,
        extra_blocked,
        unavailable_robot_ids,
        locked_task_robot_ids,
        preferred_task_robot_ids,
        delayed_unavailable_robot_ids,
        delayed_unavailable_time,
        assignment_replan_window,
        task_limit_per_robot,
    )
    paths, path_failures, charging_visits = build_paths(
        scenario,
        scenario.robots,
        assignments,
        avoid_conflicts,
        extra_blocked,
        unavailable_robot_ids,
        locked_task_robot_ids,
        delayed_blocked,
        delayed_block_time,
        True,
        active_charging_visits,
    )
    assigned_task_ids = {
        task.id
        for assignment in assignments
        for task in assignment.tasks
    }
    capacity_deferred_ids = (
        capacity_deferred_task_ids(
            scenario,
            planning_tasks,
            assignments,
            extra_blocked,
            unavailable_robot_ids,
            locked_task_robot_ids,
        )
        if task_limit_per_robot is not None
        else set()
    )
    failure_tasks = [task for task in planning_tasks if task.id not in capacity_deferred_ids]
    unassigned_failures = [
        f"任务 {task.id} 未分配"
        for task in failure_tasks
        if task.id not in assigned_task_ids
    ]
    failures = [*path_failures, *unassigned_failures]
    failure_details = build_failure_details(
        scenario,
        failure_tasks,
        assignments,
        paths,
        extra_blocked,
        unavailable_robot_ids,
        locked_task_robot_ids,
        delayed_blocked,
        delayed_block_time,
        delayed_unavailable_robot_ids,
        delayed_unavailable_time,
    )
    failure_reasons = {task_id: detail.reason for task_id, detail in failure_details.items()}
    replan_time_ms = round((time.perf_counter() - start_time) * 1000, 2)
    conflicts = detect_conflicts(paths)
    metrics = calculate_metrics(
        paths,
        conflicts,
        assignments,
        failures,
        replan_time_ms,
        failure_count=len(failure_details),
    )
    event_log = build_event_log(
        scenario,
        include_dynamic_events,
        avoid_conflicts,
        assignments,
        paths,
        conflicts,
        failures,
        failure_reasons,
        len(deferred_tasks),
    )

    return DispatchResult(
        scenarioId=scenario.id,
        avoidConflicts=avoid_conflicts,
        includeDynamic=include_dynamic,
        effectiveAssignmentReplanWindow=assignment_replan_window,
        replanWindowReason=replan_window_decision.reason,
        dynamicTriggerTime=scenario.dynamic.triggerTime if include_dynamic_events else None,
        extraBlocked=extra_blocked,
        unavailableRobotIds=unavailable_robot_ids,
        assignments=assignments,
        paths=paths,
        conflicts=conflicts,
        conflictStates=build_conflict_states(conflicts, paths, 0),
        metrics=metrics,
        failureReasons=failure_reasons,
        failureDetails=failure_details,
        chargingVisits=charging_visits,
        eventLog=event_log,
        tasks=tasks,
    )
