from fastapi.testclient import TestClient

from backend.app.main import app
from backend.tests.helpers import frontend_demo_scenario, scenario_payload


def test_demo_online_dispatch_flow() -> None:
    client = TestClient(app)

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert tick_response.status_code == 200
    tick_payload = tick_response.json()
    assert tick_payload["currentTime"] == 2
    assert any(state["locked"] for state in tick_payload["taskStates"])

    urgent_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "DEMO-URGENT",
                "type": "emergency",
                "title": "演示紧急复核",
                "priority": 5,
                "releaseTime": 2,
                "deadline": 12,
                "target": [1, 4],
            }
        },
    )
    assert urgent_response.status_code == 200
    urgent_payload = urgent_response.json()
    assert any(task["id"] == "DEMO-URGENT" for task in urgent_payload["result"]["tasks"])
    assert any("高优先级任务 DEMO-URGENT" in event["text"] for event in urgent_payload["result"]["eventLog"])

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 2], "currentTime": 2},
    )
    assert block_response.status_code == 200
    block_payload = block_response.json()
    assert [1, 2] in block_payload["result"]["extraBlocked"]
    assert any("手动封锁单元" in event["text"] for event in block_payload["result"]["eventLog"])

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 2},
    )
    assert fail_response.status_code == 200
    final_payload = fail_response.json()
    assert "R2" in final_payload["result"]["unavailableRobotIds"]
    assert len(final_payload["metricsHistory"]) >= 2
    assert any("手动标记故障机器人" in event["text"] for event in final_payload["result"]["eventLog"])


def test_frontend_campus_warehouse_demo_scenario_runs_online_flow() -> None:
    client = TestClient(app)
    scenario = frontend_demo_scenario("campus-warehouse")

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert tick_response.status_code == 200
    tick_payload = tick_response.json()
    assert tick_payload["scenarioId"] == "campus-warehouse"
    assert tick_payload["currentTime"] == 2
    assert any(state["locked"] for state in tick_payload["taskStates"])

    urgent_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "DEMO-URGENT",
                "type": "emergency",
                "title": "演示紧急复核",
                "priority": 5,
                "releaseTime": 2,
                "deadline": 12,
                "target": [1, 4],
            }
        },
    )
    assert urgent_response.status_code == 200
    urgent_payload = urgent_response.json()
    assert any(task["id"] == "DEMO-URGENT" for task in urgent_payload["result"]["tasks"])
    assert any("高优先级任务 DEMO-URGENT" in event["text"] for event in urgent_payload["result"]["eventLog"])

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 2], "currentTime": 2},
    )
    assert block_response.status_code == 200
    block_payload = block_response.json()
    assert [1, 2] in block_payload["result"]["extraBlocked"]
    assert any("手动封锁单元" in event["text"] for event in block_payload["result"]["eventLog"])

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 2},
    )
    assert fail_response.status_code == 200
    final_payload = fail_response.json()
    assert "R2" in final_payload["result"]["unavailableRobotIds"]
    assert len(final_payload["metricsHistory"]) >= 2
    assert any("手动标记故障机器人" in event["text"] for event in final_payload["result"]["eventLog"])


def test_frontend_narrow_aisle_demo_scenario_shows_conflict_avoidance_effect() -> None:
    client = TestClient(app)
    scenario = frontend_demo_scenario("narrow-aisle")

    response = client.post(
        "/api/experiments/conflict-avoidance",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}
    without_avoidance = cases["withoutConflictAvoidance"]["result"]
    with_avoidance = cases["withConflictAvoidance"]["result"]

    assert payload["scenarioId"] == "narrow-aisle"
    assert without_avoidance["metrics"]["assignedTaskCount"] == 3
    assert with_avoidance["metrics"]["assignedTaskCount"] == 3
    assert without_avoidance["metrics"]["conflictCount"] > 0
    assert with_avoidance["metrics"]["conflictCount"] == 0
    assert len(without_avoidance["conflicts"]) == without_avoidance["metrics"]["conflictCount"]
    assert with_avoidance["conflicts"] == []


def test_frontend_robot_failure_demo_scenario_triggers_dynamic_replanning() -> None:
    client = TestClient(app)
    scenario = frontend_demo_scenario("robot-failure")

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 9},
    )

    assert tick_response.status_code == 200
    payload = tick_response.json()
    task_ids = {task["id"] for task in payload["result"]["tasks"]}
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]

    assert payload["scenarioId"] == "robot-failure"
    assert payload["currentTime"] == 9
    assert payload["result"]["dynamicTriggerTime"] == 9
    assert payload["result"]["unavailableRobotIds"] == ["R1"]
    assert [5, 4] in payload["result"]["extraBlocked"]
    assert [5, 5] in payload["result"]["extraBlocked"]
    assert "E1" in task_ids
    assert any(state["robotId"] == "R1" and state["status"] == "failed" for state in payload["robotStates"])
    assert any(event_text == "T=9 场景动态事件触发" for event_text in event_texts)
    assert any("R1 故障，退出调度" in event_text for event_text in event_texts)
    assert any("新增 2 个封锁单元" in event_text for event_text in event_texts)
    assert len(payload["metricsHistory"]) >= 2
