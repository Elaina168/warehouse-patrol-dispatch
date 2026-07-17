from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.app.main import app
from backend.app.schemas import Robot, Scenario
from backend.tests.helpers import scenario_payload


def shelf_scenario_payload() -> dict:
    return {
        "id": "shelf-validation",
        "name": "货架校验场景",
        "description": "用于验证货架几何和库存规则。",
        "width": 7,
        "height": 5,
        "obstacles": [[2, 2], [4, 2]],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [],
            "delivery": [[6, 4]],
            "charging": [],
        },
        "shelves": [
            {"id": "S01", "cell": [2, 2], "serviceCell": [2, 1], "initialOccupied": False},
            {"id": "S02", "cell": [4, 2], "serviceCell": [4, 1], "initialOccupied": True},
        ],
        "robots": [
            {"id": "R1", "name": "货架机器人", "start": [0, 4], "battery": 100, "load": 1}
        ],
        "tasks": [],
        "dynamic": {"triggerTime": 10, "blockedCells": [], "failedRobots": [], "tasks": []},
    }


def create_shelf_session(scenario: dict, *, include_dynamic: bool = True):
    return TestClient(app).post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": include_dynamic},
        },
    )


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


def test_session_rejects_runtime_block_on_charging_cell() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["zones"]["charging"] = [[1, 0]]
    created = client.post("/api/sessions", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}})
    assert created.status_code == 200
    response = client.post(f"/api/sessions/{created.json()['sessionId']}/blocked-cells", json={"cell": [1, 0], "currentTime": 0})
    assert response.status_code == 409
    assert "充电地块" in response.json()["detail"]


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


def test_session_create_rejects_task_without_capable_robot() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["robots"] = [
        {
            "id": "R1",
            "name": "仅配送机器人",
            "start": [0, 0],
            "battery": 90,
            "load": 2,
            "capabilities": ["delivery"],
        }
    ]
    scenario["tasks"] = [
        {
            "id": "I1",
            "type": "inspection",
            "title": "无能力机器人巡检",
            "priority": 2,
            "targets": [[1, 0]],
        }
    ]
    scenario["dynamic"]["tasks"] = []

    response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}},
    )

    assert response.status_code == 422
    assert "任务不可达：I1 无能力机器人巡检" in response.json()["detail"]


def test_session_create_preserves_legacy_robot_without_capabilities() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    assert all("capabilities" not in robot for robot in scenario["robots"])

    response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda scenario: scenario["shelves"][1].update({"id": "S01"}), "货架 ID 重复：S01"),
        (lambda scenario: scenario["shelves"][1].update({"cell": [2, 2]}), "货架坐标重复：2,2"),
        (lambda scenario: scenario["shelves"][0].update({"cell": [3, 2], "serviceCell": [3, 1]}), "货架格不在固定障碍中：S01 3,2"),
        (lambda scenario: scenario["shelves"][0].update({"cell": [7, 2]}), "货架 S01 货架格 坐标超出地图范围：(7, 2)"),
        (lambda scenario: scenario["shelves"][0].update({"serviceCell": [7, 1]}), "货架 S01 作业格 坐标超出地图范围：(7, 1)"),
        (lambda scenario: scenario["shelves"][0].update({"serviceCell": [3, 1]}), "货架作业格不相邻：S01"),
        (lambda scenario: scenario["shelves"][1].update({"serviceCell": [2, 1]}), "货架作业格重复：2,1"),
    ],
)
def test_session_create_rejects_invalid_shelf_geometry(mutate, reason: str) -> None:
    scenario = shelf_scenario_payload()
    mutate(scenario)

    response = create_shelf_session(scenario)

    assert response.status_code == 422
    assert reason in response.json()["detail"]


def test_session_create_rejects_shelf_service_cell_on_fixed_obstacle() -> None:
    scenario = shelf_scenario_payload()
    scenario["obstacles"].append([2, 3])
    scenario["shelves"][0]["serviceCell"] = [2, 3]

    response = create_shelf_session(scenario)

    assert response.status_code == 422
    assert "货架作业格位于固定障碍：S01 2,3" in response.json()["detail"]


def test_session_create_rejects_shelf_service_cell_on_active_dynamic_block() -> None:
    scenario = shelf_scenario_payload()
    scenario["dynamic"]["triggerTime"] = 0
    scenario["dynamic"]["blockedCells"] = [[2, 1]]

    response = create_shelf_session(scenario)

    assert response.status_code == 422
    assert "货架作业格位于当前生效的动态封锁：S01 2,1" in response.json()["detail"]


@pytest.mark.parametrize(
    ("tasks", "reason"),
    [
        (
            [{"id": "IN-1", "type": "delivery", "title": "入库到有货货架", "priority": 2, "pickup": [0, 0], "dropoff": [4, 1], "demand": 1}],
            "入库货架已有货物：S02",
        ),
        (
            [
                {"id": "IN-1", "type": "delivery", "title": "首次入库", "priority": 2, "pickup": [0, 0], "dropoff": [2, 1], "demand": 1},
                {"id": "IN-2", "type": "delivery", "title": "重复入库", "priority": 2, "pickup": [0, 0], "dropoff": [2, 1], "demand": 1},
            ],
            "入库货架已被预订：S01",
        ),
        (
            [{"id": "OUT-1", "type": "delivery", "title": "空货架出库", "priority": 2, "pickup": [2, 1], "dropoff": [6, 4], "demand": 1}],
            "出库货架为空：S01",
        ),
        (
            [
                {"id": "OUT-1", "type": "delivery", "title": "首次出库", "priority": 2, "pickup": [4, 1], "dropoff": [6, 4], "demand": 1},
                {"id": "OUT-2", "type": "delivery", "title": "重复出库", "priority": 2, "pickup": [4, 1], "dropoff": [6, 4], "demand": 1},
            ],
            "出库货架已被预订：S02",
        ),
        (
            [{"id": "MOVE-1", "type": "delivery", "title": "非法取送组合", "priority": 2, "pickup": [1, 0], "dropoff": [5, 4], "demand": 1}],
            "取送任务不是合法的进货到货架或货架到出货组合：MOVE-1",
        ),
    ],
)
def test_session_create_rejects_invalid_default_shelf_inventory(tasks: list[dict], reason: str) -> None:
    scenario = shelf_scenario_payload()
    scenario["tasks"] = tasks

    response = create_shelf_session(scenario)

    assert response.status_code == 422
    assert reason in response.json()["detail"]


def test_session_create_reserves_base_and_dynamic_shelf_tasks_in_order() -> None:
    scenario = shelf_scenario_payload()
    scenario["tasks"] = [
        {"id": "IN-1", "type": "delivery", "title": "默认入库", "priority": 2, "pickup": [0, 0], "dropoff": [2, 1], "demand": 1}
    ]
    scenario["dynamic"]["tasks"] = [
        {"id": "IN-2", "type": "delivery", "title": "动态重复入库", "priority": 2, "pickup": [0, 0], "dropoff": [2, 1], "demand": 1}
    ]

    response = create_shelf_session(scenario)

    assert response.status_code == 422
    assert "入库货架已被预订：S01" in response.json()["detail"]
