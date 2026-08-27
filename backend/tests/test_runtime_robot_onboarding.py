import copy
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi.testclient import TestClient

import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.tests.helpers import frontend_demo_scenario


def onboarding_scenario() -> dict[str, Any]:
    return {
        "id": "runtime-robot-onboarding",
        "name": "runtime-robot-onboarding",
        "description": "运行时机器人接入测试场景。",
        "width": 7,
        "height": 5,
        "obstacles": [[2, 1], [4, 3]],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[6, 0]],
            "delivery": [[6, 4]],
            "charging": [[6, 4]],
        },
        "shelves": [
            {
                "id": "S1",
                "cell": [4, 3],
                "serviceCell": [4, 2],
                "initialOccupied": False,
            }
        ],
        "robots": [
            {
                "id": "R1",
                "name": "初始机器人",
                "start": [0, 0],
                "battery": 100,
                "batteryCapacity": 100,
                "load": 1,
                "capabilities": ["delivery"],
            }
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 50,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }


def robot_payload(
    robot_id: str = "R5",
    *,
    start: list[int] | None = None,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": robot_id,
        "name": "接入机器人",
        "start": start if start is not None else [1, 1],
        "battery": 80,
        "batteryCapacity": 100,
        "load": 1,
        "moveTicks": 1,
        "capabilities": capabilities if capabilities is not None else ["inspection"],
    }


def create_session(client: TestClient, scenario: dict[str, Any] | None = None) -> str:
    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario or onboarding_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["sessionId"]


def robot_state(payload: dict[str, Any], robot_id: str = "R5") -> dict[str, Any]:
    return next(state for state in payload["robotStates"] if state["robotId"] == robot_id)


def test_runtime_robot_can_join_at_t0_and_exposes_complete_config() -> None:
    client = TestClient(app)
    session_id = create_session(client)

    response = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload()},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    state = robot_state(payload)
    assert payload["currentTime"] == 0
    assert state["start"] == [1, 1]
    assert state["position"] == [1, 1]
    assert state["capabilities"] == ["inspection"]
    assert state["joinedAt"] == 0
    assert payload["result"]["pathStartTimes"]["R5"] == 0
    assert payload["result"]["paths"]["R5"][0] == [1, 1]
    assert any(event["text"].startswith("T=0 新机器人接入：R5") for event in payload["result"]["eventLog"])


def test_runtime_robot_can_join_after_time_progress_and_default_time_uses_session_time() -> None:
    client = TestClient(app)
    session_id = create_session(client)
    tick = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 3})
    assert tick.status_code == 200

    response = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(start=[1, 1])},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["currentTime"] == 3
    assert robot_state(payload)["joinedAt"] == 3
    assert payload["result"]["pathStartTimes"]["R5"] == 3
    assert payload["result"]["paths"]["R5"][0] == [1, 1]
    assert len(payload["result"]["paths"]["R5"]) <= 10_001


def test_runtime_robot_rejects_past_time_duplicate_id_and_thirty_third_robot() -> None:
    client = TestClient(app)
    session_id = create_session(client)
    assert client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 3}).status_code == 200

    past = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(), "currentTime": 2},
    )
    assert past.status_code == 422
    assert "currentTime must be >= session currentTime" in str(past.json()["detail"])

    first = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload()},
    )
    assert first.status_code == 200
    duplicate = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(start=[1, 2])},
    )
    assert duplicate.status_code == 409
    assert "机器人 ID 已存在" in duplicate.json()["detail"]

    full_scenario = onboarding_scenario()
    full_scenario["width"] = 8
    full_scenario["height"] = 4
    full_scenario["obstacles"] = []
    full_scenario["shelves"] = []
    full_scenario["zones"]["delivery"] = [[7, 3]]
    full_scenario["zones"]["charging"] = [[7, 3]]
    full_scenario["robots"] = [
        {
            "id": f"R{index + 1}",
            "name": f"机器人{index + 1}",
            "start": [index % 8, index // 8],
            "battery": 100,
            "batteryCapacity": 100,
            "load": 1,
        }
        for index in range(32)
    ]
    full_session_id = create_session(client, full_scenario)
    thirty_third = client.post(
        f"/api/sessions/{full_session_id}/robots",
        json={"robot": robot_payload(start=[0, 0])},
    )
    assert thirty_third.status_code == 422
    assert "32" in str(thirty_third.json()["detail"])


def test_runtime_robot_rejects_invalid_cells_but_allows_free_charging_or_service_cells() -> None:
    client = TestClient(app)
    session_id = create_session(client)

    invalid_cases = [
        ([7, 0], 422, "超出地图范围"),
        ([2, 1], 409, "固定障碍"),
        ([4, 3], 409, "货架"),
        ([0, 0], 409, "占用"),
    ]
    for start, status, message in invalid_cases:
        response = client.post(
            f"/api/sessions/{session_id}/robots",
            json={"robot": robot_payload(robot_id=f"BAD-{start[0]}-{start[1]}", start=start)},
        )
        assert response.status_code == status, response.text
        assert message in str(response.json()["detail"])

    blocked = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1]},
    )
    assert blocked.status_code == 200
    blocked_robot = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(robot_id="BAD-BLOCK", start=[1, 1])},
    )
    assert blocked_robot.status_code == 409
    assert "封锁" in str(blocked_robot.json()["detail"])

    charging_robot = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(robot_id="R-CHARGE", start=[6, 4])},
    )
    assert charging_robot.status_code == 200, charging_robot.text

    service_robot = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(robot_id="R-SERVICE", start=[4, 2])},
    )
    assert service_robot.status_code == 200, service_robot.text


