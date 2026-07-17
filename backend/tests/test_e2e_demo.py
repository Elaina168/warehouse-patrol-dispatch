from fastapi.testclient import TestClient

from backend.app.main import app
from backend.tests.helpers import frontend_demo_scenario


def integrated_dynamic_event_scenario() -> dict:
    scenario = frontend_demo_scenario("integrated-demo")
    event_task = next(task for task in scenario["tasks"] if task["id"] == "E1")
    scenario["tasks"] = [task for task in scenario["tasks"] if task["id"] != "E1"]
    scenario["dynamic"] = {
        "triggerTime": 12,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [event_task],
    }
    return scenario


def specialized_failure_scenario() -> dict:
    return {
        "id": "specialized-with-failure",
        "name": "specialized-with-failure",
        "description": "唯一兼容机器人故障与恢复。",
        "width": 5,
        "height": 2,
        "obstacles": [],
        "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
        "robots": [
            {
                "id": "R-EMERGENCY",
                "name": "突发机器人",
                "start": [0, 0],
                "battery": 100,
                "load": 1,
                "capabilities": ["emergency"],
            },
            {
                "id": "R-INSPECTION",
                "name": "巡检机器人",
                "start": [0, 1],
                "battery": 100,
                "load": 1,
                "capabilities": ["inspection"],
            },
        ],
        "tasks": [],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }


def assert_assignments_respect_capabilities(assignments: list[dict], scenario: dict) -> None:
    robot_by_id = {robot["id"]: robot for robot in scenario["robots"]}
    for assignment in assignments:
        robot = robot_by_id[assignment["robotId"]]
        for task in assignment["tasks"]:
            assert task["type"] in robot["capabilities"]
            if task["type"] == "delivery":
                assert robot["load"] >= task["demand"]


def test_specialized_robot_failure_recovers_through_online_session() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": specialized_failure_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    for robot_id in ("R-EMERGENCY", "R-INSPECTION"):
        failed = client.post(
            f"/api/sessions/{session_id}/failed-robots",
            json={"robotId": robot_id, "currentTime": 0},
        )
        assert failed.status_code == 200

    added = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "E-RECOVERY",
                "type": "emergency",
                "title": "唯一兼容机器人恢复任务",
                "priority": 5,
                "target": [4, 0],
            }
        },
    )
    assert added.status_code == 200
    failed_state = next(state for state in added.json()["taskStates"] if state["taskId"] == "E-RECOVERY")
    assert failed_state["status"] == "unassigned"
    assert failed_state["failureCategory"] == "temporary"
    assert failed_state["recoveryAction"] == "restoreRobot"
    assert added.json()["result"]["failureDetails"]["E-RECOVERY"]["blockingRobotIds"] == ["R-EMERGENCY"]

    restored = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R-INSPECTION", "currentTime": 0},
    )
    assert restored.status_code == 200
    unrelated_state = next(
        state for state in restored.json()["taskStates"] if state["taskId"] == "E-RECOVERY"
    )
    assert unrelated_state["status"] == "unassigned"
    assert unrelated_state["recoveryAction"] == "restoreRobot"

    recovered = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R-EMERGENCY", "currentTime": 0},
    )
    assert recovered.status_code == 200
    recovered_payload = recovered.json()
    recovered_state = next(
        state for state in recovered_payload["taskStates"] if state["taskId"] == "E-RECOVERY"
    )
    assert recovered_state["status"] in {"assigned", "running", "completed"}
    assert recovered_state["failureCategory"] is None
    assert recovered_state["recoveryAction"] is None
    assignment = next(
        assignment
        for assignment in recovered_payload["result"]["assignments"]
        if any(task["id"] == "E-RECOVERY" for task in assignment["tasks"])
    )
    assert assignment["robotId"] == "R-EMERGENCY"


