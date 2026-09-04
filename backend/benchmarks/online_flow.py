from dataclasses import dataclass

from backend.app.schemas import (
    AddBlockRequest,
    AddTaskRequest,
    CreateSessionRequest,
    DispatchOptions,
    FailRobotRequest,
    RemoveBlockRequest,
    RestoreRobotRequest,
    Scenario,
    SessionResult,
    SessionTickRequest,
    Task,
)
from backend.app.sessions import (
    SessionRegistry,
    add_blocked_cell,
    add_task,
    create_session,
    delete_session,
    fail_robot,
    remove_blocked_cell,
    restore_robot,
    tick_session,
)
from backend.benchmarks.scenarios import BenchmarkCase
from backend.app.replan_window import ReplanObservation


@dataclass(frozen=True, slots=True)
class OnlineFlowExecution:
    session: SessionResult
    safety_intervention_count: int
    observations: tuple[ReplanObservation, ...]
    runtime_mutation_count: int


def _tick_to(
    session_id: str,
    session: SessionResult,
    target_time: int,
    *,
    registry: SessionRegistry,
) -> tuple[SessionResult, int]:
    safety_intervention_count = 0
    while session.currentTime < target_time:
        session = tick_session(
            session_id,
            SessionTickRequest(currentTime=session.currentTime + 1),
            registry=registry,
        )
        if session.safetyIntervention is not None:
            safety_intervention_count += 1
    return session, safety_intervention_count


def execute_online_flow(
    case: BenchmarkCase,
    scenario: Scenario,
    options: DispatchOptions,
    *,
    registry: SessionRegistry | None = None,
) -> OnlineFlowExecution:
    if case.mode != "online":
        raise ValueError(f"基准案例不是在线流程：{case.case_id}")

    resolved_registry = registry if registry is not None else SessionRegistry(max_sessions=1)
    observations: list[ReplanObservation] = []
    session_id: str | None = None
    session: SessionResult | None = None
    safety_intervention_count = 0
    runtime_mutation_count = 0

    try:
        session = create_session(
            CreateSessionRequest(scenario=scenario, options=options),
            enforce_execution_safety=True,
            replan_observer=observations.append,
            registry=resolved_registry,
        )
        session_id = session.sessionId

        if case.case_id == "online-pressure-s17-r4-t17":
            session, count = _tick_to(
                session_id,
                session,
                8,
                registry=resolved_registry,
            )
            safety_intervention_count += count

            session = add_task(
                session_id,
                AddTaskRequest(
                    task=Task(
                        id="RUNTIME-SEED-17",
                        type="emergency",
                        title="固定种子运行时复核",
                        priority=5,
                        releaseTime=8,
                        deadline=28,
                        target=(2, 9),
                    )
                ),
                registry=resolved_registry,
            )
            runtime_mutation_count += 1
            session = add_blocked_cell(
                session_id,
                AddBlockRequest(cell=(3, 9), currentTime=8),
                registry=resolved_registry,
            )
            runtime_mutation_count += 1
            session = fail_robot(
                session_id,
                FailRobotRequest(robotId="R4", currentTime=8),
                registry=resolved_registry,
            )
            runtime_mutation_count += 1
            session = restore_robot(
                session_id,
                RestoreRobotRequest(robotId="R4", currentTime=8),
                registry=resolved_registry,
            )
            runtime_mutation_count += 1
            session = remove_blocked_cell(
                session_id,
                RemoveBlockRequest(cell=(3, 9), currentTime=8),
                registry=resolved_registry,
            )
            runtime_mutation_count += 1

            session, count = _tick_to(
                session_id,
                session,
                18,
                registry=resolved_registry,
            )
            safety_intervention_count += count

            session = add_task(
                session_id,
                AddTaskRequest(
                    task=Task(
                        id="G-SEED-17",
                        type="inspection",
                        title="随机生成巡检",
                        priority=0,
                        releaseTime=18,
                        deadline=42,
                        targets=[
                            scenario.zones.inspection[0]
                            if scenario.zones.inspection
                            else scenario.robots[0].start
                        ],
                    )
                ),
                registry=resolved_registry,
            )
            runtime_mutation_count += 1

            session, count = _tick_to(
                session_id,
                session,
                20,
                registry=resolved_registry,
            )
            safety_intervention_count += count
        else:
            if case.tick_target is None:
                raise ValueError(f"在线基准案例缺少 tick 目标：{case.case_id}")
            session, safety_intervention_count = _tick_to(
                session_id,
                session,
                case.tick_target,
                registry=resolved_registry,
            )
    finally:
        if session_id is not None:
            delete_session(session_id, registry=resolved_registry)

    if session is None:
        raise RuntimeError(f"在线基准案例未创建会话：{case.case_id}")
    return OnlineFlowExecution(
        session=session,
        safety_intervention_count=safety_intervention_count,
        observations=tuple(observations),
        runtime_mutation_count=runtime_mutation_count,
    )
