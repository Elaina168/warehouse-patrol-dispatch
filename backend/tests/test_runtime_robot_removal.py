import copy
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.app.schemas import RemoveRobotRequest
from backend.tests.helpers import frontend_demo_scenario


def removal_scenario(
    *,
    robots: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    obstacles: list[list[int]] | None = None,
    charging: list[list[int]] | None = None,
    width: int = 7,
    height: int = 3,
) -> dict[str, Any]:
    return {
        "id": "runtime-robot-removal",
        "name": "运行时机器人移除",
        "description": "运行时机器人安全移除测试场景。",
        "width": width,
        "height": height,
        "obstacles": obstacles or [],
        "zones": {
            "warehouse": [[1, 1]],
            "inspection": [[width - 1, min(1, height - 1)]],
            "delivery": [[width - 1, height - 1]],
            "charging": charging or [],
        },
        "shelves": [],
        "robots": robots or [
            {
                "id": "R1",
                "name": "一号机器人",
                "start": [0, 1],
                "battery": 100,
                "batteryCapacity": 100,
                "load": 1,
                "capabilities": ["inspection", "delivery", "emergency"],
            },
            {
                "id": "R2",
                "name": "二号机器人",
                "start": [6, 1],
                "battery": 100,
                "batteryCapacity": 100,
                "load": 1,
                "capabilities": ["inspection", "delivery", "emergency"],
            },
        ],
        "tasks": tasks or [],
        "dynamic": {
            "triggerTime": 100,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }


def create_session(client: TestClient, scenario: dict[str, Any] | None = None) -> str:
    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario or removal_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["sessionId"]


def state_for(payload: dict[str, Any], robot_id: str) -> dict[str, Any]:
    return next(state for state in payload["robotStates"] if state["robotId"] == robot_id)


def remove_robot(client: TestClient, session_id: str, robot_id: str = "R1", current_time: int | None = None):
    body: dict[str, Any] = {"robotId": robot_id}
    if current_time is not None:
        body["currentTime"] = current_time
    return client.post(f"/api/sessions/{session_id}/robots/remove", json=body)


def test_idle_robot_removal_keeps_pre_removal_history_and_reset_restores_initial_fleet() -> None:
    client = TestClient(app)
    session_id = create_session(client)

    before_remove = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 3})
    assert before_remove.status_code == 200, before_remove.text
    before_path = before_remove.json()["result"]["paths"]["R1"]

    removed = remove_robot(client, session_id, current_time=3)
    assert removed.status_code == 200, removed.text
    payload = removed.json()
    removed_state = state_for(payload, "R1")
    assert removed_state["status"] == "removed"
    assert removed_state["removedAt"] == 3
    assert payload["result"]["pathStartTimes"]["R1"] == 0
    assert len(payload["result"]["paths"]["R1"]) == 4
    assert payload["result"]["paths"]["R1"][0] == before_path[0]
    assert any(event["text"].startswith("T=3 永久移除机器人：R1") for event in payload["result"]["eventLog"])
    assert all(assignment["robotId"] != "R1" for assignment in payload["result"]["assignments"])

    after_remove = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 5})
    assert after_remove.status_code == 200, after_remove.text
    after_payload = after_remove.json()
    assert state_for(after_payload, "R1")["status"] == "removed"
    assert state_for(after_payload, "R1")["removedAt"] == 3
    assert after_payload["result"]["paths"]["R1"] == payload["result"]["paths"]["R1"]
    assert all(assignment["robotId"] != "R1" for assignment in after_payload["result"]["assignments"])

    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200, reset.text
    reset_payload = reset.json()
    assert {state["robotId"] for state in reset_payload["robotStates"]} == {"R1", "R2"}
    assert all(state["status"] != "removed" and state["removedAt"] is None for state in reset_payload["robotStates"])
    assert set(reset_payload["result"]["paths"]) == {"R1", "R2"}


