from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import HTTPException

from backend.app.dispatch import (
    ASSIGNMENT_REPLAN_WINDOW,
    astar,
    build_conflict_states as _build_conflict_states,
    cell_key,
    has_dynamic_event,
    path_at,
    run_dispatch,
    task_completion_times,
    task_service_time,
    task_waypoints,
)
from backend.app.schemas import (
    AddBlockRequest,
    AddTaskRequest,
    Assignment,
    Cell,
    CreateSessionRequest,
    DeleteSessionResult,
    DispatchOptions,
    DispatchResult,
    DynamicEvent,
    EventItem,
    FailRobotRequest,
    Metrics,
    MetricSnapshot,
    RemoveBlockRequest,
    RestoreRobotRequest,
    RobotRuntimeState,
    Scenario,
    SessionResult,
    SessionSummary,
    SessionTickRequest,
    Task,
    TaskRuntimeState,
)
from backend.app.validation import validate_scenario


SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 50
MAX_METRICS_HISTORY_SNAPSHOTS = 600
MAX_SESSION_EVENT_NOTES = 120
MAX_SESSION_CURRENT_TIME = 10_000
MAX_SESSION_TASKS = 500


def _session_now() -> float:
    return time.time()


@dataclass
class DispatchSession:
    session_id: str
    scenario: Scenario
    options: DispatchOptions
    initial_scenario: Scenario | None = None
    initial_options: DispatchOptions | None = None
    created_at: float = field(default_factory=_session_now)
    updated_at: float = field(default_factory=_session_now)
    last_accessed_at: float = field(default_factory=_session_now)
    current_time: int = 0
    manual_task_count: int = 0
    stream_task_count: int = 0
    planning_started: bool = False
    robot_positions: dict[str, Cell] = field(default_factory=dict)
    robot_path_history: dict[str, list[Cell]] = field(default_factory=dict)
    robot_travelled_distance: dict[str, int] = field(default_factory=dict)
    robot_battery_levels: dict[str, int] = field(default_factory=dict)
    completed_task_ids: set[str] = field(default_factory=set)
    task_completion_times: dict[str, int] = field(default_factory=dict)
    task_payload_positions: dict[str, Cell] = field(default_factory=dict)
    task_waypoint_progress: dict[str, int] = field(default_factory=dict)
    task_service_started_times: dict[str, int] = field(default_factory=dict)
    runtime_blocked_cells: list[Cell] = field(default_factory=list)
    runtime_failed_robot_ids: list[str] = field(default_factory=list)
    event_notes: list[EventItem] = field(default_factory=list)
    rolling_window_event_times: set[int] = field(default_factory=set)
    metrics_history: list[MetricSnapshot] = field(default_factory=list)
    locked_task_robot_ids: dict[str, str] = field(default_factory=dict)
    preferred_task_robot_ids: dict[str, str] = field(default_factory=dict)
    last_result: DispatchResult | None = None


_sessions: dict[str, DispatchSession] = {}


def create_session(request: CreateSessionRequest) -> SessionResult:
    _cleanup_sessions()
    _require_initial_task_capacity(request.scenario)
    diagnostics = validate_scenario(request.scenario, request.options)
    if diagnostics:
        raise HTTPException(status_code=422, detail=diagnostics)
    _prune_sessions_for_capacity(1)

    session_id = str(uuid4())
    now = _session_now()
    session = DispatchSession(
        session_id=session_id,
        scenario=request.scenario.model_copy(deep=True),
        options=request.options.model_copy(deep=True),
        initial_scenario=request.scenario.model_copy(deep=True),
        initial_options=request.options.model_copy(deep=True),
        created_at=now,
        updated_at=now,
        last_accessed_at=now,
        robot_positions={robot.id: robot.start for robot in request.scenario.robots},
        robot_path_history={robot.id: [robot.start] for robot in request.scenario.robots},
        robot_travelled_distance={robot.id: 0 for robot in request.scenario.robots},
        robot_battery_levels={robot.id: robot.battery for robot in request.scenario.robots},
    )
    _sessions[session_id] = session
    return _build_result(session)


def get_session(session_id: str) -> SessionResult:
    return _build_result(_require_session(session_id))


def list_sessions() -> list[SessionSummary]:
    _cleanup_sessions()
    return [_build_session_summary(session) for session in _sessions_by_recent_access()]


def delete_session(session_id: str) -> DeleteSessionResult:
    _cleanup_sessions()
    deleted = _sessions.pop(session_id, None) is not None
    if not deleted:
        raise HTTPException(status_code=404, detail=f"调度会话不存在：{session_id}")
    return DeleteSessionResult(sessionId=session_id, deleted=True)


def reset_session(session_id: str) -> SessionResult:
    session = _require_session(session_id)
    if session.initial_scenario is None or session.initial_options is None:
        raise HTTPException(status_code=409, detail=f"调度会话缺少初始快照，无法重置：{session_id}")

    _reset_session_runtime(session, updated=True)
    return _build_result(session)


def add_task(session_id: str, request: AddTaskRequest) -> SessionResult:
    session = _require_session(session_id)
    task = request.task.model_copy(deep=True)
    if any(existing.id == task.id for existing in _all_known_tasks(session)):
        raise HTTPException(status_code=409, detail=f"任务 ID 已存在：{task.id}")
    _require_task_capacity(session, 1)

    diagnostics = _validate_runtime_task(session, task)
    if diagnostics:
        raise HTTPException(status_code=422, detail=diagnostics)
    session.scenario.tasks.append(task)
    _release_locks_for_active_higher_priority_task(session, task, session.current_time)
    session.manual_task_count += 1
    _invalidate_plan(session)
    _touch_session(session, updated=True)
    _record_session_event(session, session.current_time, f"手动录入任务：{task.id} {task.title}")
    return _build_result(session)


def add_blocked_cell(session_id: str, request: AddBlockRequest) -> SessionResult:
    session = _require_session(session_id)
    current_time = _runtime_request_time(session, request)
    _require_not_past_time(session, current_time)
    cell = request.cell
    if not _is_inside(cell, session.scenario):
        raise HTTPException(status_code=422, detail=f"封锁单元超出地图范围：{cell[0]},{cell[1]}")
    if cell in session.scenario.obstacles:
        raise HTTPException(status_code=409, detail=f"封锁单元已是固定障碍：{cell[0]},{cell[1]}")
    if cell in session.scenario.zones.charging:
        raise HTTPException(status_code=409, detail=f"封锁单元是充电地块：{cell[0]},{cell[1]}")
    is_active_dynamic_block_request = (
        session.options.includeDynamic
        and current_time >= session.scenario.dynamic.triggerTime
        and cell in session.scenario.dynamic.blockedCells
    )
    if not is_active_dynamic_block_request:
        occupying_robot_id = _robot_at_cell_at_time(session, cell, current_time)
        if occupying_robot_id is not None:
            raise HTTPException(status_code=409, detail=f"封锁单元被机器人占用：{occupying_robot_id} ({cell[0]}, {cell[1]})")
    previous_time = session.current_time
    if current_time > session.current_time:
        _ensure_planning_started(session)
    _advance_session(session, current_time)
    is_dynamic_blocked_cell = _is_scenario_dynamic_active(session) and cell in session.scenario.dynamic.blockedCells
    is_new_blocked_cell = cell not in session.runtime_blocked_cells and not is_dynamic_blocked_cell
    if is_new_blocked_cell:
        current_result = session.last_result or _build_result(session).result
        _release_locks_for_blocked_cell(session, current_result, cell, current_time)
        session.runtime_blocked_cells.append(cell)
        _invalidate_plan(session)
        _touch_session(session, updated=True)
        _record_session_event(session, current_time, f"T={current_time} 手动封锁单元：({cell[0]}, {cell[1]})")
    else:
        _touch_session_if_time_changed(session, previous_time)
    return _build_result(session)