def test_integrated_demo_runs_default_online_dispatch_flow() -> None:
    client = TestClient(app)
    scenario = frontend_demo_scenario("integrated-demo")
    assert all(
        robot["capabilities"] == ["inspection", "delivery", "emergency"]
        for robot in scenario["robots"]
    )
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    initial_shelves = {item["shelfId"]: item["status"] for item in create_payload["shelfStates"]}
    assert sum(status in {"occupied", "outboundReserved"} for status in initial_shelves.values()) == 12
    assert initial_shelves["S32"] == "outboundReserved"
    assert initial_shelves["S13"] == "inboundReserved"
    assert initial_shelves["S43"] == "inboundReserved"

    planning_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 1},
    )
    assert planning_response.status_code == 200
    planning_payload = planning_response.json()
    assert_assignments_respect_capabilities(planning_payload["result"]["assignments"], scenario)
    t2_assignment = next(
        assignment
        for assignment in planning_payload["result"]["assignments"]
        if any(task["id"] == "T2" for task in assignment["tasks"])
    )
    t2_path = planning_payload["result"]["paths"][t2_assignment["robotId"]]
    pickup_tick = t2_path.index([13, 6])

    release_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 12},
    )
    assert release_response.status_code == 200
    release_payload = release_response.json()
    states_at_release = {state["taskId"]: state["status"] for state in release_payload["taskStates"]}
    assert states_at_release["E1"] in {"running", "completed"}
    assert release_payload["runtimeTaskCount"] == 0
    assert release_payload["runtimeEventCount"] == 0
    assert release_payload["result"]["extraBlocked"] == []
    assert release_payload["result"]["unavailableRobotIds"] == []

    before_pickup_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": pickup_tick - 1},
    )
    assert before_pickup_response.status_code == 200
    assert {
        item["shelfId"]: item["status"] for item in before_pickup_response.json()["shelfStates"]
    }["S32"] == "outboundReserved"

    pickup_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": pickup_tick},
    )
    assert pickup_response.status_code == 200
    pickup_payload = pickup_response.json()
    assert {item["shelfId"]: item["status"] for item in pickup_payload["shelfStates"]}["S32"] == "empty"
    assert {"time": pickup_tick, "text": "货架 S32 已取货"} in pickup_payload["result"]["eventLog"]

    completion_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 700},
    )
    assert completion_response.status_code == 200
    payload = completion_response.json()
    assert payload["scenarioId"] == "integrated-demo"
    assert payload["completedTaskCount"] == 6
    assert {state["status"] for state in payload["taskStates"]} == {"completed"}
    assert payload["runtimeTaskCount"] == 0
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["deadlineMissCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["result"]["chargingVisits"] == []
    assert payload["metricsHistory"][-1]["completedTaskCount"] == 6
    final_shelves = {item["shelfId"]: item["status"] for item in payload["shelfStates"]}
    assert sum(status == "occupied" for status in final_shelves.values()) == 13
    assert final_shelves["S13"] == "occupied"
    assert final_shelves["S32"] == "empty"
    assert final_shelves["S43"] == "occupied"


def test_integrated_demo_conflict_avoidance_reduces_baseline_conflicts() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/experiments/conflict-avoidance",
        json={
            "scenario": frontend_demo_scenario("integrated-demo"),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}
    without_avoidance = cases["withoutConflictAvoidance"]["result"]
    with_avoidance = cases["withConflictAvoidance"]["result"]

    assert payload["scenarioId"] == "integrated-demo"
    assert without_avoidance["metrics"]["assignedTaskCount"] == 6
    assert with_avoidance["metrics"]["assignedTaskCount"] == 6
    assert without_avoidance["metrics"]["conflictCount"] > 0
    assert with_avoidance["metrics"]["conflictCount"] == 0
    assert without_avoidance["conflicts"]
    assert len(with_avoidance["conflicts"]) == with_avoidance["metrics"]["conflictCount"]
    assert without_avoidance["metrics"]["failureCount"] == 0
    assert with_avoidance["metrics"]["failureCount"] == 0


def test_integrated_demo_dynamic_variant_triggers_replanning() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": integrated_dynamic_event_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 12},
    )

    assert tick_response.status_code == 200
    payload = tick_response.json()
    task_ids = {task["id"] for task in payload["result"]["tasks"]}
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]

    assert payload["scenarioId"] == "integrated-demo"
    assert payload["currentTime"] == 12
    assert payload["result"]["dynamicTriggerTime"] == 12
    assert "E1" in task_ids
    assert any(event_text == "T=12 场景动态事件触发" for event_text in event_texts)
    assert len(payload["metricsHistory"]) >= 2
