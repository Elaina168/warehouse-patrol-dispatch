"""通过既有会话服务层执行 3S 提交演示。"""

import json
from dataclasses import dataclass
from pathlib import Path

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
    add_blocked_cell,
    add_task,
    create_session,
    delete_session,
    fail_robot,
    get_session,
    remove_blocked_cell,
    restore_robot,
    tick_session,
)
from backend.competition.manifests import (
    PROJECT_ROOT,
    load_main_demo_manifest,
    load_safety_demo_manifest,
)


@dataclass(frozen=True)
class MainDemoEvidence:
    """主演示每个固定时间点的结构化证据。"""

    session_id: str
    checkpoints: dict[int, SessionResult]
    step_evidence: tuple["MainDemoStepEvidence", ...]
    final: SessionResult


@dataclass(frozen=True)
class MainDemoStepEvidence:
    """主演示单个清单步骤完成后的独立证据。"""

    time: int
    action: str
    result: SessionResult


@dataclass(frozen=True)
class SafetyDemoEvidence:
    """安全门拦截和实际轨迹检查的结构化证据。"""

    session_id: str
    checkpoints: dict[int, SessionResult]
    collision_free: bool


def run_main_demo() -> MainDemoEvidence:
    """执行固定主演示，并无论结果如何删除临时会话。"""
    manifest = load_main_demo_manifest()
    scenario = Scenario.model_validate(_load_frontend_scenario(manifest["scenarioId"]))
    options = DispatchOptions.model_validate(manifest["options"])
    created = create_session(CreateSessionRequest(scenario=scenario, options=options))
    session_id = created.sessionId
    checkpoints: dict[int, SessionResult] = {}
    step_evidence: list[MainDemoStepEvidence] = []
    try:
        for step in manifest["steps"]:
            current = tick_session(session_id, SessionTickRequest(currentTime=step["time"]))
            _require_step_time(current, step["time"])
            action = step["action"]
            _require_no_safety_intervention(current, step["time"], f"{action} 前")
            if action == "addTask":
                current = add_task(
                    session_id,
                    AddTaskRequest(task=Task.model_validate(step["task"])),
                )
                _require("DEMO-URGENT-12" in {state.taskId for state in current.taskStates}, "T=12 缺少主演示任务")
            elif action == "addBlockedCell":
                current = add_blocked_cell(
                    session_id,
                    AddBlockRequest(cell=step["cell"], currentTime=step["time"]),
                )
                _require(tuple(step["cell"]) in current.result.extraBlocked, "T=20 封锁未生效")
            elif action == "failRobot":
                current = fail_robot(
                    session_id,
                    FailRobotRequest(robotId=step["robotId"], currentTime=step["time"]),
                )
                _require(step["robotId"] in current.result.unavailableRobotIds, "T=28 故障未生效")
            elif action == "removeBlockedCell":
                current = remove_blocked_cell(
                    session_id,
                    RemoveBlockRequest(cell=step["cell"], currentTime=step["time"]),
                )
                _require(tuple(step["cell"]) not in current.result.extraBlocked, "T=36 封锁未解除")
            elif action == "restoreRobot":
                current = restore_robot(
                    session_id,
                    RestoreRobotRequest(robotId=step["robotId"], currentTime=step["time"]),
                )
                _require(tuple([10, 6]) not in current.result.extraBlocked, "T=36 封锁未解除")
                _require("R2" not in current.result.unavailableRobotIds, "T=36 机器人未恢复")
            elif action == "accept":
                _validate_main_acceptance(current)
            else:
                raise RuntimeError(f"不支持的主演示步骤：{action}")
            _require_step_time(current, step["time"])
            _require_no_safety_intervention(current, step["time"], action)
            step_evidence.append(
                MainDemoStepEvidence(
                    time=step["time"],
                    action=action,
                    result=current,
                )
            )
            checkpoints[step["time"]] = get_session(session_id)
        final = checkpoints[700]
        return MainDemoEvidence(
            session_id=session_id,
            checkpoints=checkpoints,
            step_evidence=tuple(step_evidence),
            final=final,
        )
    finally:
        delete_session(session_id)


def run_safety_demo() -> SafetyDemoEvidence:
    """执行安全门夹具，证明拦截、停滞阈值与实际轨迹安全。"""
    manifest = load_safety_demo_manifest()
    scenario = Scenario.model_validate(manifest["scenario"])
    options = DispatchOptions.model_validate(manifest["options"])
    created = create_session(CreateSessionRequest(scenario=scenario, options=options))
    session_id = created.sessionId
    checkpoints: dict[int, SessionResult] = {0: created}
    try:
        for target_time in (1, 8, 3, 4):
            current = tick_session(session_id, SessionTickRequest(currentTime=target_time))
            checkpoints[current.currentTime] = get_session(session_id)
        first = checkpoints[2]
        _require(first.safetyIntervention is not None and first.safetyIntervention.time == 2, "T=2 未触发安全门")
        _require(len({state.position for state in first.robotStates}) == len(first.robotStates), "T=2 机器人位置不唯一")
        _require(first.metricsHistory[-1].activeConflictCount == 0, "T=2 仍有活动冲突")
        third = checkpoints[4]
        _require(third.safetyStall is not None and third.safetyStall.consecutiveCount == 3, "第三次拦截未形成安全停滞")
        collision_free = _paths_are_collision_free(checkpoints)
        _require(collision_free, "安全门实际轨迹出现顶点或边交换碰撞")
        return SafetyDemoEvidence(session_id=session_id, checkpoints=checkpoints, collision_free=collision_free)
    finally:
        delete_session(session_id)


def _load_frontend_scenario(scenario_id: str) -> dict:
    scenarios_path = PROJECT_ROOT / "frontend" / "src" / "domain" / "scenarios.json"
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))
    for scenario in scenarios:
        if scenario["id"] == scenario_id:
            return scenario
    raise RuntimeError(f"未找到主演示场景：{scenario_id}")


def _validate_main_acceptance(result: SessionResult) -> None:
    _require(result.completedTaskCount == 7, "T=700 未完成七项任务")
    _require(result.safetyIntervention is None, "主演示出现安全门拦截")
    _require(result.metricsHistory[-1].activeConflictCount == 0, "主演示最终仍有活动冲突")
    _require(result.result.metrics.conflictCount == 0, "主演示最终仍有活动冲突")
    _require(result.result.metrics.failureCount == 0, "主演示最终仍有失败任务")
    _require(result.result.metrics.deadlineMissCount == 0, "主演示最终存在超期任务")


def _require_step_time(result: SessionResult, expected_time: int) -> None:
    _require(
        result.currentTime == expected_time,
        f"T={expected_time} 会话未推进到目标时间",
    )


def _require_no_safety_intervention(
    result: SessionResult,
    current_time: int,
    stage: str,
) -> None:
    _require(
        result.safetyIntervention is None,
        f"T={current_time} {stage} safetyIntervention 不为空",
    )


def _paths_are_collision_free(checkpoints: dict[int, SessionResult]) -> bool:
    ordered = [checkpoints[time] for time in sorted(checkpoints)]
    positions = [
        {state.robotId: state.position for state in result.robotStates}
        for result in ordered
    ]
    for previous, current in zip(positions, positions[1:]):
        if len(set(current.values())) != len(current):
            return False
        robot_ids = sorted(current)
        for index, first_id in enumerate(robot_ids):
            for second_id in robot_ids[index + 1 :]:
                if (
                    previous[first_id] == current[second_id]
                    and previous[second_id] == current[first_id]
                    and previous[first_id] != current[first_id]
                ):
                    return False
    return True


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)