def remove_blocked_cell(session_id: str, request: RemoveBlockRequest) -> SessionResult:
    session = _require_session(session_id)
    current_time = _runtime_request_time(session, request)
    _require_not_past_time(session, current_time)
    cell = request.cell
    if not _is_inside(cell, session.scenario):
        raise HTTPException(status_code=422, detail=f"解除封锁单元超出地图范围：{cell[0]},{cell[1]}")
    previous_time = session.current_time
    if current_time > session.current_time:
        _ensure_planning_started(session)
    _advance_session(session, current_time)
    removed = False
    if cell in session.runtime_blocked_cells:
        session.runtime_blocked_cells = [blocked for blocked in session.runtime_blocked_cells if blocked != cell]
        removed = True
    if _is_scenario_dynamic_active(session) and cell in session.scenario.dynamic.blockedCells:
        session.scenario.dynamic = session.scenario.dynamic.model_copy(
            update={
                "blockedCells": [
                    blocked for blocked in session.scenario.dynamic.blockedCells if blocked != cell
                ]
            }
        )
        removed = True
    if removed:
        _invalidate_plan(session)
        _touch_session(session, updated=True)
        _record_session_event(session, current_time, f"T={current_time} 手动解除封锁单元：({cell[0]}, {cell[1]})")
    else:
        _touch_session_if_time_changed(session, previous_time)
    return _build_result(session)


def fail_robot(session_id: str, request: FailRobotRequest) -> SessionResult:
    session = _require_session(session_id)
    current_time = _runtime_request_time(session, request)
    _require_not_past_time(session, current_time)
    robot_ids = {robot.id for robot in session.scenario.robots}
    if request.robotId not in robot_ids:
        raise HTTPException(status_code=404, detail=f"机器人不存在：{request.robotId}")
    previous_time = session.current_time
    if current_time > session.current_time:
        _ensure_planning_started(session)
    _advance_session(session, current_time)
    is_dynamic_failed_robot = _is_scenario_dynamic_active(session) and request.robotId in session.scenario.dynamic.failedRobots
    is_new_failed_robot = request.robotId not in session.runtime_failed_robot_ids and not is_dynamic_failed_robot
    if is_new_failed_robot:
        session.runtime_failed_robot_ids.append(request.robotId)
        _invalidate_plan(session)
        _release_locks_for_robot(session, request.robotId, current_time)
        _touch_session(session, updated=True)
        _record_session_event(session, current_time, f"T={current_time} 手动标记故障机器人：{request.robotId}")
    else:
        _touch_session_if_time_changed(session, previous_time)
    return _build_result(session)


def restore_robot(session_id: str, request: RestoreRobotRequest) -> SessionResult:
    session = _require_session(session_id)
    current_time = _runtime_request_time(session, request)
    _require_not_past_time(session, current_time)
    robot_ids = {robot.id for robot in session.scenario.robots}
    if request.robotId not in robot_ids:
        raise HTTPException(status_code=404, detail=f"机器人不存在：{request.robotId}")
    previous_time = session.current_time
    if current_time > session.current_time:
        _ensure_planning_started(session)
    _advance_session(session, current_time)
    restored = False
    if request.robotId in session.runtime_failed_robot_ids:
        session.runtime_failed_robot_ids = [
            robot_id for robot_id in session.runtime_failed_robot_ids if robot_id != request.robotId
        ]
        restored = True
    if _is_scenario_dynamic_active(session) and request.robotId in session.scenario.dynamic.failedRobots:
        session.scenario.dynamic = session.scenario.dynamic.model_copy(
            update={
                "failedRobots": [
                    robot_id for robot_id in session.scenario.dynamic.failedRobots if robot_id != request.robotId
                ]
            }
        )
        restored = True
    if restored:
        _invalidate_plan(session)
        _touch_session(session, updated=True)
        _record_session_event(session, current_time, f"T={current_time} 手动恢复机器人：{request.robotId}")
    else:
        _touch_session_if_time_changed(session, previous_time)
    return _build_result(session)


def tick_session(session_id: str, request: SessionTickRequest) -> SessionResult:
    session = _require_session(session_id)
    _require_not_past_time(session, request.currentTime)
    previous_time = session.current_time
    if request.currentTime > session.current_time:
        _ensure_planning_started(session)
    _advance_session(session, request.currentTime)
    if session.current_time != previous_time:
        _touch_session(session, updated=True)
    return _build_result(session)


def _require_session(session_id: str) -> DispatchSession:
    _cleanup_sessions()
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"调度会话不存在：{session_id}")
    _touch_session(session)
    return session


def _require_not_past_time(session: DispatchSession, current_time: int) -> None:
    if current_time > MAX_SESSION_CURRENT_TIME:
        raise HTTPException(
            status_code=422,
            detail=f"currentTime must be <= max session time: {current_time} > {MAX_SESSION_CURRENT_TIME}",
        )
    if current_time < session.current_time:
        raise HTTPException(
            status_code=422,
            detail=f"currentTime must be >= session currentTime: {current_time} < {session.current_time}",
        )


def _runtime_request_time(
    session: DispatchSession,
    request: AddBlockRequest | RemoveBlockRequest | FailRobotRequest | RestoreRobotRequest,
) -> int:
    if "currentTime" in request.model_fields_set:
        return request.currentTime
    return session.current_time


def _require_task_capacity(session: DispatchSession, incoming_count: int) -> None:
    current_count = len(_all_known_tasks(session))
    if current_count + incoming_count > MAX_SESSION_TASKS:
        raise HTTPException(
            status_code=409,
            detail=f"调度会话任务数已达上限：{current_count} + {incoming_count} > {MAX_SESSION_TASKS}",
        )


def _require_initial_task_capacity(scenario: Scenario) -> None:
    current_count = len(scenario.tasks) + len(scenario.dynamic.tasks)
    if current_count > MAX_SESSION_TASKS:
        raise HTTPException(
            status_code=409,
            detail=f"调度会话任务数已达上限：{current_count} > {MAX_SESSION_TASKS}",
        )


def _touch_session(session: DispatchSession, updated: bool = False) -> None:
    now = _session_now()
    session.last_accessed_at = now
    if updated:
        session.updated_at = now


def _touch_session_if_time_changed(session: DispatchSession, previous_time: int) -> None:
    if session.current_time != previous_time:
        _touch_session(session, updated=True)


def _reset_session_runtime(session: DispatchSession, updated: bool = False) -> None:
    scenario = session.initial_scenario.model_copy(deep=True) if session.initial_scenario else session.scenario
    options = session.initial_options.model_copy(deep=True) if session.initial_options else session.options
    session.scenario = scenario
    session.options = options
    session.current_time = 0
    session.manual_task_count = 0
    session.stream_task_count = 0
    session.planning_started = False
    session.robot_positions = {robot.id: robot.start for robot in scenario.robots}
    session.robot_path_history = {robot.id: [robot.start] for robot in scenario.robots}
    session.robot_travelled_distance = {robot.id: 0 for robot in scenario.robots}
    session.robot_battery_levels = {robot.id: robot.battery for robot in scenario.robots}
    session.completed_task_ids.clear()
    session.task_completion_times.clear()
    session.task_payload_positions.clear()
    session.task_waypoint_progress.clear()
    session.task_service_started_times.clear()
    session.runtime_blocked_cells.clear()
    session.runtime_failed_robot_ids.clear()
    session.event_notes.clear()
    session.rolling_window_event_times.clear()
    session.metrics_history.clear()
    session.locked_task_robot_ids.clear()
    session.preferred_task_robot_ids.clear()
    session.last_result = None
    _touch_session(session, updated=updated)


