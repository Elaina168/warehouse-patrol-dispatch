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


def test_integrated_demo_runs_online_dispatch_flow() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": frontend_demo_scenario("integrated-demo"),
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
    assert tick_payload["scenarioId"] == "integrated-demo"
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
                "deadline": 24,
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
    assert without_avoidance["metrics"]["conflictCount"] > with_avoidance["metrics"]["conflictCount"]
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