def test_failed_runtime_robot_request_does_not_mutate_authoritative_session() -> None:
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
            "last_result": copy.deepcopy(session.last_result),
        }

    response = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(start=[7, 0]), "currentTime": 6},
    )
    assert response.status_code == 422

    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert session.current_time == before["current_time"]
        assert session.scenario.model_dump() == before["scenario"]
        assert session.robot_positions == before["robot_positions"]
        assert session.robot_path_history == before["robot_path_history"]
        assert session.robot_travelled_distance == before["robot_travelled_distance"]
        assert session.robot_battery_levels == before["robot_battery_levels"]
        assert session.event_notes == before["event_notes"]
        assert session.metrics_history == before["metrics_history"]
        assert session.last_result == before["last_result"]


def test_runtime_robot_replanning_participates_and_failure_recovery_keeps_it_eligible() -> None:
    client = TestClient(app)
    session_id = create_session(client)
    joined = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload(start=[4, 0])},
    )
    assert joined.status_code == 200

    failed = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R5"},
    )
    assert failed.status_code == 200
    added_while_failed = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "R5-TASK",
                "type": "inspection",
                "title": "接入机器人巡检",
                "priority": 4,
                "targets": [[5, 0]],
            }
        },
    )
    assert added_while_failed.status_code == 200
    failed_state = next(
        state for state in added_while_failed.json()["taskStates"] if state["taskId"] == "R5-TASK"
    )
    assert failed_state["status"] == "unassigned"
    assert failed_state["recoveryAction"] == "restoreRobot"
    assert failed_state["failureReason"] is not None
    assert added_while_failed.json()["result"]["failureDetails"]["R5-TASK"]["blockingRobotIds"] == ["R5"]

    restored = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R5"},
    )
    assert restored.status_code == 200
    restored_payload = restored.json()
    assert any(
        assignment["robotId"] == "R5"
        and any(task["id"] == "R5-TASK" for task in assignment["tasks"])
        for assignment in restored_payload["result"]["assignments"]
    )

    completed = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 8},
    )
    assert completed.status_code == 200
    completed_payload = completed.json()
    assert next(state for state in completed_payload["taskStates"] if state["taskId"] == "R5-TASK")["status"] == "completed"
    assert robot_state(completed_payload)["position"] != [4, 0]


def test_runtime_robot_reset_removes_joined_robot_and_restores_initial_snapshot() -> None:
    client = TestClient(app)
    session_id = create_session(client)
    joined = client.post(
        f"/api/sessions/{session_id}/robots",
        json={"robot": robot_payload()},
    )
    assert joined.status_code == 200
    assert "R5" in {state["robotId"] for state in joined.json()["robotStates"]}

    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200
    payload = reset.json()
    assert {state["robotId"] for state in payload["robotStates"]} == {"R1"}
    assert "R5" not in payload["result"]["paths"]
    assert payload["result"]["pathStartTimes"] == {"R1": 0}
    assert all(state["joinedAt"] == 0 for state in payload["robotStates"])


def test_same_session_duplicate_runtime_robot_requests_are_serialized() -> None:
    client = TestClient(app)
    session_id = create_session(client)

    def request() -> int:
        response = client.post(
            f"/api/sessions/{session_id}/robots",
            json={"robot": robot_payload()},
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _: request(), range(2)))

    assert statuses == [200, 409]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert [robot.id for robot in session.scenario.robots].count("R5") == 1


def test_integrated_demo_runtime_robot_flow_preserves_safety_and_conflict_boundary() -> None:
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

    at_fixed_time = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 12},
    )
    assert at_fixed_time.status_code == 200, at_fixed_time.text

    joined = client.post(
        f"/api/sessions/{session_id}/robots",
        json={
            "robot": {
                "id": "R5",
                "name": "运行时巡检车",
                "start": [25, 15],
                "battery": 512,
                "batteryCapacity": 512,
                "load": 1,
                "moveTicks": 1,
                "capabilities": ["inspection"],
            },
            "currentTime": 12,
        },
    )
    assert joined.status_code == 200, joined.text
    assert robot_state(joined.json())["joinedAt"] == 12

    added = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "R5-DEMO",
                "type": "inspection",
                "title": "R5 接入巡检",
                "priority": 5,
                "releaseTime": 12,
                "deadline": 1000,
                "targets": [[24, 15]],
            }
        },
    )
    assert added.status_code == 200, added.text
    added_payload = added.json()
    assert any(
        assignment["robotId"] == "R5"
        and any(task["id"] == "R5-DEMO" for task in assignment["tasks"])
        for assignment in added_payload["result"]["assignments"]
    )

    final = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 30},
    )
    assert final.status_code == 200, final.text
    payload = final.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "R5-DEMO")
    assert task_state["status"] == "completed"
    assert robot_state(payload)["position"] != [25, 15]
    assert [24, 15] in payload["result"]["paths"]["R5"]
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["result"]["metrics"]["deadlineMissCount"] == 0
    assert payload["safetyIntervention"] is None