def _cleanup_sessions(now: float | None = None) -> None:
    now = _session_now() if now is None else now
    expired_session_ids = [
        session_id
        for session_id, session in _sessions.items()
        if now - session.last_accessed_at > SESSION_TTL_SECONDS
    ]
    for session_id in expired_session_ids:
        _sessions.pop(session_id, None)


def _prune_sessions_for_capacity(incoming_count: int = 0) -> None:
    overflow_count = len(_sessions) + incoming_count - MAX_SESSIONS
    if overflow_count <= 0:
        return

    for session in sorted(_sessions.values(), key=lambda item: item.last_accessed_at)[:overflow_count]:
        _sessions.pop(session.session_id, None)


def _sessions_by_recent_access() -> list[DispatchSession]:
    return sorted(_sessions.values(), key=lambda session: session.last_accessed_at, reverse=True)


def _build_session_summary(session: DispatchSession) -> SessionSummary:
    return SessionSummary(
        sessionId=session.session_id,
        scenarioId=session.scenario.id,
        createdAt=session.created_at,
        updatedAt=session.updated_at,
        lastAccessedAt=session.last_accessed_at,
        currentTime=session.current_time,
        manualTaskCount=session.manual_task_count,
        streamTaskCount=session.stream_task_count,
        runtimeEventCount=len(session.runtime_blocked_cells) + len(session.runtime_failed_robot_ids),
        completedTaskCount=len(session.completed_task_ids),
    )


def _build_result(session: DispatchSession) -> SessionResult:
    result = session.last_result
    if not session.planning_started and _should_delay_initial_planning(session):
        result = _build_idle_result(session)
        session.last_result = result
        result = result.model_copy(update={"eventLog": _build_session_event_log(session, result)})
        robot_states = _build_robot_states(session, result)
        task_states = _build_task_states(session, result)
        snapshot = _build_metric_snapshot(session, result, task_states)
        _record_metric_snapshot(session, snapshot)
        return SessionResult(
            sessionId=session.session_id,
            scenarioId=session.scenario.id,
            options=session.options,
            createdAt=session.created_at,
            updatedAt=session.updated_at,
            lastAccessedAt=session.last_accessed_at,
            currentTime=session.current_time,
            manualTaskCount=session.manual_task_count,
            streamTaskCount=session.stream_task_count,
            runtimeEventCount=len(session.runtime_blocked_cells) + len(session.runtime_failed_robot_ids),
            robotStates=robot_states,
            taskStates=task_states,
            metricsHistory=session.metrics_history,
            completedTaskCount=len(session.completed_task_ids),
            result=result,
        )
    if result is None:
        scenario, options, task_lookup = _build_effective_dispatch_input(session)
        result = _restore_absolute_result(
            run_dispatch(
                scenario,
                options,
                session.locked_task_robot_ids,
                session.preferred_task_robot_ids,
                include_dynamic_events=_include_scenario_dynamic_events(session),
                apply_dynamic_constraints_at_start=True,
                task_limit_per_robot=1,
            ),
            task_lookup,
            session.current_time,
            session,
        )
        _update_task_robot_preferences(session, result)

        session.last_result = result
    result = result.model_copy(update={"eventLog": _build_session_event_log(session, result)})
    robot_states = _build_robot_states(session, result)
    task_states = _build_task_states(session, result)
    _sync_completed_task_states(session, task_states)
    snapshot = _build_metric_snapshot(session, result, task_states)
    _record_metric_snapshot(session, snapshot)

    return SessionResult(
        sessionId=session.session_id,
        scenarioId=session.scenario.id,
        options=session.options,
        createdAt=session.created_at,
        updatedAt=session.updated_at,
        lastAccessedAt=session.last_accessed_at,
        currentTime=session.current_time,
        manualTaskCount=session.manual_task_count,
        streamTaskCount=session.stream_task_count,
        runtimeEventCount=len(session.runtime_blocked_cells) + len(session.runtime_failed_robot_ids),
        robotStates=robot_states,
        taskStates=task_states,
        metricsHistory=session.metrics_history,
        completedTaskCount=len(session.completed_task_ids),
        result=result,
    )


def _ensure_planning_started(session: DispatchSession) -> None:
    if session.planning_started:
        return
    session.planning_started = True
    _invalidate_plan(session)


def _should_delay_initial_planning(session: DispatchSession) -> bool:
    return session.scenario.id == "integrated-demo"


def _build_idle_result(session: DispatchSession) -> DispatchResult:
    dynamic_active = _is_scenario_dynamic_active(session)
    active_dynamic_blocked = session.scenario.dynamic.blockedCells if dynamic_active else []
    active_dynamic_failed = session.scenario.dynamic.failedRobots if dynamic_active else []
    return DispatchResult(
        scenarioId=session.scenario.id,
        avoidConflicts=session.options.avoidConflicts,
        includeDynamic=session.options.includeDynamic,
        dynamicTriggerTime=(
            session.scenario.dynamic.triggerTime
            if _include_scenario_dynamic_events(session)
            else None
        ),
        extraBlocked=_merge_cells(active_dynamic_blocked, session.runtime_blocked_cells),
        unavailableRobotIds=_merge_text(active_dynamic_failed, session.runtime_failed_robot_ids),
        assignments=[],
        paths={
            robot.id: [session.robot_positions.get(robot.id, robot.start)]
            for robot in session.scenario.robots
        },
        conflicts=[],
        metrics=Metrics(
            makespan=session.current_time,
            totalDistance=sum(session.robot_travelled_distance.values()),
            conflictCount=0,
            loadBalance=0,
            assignedTaskCount=0,
            deadlineMissCount=0,
            averageLateness=0,
            failureCount=0,
            replanTimeMs=0,
        ),
        failureReasons={},
        failureDetails={},
        eventLog=list(session.event_notes),
        tasks=_all_tasks(session),
    )


def _build_session_event_log(session: DispatchSession, result: DispatchResult) -> list[EventItem]:
    events = list(session.event_notes)
    if result.conflicts:
        _append_event_if_missing(
            events,
            EventItem(time=session.current_time, text=f"检测到 {len(result.conflicts)} 次路径冲突"),
        )
    events = _preserve_rolling_window_event_history(session, events)
    events = _preserve_triggered_dynamic_event_history(session, events)
    return sorted(events, key=lambda item: item.time)