def test_waiting_robot_without_locked_future_task_and_failed_robot_after_handoff_can_be_removed() -> None:
    client = TestClient(app)
    waiting_session = create_session(
        client,
        removal_scenario(
            robots=[
                {
                    "id": "R1",
                    "name": "等待机器人",
                    "start": [0, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R2",
                    "name": "其他机器人",
                    "start": [6, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            tasks=[
                {
                    "id": "FUTURE",
                    "type": "inspection",
                    "title": "远期巡检",
                    "priority": 1,
                    "releaseTime": 20,
                    "targets": [[6, 1]],
                }
            ],
        ),
    )
    waiting = client.get(f"/api/sessions/{waiting_session}")
    assert waiting.status_code == 200
    assert state_for(waiting.json(), "R1")["status"] == "waiting"
    removed_waiting = remove_robot(client, waiting_session)
    assert removed_waiting.status_code == 200, removed_waiting.text

    locked_session = create_session(
        client,
        removal_scenario(
            robots=[
                {
                    "id": "R1",
                    "name": "锁定机器人",
                    "start": [0, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R2",
                    "name": "其他机器人",
                    "start": [6, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            tasks=[
                {
                    "id": "FUTURE-LOCKED",
                    "type": "inspection",
                    "title": "锁定远期巡检",
                    "priority": 1,
                    "releaseTime": 20,
                    "targets": [[6, 1]],
                }
            ],
        ),
    )
    with sessions_module._locked_session(locked_session, touch_access=False) as session:
        session.locked_task_robot_ids["FUTURE-LOCKED"] = "R1"
    rejected_locked = remove_robot(client, locked_session)
    assert rejected_locked.status_code == 409, rejected_locked.text
    assert "锁定" in str(rejected_locked.json()["detail"])

    handoff_session = create_session(
        client,
        removal_scenario(
            robots=[
                {
                    "id": "R1",
                    "name": "故障机器人",
                    "start": [0, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R2",
                    "name": "接替机器人",
                    "start": [6, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
            ],
            tasks=[
                {
                    "id": "HANDOFF",
                    "type": "inspection",
                    "title": "故障移交任务",
                    "priority": 2,
                    "targets": [[1, 1]],
                }
            ],
        ),
    )
    failed = client.post(f"/api/sessions/{handoff_session}/failed-robots", json={"robotId": "R1"})
    assert failed.status_code == 200, failed.text
    failed_payload = failed.json()
    assert state_for(failed_payload, "R1")["status"] == "failed"
    assert any(
        assignment["robotId"] == "R2"
        and any(task["id"] == "HANDOFF" for task in assignment["tasks"])
        for assignment in failed_payload["result"]["assignments"]
    )
    removed_failed = remove_robot(client, handoff_session)
    assert removed_failed.status_code == 200, removed_failed.text
    assert state_for(removed_failed.json(), "R1")["status"] == "removed"


def test_removal_rejects_running_carrying_charging_locked_and_last_active_robots() -> None:
    client = TestClient(app)
    running_session = create_session(
        client,
        removal_scenario(
            robots=[
                {
                    "id": "R1",
                    "name": "执行机器人",
                    "start": [0, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R2",
                    "name": "备用机器人",
                    "start": [6, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            tasks=[
                {
                    "id": "RUNNING",
                    "type": "inspection",
                    "title": "执行中巡检",
                    "priority": 2,
                    "serviceTime": 5,
                    "targets": [[6, 1]],
                }
            ],
        ),
    )
    running_tick = client.post(f"/api/sessions/{running_session}/tick", json={"currentTime": 1})
    assert running_tick.status_code == 200, running_tick.text
    assert state_for(running_tick.json(), "R1")["status"] == "inspecting"
    rejected_running = remove_robot(client, running_session)
    assert rejected_running.status_code == 409, rejected_running.text
    assert "执行" in str(rejected_running.json()["detail"])

    carrying_session = create_session(
        client,
        removal_scenario(
            robots=[
                {
                    "id": "R1",
                    "name": "携货机器人",
                    "start": [0, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["delivery"],
                },
                {
                    "id": "R2",
                    "name": "备用机器人",
                    "start": [6, 2],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            tasks=[
                {
                    "id": "CARRYING",
                    "type": "delivery",
                    "title": "携货配送",
                    "priority": 2,
                    "serviceTime": 2,
                    "pickup": [1, 1],
                    "dropoff": [6, 1],
                    "demand": 1,
                }
            ],
        ),
    )
    carrying_tick = client.post(f"/api/sessions/{carrying_session}/tick", json={"currentTime": 2})
    assert carrying_tick.status_code == 200, carrying_tick.text
    assert state_for(carrying_tick.json(), "R1")["status"] == "delivering"
    rejected_carrying = remove_robot(client, carrying_session)
    assert rejected_carrying.status_code == 409, rejected_carrying.text
    assert "货物" in str(rejected_carrying.json()["detail"])

    carrying_at_t0_session = create_session(
        client,
        removal_scenario(
            robots=[
                {
                    "id": "R1",
                    "name": "T0 携货机器人",
                    "start": [1, 1],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["delivery"],
                },
                {
                    "id": "R2",
                    "name": "T0 备用机器人",
                    "start": [6, 2],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            tasks=[
                {
                    "id": "CARRYING-T0",
                    "type": "delivery",
                    "title": "T0 携货配送",
                    "priority": 2,
                    "pickup": [1, 1],
                    "dropoff": [6, 1],
                    "demand": 1,
                }
            ],
        ),
    )
    rejected_carrying_at_t0 = remove_robot(client, carrying_at_t0_session)
    assert rejected_carrying_at_t0.status_code == 409, rejected_carrying_at_t0.text
    assert "货物" in str(rejected_carrying_at_t0.json()["detail"])

    charging_session = create_session(
        client,
        removal_scenario(
            robots=[
                {
                    "id": "R1",
                    "name": "充电机器人",
                    "start": [0, 1],
                    "battery": 1,
                    "batteryCapacity": 100,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R2",
                    "name": "备用机器人",
                    "start": [6, 2],
                    "battery": 100,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            charging=[[0, 0]],
            tasks=[
                {
                    "id": "CHARGE",
                    "type": "inspection",
                    "title": "低电量巡检",
                    "priority": 2,
                    "targets": [[6, 1]],
                }
            ],
        ),
    )
    charging_state = state_for(client.get(f"/api/sessions/{charging_session}").json(), "R1")
    assert charging_state["status"] in {"toCharge", "charging"}
    rejected_charging = remove_robot(client, charging_session)
    assert rejected_charging.status_code == 409, rejected_charging.text
    assert "充电" in str(rejected_charging.json()["detail"])

    charging_failed = client.post(f"/api/sessions/{charging_session}/tick", json={"currentTime": 1})
    assert charging_failed.status_code == 200, charging_failed.text
    failed_charging = client.post(
        f"/api/sessions/{charging_session}/failed-robots",
        json={"robotId": "R1"},
    )
    assert failed_charging.status_code == 200, failed_charging.text
    rejected_failed_charging = remove_robot(client, charging_session)
    assert rejected_failed_charging.status_code == 409, rejected_failed_charging.text
    assert "充电" in str(rejected_failed_charging.json()["detail"])

    last_active_session = create_session(client)
    first_removed = remove_robot(client, last_active_session, "R1")
    assert first_removed.status_code == 200, first_removed.text
    rejected_last = remove_robot(client, last_active_session, "R2")
    assert rejected_last.status_code == 409, rejected_last.text
    assert "活动机器人" in str(rejected_last.json()["detail"])


def test_failed_removal_is_transactional_and_repeat_is_idempotent() -> None:
    client = TestClient(app)
    session_id = create_session(client)
    assert client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 2}).status_code == 200

    with sessions_module._locked_session(session_id, touch_access=False) as session:
        before = {
            "current_time": session.current_time,
            "scenario": session.scenario.model_dump(),
            "robot_positions": copy.deepcopy(session.robot_positions),
            "robot_path_history": copy.deepcopy(session.robot_path_history),
            "robot_travelled_distance": copy.deepcopy(session.robot_travelled_distance),
            "robot_battery_levels": copy.deepcopy(session.robot_battery_levels),
            "event_notes": copy.deepcopy(session.event_notes),
            "metrics_history": copy.deepcopy(session.metrics_history),
            "locked_task_robot_ids": copy.deepcopy(session.locked_task_robot_ids),
            "preferred_task_robot_ids": copy.deepcopy(session.preferred_task_robot_ids),
            "last_result": copy.deepcopy(session.last_result),
            "robot_removed_at": copy.deepcopy(getattr(session, "robot_removed_at", {})),
        }

    def fail_replan(*args, **kwargs):
        raise RuntimeError("测试重规划失败")

    original_run_dispatch = sessions_module.run_dispatch
    sessions_module.run_dispatch = fail_replan
    try:
        with pytest.raises(RuntimeError, match="测试重规划失败"):
            sessions_module.remove_robot(
                session_id,
                RemoveRobotRequest(robotId="R1"),
            )
    finally:
        sessions_module.run_dispatch = original_run_dispatch

    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert session.current_time == before["current_time"]
        assert session.scenario.model_dump() == before["scenario"]
        assert session.robot_positions == before["robot_positions"]
        assert session.robot_path_history == before["robot_path_history"]
        assert session.robot_travelled_distance == before["robot_travelled_distance"]
        assert session.robot_battery_levels == before["robot_battery_levels"]
        assert session.event_notes == before["event_notes"]
        assert session.metrics_history == before["metrics_history"]
        assert session.locked_task_robot_ids == before["locked_task_robot_ids"]
        assert session.preferred_task_robot_ids == before["preferred_task_robot_ids"]
        assert session.last_result == before["last_result"]
        assert getattr(session, "robot_removed_at", {}) == before["robot_removed_at"]

    first = remove_robot(client, session_id, "R1")
    assert first.status_code == 200, first.text
    first_payload = first.json()
    first_events = first_payload["result"]["eventLog"]
    assert state_for(first_payload, "R1")["status"] == "removed"

    repeated = remove_robot(client, session_id, "R1")
    assert repeated.status_code == 200, repeated.text
    repeated_payload = repeated.json()
    assert repeated_payload["result"]["eventLog"] == first_events
    assert state_for(repeated_payload, "R1")["removedAt"] == state_for(first_payload, "R1")["removedAt"]

    restore = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R1"},
    )
    assert restore.status_code == 409, restore.text
    assert "移除" in str(restore.json()["detail"])

    fail_after_removal = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1"},
    )
    assert fail_after_removal.status_code == 409, fail_after_removal.text
    assert "移除" in str(fail_after_removal.json()["detail"])

    reuse = client.post(
        f"/api/sessions/{session_id}/robots",
        json={
            "robot": {
                "id": "R1",
                "name": "复用 ID",
                "start": [3, 0],
                "battery": 100,
                "load": 1,
                "capabilities": ["inspection"],
            }
        },
    )
    assert reuse.status_code == 409, reuse.text
    assert "ID 已存在" in str(reuse.json()["detail"])


def test_integrated_demo_runtime_robot_can_finish_then_be_removed_and_reset_restores_four_robots() -> None:
    client = TestClient(app)
    scenario = frontend_demo_scenario("integrated-demo")
    created = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["sessionId"]
    initial_robot_ids = {state["robotId"] for state in created.json()["robotStates"]}
    assert len(initial_robot_ids) == 4

    activated = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 12})
    assert activated.status_code == 200, activated.text

    joined = client.post(
        f"/api/sessions/{session_id}/robots",
        json={
            "robot": {
                "id": "R5",
                "name": "运行时突发车",
                "start": [25, 15],
                "battery": 512,
                "batteryCapacity": 512,
                "load": 1,
                "moveTicks": 1,
                "capabilities": ["emergency"],
            },
            "currentTime": 12,
        },
    )
    assert joined.status_code == 200, joined.text

    added = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "R5-REMOVAL-DEMO",
                "type": "emergency",
                "title": "R5 接入任务",
                "priority": 5,
                "releaseTime": 12,
                "deadline": 1000,
                "target": [24, 15],
            }
        },
    )
    assert added.status_code == 200, added.text

    after_task = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 13})
    assert after_task.status_code == 200, after_task.text
    after_task_payload = after_task.json()
    assert state_for(after_task_payload, "R5")["status"] == "idle"
    assert next(state for state in after_task_payload["taskStates"] if state["taskId"] == "R5-REMOVAL-DEMO")["status"] == "completed"
    r5_history = after_task_payload["result"]["paths"]["R5"]

    removed = remove_robot(client, session_id, "R5", current_time=13)
    assert removed.status_code == 200, removed.text
    removed_payload = removed.json()
    assert state_for(removed_payload, "R5")["status"] == "removed"
    assert state_for(removed_payload, "R5")["removedAt"] == 13
    assert removed_payload["result"]["paths"]["R5"] == r5_history
    assert all(assignment["robotId"] != "R5" for assignment in removed_payload["result"]["assignments"])
    assert sum(event["text"].startswith("T=13 永久移除机器人：R5") for event in removed_payload["result"]["eventLog"]) == 1

    completed = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 700})
    assert completed.status_code == 200, completed.text
    completed_payload = completed.json()
    assert completed_payload["completedTaskCount"] == 7
    assert all(state["status"] == "completed" for state in completed_payload["taskStates"])
    assert state_for(completed_payload, "R5")["status"] == "removed"
    assert completed_payload["result"]["paths"]["R5"] == r5_history
    assert completed_payload["result"]["metrics"]["conflictCount"] == 0
    assert completed_payload["result"]["metrics"]["failureCount"] == 0

    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200, reset.text
    reset_payload = reset.json()
    assert {state["robotId"] for state in reset_payload["robotStates"]} == initial_robot_ids
    assert "R5" not in reset_payload["result"]["paths"]
    assert all(state["status"] != "removed" and state["removedAt"] is None for state in reset_payload["robotStates"])


def test_same_session_duplicate_removals_are_serialized_and_record_one_event() -> None:
    client = TestClient(app)
    session_id = create_session(client)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: remove_robot(client, session_id), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 200]
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert state_for(payload, "R1")["status"] == "removed"
    assert sum(event["text"].startswith("T=0 永久移除机器人：R1") for event in payload["result"]["eventLog"]) == 1


def test_future_removal_waits_until_requested_time_after_safety_hold() -> None:
    client = TestClient(app)
    scenario = removal_scenario(
        width=4,
        height=2,
        obstacles=[[0, 1], [1, 1], [2, 1]],
        robots=[
            {
                "id": "R1",
                "name": "左侧巡检机器人",
                "start": [0, 0],
                "battery": 100,
                "load": 1,
                "capabilities": ["inspection"],
            },
            {
                "id": "R2",
                "name": "右侧巡检机器人",
                "start": [3, 0],
                "battery": 100,
                "load": 1,
                "capabilities": ["emergency"],
            },
            {
                "id": "R3",
                "name": "待命应急机器人",
                "start": [3, 1],
                "battery": 100,
                "load": 1,
                "capabilities": ["delivery"],
            },
        ],
        tasks=[
            {
                "id": "SWAP-1",
                "type": "inspection",
                "title": "左到右巡检",
                "priority": 2,
                "targets": [[3, 0]],
            },
            {
                "id": "SWAP-2",
                "type": "emergency",
                "title": "右到左巡检",
                "priority": 2,
                "target": [0, 0],
            },
        ],
    )
    session_id = create_session(client, scenario)

    response = remove_robot(client, session_id, "R3", current_time=4)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["currentTime"] == 3
    assert state_for(payload, "R3")["status"] == "idle"
    assert state_for(payload, "R3")["removedAt"] is None
    assert not any("永久移除机器人：R3" in event["text"] for event in payload["result"]["eventLog"])
