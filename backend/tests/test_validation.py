from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.app.main import app
from backend.app.schemas import Robot, Scenario
from backend.tests.helpers import scenario_payload


def test_robot_move_ticks_defaults_for_legacy_scenarios_and_rejects_invalid_values() -> None:
    client = TestClient(app)

    legacy_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert legacy_response.status_code == 200
    assert [state["moveTicks"] for state in legacy_response.json()["robotStates"]] == [1, 1]

    invalid_scenario = scenario_payload()
    invalid_scenario["robots"][0]["moveTicks"] = 5
    invalid_response = client.post(
        "/api/sessions",
        json={
            "scenario": invalid_scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert invalid_response.status_code == 422


def test_charging_contract_defaults_for_legacy_scenarios() -> None:
    scenario = Scenario.model_validate(scenario_payload())

    assert scenario.chargeTime == 4
    assert scenario.zones.charging == []
    assert [robot.batteryCapacity for robot in scenario.robots] == [100, 100]


def test_robot_rejects_battery_above_capacity() -> None:
    with pytest.raises(ValidationError, match="battery must be <= batteryCapacity"):
        Robot.model_validate(
            {
                "id": "R1",
                "name": "电量校验机器人",
                "start": [0, 0],
                "battery": 101,
                "batteryCapacity": 100,
                "load": 1,
            }
        )


def test_session_create_rejects_charging_cell_outside_map() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["zones"]["charging"] = [[6, 4]]

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 422
    assert "充电区 1 坐标超出地图范围：(6, 4)" in response.json()["detail"]


def test_session_create_rejects_charging_cell_on_fixed_obstacle() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["zones"]["charging"] = [[2, 1]]

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 422
    assert "充电区 1 位于障碍或封锁单元：(2, 1)" in response.json()["detail"]


def test_session_add_task_rejects_dynamic_task_id_collision() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "E1",
                "type": "inspection",
                "title": "重复动态任务 ID",
                "priority": 2,
                "targets": [[1, 4]],
            }
        },
    )

    assert add_response.status_code == 409
    assert add_response.json()["detail"] == "任务 ID 已存在：E1"

def test_session_add_task_rejects_blocked_target() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "障碍目标巡检",
                "priority": 2,
                "targets": [[2, 1]],
            }
        },
    )

    assert add_response.status_code == 422
    assert "任务 M1 目标 1 位于固定障碍：(2, 1)" in add_response.json()["detail"]

def test_session_add_task_rejects_out_of_bounds_target() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "越界目标巡检",
                "priority": 2,
                "targets": [[8, 1]],
            }
        },
    )

    assert add_response.status_code == 422
    assert "任务 M1 目标 1 坐标超出地图范围：(8, 1)" in add_response.json()["detail"]

def test_session_create_rejects_duplicate_robot_ids() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["robots"][1]["id"] = "R1"

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 422
    assert "机器人 ID 重复：R1" in response.json()["detail"]


def test_dispatch_api_rejects_invalid_scenario() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["robots"][1]["id"] = "R1"

    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 422
    assert "机器人 ID 重复：R1" in response.json()["detail"]


def test_session_create_rejects_robot_start_on_obstacle() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["robots"][0]["start"] = [2, 1]

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 422
    assert "机器人 R1 起点 位于障碍或封锁单元：(2, 1)" in response.json()["detail"]


def test_session_create_rejects_unknown_dynamic_failed_robot() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["dynamic"]["failedRobots"] = ["R404"]

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 422
    assert "动态故障机器人不存在：R404" in response.json()["detail"]


def test_session_create_rejects_duplicate_dynamic_failed_robot_ids() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["dynamic"]["failedRobots"] = ["R1", "R1"]

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 422
    assert "动态故障机器人 ID 重复：R1" in response.json()["detail"]


def test_session_create_allows_base_tasks_before_future_dynamic_failure() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["robots"] = [
        {"id": "R1", "name": "测试机器人", "start": [0, 0], "battery": 90, "load": 2}
    ]
    scenario["tasks"] = [
        {"id": "T1", "type": "inspection", "title": "触发前巡检", "priority": 2, "targets": [[1, 0]]}
    ]
    scenario["dynamic"] = {
        "triggerTime": 10,
        "blockedCells": [],
        "failedRobots": ["R1"],
        "tasks": [],
    }

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["currentTime"] == 0
    assert payload["result"]["unavailableRobotIds"] == []


def test_session_create_rejects_base_task_when_dynamic_failure_is_initially_active() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["robots"] = [
        {"id": "R1", "name": "测试机器人", "start": [0, 0], "battery": 90, "load": 2}
    ]
    scenario["tasks"] = [
        {"id": "T1", "type": "inspection", "title": "初始故障巡检", "priority": 2, "targets": [[1, 0]]}
    ]
    scenario["dynamic"] = {
        "triggerTime": 0,
        "blockedCells": [],
        "failedRobots": ["R1"],
        "tasks": [],
    }

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 422
    assert "没有可用机器人执行任务" in response.json()["detail"]


def test_session_create_rejects_unreachable_task_target() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["width"] = 3
    scenario["height"] = 3
    scenario["obstacles"] = [[1, 2], [2, 1]]
    scenario["zones"] = {
        "warehouse": [[0, 0]],
        "inspection": [[2, 2]],
        "delivery": [[0, 2]],
    }
    scenario["robots"] = [
        {"id": "R1", "name": "测试机器人", "start": [0, 0], "battery": 90, "load": 2}
    ]
    scenario["tasks"] = [
        {"id": "T1", "type": "inspection", "title": "孤立目标巡检", "priority": 2, "targets": [[2, 2]]}
    ]
    scenario["dynamic"] = {
        "triggerTime": 6,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [],
    }

    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 422
    assert "任务不可达：T1 孤立目标巡检" in response.json()["detail"]