def _build_effective_dispatch_input(session: DispatchSession) -> tuple[Scenario, DispatchOptions, dict[str, Task]]:
    scenario = session.scenario.model_copy(deep=True)
    options = session.options.model_copy(deep=True)
    task_lookup = _active_task_lookup(session)
    dynamic_active = _is_scenario_dynamic_active(session)

    scenario.robots = [
        robot.model_copy(update={
            "start": session.robot_positions.get(robot.id, robot.start),
            "battery": session.robot_battery_levels.get(robot.id, robot.battery),
        })
        for robot in scenario.robots
    ]
    scenario.tasks = [
        _make_remaining_task(
            task,
            session.current_time,
            session.task_waypoint_progress.get(task.id, 0),
            session.task_payload_positions.get(task.id),
            session.task_service_started_times.get(task.id),
        )
        for task in scenario.tasks
        if task.id not in session.completed_task_ids
    ]
    scenario.tasks = [task for task in scenario.tasks if task is not None]

    base_blocked = scenario.dynamic.blockedCells if dynamic_active else []
    base_failed = scenario.dynamic.failedRobots if dynamic_active else []
    base_tasks = [
        _make_remaining_task(
            task,
            session.current_time,
            session.task_waypoint_progress.get(task.id, 0),
            session.task_payload_positions.get(task.id),
            session.task_service_started_times.get(task.id),
        )
        for task in (scenario.dynamic.tasks if dynamic_active else [])
        if task.id not in session.completed_task_ids
    ]
    base_tasks = [task for task in base_tasks if task is not None]

    blocked_cells = _merge_cells(base_blocked, session.runtime_blocked_cells)
    failed_robot_ids = _merge_text(base_failed, session.runtime_failed_robot_ids)
    relative_dynamic_trigger_time = scenario.dynamic.triggerTime - session.current_time
    scenario.dynamic = scenario.dynamic.model_copy(
        update={
            "triggerTime": relative_dynamic_trigger_time,
            "blockedCells": blocked_cells,
            "failedRobots": failed_robot_ids,
            "tasks": base_tasks,
        }
    )
    if session.runtime_blocked_cells or session.runtime_failed_robot_ids:
        options.includeDynamic = True

    return scenario, options, task_lookup


def _advance_session(session: DispatchSession, target_time: int) -> None:
    if target_time <= session.current_time:
        return

    result = session.last_result
    if result is None:
        result = _build_result(session).result

    dynamic_trigger_time = _next_scenario_dynamic_trigger_time(session, target_time)
    window_trigger_time = _next_rolling_window_trigger_time(session, target_time)
    active_completion_time = _next_active_task_completion_time(session, result, target_time)
    next_trigger_time = _earliest_time(
        _earliest_time(dynamic_trigger_time, window_trigger_time),
        active_completion_time,
    )
    if next_trigger_time is not None and next_trigger_time < target_time:
        _advance_session(session, next_trigger_time)
        _advance_session(session, target_time)
        return

    activate_dynamic_after_advance = dynamic_trigger_time == target_time
    activate_window_after_advance = window_trigger_time == target_time

    for robot in session.scenario.robots:
        path = result.paths.get(robot.id, [session.robot_positions.get(robot.id, robot.start)])
        history = session.robot_path_history.setdefault(robot.id, [robot.start])
        while len(history) <= target_time:
            next_position = path_at(path, len(history)) or history[-1]
            history.append(next_position)
        for tick_time in range(session.current_time + 1, target_time + 1):
            previous = history[tick_time - 1]
            current = history[tick_time]
            if current != previous:
                session.robot_travelled_distance[robot.id] = session.robot_travelled_distance.get(robot.id, 0) + 1
                session.robot_battery_levels[robot.id] = max(0, session.robot_battery_levels.get(robot.id, robot.battery) - 1)
            for visit in result.chargingVisits:
                if visit.robotId != robot.id:
                    continue
                if visit.arrivalTime == tick_time:
                    _record_session_event(session, tick_time, f"{robot.id} 开始充电")
                if visit.completionTime == tick_time:
                    session.robot_battery_levels[robot.id] = robot.batteryCapacity
                    _record_session_event(session, tick_time, f"{robot.id} 完成充电")
        position = history[target_time]
        session.robot_positions[robot.id] = position

    _update_locked_task_assignments(session, result, target_time)
    _update_task_waypoint_progress(session, result, target_time)

    completions = _session_task_completion_times(session, result)
    completed_task = False
    for task_id, completion_time in completions.items():
        if completion_time <= target_time:
            newly_completed = task_id not in session.completed_task_ids
            session.completed_task_ids.add(task_id)
            session.task_completion_times[task_id] = completion_time
            session.task_payload_positions.pop(task_id, None)
            session.preferred_task_robot_ids.pop(task_id, None)
            session.locked_task_robot_ids.pop(task_id, None)
            if newly_completed:
                completed_task = True
                _record_session_event(session, completion_time, f"任务 {task_id} 已完成")

    session.current_time = target_time
    if completed_task:
        _invalidate_plan(session)
    if activate_dynamic_after_advance:
        _activate_scenario_dynamic(session, target_time, result)
    if activate_window_after_advance:
        _activate_rolling_window(session, target_time)


def _invalidate_plan(session: DispatchSession) -> None:
    session.last_result = None


def _record_session_event(session: DispatchSession, event_time: int, text: str) -> None:
    session.event_notes.append(EventItem(time=event_time, text=text))
    if len(session.event_notes) > MAX_SESSION_EVENT_NOTES:
        session.event_notes = session.event_notes[-MAX_SESSION_EVENT_NOTES:]


def _is_scenario_dynamic_active(session: DispatchSession) -> bool:
    return (
        session.options.includeDynamic
        and _has_scenario_dynamic_event(session.scenario.dynamic)
        and session.current_time >= session.scenario.dynamic.triggerTime
    )


def _next_scenario_dynamic_trigger_time(session: DispatchSession, target_time: int) -> int | None:
    if not session.options.includeDynamic or not _has_scenario_dynamic_event(session.scenario.dynamic):
        return None
    trigger_time = session.scenario.dynamic.triggerTime
    if session.current_time < trigger_time <= target_time:
        return trigger_time
    return None


def _next_rolling_window_trigger_time(session: DispatchSession, target_time: int) -> int | None:
    trigger_times = [
        _rolling_window_trigger_time(session, task)
        for task in _all_tasks(session)
        if task.id not in session.completed_task_ids
        and task.id not in session.locked_task_robot_ids
        and _is_task_available_for_rolling_window(session, task)
    ]
    return _first_crossed_time(session.current_time, target_time, trigger_times)


def _next_active_task_completion_time(
    session: DispatchSession,
    result: DispatchResult,
    target_time: int,
) -> int | None:
    completions = _session_task_completion_times(session, result)
    return _first_crossed_time(
        session.current_time,
        target_time,
        [
            completion_time
            for task_id, completion_time in completions.items()
            if task_id not in session.completed_task_ids
        ],
    )


def _is_task_available_for_rolling_window(session: DispatchSession, task: Task) -> bool:
    if task.id in _dynamic_task_ids(session) and not _is_scenario_dynamic_active(session):
        return False
    return True


def _rolling_window_trigger_time(session: DispatchSession, task: Task) -> int:
    release_time = _session_task_release_time(session, task)
    return max(0, release_time - session.options.assignmentReplanWindow)


def _first_crossed_time(current_time: int, target_time: int, times: list[int | None]) -> int | None:
    crossed = [
        time
        for time in times
        if time is not None and current_time < time <= target_time
    ]
    return min(crossed, default=None)


def _earliest_time(first: int | None, second: int | None) -> int | None:
    values = [value for value in (first, second) if value is not None]
    return min(values, default=None)


