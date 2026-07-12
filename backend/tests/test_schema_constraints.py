from fastapi.testclient import TestClient

from backend.app.main import app
from backend.tests.helpers import scenario_payload


def test_request_models_reject_negative_time_and_non_positive_dimensions() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["width"] = 0

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 422

    valid_create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert valid_create_response.status_code == 200
    session_id = valid_create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": -1},
    )
    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "NEG",
                "type": "inspection",
                "title": "negative release",
                "priority": 1,
                "releaseTime": -1,
                "targets": [[1, 4]],
            }
        },
    )
    demand_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "ZERO-DEMAND",
                "type": "delivery",
                "title": "zero demand",
                "priority": 1,
                "pickup": [0, 0],
                "dropoff": [5, 4],
                "demand": 0,
            }
        },
    )

    assert tick_response.status_code == 422
    assert add_response.status_code == 422
    assert demand_response.status_code == 422
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["currentTime"] == 0
    assert all(task["id"] not in {"NEG", "ZERO-DEMAND"} for task in payload["result"]["tasks"])


def test_request_models_reject_unknown_fields() -> None:
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

    old_stream_response = client.post(
        f"/api/sessions/{session_id}/stream-task",
        json={"current_time": 3},
    )
    extra_task_field_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "EXTRA",
                "type": "inspection",
                "title": "extra field",
                "priority": 1,
                "targets": [[1, 4]],
                "unknownField": True,
            }
        },
    )

    assert old_stream_response.status_code == 404
    assert extra_task_field_response.status_code == 422
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["streamTaskCount"] == 0
    assert all(task["id"] != "EXTRA" for task in payload["result"]["tasks"])


def test_dispatch_options_reject_unbounded_assignment_replan_window() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {
                "avoidConflicts": True,
                "includeDynamic": False,
                "assignmentReplanWindow": 121,
            },
        },
    )

    assert create_response.status_code == 422
    assert client.get("/api/sessions").json() == []
