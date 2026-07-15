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


def test_integrated_demo_runs_default_online_dispatch_flow() -> None:
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