def _activate_scenario_dynamic(
    session: DispatchSession,
    event_time: int,
    current_result: DispatchResult,
) -> None:
    dynamic = session.scenario.dynamic
    if not _has_scenario_dynamic_event(dynamic):
        return
    if dynamic.blockedCells:
        for blocked_cell in dynamic.blockedCells:
            _release_locks_for_blocked_cell(session, current_result, blocked_cell, event_time)
    for robot_id in dynamic.failedRobots:
        _release_locks_for_robot(session, robot_id, event_time)
    for task in dynamic.tasks:
        _release_locks_for_active_higher_priority_task(
            session,
            _session_dynamic_task(task, dynamic.triggerTime),
            event_time,
        )
    _record_session_event(session, event_time, f"T={event_time} 场景动态事件触发")
    _invalidate_plan(session)


def _activate_rolling_window(session: DispatchSession, event_time: int) -> None:
    session.rolling_window_event_times.add(event_time)
    _record_session_event(session, event_time, f"T={event_time} 滚动窗口纳入远期任务")
    _invalidate_plan(session)


def _restore_absolute_result(
    result: DispatchResult,
    task_lookup: dict[str, Task],
    current_time: int,
    session: DispatchSession,
) -> DispatchResult:
    absolute_paths: dict[str, list[Cell]] = {}
    for robot in session.scenario.robots:
        position = session.robot_positions.get(robot.id, robot.start)
        path = result.paths.get(robot.id, [position])
        history = session.robot_path_history.get(robot.id, [position])
        prefix = _history_prefix(history, position, current_time)
        future_path = path[1:] if len(path) > 1 else []
        absolute_paths[robot.id] = [*prefix, *future_path]

    assignments = [
        Assignment(
            robotId=assignment.robotId,
            tasks=[task_lookup.get(task.id, task) for task in assignment.tasks],
        )
        for assignment in result.assignments
    ]
    tasks = list(task_lookup.values())
    conflicts = [conflict.model_copy(update={"time": conflict.time + current_time}) for conflict in result.conflicts]
    conflict_states = _build_conflict_states(conflicts, absolute_paths, current_time)
    event_log = [event.model_copy(update={"time": event.time + current_time}) for event in result.eventLog]
    charging_visits = [
        visit.model_copy(
            update={
                "departureTime": visit.departureTime + current_time,
                "arrivalTime": visit.arrivalTime + current_time,
                "completionTime": visit.completionTime + current_time,
            }
        )
        for visit in result.chargingVisits
    ]
    dynamic_trigger_time = (
        result.dynamicTriggerTime + current_time if result.dynamicTriggerTime is not None else None
    )
    metrics = result.metrics.model_copy(update={"makespan": result.metrics.makespan + current_time})

    return result.model_copy(
        update={
            "dynamicTriggerTime": dynamic_trigger_time,
            "assignments": assignments,
            "paths": absolute_paths,
            "conflicts": conflicts,
            "conflictStates": conflict_states,
            "metrics": metrics,
            "eventLog": event_log,
            "chargingVisits": charging_visits,
            "tasks": tasks,
        }
    )


def _preserve_triggered_dynamic_event_history(
    session: DispatchSession,
    event_log: list[EventItem],
) -> list[EventItem]:
    if not session.options.includeDynamic or session.initial_scenario is None:
        return event_log

    dynamic = session.initial_scenario.dynamic
    if not _has_scenario_dynamic_event(dynamic):
        return event_log
    if session.current_time < dynamic.triggerTime:
        return event_log

    events = list(event_log)
    _append_event_if_missing(
        events,
        EventItem(time=dynamic.triggerTime, text=f"T={dynamic.triggerTime} 场景动态事件触发"),
    )
    if dynamic.blockedCells:
        events = [
            event
            for event in events
            if not (event.time == dynamic.triggerTime and _is_dynamic_block_count_event(event.text))
        ]
        _append_event_if_missing(
            events,
            EventItem(time=dynamic.triggerTime, text=f"新增 {len(dynamic.blockedCells)} 个封锁单元"),
        )
    for robot_id in dynamic.failedRobots:
        _append_event_if_missing(events, EventItem(time=dynamic.triggerTime, text=f"{robot_id} 故障，退出调度"))
    return sorted(events, key=lambda item: item.time)


def _has_scenario_dynamic_event(dynamic: DynamicEvent) -> bool:
    return has_dynamic_event(dynamic)


def _include_scenario_dynamic_events(session: DispatchSession) -> bool:
    return session.options.includeDynamic and _has_scenario_dynamic_event(session.scenario.dynamic)


def _preserve_rolling_window_event_history(
    session: DispatchSession,
    event_log: list[EventItem],
) -> list[EventItem]:
    events = list(event_log)
    for event_time in sorted(session.rolling_window_event_times):
        _append_event_if_missing(events, EventItem(time=event_time, text=f"T={event_time} 滚动窗口纳入远期任务"))
    return sorted(events, key=lambda item: item.time)


def _append_event_if_missing(events: list[EventItem], event: EventItem) -> None:
    if any(existing.time == event.time and existing.text == event.text for existing in events):
        return
    events.append(event)


def _is_dynamic_block_count_event(text: str) -> bool:
    return text.startswith("新增 ") and text.endswith(" 个封锁单元")


def _build_robot_states(session: DispatchSession, result: DispatchResult) -> list[RobotRuntimeState]:
    states: list[RobotRuntimeState] = []
    for robot in session.scenario.robots:
        position = path_at(result.paths.get(robot.id, []), session.current_time) or session.robot_positions.get(robot.id, robot.start)
        current_task = _current_task(session, result, robot.id, session.current_time)
        current_task_id = current_task.id if current_task is not None else None
        charging_visit = next(
            (
                visit
                for visit in result.chargingVisits
                if visit.robotId == robot.id and visit.departureTime <= session.current_time < visit.completionTime
            ),
            None,
        )
        if robot.id in result.unavailableRobotIds:
            status = "failed"
        elif charging_visit is not None and session.current_time >= charging_visit.arrivalTime:
            status = "charging"
        elif charging_visit is not None:
            status = "toCharge"
        elif current_task is not None:
            status = _robot_task_status(current_task, result.paths.get(robot.id, []), session.current_time)
        elif _has_future_task(result, robot.id, session.current_time):
            status = "waiting"
        else:
            status = "idle"
        states.append(
            RobotRuntimeState(
                robotId=robot.id,
                name=robot.name,
                position=position,
                status=status,
                battery=session.robot_battery_levels.get(robot.id, robot.battery),
                batteryCapacity=robot.batteryCapacity,
                load=robot.load,
                moveTicks=robot.moveTicks,
                currentTaskId=current_task_id,
            )
        )
    return states


def _build_task_states(session: DispatchSession, result: DispatchResult) -> list[TaskRuntimeState]:
    completions = _session_task_completion_times(session, result)
    assigned_robot_ids = {
        task.id: assignment.robotId
        for assignment in result.assignments
        for task in assignment.tasks
    }
    states: list[TaskRuntimeState] = []
    for task in _all_tasks(session):
        release_time = _session_task_release_time(session, task)
        completion_time = completions.get(task.id)
        assigned_robot_id = assigned_robot_ids.get(task.id)
        failure_reason = result.failureReasons.get(task.id)
        failure_detail = result.failureDetails.get(task.id)
        if task.id in session.completed_task_ids or (
            completion_time is not None and completion_time <= session.current_time
        ):
            status = "completed"
        elif session.current_time < release_time:
            status = "pending"
        elif failure_reason is not None and completion_time is None:
            status = "unassigned"
        elif assigned_robot_id is None:
            status = "pending"
        else:
            status = "running"
        states.append(
            TaskRuntimeState(
                taskId=task.id,
                status=status,
                assignedRobotId=assigned_robot_id,
                releaseTime=release_time,
                completionTime=completion_time,
                locked=task.id in session.locked_task_robot_ids,
                failureReason=failure_reason,
                failureCategory=failure_detail.category if failure_detail is not None else None,
                recoveryAction=failure_detail.recoveryAction if failure_detail is not None else None,
            )
        )
    return states


def _sync_completed_task_states(session: DispatchSession, task_states: list[TaskRuntimeState]) -> None:
    for state in task_states:
        if state.status != "completed" or state.completionTime is None:
            continue
        if state.completionTime > session.current_time:
            continue
        session.completed_task_ids.add(state.taskId)
        session.task_completion_times[state.taskId] = state.completionTime
        session.task_payload_positions.pop(state.taskId, None)
        session.preferred_task_robot_ids.pop(state.taskId, None)
        session.locked_task_robot_ids.pop(state.taskId, None)


def _build_metric_snapshot(
    session: DispatchSession,
    result: DispatchResult,
    task_states: list[TaskRuntimeState],
) -> MetricSnapshot:
    return MetricSnapshot(
        time=session.current_time,
        completedTaskCount=sum(1 for state in task_states if state.status == "completed"),
        activeTaskCount=sum(1 for state in task_states if state.status == "running"),
        pendingTaskCount=sum(1 for state in task_states if state.status == "pending"),
        travelledDistance=sum(session.robot_travelled_distance.values()),
        activeConflictCount=sum(1 for conflict in result.conflictStates if conflict.status == "active"),
        deadlineMissCount=_deadline_miss_count(session, task_states),
        replanTimeMs=result.metrics.replanTimeMs,
    )


def _record_metric_snapshot(session: DispatchSession, snapshot: MetricSnapshot) -> None:
    session.metrics_history = [item for item in session.metrics_history if item.time != snapshot.time]
    session.metrics_history.append(snapshot)
    session.metrics_history.sort(key=lambda item: item.time)
    if len(session.metrics_history) > MAX_METRICS_HISTORY_SNAPSHOTS:
        session.metrics_history = session.metrics_history[-MAX_METRICS_HISTORY_SNAPSHOTS:]


def _deadline_miss_count(session: DispatchSession, task_states: list[TaskRuntimeState]) -> int:
    states = {state.taskId: state for state in task_states}
    miss_count = 0
    for task in _all_tasks(session):
        if task.deadline is None:
            continue
        state = states.get(task.id)
        if state is None:
            continue
        if state.status == "pending":
            continue
        if state.completionTime is not None and state.completionTime <= session.current_time:
            if state.completionTime > task.deadline:
                miss_count += 1
        elif session.current_time > task.deadline:
            miss_count += 1
    return miss_count


def _session_task_release_time(session: DispatchSession, task: Task) -> int:
    if task.id in _dynamic_task_ids(session) and session.options.includeDynamic:
        task_release = task.releaseTime if task.releaseTime is not None else session.scenario.dynamic.triggerTime
        return max(task_release, session.scenario.dynamic.triggerTime)
    if task.releaseTime is not None:
        return task.releaseTime
    return 0


def _dynamic_task_ids(session: DispatchSession) -> set[str]:
    return {task.id for task in session.scenario.dynamic.tasks}


def _update_task_robot_preferences(session: DispatchSession, result: DispatchResult) -> None:
    unavailable_robot_ids = set(result.unavailableRobotIds)
    for task_id in session.completed_task_ids:
        session.preferred_task_robot_ids.pop(task_id, None)

    for task_id, robot_id in list(session.preferred_task_robot_ids.items()):
        if robot_id in unavailable_robot_ids:
            session.preferred_task_robot_ids.pop(task_id, None)

    for assignment in result.assignments:
        for task in assignment.tasks:
            if task.id in session.completed_task_ids:
                continue
            if task.id in result.failureReasons:
                continue
            session.preferred_task_robot_ids[task.id] = assignment.robotId


def _session_task_completion_times(session: DispatchSession, result: DispatchResult) -> dict[str, int]:
    completions = dict(session.task_completion_times)
    for assignment in result.assignments:
        path = result.paths.get(assignment.robotId, [])
        cursor_index = min(session.current_time, max(0, len(path) - 1))
        for task in assignment.tasks:
            if task.id in completions:
                cursor_index = max(cursor_index, completions[task.id] + 1)
                continue
            completed_before = session.task_waypoint_progress.get(task.id, 0)
            completed_count, cursor_index, completion_index = _completed_remaining_waypoints(
                path,
                task,
                completed_before,
                cursor_index,
                len(path) - 1,
            )
            if _is_task_fully_completed(task, completed_count):
                service_started_at = session.task_service_started_times.get(task.id, completion_index)
                if service_started_at is not None:
                    completion_time = service_started_at + task_service_time(task)
                    completions[task.id] = completion_time
                    cursor_index = completion_time + 1
    return completions


def _update_locked_task_assignments(session: DispatchSession, result: DispatchResult, target_time: int) -> None:
    completions = _session_task_completion_times(session, result)
    start_times = _session_task_start_times(session, result)
    for assignment in result.assignments:
        for task in assignment.tasks:
            start_time = start_times.get(task.id)
            completion_time = completions.get(task.id)
            if start_time is None or target_time < start_time:
                continue
            if completion_time is not None and completion_time <= target_time:
                continue
            if session.locked_task_robot_ids.get(task.id) == assignment.robotId:
                continue
            session.locked_task_robot_ids[task.id] = assignment.robotId
            _record_session_event(session, target_time, f"T={target_time} 任务 {task.id} 锁定给机器人 {assignment.robotId}")


def _session_task_start_times(session: DispatchSession, result: DispatchResult) -> dict[str, int]:
    starts: dict[str, int] = {}
    for assignment in result.assignments:
        path = result.paths.get(assignment.robotId, [])
        cursor_index = min(session.current_time, max(0, len(path) - 1))
        for task in assignment.tasks:
            start_index, cursor_index = _task_execution_window(path, task, cursor_index)
            if start_index is not None:
                starts[task.id] = start_index
    return starts


def _task_execution_window(path: list[Cell], task: Task, start_index: int) -> tuple[int | None, int]:
    if not path:
        return None, start_index

    waypoints = task_waypoints(task)
    if not waypoints:
        return None, start_index

    release_time = task.releaseTime if task.releaseTime is not None else 0
    cursor_index = max(start_index, release_time)
    if cursor_index >= len(path):
        return None, start_index

    completion_index: int | None = None
    for waypoint in waypoints:
        found_index = _find_next_visit_until(path, waypoint, cursor_index, len(path) - 1)
        if found_index is None:
            return None, cursor_index
        completion_index = found_index
        cursor_index = found_index

    next_cursor_index = completion_index + task_service_time(task) + 1 if completion_index is not None else cursor_index
    return max(start_index, release_time), next_cursor_index


def _update_task_waypoint_progress(session: DispatchSession, result: DispatchResult, target_time: int) -> None:
    for assignment in result.assignments:
        path = result.paths.get(assignment.robotId, [])
        cursor_index = min(session.current_time, max(0, len(path) - 1))
        for task in assignment.tasks:
            completion_time = session.task_completion_times.get(task.id)
            if completion_time is not None:
                cursor_index = max(cursor_index, completion_time + 1)
                continue
            completed_before = session.task_waypoint_progress.get(task.id, 0)
            completed_count, cursor_index, completion_index = _completed_remaining_waypoints(
                path,
                task,
                completed_before,
                cursor_index,
                target_time,
            )
            if completed_count > completed_before:
                session.task_waypoint_progress[task.id] = completed_count
            _update_payload_position(session, task, path, target_time, completed_count)
            if _is_task_fully_completed(task, completed_count):
                service_started_at = session.task_service_started_times.get(task.id, completion_index)
                if service_started_at is not None:
                    session.task_service_started_times.setdefault(task.id, service_started_at)
                    completion_time = service_started_at + task_service_time(task)
                    cursor_index = max(cursor_index, completion_time + 1)
                    if completion_time <= target_time:
                        session.task_completion_times[task.id] = completion_time


def _completed_remaining_waypoints(
    path: list[Cell],
    task: Task,
    completed_before: int,
    start_index: int,
    target_time: int,
) -> tuple[int, int, int | None]:
    if not path:
        return completed_before, start_index, None

    waypoints = task_waypoints(task)
    end_index = min(target_time, len(path) - 1)
    release_time = task.releaseTime if task.releaseTime is not None else 0
    if target_time < release_time:
        return completed_before, start_index, None
    cursor_index = max(start_index, release_time)
    if cursor_index > end_index:
        return completed_before, cursor_index, None
    completed_count = min(completed_before, len(waypoints))
    completion_index: int | None = None
    for waypoint in waypoints[completed_count:]:
        found_index = _find_next_visit_until(path, waypoint, cursor_index, end_index)
        if found_index is None:
            break
        completed_count += 1
        cursor_index = found_index
        completion_index = found_index
    return completed_count, cursor_index, completion_index


def _is_task_fully_completed(task: Task, completed_count: int) -> bool:
    waypoints = task_waypoints(task)
    return bool(waypoints) and completed_count >= len(waypoints)


def _update_payload_position(
    session: DispatchSession,
    task: Task,
    path: list[Cell],
    target_time: int,
    completed_count: int,
) -> None:
    if task.type != "delivery":
        return
    waypoints = task_waypoints(task)
    if len(waypoints) < 2 or completed_count <= 0:
        return
    if completed_count >= len(waypoints):
        session.task_payload_positions.pop(task.id, None)
        return
    position = path_at(path, target_time)
    if position is not None:
        session.task_payload_positions[task.id] = position


def _find_next_visit_until(path: list[Cell], waypoint: Cell, start_index: int, end_index: int) -> int | None:
    for index in range(start_index, end_index + 1):
        if path[index] == waypoint:
            return index
    return None


def _release_locks_for_robot(session: DispatchSession, robot_id: str, event_time: int) -> None:
    released_task_ids = sorted(
        task_id
        for task_id, locked_robot_id in session.locked_task_robot_ids.items()
        if locked_robot_id == robot_id
    )
    for task_id in released_task_ids:
        session.locked_task_robot_ids.pop(task_id, None)
    if released_task_ids:
        _record_session_event(
            session,
            event_time,
            f"T={event_time} 机器人 {robot_id} 故障，释放锁定任务：{', '.join(released_task_ids)}"
        )


def _release_locks_for_blocked_cell(
    session: DispatchSession,
    result: DispatchResult,
    blocked_cell: Cell,
    event_time: int,
) -> None:
    completions = _session_task_completion_times(session, result)
    released_task_ids: list[str] = []
    for assignment in result.assignments:
        path = result.paths.get(assignment.robotId, [])
        for task in assignment.tasks:
            if session.locked_task_robot_ids.get(task.id) != assignment.robotId:
                continue
            completion_time = completions.get(task.id)
            if completion_time is not None and completion_time <= event_time:
                continue
            end_time = completion_time if completion_time is not None else len(path) - 1
            if _path_contains_cell(path, blocked_cell, event_time, end_time):
                released_task_ids.append(task.id)

    for task_id in sorted(set(released_task_ids)):
        session.locked_task_robot_ids.pop(task_id, None)
    if released_task_ids:
        _record_session_event(
            session,
            event_time,
            f"T={event_time} 新封锁影响路径，释放锁定任务：{', '.join(sorted(set(released_task_ids)))}"
        )


def _release_locks_for_active_higher_priority_task(
    session: DispatchSession,
    task: Task,
    event_time: int,
) -> None:
    release_time = task.releaseTime if task.releaseTime is not None else 0
    if release_time > event_time:
        return

    tasks_by_id = {existing.id: existing for existing in _all_tasks(session)}
    candidate_task_ids = [
        task_id
        for task_id in session.locked_task_robot_ids
        if task_id not in session.completed_task_ids
        and task.priority > tasks_by_id.get(task_id, task).priority
    ]
    if not candidate_task_ids:
        return

    current_locks = dict(session.locked_task_robot_ids)
    proposed_locks = {
        task_id: robot_id
        for task_id, robot_id in current_locks.items()
        if task_id not in candidate_task_ids
    }
    scenario, options, _ = _build_effective_dispatch_input(session)
    current_result = run_dispatch(
        scenario,
        options,
        current_locks,
        apply_dynamic_constraints_at_start=True,
        task_limit_per_robot=1,
    )
    proposed_result = run_dispatch(
        scenario,
        options,
        proposed_locks,
        apply_dynamic_constraints_at_start=True,
        task_limit_per_robot=1,
    )

    if _preemption_plan_score(proposed_result, task.id) >= _preemption_plan_score(current_result, task.id):
        return

    for task_id in sorted(candidate_task_ids):
        session.locked_task_robot_ids.pop(task_id, None)
    _record_session_event(
        session,
        event_time,
        f"T={event_time} 高优先级任务 {task.id} 评分更优，释放低优先级锁定任务："
        f"{', '.join(sorted(candidate_task_ids))}"
    )


def _preemption_plan_score(result: DispatchResult, trigger_task_id: str) -> tuple[float, ...]:
    completions = task_completion_times(result.assignments, result.paths)
    trigger_task = _find_result_task(result, trigger_task_id)
    trigger_completion = completions.get(trigger_task_id)
    trigger_missing = 1 if trigger_completion is None else 0
    trigger_lateness = 0
    if trigger_task is not None and trigger_task.deadline is not None and trigger_completion is not None:
        trigger_lateness = max(0, trigger_completion - trigger_task.deadline)
    return (
        trigger_missing,
        trigger_lateness,
        trigger_completion if trigger_completion is not None else 1_000_000,
        result.metrics.deadlineMissCount,
        result.metrics.failureCount,
        len(result.conflicts),
        result.metrics.averageLateness,
        result.metrics.makespan,
        result.metrics.totalDistance,
    )


def _find_result_task(result: DispatchResult, task_id: str) -> Task | None:
    for task in result.tasks:
        if task.id == task_id:
            return task
    return None


def _current_task(session: DispatchSession, result: DispatchResult, robot_id: str, current_time: int) -> Task | None:
    completions = _session_task_completion_times(session, result)
    assignment = next((item for item in result.assignments if item.robotId == robot_id), None)
    if assignment is None:
        return None
    for task in assignment.tasks:
        release_time = task.releaseTime if task.releaseTime is not None else 0
        completion_time = completions.get(task.id)
        if current_time >= release_time and (completion_time is None or current_time < completion_time):
            return task
    return None


def _robot_task_status(task: Task, path: list[Cell], current_time: int) -> str:
    if task.type == "delivery":
        pickup = task_waypoints(task)[0] if task_waypoints(task) else None
        release_time = task.releaseTime if task.releaseTime is not None else 0
        if pickup is not None and not _has_visited(path, pickup, current_time, release_time):
            return "toPickup"
        return "delivering"
    return "inspecting"


def _has_future_task(result: DispatchResult, robot_id: str, current_time: int) -> bool:
    assignment = next((item for item in result.assignments if item.robotId == robot_id), None)
    if assignment is None:
        return False
    return any((task.releaseTime if task.releaseTime is not None else 0) > current_time for task in assignment.tasks)


def _has_visited(path: list[Cell], target: Cell, current_time: int, start_time: int = 0) -> bool:
    start_index = max(0, min(start_time, len(path) - 1))
    end_index = min(current_time, len(path) - 1)
    return any(path[index] == target for index in range(start_index, end_index + 1))


def _path_distance_between(path: list[Cell], start_time: int, target_time: int) -> int:
    if not path or target_time <= start_time:
        return 0
    end_index = min(target_time, len(path) - 1)
    distance = 0
    for index in range(start_time + 1, end_index + 1):
        if path[index] != path[index - 1]:
            distance += 1
    return distance


def _path_contains_cell(path: list[Cell], cell: Cell, start_time: int, end_time: int) -> bool:
    if not path:
        return False
    start_index = max(0, min(start_time, len(path) - 1))
    end_index = max(start_index, min(end_time, len(path) - 1))
    return any(path[index] == cell for index in range(start_index, end_index + 1))


def _robot_at_cell_at_time(session: DispatchSession, cell: Cell, target_time: int) -> str | None:
    result = session.last_result or _preview_dispatch_result(session)
    for robot in session.scenario.robots:
        position = path_at(result.paths.get(robot.id, []), target_time) or session.robot_positions.get(robot.id, robot.start)
        if position == cell:
            return robot.id
    return None


def _preview_dispatch_result(session: DispatchSession) -> DispatchResult:
    scenario, options, task_lookup = _build_effective_dispatch_input(session)
    return _restore_absolute_result(
        run_dispatch(
            scenario,
            options,
            session.locked_task_robot_ids,
            session.preferred_task_robot_ids,
            include_dynamic_events=_include_scenario_dynamic_events(session),
            apply_dynamic_constraints_at_start=True,
            task_limit_per_robot=1,
        ),
        task_lookup,
        session.current_time,
        session,
    )


def _validate_runtime_task(session: DispatchSession, task: Task) -> list[str]:
    errors: list[str] = []
    waypoints = task_waypoints(task)
    if not waypoints:
        return [f"任务目标缺失：{task.id} {task.title}"]

    fixed_obstacles = {cell_key(cell) for cell in session.scenario.obstacles}
    for index, cell in enumerate(waypoints):
        if not _is_inside(cell, session.scenario):
            errors.append(f"任务 {task.id} 目标 {index + 1} 坐标超出地图范围：({cell[0]}, {cell[1]})")
        elif cell_key(cell) in fixed_obstacles:
            errors.append(f"任务 {task.id} 目标 {index + 1} 位于固定障碍：({cell[0]}, {cell[1]})")

    if not errors and not _has_reachable_robot_for_task_definition(session, task, waypoints):
        errors.append(f"任务不可达：{task.id} {task.title}")
    return errors


def _has_reachable_robot_for_task_definition(session: DispatchSession, task: Task, waypoints: list[Cell]) -> bool:
    # 运行时封锁和故障是可恢复状态，新任务应进入队列并由 failureDetails 暴露恢复动作。
    for robot in session.scenario.robots:
        if task.type == "delivery" and robot.load < (task.demand or 1):
            continue
        cursor = session.robot_positions.get(robot.id, robot.start)
        reachable = True
        for waypoint in waypoints:
            path = astar(session.scenario, cursor, waypoint, [])
            if not path:
                reachable = False
                break
            cursor = waypoint
        if reachable:
            return True
    return False


def _active_task_lookup(session: DispatchSession) -> dict[str, Task]:
    tasks = [*session.scenario.tasks]
    if session.options.includeDynamic:
        tasks.extend(_session_dynamic_tasks(session))
    return {task.id: task for task in tasks}


def _all_tasks(session: DispatchSession) -> list[Task]:
    tasks = [*session.scenario.tasks]
    if session.options.includeDynamic:
        tasks.extend(_session_dynamic_tasks(session))
    return tasks


def _all_known_tasks(session: DispatchSession) -> list[Task]:
    return [*session.scenario.tasks, *session.scenario.dynamic.tasks]


def _session_dynamic_tasks(session: DispatchSession) -> list[Task]:
    return [
        _session_dynamic_task(task, session.scenario.dynamic.triggerTime)
        for task in session.scenario.dynamic.tasks
    ]


def _session_dynamic_task(task: Task, trigger_time: int) -> Task:
    if task.releaseTime is not None and task.releaseTime >= trigger_time:
        return task
    return task.model_copy(update={"releaseTime": trigger_time})


def _make_relative_task(task: Task, current_time: int) -> Task:
    release_time = task.releaseTime if task.releaseTime is not None else 0
    deadline = task.deadline if task.deadline is not None else None
    return task.model_copy(
        update={
            "releaseTime": max(0, release_time - current_time),
            "deadline": max(0, deadline - current_time) if deadline is not None else None,
        }
    )


def _make_remaining_task(
    task: Task,
    current_time: int,
    completed_waypoint_count: int,
    payload_position: Cell | None,
    service_started_at: int | None,
) -> Task | None:
    relative_task = _make_relative_task(task, current_time)
    if completed_waypoint_count <= 0:
        return relative_task

    waypoints = task_waypoints(task)
    remaining = waypoints[completed_waypoint_count:]
    if not remaining:
        if not waypoints or service_started_at is None:
            return None
        remaining_service_time = max(0, task_service_time(task) - (current_time - service_started_at))
        if remaining_service_time <= 0:
            return None
        service_task = relative_task.model_copy(update={"serviceTime": remaining_service_time})
        endpoint = waypoints[-1]
        if task.type == "inspection":
            return service_task.model_copy(update={"targets": [endpoint]})
        if task.type == "delivery":
            return service_task.model_copy(update={"pickup": endpoint, "dropoff": endpoint, "target": None})
        return service_task.model_copy(update={"target": endpoint})

    if task.type == "inspection":
        return relative_task.model_copy(update={"targets": remaining})
    if task.type == "delivery":
        if completed_waypoint_count >= 1:
            return relative_task.model_copy(
                update={
                    "pickup": payload_position or task.pickup or remaining[0],
                    "dropoff": remaining[0],
                    "target": None,
                }
            )
        return relative_task
    return relative_task.model_copy(update={"target": remaining[0]})


def _history_prefix(history: list[Cell], fallback_position: Cell, current_time: int) -> list[Cell]:
    if current_time < 0:
        return []
    prefix = history[: current_time + 1]
    if not prefix:
        prefix = [fallback_position]
    while len(prefix) <= current_time:
        prefix.append(prefix[-1])
    return prefix


def _first_available(cells: list[Cell], fallback: Cell) -> Cell:
    return cells[0] if cells else fallback


def _is_inside(cell: Cell, scenario: Scenario) -> bool:
    return 0 <= cell[0] < scenario.width and 0 <= cell[1] < scenario.height


def _merge_cells(first: list[Cell], second: list[Cell]) -> list[Cell]:
    merged: list[Cell] = []
    for cell in [*first, *second]:
        if cell not in merged:
            merged.append(cell)
    return merged


def _merge_text(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*first, *second]:
        if value not in merged:
            merged.append(value)
    return merged
