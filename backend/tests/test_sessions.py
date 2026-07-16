from typing import Any

from fastapi.testclient import TestClient

import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.app.schemas import Assignment, Conflict, DispatchOptions, DispatchResult, Metrics, Scenario, Task
from backend.tests.helpers import frontend_demo_scenario, scenario_payload, seeded_pressure_scenario


def _assert_online_payload_consistent(payload: dict[str, Any]) -> None:
    current_time = payload["currentTime"]
    metric_times = [snapshot["time"] for snapshot in payload["metricsHistory"]]
    assert metric_times == sorted(set(metric_times))
    assert metric_times[-1] == current_time
    assert payload["completedTaskCount"] == sum(
        1 for state in payload["taskStates"] if state["status"] == "completed"
    )
    assert payload["result"]["metrics"]["failureCount"] == len(payload["result"]["failureDetails"])

    event_times = [event["time"] for event in payload["result"]["eventLog"]]
    assert event_times == sorted(event_times)

    for state in payload["robotStates"]:
        path = payload["result"]["paths"][state["robotId"]]
        assert path[min(current_time, len(path) - 1)] == state["position"]
        if state["robotId"] in payload["result"]["unavailableRobotIds"]:
            assert state["status"] == "failed"

    blocked_cells = {tuple(cell) for cell in payload["result"]["extraBlocked"]}
    if blocked_cells:
        for path in payload["result"]["paths"].values():
            for cell in path[current_time + 1 :]:
                assert tuple(cell) not in blocked_cells

    failure_reasons = payload["result"]["failureReasons"]
    failure_details = payload["result"]["failureDetails"]
    for state in payload["taskStates"]:
        task_id = state["taskId"]
        if state["failureReason"] is None:
            assert state["failureCategory"] is None
            assert state["recoveryAction"] is None
            assert task_id not in failure_reasons
            assert task_id not in failure_details
        else:
            assert failure_reasons[task_id] == state["failureReason"]
            detail = failure_details[task_id]
            assert detail["reason"] == state["failureReason"]
            assert detail["category"] == state["failureCategory"]
            assert detail["recoveryAction"] == state["recoveryAction"]
        if state["status"] == "unassigned":
            assert state["failureReason"] is not None
            assert state["failureCategory"] in {"temporary", "permanent"}
            assert state["recoveryAction"] is not None


def _post_generated_task(
    client: TestClient,
    session_id: str,
    current_time: int,
    task_id: str | None = None,
    target: list[int] | None = None,
) -> Any:
    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": current_time},
    )
    assert tick_response.status_code == 200
    if task_id is None:
        sequence = getattr(_post_generated_task, "_sequence", 0) + 1
        setattr(_post_generated_task, "_sequence", sequence)
        task_id = f"G{sequence}"
    return client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": task_id,
                "type": "inspection",
                "title": f"随机生成巡检 {task_id}",
                "priority": 0,
                "releaseTime": current_time,
                "deadline": current_time + 24,
                "targets": [target if target is not None else [1, 4]],
            }
        },
    )


def shelf_session_scenario() -> dict[str, Any]:
    return {
        "id": "shelf-session",
        "name": "shelf-session",
        "description": "货架库存在线会话测试",
        "width": 7,
        "height": 5,
        "obstacles": [[2, 2], [4, 2]],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[3, 0]],
            "delivery": [[6, 4]],
            "charging": [],
        },
        "shelves": [
            {"id": "S01", "cell": [2, 2], "serviceCell": [2, 1], "initialOccupied": False},
            {"id": "S02", "cell": [4, 2], "serviceCell": [4, 1], "initialOccupied": True},
        ],
        "robots": [{"id": "R1", "name": "R1", "start": [0, 4], "battery": 100, "load": 1}],
        "tasks": [],
        "dynamic": {"triggerTime": 10, "blockedCells": [], "failedRobots": [], "tasks": []},
    }


def inbound_runtime_task(task_id: str = "IN") -> dict[str, Any]:
    return {
        "id": task_id,
        "type": "delivery",
        "title": task_id,
        "priority": 2,
        "pickup": [0, 0],
        "dropoff": [2, 1],
        "demand": 1,
        "serviceTime": 3,
    }


def outbound_runtime_task(task_id: str = "OUT", pickup: list[int] | None = None) -> dict[str, Any]:
    return {
        "id": task_id,
        "type": "delivery",
        "title": task_id,
        "priority": 2,
        "pickup": pickup if pickup is not None else [4, 1],
        "dropoff": [6, 4],
        "demand": 1,
    }


def shelf_states(payload: dict[str, Any]) -> dict[str, str]:
    return {item["shelfId"]: item["status"] for item in payload["shelfStates"]}


def _assigned_task_path(payload: dict[str, Any], task_id: str) -> list[list[int]]:
    assignment = next(
        item
        for item in payload["result"]["assignments"]
        if any(task["id"] == task_id for task in item["tasks"])
    )
    return payload["result"]["paths"][assignment["robotId"]]


def test_session_reserves_completes_and_resets_shelf_inventory() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": shelf_session_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert created.status_code == 200
    session_id = created.json()["sessionId"]
    assert shelf_states(created.json()) == {"S01": "empty", "S02": "occupied"}

    inbound = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": inbound_runtime_task()},
    )
    assert inbound.status_code == 200
    assert shelf_states(inbound.json())["S01"] == "inboundReserved"

    outbound = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": outbound_runtime_task()},
    )
    assert outbound.status_code == 200
    assert shelf_states(outbound.json())["S02"] == "outboundReserved"

    completed = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 40})
    assert completed.status_code == 200
    assert shelf_states(completed.json()) == {"S01": "occupied", "S02": "empty"}

    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200
    assert shelf_states(reset.json()) == {"S01": "empty", "S02": "occupied"}


def test_session_ignores_dynamic_shelf_reservations_when_dynamic_events_are_disabled() -> None:
    client = TestClient(app)
    scenario = shelf_session_scenario()
    scenario["dynamic"]["tasks"] = [
        inbound_runtime_task("D-IN"),
        outbound_runtime_task("D-OUT"),
    ]

    created = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert created.status_code == 200
    session_id = created.json()["sessionId"]
    assert shelf_states(created.json()) == {"S01": "empty", "S02": "occupied"}

    inbound = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": inbound_runtime_task()},
    )
    assert inbound.status_code == 200
    assert shelf_states(inbound.json())["S01"] == "inboundReserved"

    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200
    assert shelf_states(reset.json()) == {"S01": "empty", "S02": "occupied"}


def test_session_shelf_inventory_outbound_pickup_empties_at_actual_visit_time() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": shelf_session_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = created.json()["sessionId"]
    added = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": outbound_runtime_task()},
    ).json()
    path = _assigned_task_path(added, "OUT")
    pickup_tick = path.index([4, 1])

    before_pickup = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": pickup_tick - 1},
    ).json()
    at_pickup = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": pickup_tick},
    ).json()

    assert shelf_states(before_pickup)["S02"] == "outboundReserved"
    assert shelf_states(at_pickup)["S02"] == "empty"
    assert any(
        event == {"time": pickup_tick, "text": "货架 S02 已取货"}
        for event in at_pickup["result"]["eventLog"]
    )


def test_session_shelf_inventory_uses_exact_pickup_time_after_prior_service(
    monkeypatch,
) -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": shelf_session_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = created.json()["sessionId"]
    inbound_task = inbound_runtime_task()
    inbound_task["priority"] = 4
    client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": inbound_task},
    )
    added = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": outbound_runtime_task()},
    ).json()
    assignment = added["result"]["assignments"][0]
    assert [task["id"] for task in assignment["tasks"]] == ["IN"]
    path = added["result"]["paths"][assignment["robotId"]]
    inbound_dropoff_tick = path.index([2, 1], path.index([0, 0]))
    inbound_completion_tick = inbound_dropoff_tick + 3
    assert inbound_completion_tick == 10
    replanned = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": inbound_completion_tick},
    ).json()
    outbound_path = _assigned_task_path(replanned, "OUT")
    pickup_tick = outbound_path.index([4, 1], inbound_completion_tick)
    assert pickup_tick == 12
    target_time = 17
    monkeypatch.setattr(
        sessions_module,
        "_outbound_pickup_times_until",
        lambda *_args: {},
        raising=False,
    )

    completed = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": target_time},
    ).json()
    pickup_events = [
        event
        for event in completed["result"]["eventLog"]
        if event["text"] == "货架 S02 已取货"
    ]

    assert shelf_states(completed)["S02"] == "empty"
    assert pickup_events == [{"time": pickup_tick, "text": "货架 S02 已取货"}]


def test_session_shelf_inventory_inbound_waits_full_service_time() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": shelf_session_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = created.json()["sessionId"]
    added = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": inbound_runtime_task()},
    ).json()
    path = _assigned_task_path(added, "IN")
    dropoff_tick = path.index([2, 1], path.index([0, 0]))

    at_dropoff = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": dropoff_tick},
    ).json()
    before_service_done = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": dropoff_tick + 2},
    ).json()
    after_service_done = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": dropoff_tick + 3},
    ).json()

    assert shelf_states(at_dropoff)["S01"] == "inboundReserved"
    assert shelf_states(before_service_done)["S01"] == "inboundReserved"
    assert shelf_states(after_service_done)["S01"] == "occupied"
    assert any(
        event == {"time": dropoff_tick + 3, "text": "货架 S01 已放货"}
        for event in after_service_done["result"]["eventLog"]
    )


def test_session_shelf_task_rejection_has_no_runtime_side_effects() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": shelf_session_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = created.json()["sessionId"]
    accepted = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": inbound_runtime_task()},
    ).json()

    for invalid_task in [
        inbound_runtime_task("IN-2"),
        outbound_runtime_task("OUT-EMPTY", pickup=[2, 1]),
    ]:
        rejected = client.post(
            f"/api/sessions/{session_id}/tasks",
            json={"task": invalid_task},
        )
        assert rejected.status_code == 422
        current = client.get(f"/api/sessions/{session_id}").json()
        assert current["currentTime"] == accepted["currentTime"]
        assert current["runtimeTaskCount"] == accepted["runtimeTaskCount"]
        assert current["metricsHistory"] == accepted["metricsHistory"]
        assert [task["id"] for task in current["result"]["tasks"]] == [
            task["id"] for task in accepted["result"]["tasks"]
        ]
        assert shelf_states(current) == shelf_states(accepted)
        assert current["result"] == accepted["result"]


def test_session_shelf_inventory_reservations_survive_robot_failure_and_restore() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": shelf_session_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = created.json()["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": inbound_runtime_task()},
    )
    reserved = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": outbound_runtime_task()},
    ).json()
    assert shelf_states(reserved) == {"S01": "inboundReserved", "S02": "outboundReserved"}

    failed = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 0},
    )
    restored = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R1", "currentTime": 0},
    )

    assert failed.status_code == 200
    assert restored.status_code == 200
    assert shelf_states(failed.json()) == shelf_states(reserved)
    assert shelf_states(restored.json()) == shelf_states(reserved)


def test_conflict_states_follow_current_robot_overlap() -> None:
    conflict = Conflict(time=1, type="vertex", robots=["R1", "R2"], cell=(7, 4))
    paths = {
        "R1": [(6, 4), (7, 4), (7, 4), (8, 4)],
        "R2": [(8, 4), (7, 4), (7, 4), (7, 4)],
    }

    active_state = sessions_module._build_conflict_states([conflict], paths, 2)[0]
    resolved_state = sessions_module._build_conflict_states([conflict], paths, 3)[0]

    assert active_state.status == "active"
    assert active_state.startedAt == 1
    assert active_state.resolvedAt == 3
    assert resolved_state.status == "resolved"
    assert resolved_state.resolvedAt == 3


def test_session_refreshes_cached_conflict_states_at_the_current_tick() -> None:
    client = TestClient(app)
    scenario = {
        "id": "cached-conflict-state",
        "name": "cached-conflict-state",
        "description": "cached conflict state should follow the current session tick",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [[0, 0]], "inspection": [[1, 0]], "delivery": []},
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [2, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "左侧巡检", "priority": 2, "targets": [[1, 0]]},
            {"id": "T2", "type": "inspection", "title": "右侧巡检", "priority": 2, "targets": [[1, 0]]},
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    create_response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": False, "includeDynamic": False}},
    )

    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    tick_response = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 1})

    assert tick_response.status_code == 200
    payload = tick_response.json()
    assert payload["result"]["conflicts"] == [
        {"time": 1, "type": "vertex", "robots": ["R1", "R2"], "cell": [1, 0]}
    ]
    assert payload["result"]["conflictStates"] == [
        {
            "time": 1,
            "type": "vertex",
            "robots": ["R1", "R2"],
            "cell": [1, 0],
            "status": "active",
            "startedAt": 1,
            "resolvedAt": None,
        }
    ]
    assert payload["metricsHistory"][-1]["activeConflictCount"] == 1


def test_integrated_demo_avoidance_keeps_idle_robot_out_of_active_task_path() -> None:
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
    tick_response = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 22})

    assert tick_response.status_code == 200
    payload = tick_response.json()
    assert payload["result"]["conflicts"] == []
    assert payload["metricsHistory"][-1]["activeConflictCount"] == 0


def test_online_session_reports_slow_robot_position_and_keeps_service_time_independent() -> None:
    client = TestClient(app)
    scenario = {
        "id": "online-speed-progress",
        "name": "online-speed-progress",
        "description": "slow movement and service time should use separate ticks",
        "width": 2,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [], "inspection": [[1, 0]], "delivery": []},
        "robots": [
            {"id": "R1", "name": "slow", "start": [0, 0], "battery": 90, "load": 1, "moveTicks": 3},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "slow inspection", "priority": 2, "targets": [[1, 0]], "serviceTime": 2},
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    create_response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}},
    )

    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    second_tick = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 2}).json()
    arrival_tick = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 3}).json()
    completion_tick = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 5}).json()

    second_robot = next(state for state in second_tick["robotStates"] if state["robotId"] == "R1")
    assert second_robot["position"] == [0, 0]
    assert second_robot["moveTicks"] == 3
    assert next(state for state in arrival_tick["taskStates"] if state["taskId"] == "T1")["status"] == "running"
    completion_state = next(state for state in completion_tick["taskStates"] if state["taskId"] == "T1")
    assert completion_state["completionTime"] == 5
    assert completion_state["status"] == "completed"


def test_session_result_metrics_keep_completed_deadline_miss() -> None:
    client = TestClient(app)
    scenario = {
        "id": "completed-deadline-miss",
        "name": "completed-deadline-miss",
        "description": "completed late task should remain in session deadline metrics",
        "width": 2,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [], "inspection": [[1, 0]], "delivery": []},
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {
                "id": "T1",
                "type": "inspection",
                "title": "late inspection",
                "priority": 1,
                "targets": [[1, 0]],
                "deadline": 0,
            },
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    create_response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}},
    )

    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    tick_response = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 2})

    assert tick_response.status_code == 200
    payload = tick_response.json()
    assert payload["completedTaskCount"] == 1
    assert payload["result"]["metrics"]["deadlineMissCount"] == 1
    assert payload["metricsHistory"][-1]["deadlineMissCount"] == 1


def test_session_api_accepts_manual_task() -> None:
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

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "人工追加巡检",
                "priority": 4,
                "releaseTime": 3,
                "deadline": 18,
                "targets": [[1, 4]],
            }
        },
    )

    assert add_response.status_code == 200
    payload = add_response.json()
    assert payload["runtimeTaskCount"] == 1
    assert payload["result"]["metrics"]["assignedTaskCount"] == 0
    assert any(task["id"] == "M1" for task in payload["result"]["tasks"])


def test_session_create_keeps_tasks_waiting_until_first_tick() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": frontend_demo_scenario("integrated-demo"),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["currentTime"] == 0
    assert payload["result"]["assignments"] == []
    assert all(state["status"] == "pending" for state in payload["taskStates"])
    assert all(state["assignedRobotId"] is None for state in payload["taskStates"])
    assert all(state["status"] == "idle" for state in payload["robotStates"])
    assert payload["result"]["metrics"]["assignedTaskCount"] == 0


def test_session_runtime_robot_failure_before_play_is_visible_without_starting_planning() -> None:
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

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 0},
    )

    assert fail_response.status_code == 200
    payload = fail_response.json()
    robot_state = next(state for state in payload["robotStates"] if state["robotId"] == "R1")
    assert payload["currentTime"] == 0
    assert payload["result"]["assignments"] == []
    assert payload["result"]["unavailableRobotIds"] == ["R1"]
    assert robot_state["status"] == "failed"
    assert payload["result"]["metrics"]["assignedTaskCount"] == 0


def test_session_add_task_before_play_stays_pending_until_tick() -> None:
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

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "仿真前追加巡检",
                "priority": 4,
                "deadline": 36,
                "targets": [[3, 0]],
            }
        },
    )

    assert add_response.status_code == 200
    added_payload = add_response.json()
    assert added_payload["result"]["assignments"] == []
    assert {state["status"] for state in added_payload["taskStates"]} == {"pending"}
    assert any(event["text"] == "手动录入任务：M1 仿真前追加巡检" for event in added_payload["result"]["eventLog"])

    tick_response = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 1})

    assert tick_response.status_code == 200
    tick_payload = tick_response.json()
    assert tick_payload["currentTime"] == 1
    assert tick_payload["result"]["assignments"]
    assert any(state["status"] == "running" for state in tick_payload["taskStates"])


def test_session_keeps_task_running_until_service_time_ends() -> None:
    client = TestClient(app)
    scenario = {
        "id": "service-time-session",
        "name": "service-time-session",
        "description": "service time session regression",
        "width": 2,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [[0, 0]], "inspection": [[1, 0]], "delivery": []},
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {
                "id": "T1",
                "type": "inspection",
                "title": "终点作业",
                "priority": 2,
                "targets": [[1, 0]],
                "serviceTime": 2,
            },
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    create_response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": True}},
    )

    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    first_tick = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 1}).json()
    replan_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "T2",
                "type": "inspection",
                "title": "作业中新增任务",
                "priority": 1,
                "releaseTime": 1,
                "targets": [[0, 0]],
            }
        },
    )
    assert replan_response.status_code == 200
    second_tick = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 2}).json()
    final_tick = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 3}).json()

    first_state = next(state for state in first_tick["taskStates"] if state["taskId"] == "T1")
    replanned_state = next(state for state in replan_response.json()["taskStates"] if state["taskId"] == "T1")
    second_state = next(state for state in second_tick["taskStates"] if state["taskId"] == "T1")
    final_state = next(state for state in final_tick["taskStates"] if state["taskId"] == "T1")
    assert first_state["status"] == "running"
    assert replanned_state["status"] == "running"
    assert replanned_state["completionTime"] == 3
    assert second_state["status"] == "running"
    assert final_state["status"] == "completed"
    assert final_state["completionTime"] == 3


def test_session_stream_task_endpoint_is_removed() -> None:
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

    stream_response = client.post(f"/api/sessions/{session_id}/stream-task", json={"currentTime": 0})

    assert stream_response.status_code == 404


def test_session_assigns_same_target_tasks_to_distinct_available_robots() -> None:
    client = TestClient(app)
    scenario = {
        "id": "same-target-capacity",
        "name": "same-target-capacity",
        "description": "same target capacity regression",
        "width": 21,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [[0, 0]], "inspection": [[2, 0]], "delivery": []},
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [10, 0], "battery": 90, "load": 1},
            {"id": "R3", "name": "R3", "start": [20, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {"triggerTime": 99, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    create_response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    for task_id in ("M1", "M2", "M3"):
        response = client.post(
            f"/api/sessions/{session_id}/tasks",
            json={
                "task": {
                    "id": task_id,
                    "type": "inspection",
                    "title": task_id,
                    "priority": 3,
                    "releaseTime": 0,
                    "deadline": 20,
                    "targets": [[2, 0]],
                }
            },
        )
        assert response.status_code == 200

    payload = response.json()
    assignments = {
        assignment["robotId"]: [task["id"] for task in assignment["tasks"]]
        for assignment in payload["result"]["assignments"]
    }
    task_states = {state["taskId"]: state for state in payload["taskStates"]}

    assert all(len(task_ids) <= 1 for task_ids in assignments.values())
    assert {state["assignedRobotId"] for state in task_states.values()} == {"R1", "R2", "R3"}
    assert all(state["status"] == "running" for state in task_states.values())


def test_session_replans_waiting_task_when_the_active_task_completes() -> None:
    client = TestClient(app)
    scenario = {
        "id": "single-robot-task-queue",
        "name": "single-robot-task-queue",
        "description": "single active task regression",
        "width": 4,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [[0, 0]], "inspection": [[2, 0], [3, 0]], "delivery": []},
        "robots": [{"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1}],
        "tasks": [],
        "dynamic": {"triggerTime": 99, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    create_response = client.post(
        "/api/sessions",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    for task_id, target in (("M1", [2, 0]), ("M2", [3, 0])):
        response = client.post(
            f"/api/sessions/{session_id}/tasks",
            json={
                "task": {
                    "id": task_id,
                    "type": "inspection",
                    "title": task_id,
                    "priority": 3,
                    "releaseTime": 0,
                    "deadline": 20,
                    "targets": [target],
                }
            },
        )
        assert response.status_code == 200

    queued_state = next(state for state in response.json()["taskStates"] if state["taskId"] == "M2")
    assert queued_state["status"] == "pending"
    assert queued_state["failureReason"] is None

    tick_response = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 2})
    assert tick_response.status_code == 200
    task_states = {state["taskId"]: state for state in tick_response.json()["taskStates"]}

    assert task_states["M1"]["status"] == "completed"
    assert task_states["M2"]["status"] == "running"
    assert task_states["M2"]["assignedRobotId"] == "R1"


def test_session_integrated_demo_flow_stays_consistent_through_online_updates() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": frontend_demo_scenario("integrated-demo"),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    session_id = payload["sessionId"]
    _assert_online_payload_consistent(payload)

    for current_time in range(1, 13):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    assert payload["scenarioId"] == "integrated-demo"
    assert payload["currentTime"] == 12
    assert payload["runtimeTaskCount"] == 0
    assert payload["result"]["dynamicTriggerTime"] is None
    assert task_states["E1"]["status"] == "running"
    assert task_states["E1"]["failureReason"] is None
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["metricsHistory"][-1]["time"] == 12
    assert payload["metricsHistory"][-1]["completedTaskCount"] == payload["completedTaskCount"]

    manual_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "DEMO-RUNTIME-12",
                "type": "emergency",
                "title": "演示动态后复核",
                "priority": 5,
                "releaseTime": 12,
                "deadline": 24,
                "target": [1, 4],
            }
        },
    )
    assert manual_response.status_code == 200
    payload = manual_response.json()
    _assert_online_payload_consistent(payload)

    task_ids = {task["id"] for task in payload["result"]["tasks"]}
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    assert payload["currentTime"] == 12
    assert payload["runtimeTaskCount"] == 1
    assert "DEMO-RUNTIME-12" in task_ids
    assert any(
        task["id"] == "DEMO-RUNTIME-12" and task["title"] == "演示动态后复核"
        for task in payload["result"]["tasks"]
    )
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert any("DEMO-RUNTIME-12" in text for text in event_texts)


def test_session_fixed_seed_pressure_flow_handles_runtime_events_consistently() -> None:
    client = TestClient(app)
    scenario = seeded_pressure_scenario("seed-17", seed=17, robot_count=4, task_count=12).model_dump(mode="json")
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 120},
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    session_id = payload["sessionId"]
    _assert_online_payload_consistent(payload)

    assert payload["scenarioId"] == "seeded-pressure-seed-17"
    assert payload["result"]["dynamicTriggerTime"] == 8
    assert payload["result"]["metrics"]["assignedTaskCount"] == 4
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0

    for current_time in range(1, 9):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)
        assert payload["result"]["metrics"]["failureCount"] == 0
        assert payload["metricsHistory"][-1]["activeConflictCount"] == 0

    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    dynamic_task_ids = {state["taskId"] for state in payload["taskStates"] if state["taskId"].startswith("E")}
    assert payload["currentTime"] == 8
    assert payload["result"]["metrics"]["assignedTaskCount"] == 4
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["result"]["extraBlocked"] == scenario["dynamic"]["blockedCells"]
    assert dynamic_task_ids == {"E1", "E2", "E3"}
    assert all(
        state["status"] in {"pending", "running"} and state["failureReason"] is None
        for state in payload["taskStates"]
        if state["taskId"] in dynamic_task_ids
    )
    assert any(text == "T=8 场景动态事件触发" for text in event_texts)
    assert any("新增 2 个封锁单元" in text for text in event_texts)

    manual_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "RUNTIME-SEED-17",
                "type": "emergency",
                "title": "固定种子运行时复核",
                "priority": 5,
                "releaseTime": 8,
                "deadline": 28,
                "target": [2, 9],
            }
        },
    )
    assert manual_response.status_code == 200
    payload = manual_response.json()
    _assert_online_payload_consistent(payload)
    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    assert payload["runtimeTaskCount"] == 1
    assert payload["result"]["metrics"]["assignedTaskCount"] == 4
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert task_states["RUNTIME-SEED-17"]["status"] == "running"
    assert task_states["RUNTIME-SEED-17"]["failureReason"] is None

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [3, 9], "currentTime": 8},
    )
    assert block_response.status_code == 200
    payload = block_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 1
    assert [3, 9] in payload["result"]["extraBlocked"]
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["result"]["failureDetails"] == {}

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R4", "currentTime": 8},
    )
    assert fail_response.status_code == 200
    payload = fail_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 2
    assert "R4" in payload["result"]["unavailableRobotIds"]
    assert payload["result"]["metrics"]["assignedTaskCount"] == 3
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["failureDetails"] == {}

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R4", "currentTime": 8},
    )
    assert restore_response.status_code == 200
    payload = restore_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 1
    assert "R4" not in payload["result"]["unavailableRobotIds"]
    assert payload["result"]["metrics"]["assignedTaskCount"] == 4
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [3, 9], "currentTime": 8},
    )
    assert unblock_response.status_code == 200
    payload = unblock_response.json()
    _assert_online_payload_consistent(payload)
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    assert payload["runtimeEventCount"] == 0
    assert [3, 9] not in payload["result"]["extraBlocked"]
    assert payload["result"]["extraBlocked"] == scenario["dynamic"]["blockedCells"]
    assert payload["result"]["metrics"]["assignedTaskCount"] == 4
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["result"]["failureDetails"] == {}
    assert payload["metricsHistory"][-1]["completedTaskCount"] == payload["completedTaskCount"]
    assert any("RUNTIME-SEED-17" in text for text in event_texts)
    assert any(text == "T=8 手动封锁单元：(3, 9)" for text in event_texts)
    assert any(text == "T=8 手动标记故障机器人：R4" for text in event_texts)
    assert any(text == "T=8 手动恢复机器人：R4" for text in event_texts)
    assert any(text == "T=8 手动解除封锁单元：(3, 9)" for text in event_texts)


def test_session_create_returns_default_assignment_replan_window() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["options"]["assignmentReplanWindow"] == 24

    get_response = client.get(f"/api/sessions/{payload['sessionId']}")
    assert get_response.status_code == 200
    assert get_response.json()["options"]["assignmentReplanWindow"] == 24


def test_session_add_future_manual_task_does_not_advance_time() -> None:
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

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "manual future inspection",
                "priority": 4,
                "releaseTime": 5,
                "deadline": 18,
                "targets": [[1, 4]],
            }
        },
    )

    assert add_response.status_code == 200
    payload = add_response.json()
    assert payload["currentTime"] == 0
    manual_state = next(state for state in payload["taskStates"] if state["taskId"] == "M1")
    assert manual_state["status"] == "pending"
    assert manual_state["releaseTime"] == 5

def test_session_create_syncs_immediately_completed_task_count() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["id"] = "instant-complete"
    scenario["width"] = 3
    scenario["height"] = 3
    scenario["obstacles"] = []
    scenario["zones"] = {
        "warehouse": [[0, 0]],
        "inspection": [[0, 0]],
        "delivery": [[2, 2]],
    }
    scenario["robots"] = [
        {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
    ]
    scenario["tasks"] = [
        {"id": "T0", "type": "inspection", "title": "instant", "priority": 1, "targets": [[0, 0]]},
    ]
    scenario["dynamic"] = {
        "triggerTime": 99,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [],
    }

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert create_response.status_code == 200
    payload = create_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "T0")
    assert task_state["status"] == "completed"
    assert task_state["completionTime"] == 0
    assert payload["completedTaskCount"] == 1
    assert payload["metricsHistory"][-1]["completedTaskCount"] == 1

def test_session_add_manual_task_preserves_existing_locked_assignments() -> None:
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
    locked_before = {
        state["taskId"]: state["assignedRobotId"]
        for state in tick_response.json()["taskStates"]
        if state["locked"] and state["assignedRobotId"] is not None
    }
    assert locked_before

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "manual online inspection",
                "priority": 4,
                "releaseTime": 4,
                "deadline": 18,
                "targets": [[1, 4]],
            }
        },
    )

    assert add_response.status_code == 200
    payload = add_response.json()
    assert payload["currentTime"] == 2
    locked_after = {
        state["taskId"]: state["assignedRobotId"]
        for state in payload["taskStates"]
        if state["locked"] and state["assignedRobotId"] is not None
    }
    for task_id, robot_id in locked_before.items():
        assert locked_after.get(task_id) == robot_id
    assert any(task["id"] == "M1" for task in payload["result"]["tasks"])

def test_session_passive_tick_exposes_new_lock_events() -> None:
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
    payload = tick_response.json()
    locked_task_ids = {state["taskId"] for state in payload["taskStates"] if state["locked"]}
    lock_event_task_ids = {
        event["text"].split(" 任务 ", 1)[1].split(" 锁定给机器人 ", 1)[0]
        for event in payload["result"]["eventLog"]
        if " 锁定给机器人 " in event["text"]
    }
    assert locked_task_ids
    assert locked_task_ids.issubset(lock_event_task_ids)

def test_session_add_active_high_priority_task_releases_locks_when_score_improves() -> None:
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
    locked_before = {
        state["taskId"]
        for state in tick_response.json()["taskStates"]
        if state["locked"] and state["assignedRobotId"] is not None
    }
    assert locked_before

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "emergency",
                "title": "active emergency task",
                "priority": 5,
                "releaseTime": 2,
                "deadline": 10,
                "target": [1, 4],
            }
        },
    )

    assert add_response.status_code == 200
    payload = add_response.json()
    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    for task_id in locked_before:
        assert task_states[task_id]["locked"] is False
    assert task_states["M1"]["assignedRobotId"] is not None
    assert any("高优先级任务 M1 评分更优，释放低优先级锁定任务" in event["text"] for event in payload["result"]["eventLog"])

def test_preemption_releases_lower_priority_locks_when_score_improves(monkeypatch) -> None:
    scenario = Scenario.model_validate(scenario_payload())
    low_task = scenario.tasks[0]
    urgent_task = Task(
        id="M1",
        type="emergency",
        title="scored emergency",
        priority=5,
        releaseTime=0,
        deadline=8,
        target=(0, 1),
    )
    session = sessions_module.DispatchSession(
        session_id="scored-preemption",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
        robot_positions={robot.id: robot.start for robot in scenario.robots},
        robot_travelled_distance={robot.id: 0 for robot in scenario.robots},
        locked_task_robot_ids={low_task.id: "R1"},
    )
    session.scenario.tasks.append(urgent_task)

    def metrics(makespan: int, total_distance: int) -> Metrics:
        return Metrics(
            makespan=makespan,
            totalDistance=total_distance,
            conflictCount=0,
            loadBalance=0,
            assignedTaskCount=2,
            deadlineMissCount=0,
            averageLateness=0,
            failureCount=0,
            replanTimeMs=0,
        )

    current_result = DispatchResult(
        scenarioId=scenario.id,
        avoidConflicts=True,
        includeDynamic=False,
        dynamicTriggerTime=None,
        extraBlocked=[],
        unavailableRobotIds=[],
        assignments=[Assignment(robotId="R1", tasks=[low_task, urgent_task])],
        paths={
            "R1": [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (4, 0), (3, 0), (2, 0), (1, 0), (0, 0), (0, 1)],
            "R2": [(0, 4)],
        },
        conflicts=[],
        metrics=metrics(makespan=11, total_distance=11),
        eventLog=[],
        tasks=[low_task, urgent_task],
    )
    proposed_result = DispatchResult(
        scenarioId=scenario.id,
        avoidConflicts=True,
        includeDynamic=False,
        dynamicTriggerTime=None,
        extraBlocked=[],
        unavailableRobotIds=[],
        assignments=[Assignment(robotId="R1", tasks=[urgent_task]), Assignment(robotId="R2", tasks=[low_task])],
        paths={
            "R1": [(0, 0), (0, 1)],
            "R2": [(0, 4), (1, 4), (2, 4), (3, 4), (4, 4), (5, 4), (5, 3), (5, 2), (5, 1), (5, 0)],
        },
        conflicts=[],
        metrics=metrics(makespan=9, total_distance=10),
        eventLog=[],
        tasks=[low_task, urgent_task],
    )

    def fake_run_dispatch(scenario_arg, options_arg, locked_task_robot_ids, *args, **kwargs):
        if locked_task_robot_ids.get(low_task.id) == "R1":
            return current_result
        return proposed_result

    monkeypatch.setattr(sessions_module, "run_dispatch", fake_run_dispatch)

    sessions_module._release_locks_for_active_higher_priority_task(session, urgent_task, 0)

    assert low_task.id not in session.locked_task_robot_ids
    assert any("评分更优，释放低优先级锁定任务" in note.text for note in session.event_notes)

def test_preemption_keeps_lower_priority_locks_when_score_does_not_improve(monkeypatch) -> None:
    scenario = Scenario.model_validate(scenario_payload())
    low_task = scenario.tasks[0]
    urgent_task = Task(
        id="M1",
        type="emergency",
        title="scored emergency",
        priority=5,
        releaseTime=0,
        deadline=8,
        target=(0, 1),
    )
    session = sessions_module.DispatchSession(
        session_id="scored-no-preemption",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
        robot_positions={robot.id: robot.start for robot in scenario.robots},
        robot_travelled_distance={robot.id: 0 for robot in scenario.robots},
        locked_task_robot_ids={low_task.id: "R1"},
    )
    session.scenario.tasks.append(urgent_task)

    def metrics(makespan: int, total_distance: int) -> Metrics:
        return Metrics(
            makespan=makespan,
            totalDistance=total_distance,
            conflictCount=0,
            loadBalance=0,
            assignedTaskCount=2,
            deadlineMissCount=0,
            averageLateness=0,
            failureCount=0,
            replanTimeMs=0,
        )

    current_result = DispatchResult(
        scenarioId=scenario.id,
        avoidConflicts=True,
        includeDynamic=False,
        dynamicTriggerTime=None,
        extraBlocked=[],
        unavailableRobotIds=[],
        assignments=[Assignment(robotId="R1", tasks=[urgent_task])],
        paths={"R1": [(0, 0), (0, 1)], "R2": [(0, 4)]},
        conflicts=[],
        metrics=metrics(makespan=1, total_distance=1),
        eventLog=[],
        tasks=[low_task, urgent_task],
    )
    proposed_result = current_result.model_copy(
        update={
            "paths": {"R1": [(0, 0), (1, 0), (1, 1), (0, 1)], "R2": [(0, 4)]},
            "metrics": metrics(makespan=3, total_distance=3),
        }
    )

    def fake_run_dispatch(scenario_arg, options_arg, locked_task_robot_ids, *args, **kwargs):
        if locked_task_robot_ids.get(low_task.id) == "R1":
            return current_result
        return proposed_result

    monkeypatch.setattr(sessions_module, "run_dispatch", fake_run_dispatch)

    sessions_module._release_locks_for_active_higher_priority_task(session, urgent_task, 0)

    assert session.locked_task_robot_ids[low_task.id] == "R1"
    assert not session.event_notes

def test_session_api_accepts_generated_task_through_unified_queue() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    generated_response = _post_generated_task(client, session_id, 7, "G1")

    assert generated_response.status_code == 200
    payload = generated_response.json()
    assert payload["runtimeTaskCount"] == 1
    assert "manualTaskCount" not in payload
    assert "streamTaskCount" not in payload
    assert any(task["id"] == "G1" and task["releaseTime"] == 7 for task in payload["result"]["tasks"])
    assert any("手动录入任务：G1" in event["text"] for event in payload["result"]["eventLog"])


def test_session_generated_task_rejects_existing_task_ids() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["tasks"].append(
        {
            "id": "A1",
            "type": "inspection",
            "title": "已有自动编号任务",
            "priority": 2,
            "targets": [[1, 4]],
        }
    )
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    duplicate_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "A1",
                "type": "inspection",
                "title": "重复编号任务",
                "priority": 0,
                "releaseTime": 1,
                "deadline": 25,
                "targets": [[1, 4]],
            }
        },
    )

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"] == "任务 ID 已存在：A1"


def test_runtime_update_omitted_current_time_uses_session_current_time() -> None:
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
        json={"currentTime": 4},
    )
    assert tick_response.status_code == 200

    generated_response = _post_generated_task(client, session_id, 4, "G4")

    assert generated_response.status_code == 200
    payload = generated_response.json()
    assert payload["currentTime"] == 4
    assert payload["runtimeTaskCount"] == 1
    assert "streamTaskCount" not in payload
    assert any(task["id"] == "G4" and task["releaseTime"] == 4 for task in payload["result"]["tasks"])


def test_session_tick_tracks_runtime_state() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    assert create_payload["metricsHistory"][0]["time"] == 0

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )

    assert tick_response.status_code == 200
    payload = tick_response.json()
    assert payload["currentTime"] == 2
    assert len(payload["robotStates"]) == 2
    assert len(payload["taskStates"]) >= 2
    assert any(state["locked"] for state in payload["taskStates"])
    assert any(
        state["status"] in {"waiting", "toPickup", "delivering", "inspecting"}
        for state in payload["robotStates"]
    )
    assert [item["time"] for item in payload["metricsHistory"]] == [0, 2]
    assert payload["metricsHistory"][-1]["completedTaskCount"] == payload["completedTaskCount"]
    assert payload["metricsHistory"][-1]["travelledDistance"] > 0
    assert any(state["position"] != [0, 0] for state in payload["robotStates"])
    for state in payload["robotStates"]:
        path = payload["result"]["paths"][state["robotId"]]
        assert path[payload["currentTime"]] == state["position"]

    get_response = client.get(f"/api/sessions/{session_id}")
    assert get_response.status_code == 200
    assert [item["time"] for item in get_response.json()["metricsHistory"]] == [0, 2]


def test_delivery_status_ignores_pickup_visits_before_release_time() -> None:
    client = TestClient(app)
    scenario = {
        "id": "release-pickup-status",
        "name": "release-pickup-status",
        "description": "delivery status release-time regression",
        "width": 4,
        "height": 2,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[1, 0], [0, 0]],
            "delivery": [[3, 0]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {
                "id": "I1",
                "type": "inspection",
                "title": "I1",
                "priority": 1,
                "deadline": 1,
                "targets": [[1, 0]],
            },
            {
                "id": "I2",
                "type": "inspection",
                "title": "I2",
                "priority": 1,
                "deadline": 2,
                "targets": [[0, 0]],
            },
            {
                "id": "D1",
                "type": "delivery",
                "title": "D1",
                "priority": 1,
                "releaseTime": 5,
                "deadline": 20,
                "pickup": [1, 0],
                "dropoff": [3, 0],
                "demand": 1,
            },
        ],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 5},
    )

    assert tick_response.status_code == 200
    payload = tick_response.json()
    robot_state = payload["robotStates"][0]
    delivery_state = next(state for state in payload["taskStates"] if state["taskId"] == "D1")
    assert robot_state["currentTaskId"] == "D1"
    assert robot_state["status"] == "toPickup"
    assert delivery_state["status"] == "running"


def test_session_delivery_payload_keeps_failed_carrier_position_reserved() -> None:
    client = TestClient(app)
    scenario = {
        "id": "delivery-carrier-failure",
        "name": "delivery-carrier-failure",
        "description": "delivery payload carrier failure regression",
        "width": 6,
        "height": 2,
        "obstacles": [[1, 1]],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [],
            "delivery": [[4, 0]],
        },
        "shelves": [
            {"id": "S01", "cell": [1, 1], "serviceCell": [1, 0], "initialOccupied": True},
        ],
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [5, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {
                "id": "D1",
                "type": "delivery",
                "title": "D1",
                "priority": 1,
                "releaseTime": 0,
                "deadline": 20,
                "pickup": [1, 0],
                "dropoff": [4, 0],
                "demand": 1,
            },
        ],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
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
    _assert_online_payload_consistent(tick_payload)
    carrier_state = next(state for state in tick_payload["robotStates"] if state["robotId"] == "R1")
    delivery_state = next(state for state in tick_payload["taskStates"] if state["taskId"] == "D1")
    assert carrier_state["position"] == [2, 0]
    assert carrier_state["currentTaskId"] == "D1"
    assert carrier_state["status"] == "delivering"
    assert delivery_state["status"] == "running"

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 2},
    )
    assert fail_response.status_code == 200
    failed_payload = fail_response.json()
    _assert_online_payload_consistent(failed_payload)

    failed_state = next(state for state in failed_payload["taskStates"] if state["taskId"] == "D1")
    replacement_path = failed_payload["result"]["paths"]["R2"]

    assert "R1" in failed_payload["result"]["unavailableRobotIds"]
    assert failed_payload["result"]["metrics"]["conflictCount"] == 0
    assert "D1" in failed_payload["result"]["failureReasons"]
    assert failed_payload["result"]["failureDetails"]["D1"]["category"] == "temporary"
    assert failed_state["assignedRobotId"] == "R2"
    assert failed_state["status"] == "unassigned"
    assert [2, 0] not in replacement_path[failed_payload["currentTime"] + 1 :]
    assert shelf_states(failed_payload)["S01"] == "empty"

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R1", "currentTime": 2},
    )
    assert restore_response.status_code == 200
    restored_payload = restore_response.json()
    _assert_online_payload_consistent(restored_payload)
    assert shelf_states(restored_payload)["S01"] == "empty"


def test_session_tick_reuses_plan_between_runtime_changes() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    initial_horizon = max(len(path) for path in create_payload["result"]["paths"].values()) - 1
    trigger_time = create_payload["result"]["dynamicTriggerTime"]
    assert initial_horizon > 0
    assert trigger_time is not None

    payload = create_payload
    for current_time in range(1, trigger_time):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        returned_horizon = max(len(path) for path in payload["result"]["paths"].values()) - 1
        assert returned_horizon == initial_horizon

    trigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": trigger_time},
    )
    assert trigger_response.status_code == 200
    payload = trigger_response.json()
    replanned_horizon = max(len(path) for path in payload["result"]["paths"].values()) - 1
    assert replanned_horizon >= initial_horizon

    for current_time in range(trigger_time + 1, replanned_horizon + 1):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        returned_horizon = max(len(path) for path in payload["result"]["paths"].values()) - 1
        assert returned_horizon >= current_time

    assert payload["currentTime"] == replanned_horizon
    assert 0 < payload["completedTaskCount"] < len(payload["result"]["tasks"])
    assert any(state["status"] in {"pending", "running"} for state in payload["taskStates"])


def test_session_event_log_records_only_elapsed_runtime_events() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["result"]["eventLog"] == []
    session_id = create_response.json()["sessionId"]

    payload = create_response.json()
    for current_time in range(1, 30):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()

    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    assert any("锁定给机器人" in text for text in event_texts)
    assert any("任务 T1 已完成" in text for text in event_texts)
    assert all(event["time"] <= payload["currentTime"] for event in payload["result"]["eventLog"])
    assert not any("未检测到时空路径冲突" in text for text in event_texts)


def test_integrated_demo_failure_position_stays_reserved_after_new_tasks() -> None:
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

    payload = create_response.json()
    for current_time in range(1, 19):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()

    assert all("场景动态事件触发" not in event["text"] for event in payload["result"]["eventLog"])
    assert payload["result"]["extraBlocked"] == []
    assert "R3" not in payload["result"]["unavailableRobotIds"]
    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    assert "E1" in task_states
    assert task_states["E1"]["releaseTime"] == 12

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R3", "currentTime": 18},
    )
    assert fail_response.status_code == 200
    payload = fail_response.json()

    failed_robot = next(state for state in payload["robotStates"] if state["robotId"] == "R3")
    assert failed_robot["status"] == "failed"
    failed_position = failed_robot["position"]
    assert failed_position != [6, 4]

    manual_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "人工追加任务",
                "priority": 3,
                "releaseTime": 18,
                "deadline": 56,
                "targets": [[3, 0]],
            }
        },
    )
    assert manual_response.status_code == 200
    generated_response = _post_generated_task(client, session_id, 18)
    assert generated_response.status_code == 200
    payload = generated_response.json()

    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["conflicts"] == []
    assert failed_position not in payload["result"]["paths"]["R4"][19:]

def test_session_stream_task_replans_from_current_time() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert tick_response.status_code == 200
    locked_before = {
        state["taskId"]: state["assignedRobotId"]
        for state in tick_response.json()["taskStates"]
        if state["locked"]
    }

    generated_response = _post_generated_task(client, session_id, 4)
    assert generated_response.status_code == 200
    payload = generated_response.json()
    assert payload["currentTime"] == 4
    assert "streamTaskCount" not in payload
    generated_events = [event for event in payload["result"]["eventLog"] if event["text"].startswith("手动录入任务：G")]
    assert generated_events
    assert {event["time"] for event in generated_events} == {4}
    locked_after = {
        state["taskId"]: state["assignedRobotId"]
        for state in payload["taskStates"]
        if state["locked"]
    }
    assert locked_before
    for task_id, robot_id in locked_before.items():
        if task_id in locked_after:
            assert locked_after[task_id] == robot_id
    for state in payload["robotStates"]:
        path = payload["result"]["paths"][state["robotId"]]
        assert path[payload["currentTime"]] == state["position"]

def test_session_replan_keeps_dynamic_trigger_time_absolute() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    trigger_time = create_payload["result"]["dynamicTriggerTime"]
    assert trigger_time == 6

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert tick_response.status_code == 200

    generated_response = _post_generated_task(client, session_id, 4)
    assert generated_response.status_code == 200
    payload = generated_response.json()
    dynamic_events = [
        event for event in payload["result"]["eventLog"] if event["text"] == f"T={trigger_time} 场景动态事件触发"
    ]
    assert payload["currentTime"] == 4
    assert payload["result"]["dynamicTriggerTime"] == trigger_time
    assert dynamic_events == []

    trigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": trigger_time},
    )
    assert trigger_response.status_code == 200
    trigger_payload = trigger_response.json()
    dynamic_events = [
        event for event in trigger_payload["result"]["eventLog"] if event["text"] == f"T={trigger_time} 场景动态事件触发"
    ]
    assert {event["time"] for event in dynamic_events} == {trigger_time}


def test_session_dynamic_task_release_time_is_not_before_dynamic_trigger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dynamic-task-release-floor",
        "name": "dynamic-task-release-floor",
        "description": "dynamic task state should not release before dynamic trigger",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 5,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [
                {
                    "id": "DYN-EARLY",
                    "type": "inspection",
                    "title": "DYN-EARLY",
                    "priority": 3,
                    "releaseTime": 0,
                    "targets": [[2, 0]],
                }
            ],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert create_response.status_code == 200
    payload = create_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "DYN-EARLY")
    assigned_task_ids = [
        task["id"]
        for assignment in payload["result"]["assignments"]
        for task in assignment["tasks"]
    ]
    assert task_state["status"] == "pending"
    assert task_state["releaseTime"] == 5
    assert task_state["assignedRobotId"] is None
    assert "DYN-EARLY" not in assigned_task_ids

    trigger_response = client.post(
        f"/api/sessions/{payload['sessionId']}/tick",
        json={"currentTime": 5},
    )

    assert trigger_response.status_code == 200
    triggered_payload = trigger_response.json()
    triggered_state = next(state for state in triggered_payload["taskStates"] if state["taskId"] == "DYN-EARLY")
    result_task = next(task for task in triggered_payload["result"]["tasks"] if task["id"] == "DYN-EARLY")
    assignment_task = next(
        task
        for assignment in triggered_payload["result"]["assignments"]
        for task in assignment["tasks"]
        if task["id"] == "DYN-EARLY"
    )
    assert triggered_state["releaseTime"] == 5
    assert result_task["releaseTime"] == 5
    assert assignment_task["releaseTime"] == 5


def test_session_pending_dynamic_task_deadline_does_not_count_before_trigger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dynamic-pending-deadline",
        "name": "dynamic-pending-deadline",
        "description": "pending dynamic task should not miss deadline before trigger",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 5,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [
                {
                    "id": "DYN-DEADLINE",
                    "type": "inspection",
                    "title": "DYN-DEADLINE",
                    "priority": 3,
                    "releaseTime": 0,
                    "deadline": 1,
                    "targets": [[2, 0]],
                }
            ],
        },
    }
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
    payload = tick_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "DYN-DEADLINE")
    assert task_state["status"] == "pending"
    assert task_state["releaseTime"] == 5
    assert payload["metricsHistory"][-1]["deadlineMissCount"] == 0


def test_session_logs_rolling_window_when_it_matches_dynamic_trigger() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["tasks"].append(
        {
            "id": "WINDOW-SAME-TICK",
            "type": "inspection",
            "title": "同刻滚动窗口任务",
            "priority": 4,
            "releaseTime": scenario["dynamic"]["triggerTime"] + sessions_module.ASSIGNMENT_REPLAN_WINDOW,
            "targets": [[1, 4]],
        }
    )
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    trigger_time = scenario["dynamic"]["triggerTime"]
    trigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": trigger_time},
    )

    assert trigger_response.status_code == 200
    payload = trigger_response.json()
    _assert_online_payload_consistent(payload)
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    assert f"T={trigger_time} 场景动态事件触发" in event_texts
    assert f"T={trigger_time} 滚动窗口纳入远期任务" in event_texts
    assigned_task_ids = [task["id"] for assignment in payload["result"]["assignments"] for task in assignment["tasks"]]
    assert "WINDOW-SAME-TICK" in assigned_task_ids


def test_session_api_accepts_runtime_blocked_cell() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 5},
    )

    assert block_response.status_code == 200
    payload = block_response.json()
    assert payload["runtimeEventCount"] == 1
    assert payload["result"]["includeDynamic"] is True
    assert [1, 1] in payload["result"]["extraBlocked"]
    assert any("手动封锁单元" in event["text"] for event in payload["result"]["eventLog"])

def test_runtime_blocked_cell_does_not_reenable_scenario_dynamic_metadata() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 5},
    )

    assert block_response.status_code == 200
    payload = block_response.json()
    assert payload["result"]["includeDynamic"] is True
    assert payload["result"]["dynamicTriggerTime"] is None

def test_session_rejects_blocking_occupied_robot_cell() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [0, 0], "currentTime": 0},
    )

    assert block_response.status_code == 409
    assert block_response.json()["detail"] == "封锁单元被机器人占用：R1 (0, 0)"

def test_session_blocked_cell_releases_affected_locked_task() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert tick_response.status_code == 200
    tick_payload = tick_response.json()
    locked_task = next(
        state
        for state in tick_payload["taskStates"]
        if state["locked"] and state["assignedRobotId"] is not None and state["completionTime"] is not None
    )
    path = tick_payload["result"]["paths"][locked_task["assignedRobotId"]]
    blocked_cell = next(
        path[index]
        for index in range(tick_payload["currentTime"] + 1, locked_task["completionTime"] + 1)
        if path[index] != path[tick_payload["currentTime"]]
    )

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": blocked_cell, "currentTime": tick_payload["currentTime"]},
    )

    assert block_response.status_code == 200
    payload = block_response.json()
    affected_state = next(state for state in payload["taskStates"] if state["taskId"] == locked_task["taskId"])
    assert affected_state["locked"] is False
    assert blocked_cell in payload["result"]["extraBlocked"]
    assert any("新封锁影响路径，释放锁定任务" in event["text"] for event in payload["result"]["eventLog"])

def test_duplicate_runtime_events_do_not_reappear_after_replan() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    first_block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 5},
    )
    duplicate_block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 5},
    )
    generated_response = _post_generated_task(client, session_id, 6)

    assert first_block_response.status_code == 200
    assert duplicate_block_response.status_code == 200
    block_payload = generated_response.json()
    assert block_payload["runtimeEventCount"] == 1
    assert sum(
        1
        for event in block_payload["result"]["eventLog"]
        if event["text"] == "T=5 手动封锁单元：(1, 1)"
    ) == 1

    first_fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 6},
    )
    duplicate_fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 6},
    )
    replan_response = _post_generated_task(client, session_id, 7)

    assert first_fail_response.status_code == 200
    assert duplicate_fail_response.status_code == 200
    replan_payload = replan_response.json()
    assert replan_payload["runtimeEventCount"] == 2
    assert sum(
        1
        for event in replan_payload["result"]["eventLog"]
        if event["text"] == "T=6 手动标记故障机器人：R1"
    ) == 1

def test_runtime_event_apis_reject_past_current_time() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 5},
    )
    assert tick_response.status_code == 200

    past_tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 3},
    )
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 3},
    )
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 3},
    )
    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [1, 1], "currentTime": 3},
    )
    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R1", "currentTime": 3},
    )

    assert past_tick_response.status_code == 422
    assert block_response.status_code == 422
    assert fail_response.status_code == 422
    assert unblock_response.status_code == 422
    assert restore_response.status_code == 422
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["currentTime"] == 5
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0


def test_idempotent_runtime_events_that_advance_time_update_session_metadata(monkeypatch) -> None:
    clock = {"now": 5000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
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

    clock["now"] = 5001.0
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 1},
    )
    assert block_response.status_code == 200
    assert block_response.json()["updatedAt"] == 5001.0

    clock["now"] = 5002.0
    duplicate_block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 3},
    )
    assert duplicate_block_response.status_code == 200
    duplicate_block_payload = duplicate_block_response.json()
    assert duplicate_block_payload["currentTime"] == 3
    assert duplicate_block_payload["updatedAt"] == 5002.0
    assert duplicate_block_payload["runtimeEventCount"] == 1

    clock["now"] = 5003.0
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 4},
    )
    assert fail_response.status_code == 200
    assert fail_response.json()["updatedAt"] == 5003.0

    clock["now"] = 5004.0
    duplicate_fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 5},
    )
    assert duplicate_fail_response.status_code == 200
    duplicate_fail_payload = duplicate_fail_response.json()
    assert duplicate_fail_payload["currentTime"] == 5
    assert duplicate_fail_payload["updatedAt"] == 5004.0
    assert duplicate_fail_payload["runtimeEventCount"] == 2

    clock["now"] = 5005.0
    remove_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [1, 1], "currentTime": 6},
    )
    assert remove_response.status_code == 200
    assert remove_response.json()["updatedAt"] == 5005.0

    clock["now"] = 5006.0
    duplicate_remove_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [1, 1], "currentTime": 7},
    )
    assert duplicate_remove_response.status_code == 200
    duplicate_remove_payload = duplicate_remove_response.json()
    assert duplicate_remove_payload["currentTime"] == 7
    assert duplicate_remove_payload["updatedAt"] == 5006.0
    assert duplicate_remove_payload["runtimeEventCount"] == 1

    clock["now"] = 5007.0
    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R2", "currentTime": 8},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["updatedAt"] == 5007.0

    clock["now"] = 5008.0
    duplicate_restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R2", "currentTime": 9},
    )
    assert duplicate_restore_response.status_code == 200
    duplicate_restore_payload = duplicate_restore_response.json()
    assert duplicate_restore_payload["currentTime"] == 9
    assert duplicate_restore_payload["updatedAt"] == 5008.0
    assert duplicate_restore_payload["runtimeEventCount"] == 0


def test_invalid_future_runtime_events_do_not_advance_session_time(monkeypatch) -> None:
    clock = {"now": 6000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    client = TestClient(app)
    invalid_requests = [
        ("blocked-cells", {"cell": [99, 99], "currentTime": 4}, 422),
        ("blocked-cells", {"cell": [2, 1], "currentTime": 4}, 409),
        ("blocked-cells/remove", {"cell": [99, 99], "currentTime": 4}, 422),
        ("failed-robots", {"robotId": "R404", "currentTime": 4}, 404),
        ("failed-robots/restore", {"robotId": "R404", "currentTime": 4}, 404),
    ]

    for index, (endpoint, body, expected_status) in enumerate(invalid_requests):
        create_time = 6000.0 + index * 10
        clock["now"] = create_time
        create_response = client.post(
            "/api/sessions",
            json={
                "scenario": scenario_payload(),
                "options": {"avoidConflicts": True, "includeDynamic": False},
            },
        )
        assert create_response.status_code == 200
        session_id = create_response.json()["sessionId"]

        clock["now"] = create_time + 1
        invalid_response = client.post(f"/api/sessions/{session_id}/{endpoint}", json=body)
        assert invalid_response.status_code == expected_status

        clock["now"] = create_time + 2
        payload = client.get(f"/api/sessions/{session_id}").json()
        assert payload["currentTime"] == 0
        assert payload["updatedAt"] == create_time
        assert payload["runtimeEventCount"] == 0
        assert payload["metricsHistory"][-1]["time"] == 0


def test_future_blocked_cell_rejected_for_robot_occupancy_does_not_advance_session_time(monkeypatch) -> None:
    clock = {"now": 7000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    occupied_time, occupied_cell = next(
        (time_index, cell)
        for path in create_payload["result"]["paths"].values()
        for time_index, cell in enumerate(path[1:], start=1)
        if cell != path[0]
    )

    clock["now"] = 7001.0
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": occupied_cell, "currentTime": occupied_time},
    )
    assert block_response.status_code == 409
    assert "封锁单元被机器人占用" in block_response.json()["detail"]

    clock["now"] = 7002.0
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["currentTime"] == 0
    assert payload["updatedAt"] == 7000.0
    assert payload["runtimeEventCount"] == 0
    assert payload["metricsHistory"][-1]["time"] == 0


def test_future_blocked_cell_occupancy_validation_does_not_write_plan_cache(monkeypatch) -> None:
    clock = {"now": 7100.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    occupied_time, occupied_cell = next(
        (time_index, cell)
        for path in create_payload["result"]["paths"].values()
        for time_index, cell in enumerate(path[1:], start=1)
        if cell != path[0]
    )
    session = sessions_module._sessions[session_id]
    session.last_result = None
    session.metrics_history.clear()
    session.preferred_task_robot_ids.clear()

    clock["now"] = 7101.0
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": occupied_cell, "currentTime": occupied_time},
    )

    assert block_response.status_code == 409
    assert session.current_time == 0
    assert session.updated_at == 7100.0
    assert session.last_result is None
    assert session.metrics_history == []
    assert session.preferred_task_robot_ids == {}


def test_session_time_limit_rejects_unbounded_tick_growth(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSION_CURRENT_TIME", 5)
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
        json={"currentTime": 6},
    )

    assert tick_response.status_code == 422
    assert tick_response.json()["detail"] == "currentTime must be <= max session time: 6 > 5"
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["currentTime"] == 0
    assert payload["metricsHistory"][-1]["time"] == 0


def test_runtime_event_apis_reject_unbounded_time_without_mutating_session(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSION_CURRENT_TIME", 5)
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
        json={"currentTime": 4},
    )
    assert tick_response.status_code == 200
    before_payload = tick_response.json()

    requests = [
        ("blocked-cells", {"cell": [1, 1], "currentTime": 6}),
        ("blocked-cells/remove", {"cell": [1, 1], "currentTime": 6}),
        ("failed-robots", {"robotId": "R1", "currentTime": 6}),
        ("failed-robots/restore", {"robotId": "R1", "currentTime": 6}),
    ]

    for endpoint, body in requests:
        response = client.post(f"/api/sessions/{session_id}/{endpoint}", json=body)
        assert response.status_code == 422
        assert response.json()["detail"] == "currentTime must be <= max session time: 6 > 5"

    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["currentTime"] == 4
    assert payload["updatedAt"] == before_payload["updatedAt"]
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert payload["metricsHistory"][-1]["time"] == 4
    assert payload["result"]["extraBlocked"] == []
    assert payload["result"]["unavailableRobotIds"] == []


def test_session_task_state_exposes_temporary_recovery_detail_after_runtime_block() -> None:
    client = TestClient(app)
    scenario = {
        "id": "runtime-recovery-detail",
        "name": "runtime-recovery-detail",
        "description": "runtime recovery detail regression",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "BLOCKED", "type": "inspection", "title": "BLOCKED", "priority": 1, "targets": [[2, 0]]},
        ],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 0], "currentTime": 0},
    )

    assert block_response.status_code == 200
    payload = block_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "BLOCKED")
    assert task_state["status"] == "unassigned"
    assert task_state["failureReason"] == payload["result"]["failureReasons"]["BLOCKED"]
    assert payload["result"]["failureDetails"]["BLOCKED"]["reason"] == task_state["failureReason"]
    assert task_state["failureCategory"] == "temporary"
    assert task_state["recoveryAction"] == "clearBlockedCells"
    assert payload["result"]["failureDetails"]["BLOCKED"]["category"] == "temporary"
    assert payload["result"]["failureDetails"]["BLOCKED"]["recoveryAction"] == "clearBlockedCells"
    assert payload["result"]["failureDetails"]["BLOCKED"]["blockingCells"] == [[1, 0]]
    assert payload["result"]["failureDetails"]["BLOCKED"]["blockingRobotIds"] == []

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [1, 0], "currentTime": 0},
    )

    assert unblock_response.status_code == 200
    recovered_payload = unblock_response.json()
    recovered_state = next(state for state in recovered_payload["taskStates"] if state["taskId"] == "BLOCKED")
    assert recovered_payload["runtimeEventCount"] == 0
    assert [1, 0] not in recovered_payload["result"]["extraBlocked"]
    assert "BLOCKED" not in recovered_payload["result"]["failureReasons"]
    assert "BLOCKED" not in recovered_payload["result"]["failureDetails"]
    assert recovered_state["status"] != "unassigned"
    assert recovered_state["failureReason"] is None
    assert recovered_state["failureCategory"] is None
    assert recovered_state["recoveryAction"] is None


def test_session_accepts_new_task_temporarily_blocked_by_runtime_condition() -> None:
    client = TestClient(app)
    scenario = {
        "id": "runtime-new-task-temporary-block",
        "name": "runtime-new-task-temporary-block",
        "description": "new task should enter queue when only runtime block prevents execution",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 0], "currentTime": 0},
    )
    assert block_response.status_code == 200

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "NEW-BLOCKED",
                "type": "inspection",
                "title": "NEW-BLOCKED",
                "priority": 2,
                "targets": [[2, 0]],
            }
        },
    )

    assert add_response.status_code == 200
    payload = add_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "NEW-BLOCKED")
    assert task_state["status"] == "unassigned"
    assert task_state["failureCategory"] == "temporary"
    assert task_state["recoveryAction"] == "clearBlockedCells"
    assert payload["result"]["failureDetails"]["NEW-BLOCKED"]["blockingCells"] == [[1, 0]]

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [1, 0], "currentTime": 0},
    )

    assert unblock_response.status_code == 200
    recovered_payload = unblock_response.json()
    recovered_state = next(state for state in recovered_payload["taskStates"] if state["taskId"] == "NEW-BLOCKED")
    assert "NEW-BLOCKED" not in recovered_payload["result"]["failureDetails"]
    assert recovered_state["status"] != "unassigned"
    assert recovered_state["failureReason"] is None
    assert recovered_state["failureCategory"] is None
    assert recovered_state["recoveryAction"] is None


def test_session_accepts_new_task_on_runtime_blocked_target() -> None:
    client = TestClient(app)
    scenario = {
        "id": "runtime-new-task-blocked-target",
        "name": "runtime-new-task-blocked-target",
        "description": "new task target may be temporarily blocked by runtime event",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [2, 0], "currentTime": 0},
    )
    assert block_response.status_code == 200

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "TARGET-BLOCKED",
                "type": "inspection",
                "title": "TARGET-BLOCKED",
                "priority": 2,
                "targets": [[2, 0]],
            }
        },
    )

    assert add_response.status_code == 200
    payload = add_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "TARGET-BLOCKED")
    assert task_state["status"] == "unassigned"
    assert task_state["failureCategory"] == "temporary"
    assert task_state["recoveryAction"] == "clearBlockedCells"
    assert payload["result"]["failureDetails"]["TARGET-BLOCKED"]["blockingCells"] == [[2, 0]]

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [2, 0], "currentTime": 0},
    )

    assert unblock_response.status_code == 200
    recovered_payload = unblock_response.json()
    recovered_state = next(state for state in recovered_payload["taskStates"] if state["taskId"] == "TARGET-BLOCKED")
    assert "TARGET-BLOCKED" not in recovered_payload["result"]["failureDetails"]
    assert recovered_state["status"] != "unassigned"
    assert recovered_state["failureReason"] is None
    assert recovered_state["failureCategory"] is None
    assert recovered_state["recoveryAction"] is None


def test_session_remove_blocked_cell_recovers_task_blocked_by_active_dynamic_cell() -> None:
    client = TestClient(app)
    scenario = {
        "id": "active-dynamic-block-recovery",
        "name": "active-dynamic-block-recovery",
        "description": "active dynamic blocked cell should be clearable by recovery API",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 1,
            "blockedCells": [[2, 0]],
            "failedRobots": [],
            "tasks": [],
        },
    }
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
        json={"currentTime": 1},
    )
    assert tick_response.status_code == 200
    assert [2, 0] in tick_response.json()["result"]["extraBlocked"]

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "DYNAMIC-BLOCKED",
                "type": "inspection",
                "title": "DYNAMIC-BLOCKED",
                "priority": 2,
                "releaseTime": 1,
                "targets": [[2, 0]],
            }
        },
    )
    assert add_response.status_code == 200
    payload = add_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "DYNAMIC-BLOCKED")
    assert task_state["status"] == "unassigned"
    assert task_state["failureCategory"] == "temporary"
    assert task_state["recoveryAction"] == "clearBlockedCells"
    assert payload["result"]["failureDetails"]["DYNAMIC-BLOCKED"]["blockingCells"] == [[2, 0]]

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [2, 0], "currentTime": 1},
    )

    assert unblock_response.status_code == 200
    recovered_payload = unblock_response.json()
    recovered_state = next(state for state in recovered_payload["taskStates"] if state["taskId"] == "DYNAMIC-BLOCKED")
    assert [2, 0] not in recovered_payload["result"]["extraBlocked"]
    assert any("新增 1 个封锁单元" in event["text"] for event in recovered_payload["result"]["eventLog"])
    assert "DYNAMIC-BLOCKED" not in recovered_payload["result"]["failureDetails"]
    assert recovered_state["status"] != "unassigned"
    assert recovered_state["failureReason"] is None
    assert recovered_state["failureCategory"] is None
    assert recovered_state["recoveryAction"] is None


def test_session_event_log_keeps_original_dynamic_block_count_after_partial_recovery() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dynamic-block-history-count",
        "name": "dynamic-block-history-count",
        "description": "dynamic block event history should keep original count",
        "width": 4,
        "height": 2,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[3, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 1,
            "blockedCells": [[2, 0], [3, 0]],
            "failedRobots": [],
            "tasks": [],
        },
    }
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
        json={"currentTime": 1},
    )
    assert tick_response.status_code == 200
    assert sorted(tick_response.json()["result"]["extraBlocked"]) == [[2, 0], [3, 0]]

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [3, 0], "currentTime": 1},
    )

    assert unblock_response.status_code == 200
    payload = unblock_response.json()
    dynamic_block_events = [
        event["text"]
        for event in payload["result"]["eventLog"]
        if event["text"].startswith("新增 ") and event["text"].endswith(" 个封锁单元")
    ]
    assert payload["result"]["extraBlocked"] == [[2, 0]]
    assert dynamic_block_events == ["新增 2 个封锁单元"]


def test_session_manual_block_existing_dynamic_blocked_cell_is_idempotent() -> None:
    client = TestClient(app)
    scenario = {
        "id": "active-dynamic-block-duplicate",
        "name": "active-dynamic-block-duplicate",
        "description": "manual block should not duplicate active dynamic block",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 1,
            "blockedCells": [[2, 0]],
            "failedRobots": [],
            "tasks": [],
        },
    }
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
        json={"currentTime": 1},
    )
    assert tick_response.status_code == 200
    assert tick_response.json()["runtimeEventCount"] == 0
    assert tick_response.json()["result"]["extraBlocked"] == [[2, 0]]

    duplicate_block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [2, 0], "currentTime": 1},
    )

    assert duplicate_block_response.status_code == 200
    payload = duplicate_block_response.json()
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["extraBlocked"] == [[2, 0]]
    assert sum(
        1
        for event in payload["result"]["eventLog"]
        if event["text"] == "T=1 手动封锁单元：(2, 0)"
    ) == 0


def test_session_manual_block_existing_dynamic_blocked_cell_skips_occupancy_rejection() -> None:
    client = TestClient(app)
    scenario = {
        "id": "active-dynamic-block-occupied-duplicate",
        "name": "active-dynamic-block-occupied-duplicate",
        "description": "manual block should not reject active dynamic block just because a robot occupies it",
        "width": 3,
        "height": 2,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[2, 0]]},
        ],
        "dynamic": {
            "triggerTime": 1,
            "blockedCells": [[1, 0]],
            "failedRobots": [],
            "tasks": [],
        },
    }
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
        json={"currentTime": 1},
    )
    assert tick_response.status_code == 200
    assert tick_response.json()["robotStates"][0]["position"] == [1, 0]
    assert tick_response.json()["result"]["extraBlocked"] == [[1, 0]]

    duplicate_block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 0], "currentTime": 1},
    )

    assert duplicate_block_response.status_code == 200
    payload = duplicate_block_response.json()
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["extraBlocked"] == [[1, 0]]
    assert sum(
        1
        for event in payload["result"]["eventLog"]
        if event["text"] == "T=1 手动封锁单元：(1, 0)"
    ) == 0


def test_session_event_log_preserves_dynamic_trigger_after_event_note_trim(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSION_EVENT_NOTES", 2)
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    trigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 6},
    )
    assert trigger_response.status_code == 200

    first_stream_response = _post_generated_task(client, session_id, 7)
    second_stream_response = _post_generated_task(client, session_id, 8)

    assert first_stream_response.status_code == 200
    assert second_stream_response.status_code == 200
    payload = second_stream_response.json()
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    assert "T=6 场景动态事件触发" in event_texts
    assert "新增 1 个封锁单元" in event_texts


def test_session_event_log_preserves_rolling_window_trigger_after_event_note_trim(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSION_EVENT_NOTES", 2)
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["dynamic"]["tasks"] = []
    scenario["tasks"].append(
        {
            "id": "FAR-TRIM",
            "type": "inspection",
            "title": "远期裁剪回归",
            "priority": 4,
            "releaseTime": sessions_module.ASSIGNMENT_REPLAN_WINDOW + 3,
            "targets": [[1, 4]],
        }
    )
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    trigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 3},
    )
    assert trigger_response.status_code == 200
    first_stream_response = _post_generated_task(client, session_id, 4)
    second_stream_response = _post_generated_task(client, session_id, 5)

    assert first_stream_response.status_code == 200
    assert second_stream_response.status_code == 200
    payload = second_stream_response.json()
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    assert "T=3 滚动窗口纳入远期任务" in event_texts
    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    assert any(task["id"] == "FAR-TRIM" for task in payload["result"]["tasks"])
    assert task_states["FAR-TRIM"]["status"] == "pending"
    assert task_states["FAR-TRIM"]["failureReason"] is None


def test_session_dynamic_far_future_task_waits_for_rolling_window_after_trigger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dynamic-far-future-window",
        "name": "dynamic-far-future-window",
        "description": "dynamic far-future task should stay deferred until rolling window",
        "width": 5,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[4, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 2,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [
                {
                    "id": "DYN-FAR",
                    "type": "inspection",
                    "title": "DYN-FAR",
                    "priority": 3,
                    "releaseTime": sessions_module.ASSIGNMENT_REPLAN_WINDOW + 5,
                    "targets": [[4, 0]],
                }
            ],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    trigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert trigger_response.status_code == 200
    trigger_payload = trigger_response.json()
    trigger_assignment_ids = [
        task["id"]
        for assignment in trigger_payload["result"]["assignments"]
        for task in assignment["tasks"]
    ]
    assert any(task["id"] == "DYN-FAR" for task in trigger_payload["result"]["tasks"])
    assert "DYN-FAR" not in trigger_assignment_ids
    assert any(event["text"] == "T=2 场景动态事件触发" for event in trigger_payload["result"]["eventLog"])

    window_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 5},
    )
    assert window_response.status_code == 200
    window_payload = window_response.json()
    window_assignment_ids = [
        task["id"]
        for assignment in window_payload["result"]["assignments"]
        for task in assignment["tasks"]
    ]
    event_texts = [event["text"] for event in window_payload["result"]["eventLog"]]
    assert "DYN-FAR" in window_assignment_ids
    assert "T=5 滚动窗口纳入远期任务" in event_texts


def test_session_uses_configured_assignment_replan_window_for_rolling_trigger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "session-configured-window",
        "name": "session-configured-window",
        "description": "session should trigger rolling window from DispatchOptions",
        "width": 8,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[1, 0], [7, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "NOW", "type": "inspection", "title": "NOW", "priority": 1, "releaseTime": 0, "targets": [[1, 0]]},
            {
                "id": "SOON",
                "type": "inspection",
                "title": "SOON",
                "priority": 5,
                "releaseTime": 5,
                "targets": [[7, 0]],
            },
        ],
        "dynamic": {
            "triggerTime": 0,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {
                "avoidConflicts": True,
                "includeDynamic": False,
                "assignmentReplanWindow": 4,
            },
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    initial_assignment_ids = [
        task["id"]
        for assignment in create_payload["result"]["assignments"]
        for task in assignment["tasks"]
    ]
    assert initial_assignment_ids == ["NOW"]

    window_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 1},
    )
    assert window_response.status_code == 200
    window_payload = window_response.json()
    window_assignment_ids = [
        task["id"]
        for assignment in window_payload["result"]["assignments"]
        for task in assignment["tasks"]
    ]
    event_texts = [event["text"] for event in window_payload["result"]["eventLog"]]
    assert "SOON" in window_assignment_ids
    assert "T=1 滚动窗口纳入远期任务" in event_texts


def test_session_reports_and_resets_adaptive_replan_window() -> None:
    client = TestClient(app)
    scenario = {
        "id": "session-adaptive-window",
        "name": "session-adaptive-window",
        "description": "adaptive window should expose the effective decision",
        "width": 8,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[7, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {
                "id": "FUTURE",
                "type": "inspection",
                "title": "FUTURE",
                "priority": 1,
                "releaseTime": 40,
                "targets": [[7, 0]],
            },
        ],
        "dynamic": {
            "triggerTime": 0,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    created = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {
                "avoidConflicts": True,
                "includeDynamic": False,
                "assignmentReplanWindow": 24,
                "adaptiveReplanWindow": True,
            },
        },
    )

    assert created.status_code == 200
    payload = created.json()
    assert payload["options"]["adaptiveReplanWindow"] is True
    assert payload["result"]["effectiveAssignmentReplanWindow"] == 48
    assert payload["result"]["replanWindowReason"] == "当前负载较低且存在远期任务，扩大窗口"

    reset = client.post(f"/api/sessions/{payload['sessionId']}/reset")
    assert reset.status_code == 200
    reset_payload = reset.json()
    assert reset_payload["options"]["adaptiveReplanWindow"] is True
    assert reset_payload["result"]["effectiveAssignmentReplanWindow"] == 48


def test_session_slow_replan_feedback_changes_window_trigger_time() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "session-adaptive-slow-window",
            "name": "session-adaptive-slow-window",
            "description": "slow feedback should contract the rolling trigger",
            "width": 8,
            "height": 1,
            "obstacles": [],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[7, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {
                    "id": "FUTURE",
                    "type": "inspection",
                    "title": "FUTURE",
                    "priority": 1,
                    "releaseTime": 40,
                    "targets": [[7, 0]],
                },
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    session = sessions_module.DispatchSession(
        session_id="adaptive-slow",
        scenario=scenario,
        options=DispatchOptions(
            avoidConflicts=True,
            includeDynamic=False,
            assignmentReplanWindow=24,
            adaptiveReplanWindow=True,
        ),
        last_replan_time_ms=50,
    )
    task = scenario.tasks[0]

    decision = sessions_module._session_replan_window_decision(session)

    assert decision.window == 12
    assert sessions_module._rolling_window_trigger_time(session, task) == 28


def test_session_task_state_recovers_after_runtime_robot_restore() -> None:
    client = TestClient(app)
    scenario = {
        "id": "runtime-robot-restore",
        "name": "runtime-robot-restore",
        "description": "runtime robot restore regression",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[2, 0]]},
        ],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 0},
    )

    assert fail_response.status_code == 200
    failed_payload = fail_response.json()
    failed_state = next(state for state in failed_payload["taskStates"] if state["taskId"] == "T1")
    assert failed_payload["runtimeEventCount"] == 1
    assert "R1" in failed_payload["result"]["unavailableRobotIds"]
    assert failed_state["status"] == "unassigned"
    assert failed_state["failureCategory"] == "temporary"
    assert failed_state["recoveryAction"] == "restoreRobot"
    assert failed_payload["result"]["failureDetails"]["T1"]["blockingCells"] == []
    assert failed_payload["result"]["failureDetails"]["T1"]["blockingRobotIds"] == ["R1"]

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R1", "currentTime": 0},
    )

    assert restore_response.status_code == 200
    restored_payload = restore_response.json()
    restored_state = next(state for state in restored_payload["taskStates"] if state["taskId"] == "T1")
    assert restored_payload["runtimeEventCount"] == 0
    assert "R1" not in restored_payload["result"]["unavailableRobotIds"]
    assert "T1" not in restored_payload["result"]["failureReasons"]
    assert "T1" not in restored_payload["result"]["failureDetails"]
    assert restored_state["status"] != "unassigned"
    assert restored_state["failureReason"] is None
    assert restored_state["failureCategory"] is None
    assert restored_state["recoveryAction"] is None


def test_session_restore_robot_recovers_task_blocked_by_active_dynamic_failure() -> None:
    client = TestClient(app)
    scenario = {
        "id": "active-dynamic-robot-restore",
        "name": "active-dynamic-robot-restore",
        "description": "active dynamic failed robot should be restorable by recovery API",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 1,
            "blockedCells": [],
            "failedRobots": ["R1"],
            "tasks": [],
        },
    }
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
        json={"currentTime": 1},
    )
    assert tick_response.status_code == 200
    assert tick_response.json()["result"]["unavailableRobotIds"] == ["R1"]

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "DYNAMIC-ROBOT",
                "type": "inspection",
                "title": "DYNAMIC-ROBOT",
                "priority": 2,
                "releaseTime": 1,
                "targets": [[2, 0]],
            }
        },
    )
    assert add_response.status_code == 200
    payload = add_response.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "DYNAMIC-ROBOT")
    assert task_state["status"] == "unassigned"
    assert task_state["failureCategory"] == "temporary"
    assert task_state["recoveryAction"] == "restoreRobot"
    assert payload["result"]["failureDetails"]["DYNAMIC-ROBOT"]["blockingRobotIds"] == ["R1"]

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R1", "currentTime": 1},
    )

    assert restore_response.status_code == 200
    restored_payload = restore_response.json()
    restored_state = next(state for state in restored_payload["taskStates"] if state["taskId"] == "DYNAMIC-ROBOT")
    assert restored_payload["result"]["unavailableRobotIds"] == []
    assert "DYNAMIC-ROBOT" not in restored_payload["result"]["failureDetails"]
    assert restored_state["status"] != "unassigned"
    assert restored_state["failureReason"] is None
    assert restored_state["failureCategory"] is None
    assert restored_state["recoveryAction"] is None


def test_session_manual_fail_existing_dynamic_failed_robot_is_idempotent() -> None:
    client = TestClient(app)
    scenario = {
        "id": "active-dynamic-robot-duplicate-fail",
        "name": "active-dynamic-robot-duplicate-fail",
        "description": "manual failure should not duplicate active dynamic failure",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 1,
            "blockedCells": [],
            "failedRobots": ["R1"],
            "tasks": [],
        },
    }
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
        json={"currentTime": 1},
    )
    assert tick_response.status_code == 200
    assert tick_response.json()["runtimeEventCount"] == 0
    assert tick_response.json()["result"]["unavailableRobotIds"] == ["R1"]

    duplicate_fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 1},
    )

    assert duplicate_fail_response.status_code == 200
    payload = duplicate_fail_response.json()
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["unavailableRobotIds"] == ["R1"]
    assert sum(
        1
        for event in payload["result"]["eventLog"]
        if event["text"] == "T=1 手动标记故障机器人：R1"
    ) == 0


def test_session_task_state_narrows_joint_recovery_after_partial_runtime_fix() -> None:
    client = TestClient(app)
    scenario = {
        "id": "runtime-joint-recovery",
        "name": "runtime-joint-recovery",
        "description": "runtime joint recovery regression",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [],
            "delivery": [[2, 0]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [0, 0], "battery": 90, "load": 2},
        ],
        "tasks": [
            {
                "id": "JOINT",
                "type": "delivery",
                "title": "JOINT",
                "priority": 2,
                "pickup": [0, 0],
                "dropoff": [2, 0],
                "demand": 2,
            },
        ],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 0], "currentTime": 0},
    )
    assert block_response.status_code == 200
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 0},
    )

    assert fail_response.status_code == 200
    failed_payload = fail_response.json()
    failed_state = next(state for state in failed_payload["taskStates"] if state["taskId"] == "JOINT")
    assert failed_payload["runtimeEventCount"] == 2
    assert failed_state["status"] == "unassigned"
    assert failed_state["failureCategory"] == "temporary"
    assert failed_state["recoveryAction"] == "clearBlockedCellsAndRestoreRobot"
    assert failed_payload["result"]["failureDetails"]["JOINT"]["blockingCells"] == [[1, 0]]
    assert failed_payload["result"]["failureDetails"]["JOINT"]["blockingRobotIds"] == ["R2"]

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [1, 0], "currentTime": 0},
    )
    assert unblock_response.status_code == 200
    partially_recovered_payload = unblock_response.json()
    partially_recovered_state = next(
        state for state in partially_recovered_payload["taskStates"] if state["taskId"] == "JOINT"
    )
    assert partially_recovered_payload["runtimeEventCount"] == 1
    assert partially_recovered_state["status"] == "unassigned"
    assert partially_recovered_state["failureCategory"] == "temporary"
    assert partially_recovered_state["recoveryAction"] == "restoreRobot"
    assert partially_recovered_payload["result"]["failureDetails"]["JOINT"]["blockingCells"] == []
    assert partially_recovered_payload["result"]["failureDetails"]["JOINT"]["blockingRobotIds"] == ["R2"]

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R2", "currentTime": 0},
    )
    assert restore_response.status_code == 200
    restored_payload = restore_response.json()
    restored_state = next(state for state in restored_payload["taskStates"] if state["taskId"] == "JOINT")
    assert restored_payload["runtimeEventCount"] == 0
    assert "JOINT" not in restored_payload["result"]["failureReasons"]
    assert "JOINT" not in restored_payload["result"]["failureDetails"]
    assert restored_state["status"] != "unassigned"
    assert restored_state["failureReason"] is None
    assert restored_state["failureCategory"] is None
    assert restored_state["recoveryAction"] is None


def test_session_task_state_ignores_unrelated_blocks_for_load_recovery() -> None:
    client = TestClient(app)
    scenario = {
        "id": "runtime-load-robot-recovery",
        "name": "runtime-load-robot-recovery",
        "description": "runtime load recovery should not blame unrelated blocked cells",
        "width": 3,
        "height": 2,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [],
            "delivery": [[2, 0]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [0, 0], "battery": 90, "load": 2},
        ],
        "tasks": [
            {
                "id": "LOAD-ROBOT",
                "type": "delivery",
                "title": "LOAD-ROBOT",
                "priority": 2,
                "pickup": [0, 0],
                "dropoff": [2, 0],
                "demand": 2,
            },
        ],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [0, 1], "currentTime": 0},
    )
    assert block_response.status_code == 200
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 0},
    )

    assert fail_response.status_code == 200
    failed_payload = fail_response.json()
    failed_state = next(state for state in failed_payload["taskStates"] if state["taskId"] == "LOAD-ROBOT")
    assert failed_payload["runtimeEventCount"] == 2
    assert failed_payload["result"]["extraBlocked"] == [[0, 1]]
    assert failed_state["status"] == "unassigned"
    assert failed_state["failureCategory"] == "temporary"
    assert failed_state["recoveryAction"] == "restoreRobot"
    assert failed_payload["result"]["failureDetails"]["LOAD-ROBOT"]["blockingCells"] == []
    assert failed_payload["result"]["failureDetails"]["LOAD-ROBOT"]["blockingRobotIds"] == ["R2"]

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R2", "currentTime": 0},
    )
    assert restore_response.status_code == 200
    restored_payload = restore_response.json()
    restored_state = next(state for state in restored_payload["taskStates"] if state["taskId"] == "LOAD-ROBOT")
    assert restored_payload["runtimeEventCount"] == 1
    assert restored_payload["result"]["extraBlocked"] == [[0, 1]]
    assert "LOAD-ROBOT" not in restored_payload["result"]["failureReasons"]
    assert "LOAD-ROBOT" not in restored_payload["result"]["failureDetails"]
    assert restored_state["status"] != "unassigned"
    assert restored_state["failureReason"] is None
    assert restored_state["failureCategory"] is None
    assert restored_state["recoveryAction"] is None


def test_session_task_state_reports_either_block_clear_or_robot_restore_recovery() -> None:
    client = TestClient(app)
    scenario = {
        "id": "runtime-either-recovery",
        "name": "runtime-either-recovery",
        "description": "runtime either recovery regression",
        "width": 3,
        "height": 2,
        "obstacles": [[1, 1]],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [2, 1], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "EITHER", "type": "inspection", "title": "EITHER", "priority": 1, "targets": [[2, 0]]},
        ],
        "dynamic": {
            "triggerTime": 99,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 0], "currentTime": 0},
    )
    assert block_response.status_code == 200
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 0},
    )

    assert fail_response.status_code == 200
    failed_payload = fail_response.json()
    failed_state = next(state for state in failed_payload["taskStates"] if state["taskId"] == "EITHER")
    assert failed_payload["runtimeEventCount"] == 2
    assert failed_state["status"] == "unassigned"
    assert failed_state["failureCategory"] == "temporary"
    assert failed_state["recoveryAction"] == "clearBlockedCellsOrRestoreRobot"
    assert failed_payload["result"]["failureDetails"]["EITHER"]["blockingCells"] == [[1, 0]]
    assert failed_payload["result"]["failureDetails"]["EITHER"]["blockingRobotIds"] == ["R2"]

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": [1, 0], "currentTime": 0},
    )
    assert unblock_response.status_code == 200
    unblocked_payload = unblock_response.json()
    unblocked_state = next(state for state in unblocked_payload["taskStates"] if state["taskId"] == "EITHER")
    assert unblocked_payload["runtimeEventCount"] == 1
    assert "R2" in unblocked_payload["result"]["unavailableRobotIds"]
    assert "EITHER" not in unblocked_payload["result"]["failureReasons"]
    assert "EITHER" not in unblocked_payload["result"]["failureDetails"]
    assert unblocked_state["status"] != "unassigned"
    assert unblocked_state["failureReason"] is None
    assert unblocked_state["failureCategory"] is None
    assert unblocked_state["recoveryAction"] is None


def test_session_long_online_pressure_sequence_stays_consistent() -> None:
    client = TestClient(app)
    scenario = {
        "id": "online-pressure",
        "name": "online-pressure",
        "description": "online pressure regression",
        "width": 8,
        "height": 6,
        "obstacles": [[3, 1], [3, 2], [3, 4]],
        "zones": {
            "warehouse": [[0, 0], [0, 5], [7, 0]],
            "inspection": [[7, 5], [6, 1], [5, 4], [1, 3], [7, 2]],
            "delivery": [[6, 5], [7, 5]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 2},
            {"id": "R2", "name": "R2", "start": [0, 5], "battery": 90, "load": 2},
            {"id": "R3", "name": "R3", "start": [7, 0], "battery": 90, "load": 2},
        ],
        "tasks": [
            {
                "id": "T1",
                "type": "inspection",
                "title": "T1",
                "priority": 2,
                "releaseTime": 0,
                "deadline": 35,
                "targets": [[7, 5]],
            },
            {
                "id": "T2",
                "type": "delivery",
                "title": "T2",
                "priority": 3,
                "releaseTime": 0,
                "deadline": 35,
                "pickup": [0, 0],
                "dropoff": [6, 5],
                "demand": 1,
            },
            {
                "id": "T3",
                "type": "inspection",
                "title": "T3",
                "priority": 2,
                "releaseTime": 2,
                "deadline": 30,
                "targets": [[6, 1]],
            },
            {
                "id": "T4",
                "type": "inspection",
                "title": "T4",
                "priority": 1,
                "releaseTime": 8,
                "deadline": 42,
                "targets": [[1, 3]],
            },
        ],
        "dynamic": {
            "triggerTime": 5,
            "blockedCells": [[4, 3]],
            "failedRobots": [],
            "tasks": [
                {"id": "E1", "type": "emergency", "title": "E1", "priority": 5, "target": [7, 2]}
            ],
        },
    }

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    payload = create_response.json()
    _assert_online_payload_consistent(payload)

    for current_time in (1, 2):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    urgent_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "URGENT-1",
                "type": "emergency",
                "title": "URGENT-1",
                "priority": 5,
                "releaseTime": 2,
                "deadline": 16,
                "target": [2, 5],
            }
        },
    )
    assert urgent_response.status_code == 200
    payload = urgent_response.json()
    _assert_online_payload_consistent(payload)

    generated_response = _post_generated_task(client, session_id, 3, target=scenario["zones"]["inspection"][2])
    assert generated_response.status_code == 200
    payload = generated_response.json()
    _assert_online_payload_consistent(payload)

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 4},
    )
    assert tick_response.status_code == 200
    payload = tick_response.json()
    _assert_online_payload_consistent(payload)

    occupied_cells = {tuple(state["position"]) for state in payload["robotStates"]}
    blocked_cells = {tuple(cell) for cell in [*scenario["obstacles"], *scenario["dynamic"]["blockedCells"]]}
    runtime_block_cell = next(
        cell
        for cell in ([2, 4], [2, 0], [4, 2], [5, 3], [1, 1])
        if tuple(cell) not in occupied_cells and tuple(cell) not in blocked_cells
    )
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": runtime_block_cell, "currentTime": 4},
    )
    assert block_response.status_code == 200
    payload = block_response.json()
    _assert_online_payload_consistent(payload)

    for current_time in (5, 6):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 6},
    )
    assert fail_response.status_code == 200
    payload = fail_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 2
    assert runtime_block_cell in payload["result"]["extraBlocked"]
    assert "R2" in payload["result"]["unavailableRobotIds"]
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R2")["status"] == "failed"

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": runtime_block_cell, "currentTime": 6},
    )
    assert unblock_response.status_code == 200
    payload = unblock_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 1
    assert runtime_block_cell not in payload["result"]["extraBlocked"]
    assert "R2" in payload["result"]["unavailableRobotIds"]

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R2", "currentTime": 6},
    )
    assert restore_response.status_code == 200
    payload = restore_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 0
    assert "R2" not in payload["result"]["unavailableRobotIds"]
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R2")["status"] != "failed"

    for index, current_time in enumerate((7, 9), start=3):
        generated_response = _post_generated_task(client, session_id, current_time, target=scenario["zones"]["inspection"][index])
        assert generated_response.status_code == 200
        payload = generated_response.json()
        _assert_online_payload_consistent(payload)

    for current_time in (10, 12, 14):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    assert payload["currentTime"] == 14
    assert payload["runtimeTaskCount"] == 4
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["dynamicTriggerTime"] == 5
    assert runtime_block_cell not in payload["result"]["extraBlocked"]
    assert "R2" not in payload["result"]["unavailableRobotIds"]
    assert payload["result"]["metrics"]["replanTimeMs"] >= 0
    assert payload["metricsHistory"][-1]["completedTaskCount"] == payload["completedTaskCount"]
    generated_task_ids = {task_id for task_id in task_states if task_id.startswith("G")}
    assert {"URGENT-1", "E1"}.issubset(task_states)
    assert len(generated_task_ids) >= 3
    assert task_states["URGENT-1"]["status"] != "unassigned"
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R2")["status"] != "failed"


def test_session_larger_online_pressure_keeps_recovery_and_deferred_tasks_consistent() -> None:
    client = TestClient(app)
    scenario = {
        "id": "online-pressure-large",
        "name": "online-pressure-large",
        "description": "larger online pressure regression",
        "width": 12,
        "height": 8,
        "obstacles": [[5, 1], [5, 2], [5, 4], [5, 5], [6, 2], [6, 5]],
        "zones": {
            "warehouse": [[0, 0], [11, 0], [0, 7], [11, 7]],
            "inspection": [[2, 2], [3, 5], [8, 1], [9, 6], [6, 3], [1, 4], [10, 3], [4, 6]],
            "delivery": [[10, 6], [11, 4]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 95, "load": 2},
            {"id": "R2", "name": "R2", "start": [11, 0], "battery": 95, "load": 2},
            {"id": "R3", "name": "R3", "start": [0, 7], "battery": 95, "load": 2},
            {"id": "R4", "name": "R4", "start": [11, 7], "battery": 95, "load": 2},
        ],
        "tasks": [
            {
                "id": "T1",
                "type": "inspection",
                "title": "T1",
                "priority": 2,
                "releaseTime": 0,
                "deadline": 55,
                "targets": [[2, 2], [3, 5]],
            },
            {
                "id": "T2",
                "type": "delivery",
                "title": "T2",
                "priority": 3,
                "releaseTime": 0,
                "deadline": 60,
                "pickup": [0, 0],
                "dropoff": [10, 6],
                "demand": 1,
            },
            {
                "id": "T3",
                "type": "inspection",
                "title": "T3",
                "priority": 2,
                "releaseTime": 2,
                "deadline": 55,
                "targets": [[8, 1]],
            },
            {
                "id": "T4",
                "type": "inspection",
                "title": "T4",
                "priority": 2,
                "releaseTime": 4,
                "deadline": 60,
                "targets": [[9, 6]],
            },
            {
                "id": "T5",
                "type": "delivery",
                "title": "T5",
                "priority": 2,
                "releaseTime": 8,
                "deadline": 70,
                "pickup": [11, 0],
                "dropoff": [11, 4],
                "demand": 1,
            },
            {
                "id": "T6",
                "type": "inspection",
                "title": "T6",
                "priority": 1,
                "releaseTime": 16,
                "deadline": 85,
                "targets": [[1, 4], [4, 6]],
            },
            {
                "id": "WINDOW-1",
                "type": "inspection",
                "title": "WINDOW-1",
                "priority": 1,
                "releaseTime": 50,
                "deadline": 95,
                "targets": [[10, 3]],
            },
            {
                "id": "FAR-1",
                "type": "inspection",
                "title": "FAR-1",
                "priority": 1,
                "releaseTime": 80,
                "deadline": 120,
                "targets": [[6, 3]],
            },
        ],
        "dynamic": {
            "triggerTime": 6,
            "blockedCells": [[5, 3]],
            "failedRobots": [],
            "tasks": [
                {"id": "E-LARGE", "type": "emergency", "title": "E-LARGE", "priority": 5, "target": [10, 3]}
            ],
        },
    }

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    payload = create_response.json()
    _assert_online_payload_consistent(payload)

    for current_time in (1, 2, 3):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    urgent_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "URGENT-LARGE",
                "type": "emergency",
                "title": "URGENT-LARGE",
                "priority": 5,
                "releaseTime": 3,
                "deadline": 24,
                "target": [1, 6],
            }
        },
    )
    assert urgent_response.status_code == 200
    payload = urgent_response.json()
    _assert_online_payload_consistent(payload)

    for index, current_time in enumerate((4, 5)):
        generated_response = _post_generated_task(client, session_id, current_time, target=scenario["zones"]["inspection"][index + 4])
        assert generated_response.status_code == 200
        payload = generated_response.json()
        _assert_online_payload_consistent(payload)

    occupied_cells = {tuple(state["position"]) for state in payload["robotStates"]}
    blocked_cells = {tuple(cell) for cell in [*scenario["obstacles"], *scenario["dynamic"]["blockedCells"]]}
    runtime_block_cell = next(
        cell
        for cell in ([4, 4], [7, 4], [3, 3], [8, 4], [2, 6])
        if tuple(cell) not in occupied_cells and tuple(cell) not in blocked_cells
    )
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": runtime_block_cell, "currentTime": 5},
    )
    assert block_response.status_code == 200
    payload = block_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 1
    assert runtime_block_cell in payload["result"]["extraBlocked"]

    for current_time in (6, 7):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R4", "currentTime": 7},
    )
    assert fail_response.status_code == 200
    payload = fail_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 2
    assert "R4" in payload["result"]["unavailableRobotIds"]
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R4")["status"] == "failed"

    for index, current_time in enumerate((8, 9, 11)):
        generated_response = _post_generated_task(client, session_id, current_time, target=scenario["zones"]["inspection"][index + 5])
        assert generated_response.status_code == 200
        payload = generated_response.json()
        _assert_online_payload_consistent(payload)

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": runtime_block_cell, "currentTime": 11},
    )
    assert unblock_response.status_code == 200
    payload = unblock_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 1
    assert runtime_block_cell not in payload["result"]["extraBlocked"]

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R4", "currentTime": 11},
    )
    assert restore_response.status_code == 200
    payload = restore_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 0
    assert "R4" not in payload["result"]["unavailableRobotIds"]
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R4")["status"] != "failed"

    for current_time in (12, 16, 20, 24, 26, 28):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    assert payload["currentTime"] == 28
    assert payload["runtimeTaskCount"] == 6
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["dynamicTriggerTime"] == 6
    assert runtime_block_cell not in payload["result"]["extraBlocked"]
    assert payload["result"]["unavailableRobotIds"] == []
    assert payload["result"]["metrics"]["replanTimeMs"] >= 0
    assert payload["metricsHistory"][-1]["completedTaskCount"] == payload["completedTaskCount"]
    generated_task_ids = {task_id for task_id in task_states if task_id.startswith("G")}
    assert {"URGENT-LARGE", "E-LARGE", "WINDOW-1", "FAR-1"}.issubset(
        task_states
    )
    assert len(generated_task_ids) >= 5
    assert task_states["URGENT-LARGE"]["status"] != "unassigned"
    assert task_states["WINDOW-1"]["status"] == "pending"
    assert task_states["WINDOW-1"]["releaseTime"] == 50
    assert task_states["FAR-1"]["status"] == "pending"
    assert task_states["FAR-1"]["assignedRobotId"] is None
    assert task_states["FAR-1"]["releaseTime"] == 80


def test_session_eight_robot_online_pressure_handles_streams_and_recovery() -> None:
    client = TestClient(app)
    inspection_cells = [
        [2, 2],
        [3, 8],
        [6, 2],
        [6, 8],
        [9, 2],
        [9, 8],
        [14, 2],
        [15, 8],
        [2, 5],
        [15, 5],
        [8, 5],
        [10, 5],
    ]
    scenario = {
        "id": "online-eight-robot-pressure",
        "name": "online-eight-robot-pressure",
        "description": "8 robot online pressure regression",
        "width": 18,
        "height": 12,
        "obstacles": [
            [5, 0],
            [5, 1],
            [5, 3],
            [5, 4],
            [5, 6],
            [5, 7],
            [5, 9],
            [5, 10],
            [5, 11],
            [12, 0],
            [12, 1],
            [12, 2],
            [12, 4],
            [12, 5],
            [12, 7],
            [12, 8],
            [12, 10],
            [12, 11],
        ],
        "zones": {
            "warehouse": [[1, 1], [16, 1], [1, 10], [16, 10], [1, 5], [16, 5], [8, 1], [9, 10]],
            "inspection": inspection_cells,
            "delivery": [[16, 10], [8, 10], [16, 1], [1, 1], [16, 5], [1, 5], [8, 10], [9, 1]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 95, "load": 3},
            {"id": "R2", "name": "R2", "start": [17, 0], "battery": 95, "load": 3},
            {"id": "R3", "name": "R3", "start": [0, 11], "battery": 95, "load": 3},
            {"id": "R4", "name": "R4", "start": [17, 11], "battery": 95, "load": 3},
            {"id": "R5", "name": "R5", "start": [0, 5], "battery": 90, "load": 2},
            {"id": "R6", "name": "R6", "start": [17, 5], "battery": 90, "load": 2},
            {"id": "R7", "name": "R7", "start": [8, 0], "battery": 88, "load": 2},
            {"id": "R8", "name": "R8", "start": [8, 11], "battery": 88, "load": 2},
        ],
        "tasks": [
            {
                "id": "I1",
                "type": "inspection",
                "title": "I1",
                "priority": 2,
                "releaseTime": 0,
                "deadline": 70,
                "targets": [[2, 2], [3, 8]],
            },
            {
                "id": "D1",
                "type": "delivery",
                "title": "D1",
                "priority": 3,
                "releaseTime": 0,
                "deadline": 80,
                "pickup": [1, 1],
                "dropoff": [16, 10],
                "demand": 2,
            },
            {
                "id": "I2",
                "type": "inspection",
                "title": "I2",
                "priority": 2,
                "releaseTime": 3,
                "deadline": 75,
                "targets": [[6, 2], [7, 8]],
            },
            {
                "id": "D2",
                "type": "delivery",
                "title": "D2",
                "priority": 2,
                "releaseTime": 4,
                "deadline": 85,
                "pickup": [16, 1],
                "dropoff": [1, 10],
                "demand": 1,
            },
            {
                "id": "I3",
                "type": "inspection",
                "title": "I3",
                "priority": 1,
                "releaseTime": 10,
                "deadline": 90,
                "targets": [[9, 2], [9, 8]],
            },
            {
                "id": "WINDOW-8",
                "type": "inspection",
                "title": "WINDOW-8",
                "priority": 1,
                "releaseTime": 46,
                "deadline": 105,
                "targets": [[14, 2]],
            },
            {
                "id": "FAR-8",
                "type": "inspection",
                "title": "FAR-8",
                "priority": 1,
                "releaseTime": 90,
                "deadline": 140,
                "targets": [[15, 8]],
            },
        ],
        "dynamic": {
            "triggerTime": 6,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [
                {"id": "E8", "type": "emergency", "title": "E8", "priority": 5, "target": [10, 5]}
            ],
        },
    }

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    payload = create_response.json()
    _assert_online_payload_consistent(payload)

    for current_time in (1, 2, 3):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    urgent_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "URGENT-8",
                "type": "emergency",
                "title": "URGENT-8",
                "priority": 5,
                "releaseTime": 3,
                "deadline": 28,
                "target": [2, 5],
            }
        },
    )
    assert urgent_response.status_code == 200
    payload = urgent_response.json()
    _assert_online_payload_consistent(payload)

    for index, current_time in enumerate((4, 5)):
        generated_response = _post_generated_task(client, session_id, current_time, target=inspection_cells[index + 4])
        assert generated_response.status_code == 200
        payload = generated_response.json()
        _assert_online_payload_consistent(payload)

    occupied_cells = {tuple(state["position"]) for state in payload["robotStates"]}
    blocked_cells = {tuple(cell) for cell in scenario["obstacles"]}
    runtime_block_cell = next(
        cell
        for cell in ([4, 4], [7, 4], [10, 4], [13, 4], [2, 7])
        if tuple(cell) not in occupied_cells and tuple(cell) not in blocked_cells
    )
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": runtime_block_cell, "currentTime": 5},
    )
    assert block_response.status_code == 200
    payload = block_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 1
    assert runtime_block_cell in payload["result"]["extraBlocked"]

    for current_time in (6, 7):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R6", "currentTime": 7},
    )
    assert fail_response.status_code == 200
    payload = fail_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 2
    assert "R6" in payload["result"]["unavailableRobotIds"]
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R6")["status"] == "failed"

    for target, current_time in zip((inspection_cells[6], inspection_cells[7], inspection_cells[10]), (8, 10, 12)):
        generated_response = _post_generated_task(client, session_id, current_time, target=target)
        assert generated_response.status_code == 200
        payload = generated_response.json()
        _assert_online_payload_consistent(payload)

    unblock_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells/remove",
        json={"cell": runtime_block_cell, "currentTime": 12},
    )
    assert unblock_response.status_code == 200
    payload = unblock_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 1
    assert runtime_block_cell not in payload["result"]["extraBlocked"]

    restore_response = client.post(
        f"/api/sessions/{session_id}/failed-robots/restore",
        json={"robotId": "R6", "currentTime": 12},
    )
    assert restore_response.status_code == 200
    payload = restore_response.json()
    _assert_online_payload_consistent(payload)
    assert payload["runtimeEventCount"] == 0
    assert "R6" not in payload["result"]["unavailableRobotIds"]
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R6")["status"] != "failed"

    for current_time in (14, 18, 22, 24):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    assert payload["currentTime"] == 24
    assert payload["runtimeTaskCount"] == 6
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["dynamicTriggerTime"] == 6
    assert all(conflict["time"] > payload["currentTime"] for conflict in payload["result"]["conflicts"])
    assert payload["result"]["unavailableRobotIds"] == []
    assert runtime_block_cell not in payload["result"]["extraBlocked"]
    assert payload["result"]["metrics"]["replanTimeMs"] >= 0
    assert payload["metricsHistory"][-1]["completedTaskCount"] == payload["completedTaskCount"]
    generated_task_ids = {task_id for task_id in task_states if task_id.startswith("G")}
    assert {"URGENT-8", "E8", "WINDOW-8", "FAR-8"}.issubset(task_states)
    assert len(generated_task_ids) >= 5
    assert task_states["URGENT-8"]["status"] != "unassigned"
    assert task_states["E8"]["status"] != "unassigned"
    assert task_states["WINDOW-8"]["status"] == "pending"
    assert task_states["WINDOW-8"]["releaseTime"] == 46
    assert task_states["FAR-8"]["status"] == "pending"
    assert task_states["FAR-8"]["assignedRobotId"] is None
    assert task_states["FAR-8"]["releaseTime"] == 90


def test_session_eight_robot_long_horizon_runtime_stress_stays_consistent() -> None:
    client = TestClient(app)
    scenario = {
        "id": "online-eight-robot-long-horizon",
        "name": "online-eight-robot-long-horizon",
        "description": "8 robot long horizon runtime stress regression",
        "width": 14,
        "height": 8,
        "obstacles": [[6, 1], [6, 6]],
        "zones": {
            "warehouse": [[1, 1], [12, 1], [1, 6], [12, 6]],
            "inspection": [[3, 2], [4, 5], [9, 2], [10, 5], [7, 3], [7, 4]],
            "delivery": [[12, 6], [1, 6], [12, 1], [1, 1]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 96, "load": 3},
            {"id": "R2", "name": "R2", "start": [13, 0], "battery": 96, "load": 3},
            {"id": "R3", "name": "R3", "start": [0, 7], "battery": 96, "load": 3},
            {"id": "R4", "name": "R4", "start": [13, 7], "battery": 96, "load": 3},
            {"id": "R5", "name": "R5", "start": [0, 3], "battery": 92, "load": 2},
            {"id": "R6", "name": "R6", "start": [13, 3], "battery": 92, "load": 2},
            {"id": "R7", "name": "R7", "start": [7, 0], "battery": 90, "load": 2},
            {"id": "R8", "name": "R8", "start": [7, 7], "battery": 90, "load": 2},
        ],
        "tasks": [
            {
                "id": "I-LONG-1",
                "type": "inspection",
                "title": "I-LONG-1",
                "priority": 2,
                "releaseTime": 0,
                "deadline": 50,
                "targets": [[3, 2], [4, 5]],
            },
            {
                "id": "D-LONG-1",
                "type": "delivery",
                "title": "D-LONG-1",
                "priority": 3,
                "releaseTime": 0,
                "deadline": 55,
                "pickup": [1, 1],
                "dropoff": [12, 6],
                "demand": 2,
            },
            {
                "id": "I-LONG-2",
                "type": "inspection",
                "title": "I-LONG-2",
                "priority": 2,
                "releaseTime": 4,
                "deadline": 60,
                "targets": [[9, 2]],
            },
            {
                "id": "D-LONG-2",
                "type": "delivery",
                "title": "D-LONG-2",
                "priority": 2,
                "releaseTime": 8,
                "deadline": 65,
                "pickup": [12, 1],
                "dropoff": [1, 6],
                "demand": 1,
            },
            {
                "id": "I-LONG-3",
                "type": "inspection",
                "title": "I-LONG-3",
                "priority": 1,
                "releaseTime": 18,
                "deadline": 72,
                "targets": [[10, 5]],
            },
            {
                "id": "WINDOW-LONG",
                "type": "inspection",
                "title": "WINDOW-LONG",
                "priority": 1,
                "releaseTime": 38,
                "deadline": 90,
                "targets": [[7, 3]],
            },
            {
                "id": "FAR-LONG",
                "type": "inspection",
                "title": "FAR-LONG",
                "priority": 1,
                "releaseTime": 70,
                "deadline": 120,
                "targets": [[7, 4]],
            },
        ],
        "dynamic": {
            "triggerTime": 6,
            "blockedCells": [[7, 3]],
            "failedRobots": ["R8"],
            "tasks": [
                {"id": "E-LONG", "type": "emergency", "title": "E-LONG", "priority": 5, "target": [7, 4]}
            ],
        },
    }

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 10},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    payload = create_response.json()
    _assert_online_payload_consistent(payload)
    runtime_blocks: list[list[int]] = []

    for current_time in range(1, 21):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

        if current_time in {3, 11}:
            manual_response = client.post(
                f"/api/sessions/{session_id}/tasks",
                json={
                    "task": {
                        "id": f"URGENT-LONG-{current_time}",
                        "type": "emergency",
                        "title": f"URGENT-LONG-{current_time}",
                        "priority": 5,
                        "releaseTime": current_time,
                        "deadline": current_time + 24,
                        "target": [2 if current_time == 3 else 11, 4],
                    }
                },
            )
            assert manual_response.status_code == 200
            payload = manual_response.json()
            _assert_online_payload_consistent(payload)

        if current_time in {4, 8, 14}:
            target_index = {4: 0, 8: 1, 14: 2}[current_time]
            generated_response = _post_generated_task(
                client,
                session_id,
                current_time,
                target=scenario["zones"]["inspection"][target_index],
            )
            assert generated_response.status_code == 200
            payload = generated_response.json()
            _assert_online_payload_consistent(payload)

        if current_time in {5, 13}:
            occupied_cells = {tuple(state["position"]) for state in payload["robotStates"]}
            blocked_cells = {
                tuple(cell)
                for cell in [
                    *scenario["obstacles"],
                    *runtime_blocks,
                ]
            }
            runtime_block_cell = next(
                cell
                for cell in ([5, 3], [8, 4], [4, 4], [9, 3], [2, 4], [11, 3])
                if tuple(cell) not in occupied_cells and tuple(cell) not in blocked_cells
            )
            block_response = client.post(
                f"/api/sessions/{session_id}/blocked-cells",
                json={"cell": runtime_block_cell, "currentTime": current_time},
            )
            assert block_response.status_code == 200
            payload = block_response.json()
            _assert_online_payload_consistent(payload)
            runtime_blocks.append(runtime_block_cell)

        if current_time == 7:
            fail_response = client.post(
                f"/api/sessions/{session_id}/failed-robots",
                json={"robotId": "R4", "currentTime": current_time},
            )
            assert fail_response.status_code == 200
            payload = fail_response.json()
            _assert_online_payload_consistent(payload)
            assert "R4" in payload["result"]["unavailableRobotIds"]

        if current_time == 9:
            restore_response = client.post(
                f"/api/sessions/{session_id}/failed-robots/restore",
                json={"robotId": "R4", "currentTime": current_time},
            )
            assert restore_response.status_code == 200
            payload = restore_response.json()
            _assert_online_payload_consistent(payload)
            assert "R4" not in payload["result"]["unavailableRobotIds"]

        if current_time == 7:
            assert "R8" in payload["result"]["unavailableRobotIds"]

        if current_time == 10:
            restore_response = client.post(
                f"/api/sessions/{session_id}/failed-robots/restore",
                json={"robotId": "R8", "currentTime": current_time},
            )
            assert restore_response.status_code == 200
            payload = restore_response.json()
            _assert_online_payload_consistent(payload)
            assert "R8" not in payload["result"]["unavailableRobotIds"]

        if current_time in {12, 16}:
            cell_to_remove = scenario["dynamic"]["blockedCells"][0] if current_time == 12 else runtime_blocks[0]
            unblock_response = client.post(
                f"/api/sessions/{session_id}/blocked-cells/remove",
                json={"cell": cell_to_remove, "currentTime": current_time},
            )
            assert unblock_response.status_code == 200
            payload = unblock_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 18:
            assert len(runtime_blocks) == 2
            unblock_response = client.post(
                f"/api/sessions/{session_id}/blocked-cells/remove",
                json={"cell": runtime_blocks[1], "currentTime": current_time},
            )
            assert unblock_response.status_code == 200
            payload = unblock_response.json()
            _assert_online_payload_consistent(payload)

    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    metric_times = [snapshot["time"] for snapshot in payload["metricsHistory"]]
    event_texts = [event["text"] for event in payload["result"]["eventLog"]]
    assert payload["currentTime"] == 20
    assert payload["runtimeTaskCount"] == 5
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["extraBlocked"] == []
    assert payload["result"]["unavailableRobotIds"] == []
    assert all(conflict["time"] > payload["currentTime"] for conflict in payload["result"]["conflicts"])
    assert metric_times == list(range(0, 21))
    assert payload["metricsHistory"][-1]["completedTaskCount"] == payload["completedTaskCount"]
    assert any(text == "T=6 场景动态事件触发" for text in event_texts)
    generated_task_ids = {task_id for task_id in task_states if task_id.startswith("G")}
    assert {"URGENT-LONG-3", "URGENT-LONG-11", "E-LONG"}.issubset(
        task_states
    )
    assert len(generated_task_ids) >= 3
    assert task_states["URGENT-LONG-3"]["status"] != "unassigned"
    assert task_states["URGENT-LONG-11"]["status"] != "unassigned"
    assert task_states["E-LONG"]["status"] != "unassigned"
    assert task_states["WINDOW-LONG"]["status"] == "pending"
    assert task_states["WINDOW-LONG"]["releaseTime"] == 38
    assert task_states["FAR-LONG"]["status"] == "pending"
    assert task_states["FAR-LONG"]["assignedRobotId"] is None
    assert task_states["FAR-LONG"]["releaseTime"] == 70


def test_session_continuous_online_ticks_with_repeated_runtime_events_stays_consistent() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]
    payload = create_response.json()
    runtime_block_cell: list[int] | None = None

    for current_time in range(1, 19):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

        if current_time == 3:
            manual_response = client.post(
                f"/api/sessions/{session_id}/tasks",
                json={
                    "task": {
                        "id": "U-CONT",
                        "type": "emergency",
                        "title": "U-CONT",
                        "priority": 5,
                        "releaseTime": current_time,
                        "deadline": current_time + 16,
                        "target": [1, 4],
                    }
                },
            )
            assert manual_response.status_code == 200
            payload = manual_response.json()
            _assert_online_payload_consistent(payload)

        if current_time in {4, 12, 16}:
            target = {4: [5, 0], 12: [5, 4], 16: [4, 4]}[current_time]
            generated_response = _post_generated_task(client, session_id, current_time, target=target)
            assert generated_response.status_code == 200
            payload = generated_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 5:
            occupied_cells = {tuple(state["position"]) for state in payload["robotStates"]}
            blocked_cells = {tuple(cell) for cell in [*scenario_payload()["obstacles"], *scenario_payload()["dynamic"]["blockedCells"]]}
            runtime_block_cell = next(
                cell
                for cell in ([1, 1], [3, 1], [4, 2], [1, 3])
                if tuple(cell) not in occupied_cells and tuple(cell) not in blocked_cells
            )
            block_response = client.post(
                f"/api/sessions/{session_id}/blocked-cells",
                json={"cell": runtime_block_cell, "currentTime": current_time},
            )
            assert block_response.status_code == 200
            payload = block_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 7:
            fail_response = client.post(
                f"/api/sessions/{session_id}/failed-robots",
                json={"robotId": "R2", "currentTime": current_time},
            )
            assert fail_response.status_code == 200
            payload = fail_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 8:
            assert runtime_block_cell is not None
            unblock_response = client.post(
                f"/api/sessions/{session_id}/blocked-cells/remove",
                json={"cell": runtime_block_cell, "currentTime": current_time},
            )
            assert unblock_response.status_code == 200
            payload = unblock_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 9:
            restore_response = client.post(
                f"/api/sessions/{session_id}/failed-robots/restore",
                json={"robotId": "R2", "currentTime": current_time},
            )
            assert restore_response.status_code == 200
            payload = restore_response.json()
            _assert_online_payload_consistent(payload)

    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    metric_times = [snapshot["time"] for snapshot in payload["metricsHistory"]]
    assert payload["currentTime"] == 18
    assert payload["runtimeTaskCount"] == 4
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert metric_times == list(range(0, 19))
    generated_task_ids = {task_id for task_id in task_states if task_id.startswith("G")}
    assert {"U-CONT", "E1"}.issubset(task_states)
    assert len(generated_task_ids) >= 3
    assert task_states["U-CONT"]["status"] != "unassigned"
    assert "R2" not in payload["result"]["unavailableRobotIds"]
    assert next(state for state in payload["robotStates"] if state["robotId"] == "R2")["status"] != "failed"


def test_session_continuous_online_sequence_preserves_recent_metrics_after_history_trim(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_METRICS_HISTORY_SNAPSHOTS", 5)
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
    payload = create_response.json()
    runtime_block_cell: list[int] | None = None

    for current_time in range(1, 13):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        _assert_online_payload_consistent(payload)

        if current_time in {2, 8}:
            generated_response = _post_generated_task(client, session_id, current_time)
            assert generated_response.status_code == 200
            payload = generated_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 3:
            occupied_cells = {tuple(state["position"]) for state in payload["robotStates"]}
            blocked_cells = {tuple(cell) for cell in scenario_payload()["obstacles"]}
            runtime_block_cell = next(
                cell
                for cell in ([1, 1], [3, 1], [4, 2], [1, 3])
                if tuple(cell) not in occupied_cells and tuple(cell) not in blocked_cells
            )
            block_response = client.post(
                f"/api/sessions/{session_id}/blocked-cells",
                json={"cell": runtime_block_cell, "currentTime": current_time},
            )
            assert block_response.status_code == 200
            payload = block_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 4:
            fail_response = client.post(
                f"/api/sessions/{session_id}/failed-robots",
                json={"robotId": "R2", "currentTime": current_time},
            )
            assert fail_response.status_code == 200
            payload = fail_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 6:
            assert runtime_block_cell is not None
            unblock_response = client.post(
                f"/api/sessions/{session_id}/blocked-cells/remove",
                json={"cell": runtime_block_cell, "currentTime": current_time},
            )
            assert unblock_response.status_code == 200
            payload = unblock_response.json()
            _assert_online_payload_consistent(payload)

        if current_time == 7:
            restore_response = client.post(
                f"/api/sessions/{session_id}/failed-robots/restore",
                json={"robotId": "R2", "currentTime": current_time},
            )
            assert restore_response.status_code == 200
            payload = restore_response.json()
            _assert_online_payload_consistent(payload)

    metric_times = [snapshot["time"] for snapshot in payload["metricsHistory"]]
    assert payload["currentTime"] == 12
    assert metric_times == [8, 9, 10, 11, 12]
    assert payload["runtimeTaskCount"] == 2
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert "R2" not in payload["result"]["unavailableRobotIds"]
    assert runtime_block_cell not in payload["result"]["extraBlocked"]


def test_session_lifecycle_metadata_list_and_delete(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    client = TestClient(app)

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    session_id = create_payload["sessionId"]
    assert create_payload["createdAt"] == 1000.0
    assert create_payload["updatedAt"] == 1000.0
    assert create_payload["lastAccessedAt"] == 1000.0

    clock["now"] = 1005.0
    get_response = client.get(f"/api/sessions/{session_id}")
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["createdAt"] == 1000.0
    assert get_payload["updatedAt"] == 1000.0
    assert get_payload["lastAccessedAt"] == 1005.0

    list_response = client.get("/api/sessions")
    assert list_response.status_code == 200
    summaries = list_response.json()
    assert [summary["sessionId"] for summary in summaries] == [session_id]
    assert summaries[0]["lastAccessedAt"] == 1005.0

    delete_response = client.delete(f"/api/sessions/{session_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"sessionId": session_id, "deleted": True}
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_session_list_summary_matches_latest_runtime_state(monkeypatch) -> None:
    clock = {"now": 1200.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
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

    clock["now"] = 1201.0
    task_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "摘要巡检",
                "priority": 4,
                "releaseTime": 2,
                "deadline": 20,
                "targets": [[1, 4]],
            }
        },
    )
    assert task_response.status_code == 200

    clock["now"] = 1202.0
    generated_response = _post_generated_task(client, session_id, 4)
    assert generated_response.status_code == 200

    clock["now"] = 1203.0
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 5},
    )
    assert block_response.status_code == 200

    clock["now"] = 1204.0
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 5},
    )
    assert fail_response.status_code == 200
    latest_payload = fail_response.json()

    list_response = client.get("/api/sessions")
    assert list_response.status_code == 200
    summaries = list_response.json()
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["sessionId"] == latest_payload["sessionId"]
    assert summary["scenarioId"] == latest_payload["scenarioId"]
    assert summary["createdAt"] == latest_payload["createdAt"]
    assert summary["updatedAt"] == latest_payload["updatedAt"]
    assert summary["lastAccessedAt"] == latest_payload["lastAccessedAt"]
    assert summary["currentTime"] == latest_payload["currentTime"]
    assert summary["runtimeTaskCount"] == latest_payload["runtimeTaskCount"]
    assert "manualTaskCount" not in summary
    assert "streamTaskCount" not in summary
    assert summary["runtimeEventCount"] == latest_payload["runtimeEventCount"]
    assert summary["completedTaskCount"] == latest_payload["completedTaskCount"]


def test_session_reset_restores_initial_runtime_state(monkeypatch) -> None:
    clock = {"now": 1500.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
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

    clock["now"] = 1501.0
    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert tick_response.status_code == 200

    clock["now"] = 1502.0
    manual_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "M1",
                "priority": 4,
                "releaseTime": 2,
                "deadline": 20,
                "targets": [[1, 4]],
            }
        },
    )
    assert manual_response.status_code == 200

    clock["now"] = 1503.0
    generated_response = _post_generated_task(client, session_id, 4)
    assert generated_response.status_code == 200

    clock["now"] = 1504.0
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1], "currentTime": 5},
    )
    assert block_response.status_code == 200

    clock["now"] = 1505.0
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 5},
    )
    assert fail_response.status_code == 200
    dirty_payload = fail_response.json()
    assert dirty_payload["currentTime"] == 5
    assert dirty_payload["runtimeTaskCount"] == 2
    assert "manualTaskCount" not in dirty_payload
    assert "streamTaskCount" not in dirty_payload
    assert dirty_payload["runtimeEventCount"] == 2
    assert len(dirty_payload["metricsHistory"]) > 1

    clock["now"] = 1506.0
    reset_response = client.post(f"/api/sessions/{session_id}/reset")

    assert reset_response.status_code == 200
    payload = reset_response.json()
    task_ids = {task["id"] for task in payload["result"]["tasks"]}
    assert payload["sessionId"] == session_id
    assert payload["createdAt"] == 1500.0
    assert payload["updatedAt"] == 1506.0
    assert payload["lastAccessedAt"] == 1506.0
    assert payload["currentTime"] == 0
    assert payload["runtimeTaskCount"] == 0
    assert "manualTaskCount" not in payload
    assert "streamTaskCount" not in payload
    assert payload["runtimeEventCount"] == 0
    assert payload["completedTaskCount"] == 0
    assert [item["time"] for item in payload["metricsHistory"]] == [0]
    assert task_ids == {"T1", "T2"}
    assert payload["result"]["extraBlocked"] == []
    assert payload["result"]["unavailableRobotIds"] == []
    assert "M1" not in task_ids
    assert "A1" not in task_ids
    assert {state["robotId"]: state["position"] for state in payload["robotStates"]} == {
        "R1": [0, 0],
        "R2": [0, 4],
    }


def test_session_reset_after_dynamic_and_runtime_events_restarts_clean_online_flow(monkeypatch) -> None:
    clock = {"now": 8000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    client = TestClient(app)

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    clock["now"] = 8001.0
    tick_dynamic_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 6},
    )
    assert tick_dynamic_response.status_code == 200
    dynamic_payload = tick_dynamic_response.json()
    _assert_online_payload_consistent(dynamic_payload)
    assert dynamic_payload["currentTime"] == 6
    assert dynamic_payload["result"]["dynamicTriggerTime"] == 6
    assert [3, 2] in dynamic_payload["result"]["extraBlocked"]
    assert any(event["text"] == "T=6 场景动态事件触发" for event in dynamic_payload["result"]["eventLog"])

    occupied_cells = {tuple(state["position"]) for state in dynamic_payload["robotStates"]}
    runtime_block_cell = next(
        cell
        for cell in ([1, 1], [3, 1], [4, 2], [1, 3])
        if tuple(cell) not in occupied_cells
        and tuple(cell) not in {tuple(item) for item in dynamic_payload["result"]["extraBlocked"]}
    )

    clock["now"] = 8002.0
    block_response = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": runtime_block_cell, "currentTime": 6},
    )
    assert block_response.status_code == 200

    clock["now"] = 8003.0
    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R2", "currentTime": 6},
    )
    assert fail_response.status_code == 200

    clock["now"] = 8004.0
    generated_response = _post_generated_task(client, session_id, 7)
    assert generated_response.status_code == 200
    dirty_payload = generated_response.json()
    _assert_online_payload_consistent(dirty_payload)
    assert dirty_payload["currentTime"] == 7
    assert "streamTaskCount" not in dirty_payload
    assert dirty_payload["runtimeEventCount"] == 2
    assert runtime_block_cell in dirty_payload["result"]["extraBlocked"]
    assert [3, 2] in dirty_payload["result"]["extraBlocked"]
    assert "R2" in dirty_payload["result"]["unavailableRobotIds"]
    assert any(task["id"].startswith("G") for task in dirty_payload["result"]["tasks"])

    clock["now"] = 8005.0
    reset_response = client.post(f"/api/sessions/{session_id}/reset")
    assert reset_response.status_code == 200
    reset_payload = reset_response.json()
    _assert_online_payload_consistent(reset_payload)
    assert reset_payload["sessionId"] == session_id
    assert reset_payload["createdAt"] == 8000.0
    assert reset_payload["updatedAt"] == 8005.0
    assert reset_payload["lastAccessedAt"] == 8005.0
    assert reset_payload["currentTime"] == 0
    assert "streamTaskCount" not in reset_payload
    assert reset_payload["runtimeEventCount"] == 0
    assert reset_payload["completedTaskCount"] == 0
    assert reset_payload["result"]["dynamicTriggerTime"] == 6
    assert reset_payload["result"]["extraBlocked"] == []
    assert reset_payload["result"]["unavailableRobotIds"] == []
    assert [item["time"] for item in reset_payload["metricsHistory"]] == [0]
    assert all(task["id"] != "A1" for task in reset_payload["result"]["tasks"])
    assert all(event["text"] != "T=6 场景动态事件触发" for event in reset_payload["result"]["eventLog"])

    clock["now"] = 8006.0
    retrigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 6},
    )
    assert retrigger_response.status_code == 200
    retrigger_payload = retrigger_response.json()
    _assert_online_payload_consistent(retrigger_payload)
    assert retrigger_payload["currentTime"] == 6
    assert retrigger_payload["runtimeEventCount"] == 0
    assert "streamTaskCount" not in retrigger_payload
    assert retrigger_payload["result"]["dynamicTriggerTime"] == 6
    assert retrigger_payload["result"]["extraBlocked"] == [[3, 2]]
    assert retrigger_payload["result"]["unavailableRobotIds"] == []
    assert any(task["id"] == "E1" for task in retrigger_payload["result"]["tasks"])
    assert all(task["id"] != "A1" for task in retrigger_payload["result"]["tasks"])
    assert any(event["text"] == "T=6 场景动态事件触发" for event in retrigger_payload["result"]["eventLog"])


def test_session_reset_preserves_configured_assignment_replan_window(monkeypatch) -> None:
    clock = {"now": 9000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    client = TestClient(app)
    scenario = {
        "id": "reset-configured-window",
        "name": "reset-configured-window",
        "description": "reset should preserve configured rolling window",
        "width": 8,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[1, 0], [7, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "NOW", "type": "inspection", "title": "NOW", "priority": 1, "releaseTime": 0, "targets": [[1, 0]]},
            {
                "id": "SOON",
                "type": "inspection",
                "title": "SOON",
                "priority": 5,
                "releaseTime": 5,
                "targets": [[7, 0]],
            },
        ],
        "dynamic": {
            "triggerTime": 0,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {
                "avoidConflicts": True,
                "includeDynamic": False,
                "assignmentReplanWindow": 4,
            },
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    clock["now"] = 9001.0
    first_trigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 1},
    )
    assert first_trigger_response.status_code == 200
    first_trigger_payload = first_trigger_response.json()
    assert first_trigger_payload["options"]["assignmentReplanWindow"] == 4
    assert any(event["text"] == "T=1 滚动窗口纳入远期任务" for event in first_trigger_payload["result"]["eventLog"])
    assert any(
        task["id"] == "SOON"
        for assignment in first_trigger_payload["result"]["assignments"]
        for task in assignment["tasks"]
    )

    clock["now"] = 9002.0
    reset_response = client.post(f"/api/sessions/{session_id}/reset")
    assert reset_response.status_code == 200
    reset_payload = reset_response.json()
    assert reset_payload["options"]["assignmentReplanWindow"] == 4
    assert reset_payload["currentTime"] == 0
    assert all(event["text"] != "T=1 滚动窗口纳入远期任务" for event in reset_payload["result"]["eventLog"])
    assert all(
        task["id"] != "SOON"
        for assignment in reset_payload["result"]["assignments"]
        for task in assignment["tasks"]
    )

    clock["now"] = 9003.0
    retrigger_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 1},
    )
    assert retrigger_response.status_code == 200
    retrigger_payload = retrigger_response.json()
    assert retrigger_payload["options"]["assignmentReplanWindow"] == 4
    assert any(event["text"] == "T=1 滚动窗口纳入远期任务" for event in retrigger_payload["result"]["eventLog"])
    assert any(
        task["id"] == "SOON"
        for assignment in retrigger_payload["result"]["assignments"]
        for task in assignment["tasks"]
    )


def test_session_store_expires_idle_sessions(monkeypatch) -> None:
    clock = {"now": 2000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    monkeypatch.setattr(sessions_module, "SESSION_TTL_SECONDS", 10)
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

    clock["now"] = 2011.0
    list_response = client.get("/api/sessions")

    assert list_response.status_code == 200
    assert list_response.json() == []
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_session_list_does_not_extend_idle_session_ttl(monkeypatch) -> None:
    clock = {"now": 2100.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    monkeypatch.setattr(sessions_module, "SESSION_TTL_SECONDS", 10)
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

    clock["now"] = 2105.0
    active_list_response = client.get("/api/sessions")
    assert active_list_response.status_code == 200
    assert [summary["sessionId"] for summary in active_list_response.json()] == [session_id]
    assert active_list_response.json()[0]["lastAccessedAt"] == 2100.0

    clock["now"] = 2111.0
    expired_list_response = client.get("/api/sessions")
    assert expired_list_response.status_code == 200
    assert expired_list_response.json() == []
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_session_store_prunes_oldest_sessions_when_capacity_is_reached(monkeypatch) -> None:
    clock = {"now": 3000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 2)
    client = TestClient(app)

    session_ids: list[str] = []
    for offset in range(3):
        clock["now"] = 3000.0 + offset
        response = client.post(
            "/api/sessions",
            json={
                "scenario": scenario_payload(),
                "options": {"avoidConflicts": True, "includeDynamic": False},
            },
        )
        assert response.status_code == 200
        session_ids.append(response.json()["sessionId"])

    list_response = client.get("/api/sessions")
    assert list_response.status_code == 200
    remaining_ids = [summary["sessionId"] for summary in list_response.json()]
    assert session_ids[0] not in remaining_ids
    assert set(remaining_ids) == set(session_ids[1:])
    assert client.get(f"/api/sessions/{session_ids[0]}").status_code == 404


def test_session_store_prunes_least_recently_accessed_session(monkeypatch) -> None:
    clock = {"now": 3100.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 2)
    client = TestClient(app)

    first_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert first_response.status_code == 200
    first_session_id = first_response.json()["sessionId"]

    clock["now"] = 3101.0
    second_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert second_response.status_code == 200
    second_session_id = second_response.json()["sessionId"]

    clock["now"] = 3102.0
    access_response = client.get(f"/api/sessions/{first_session_id}")
    assert access_response.status_code == 200
    assert access_response.json()["lastAccessedAt"] == 3102.0

    clock["now"] = 3103.0
    third_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert third_response.status_code == 200
    third_session_id = third_response.json()["sessionId"]

    list_response = client.get("/api/sessions")
    assert list_response.status_code == 200
    remaining_ids = [summary["sessionId"] for summary in list_response.json()]
    assert second_session_id not in remaining_ids
    assert remaining_ids == [third_session_id, first_session_id]
    assert client.get(f"/api/sessions/{second_session_id}").status_code == 404


def test_invalid_session_create_does_not_prune_existing_sessions(monkeypatch) -> None:
    clock = {"now": 4000.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
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

    invalid_scenario = scenario_payload()
    invalid_scenario["robots"][0]["start"] = [2, 1]
    invalid_response = client.post(
        "/api/sessions",
        json={
            "scenario": invalid_scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert invalid_response.status_code == 422
    list_response = client.get("/api/sessions")
    assert list_response.status_code == 200
    assert [summary["sessionId"] for summary in list_response.json()] == [session_id]
    assert client.get(f"/api/sessions/{session_id}").status_code == 200


def test_oversized_session_create_does_not_prune_existing_sessions(monkeypatch) -> None:
    clock = {"now": 4100.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    monkeypatch.setattr(sessions_module, "MAX_SESSION_TASKS", 2)
    client = TestClient(app)

    existing_scenario = scenario_payload()
    existing_scenario["dynamic"]["tasks"] = []
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": existing_scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    oversized_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert oversized_response.status_code == 409
    assert "任务数已达上限" in oversized_response.json()["detail"]
    list_response = client.get("/api/sessions")
    assert list_response.status_code == 200
    assert [summary["sessionId"] for summary in list_response.json()] == [session_id]


def test_oversized_invalid_session_create_is_rejected_before_validation_and_pruning(monkeypatch) -> None:
    clock = {"now": 4200.0}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    monkeypatch.setattr(sessions_module, "MAX_SESSION_TASKS", 2)
    client = TestClient(app)

    existing_scenario = scenario_payload()
    existing_scenario["dynamic"]["tasks"] = []
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": existing_scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    oversized_invalid_scenario = scenario_payload()
    oversized_invalid_scenario["tasks"][0]["targets"] = [[99, 99]]
    oversized_response = client.post(
        "/api/sessions",
        json={
            "scenario": oversized_invalid_scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert oversized_response.status_code == 409
    assert oversized_response.json()["detail"] == "调度会话任务数已达上限：3 > 2"
    list_response = client.get("/api/sessions")
    assert list_response.status_code == 200
    assert [summary["sessionId"] for summary in list_response.json()] == [session_id]


def test_session_metrics_history_keeps_recent_snapshots_when_capacity_is_reached(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_METRICS_HISTORY_SNAPSHOTS", 3)
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

    payload = create_response.json()
    for current_time in (1, 2, 3, 4):
        tick_response = client.post(
            f"/api/sessions/{session_id}/tick",
            json={"currentTime": current_time},
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()

    assert [item["time"] for item in payload["metricsHistory"]] == [2, 3, 4]
    assert payload["metricsHistory"][-1]["time"] == payload["currentTime"]


def test_session_event_notes_keep_recent_entries_when_capacity_is_reached(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSION_EVENT_NOTES", 3)
    scenario = Scenario.model_validate(scenario_payload())
    session = sessions_module.DispatchSession(
        session_id="event-note-cap",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
    )

    for event_time in range(5):
        sessions_module._record_session_event(session, event_time, f"event {event_time}")

    assert [(event.time, event.text) for event in session.event_notes] == [
        (2, "event 2"),
        (3, "event 3"),
        (4, "event 4"),
    ]


def test_session_result_event_log_includes_all_retained_runtime_events() -> None:
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

    payload = create_response.json()
    for current_time in range(1, 10):
        generated_response = _post_generated_task(client, session_id, current_time)
        assert generated_response.status_code == 200
        payload = generated_response.json()

    generated_events = [
        event["text"]
        for event in payload["result"]["eventLog"]
        if event["text"].startswith("手动录入任务：G")
    ]
    generated_event_ids = [text.split("：", 1)[1].split(" ", 1)[0] for text in generated_events]
    assert len(generated_event_ids) == 9
    assert len(set(generated_event_ids)) == 9


def test_session_rejects_manual_task_when_task_capacity_is_reached(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSION_TASKS", 2)
    client = TestClient(app)

    scenario = scenario_payload()
    scenario["dynamic"]["tasks"] = []
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "OVER-CAP",
                "type": "inspection",
                "title": "OVER-CAP",
                "priority": 2,
                "targets": [[1, 4]],
            }
        },
    )

    assert add_response.status_code == 409
    assert add_response.json()["detail"] == "调度会话任务数已达上限：2 + 1 > 2"
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["runtimeTaskCount"] == 0
    assert all(task["id"] != "OVER-CAP" for task in payload["result"]["tasks"])


def test_session_rejects_generated_task_when_task_capacity_is_reached(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSION_TASKS", 3)
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

    stream_response = _post_generated_task(client, session_id, 4)

    assert stream_response.status_code == 409
    assert stream_response.json()["detail"] == "调度会话任务数已达上限：3 + 1 > 3"
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert payload["currentTime"] == 4
    assert "streamTaskCount" not in payload
    assert all(not task["id"].startswith("G") for task in payload["result"]["tasks"])


def test_session_api_accepts_runtime_robot_failure() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": "R1", "currentTime": 6},
    )

    assert fail_response.status_code == 200
    payload = fail_response.json()
    assert payload["runtimeEventCount"] == 1
    assert "R1" in payload["result"]["unavailableRobotIds"]
    failed_state = next(state for state in payload["robotStates"] if state["robotId"] == "R1")
    assert failed_state["status"] == "failed"
    assert payload["result"]["paths"]["R1"][payload["currentTime"]] == failed_state["position"]
    assert any("手动标记故障机器人" in event["text"] for event in payload["result"]["eventLog"])

def test_session_robot_failure_releases_locked_tasks_for_reassignment() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    session_id = create_response.json()["sessionId"]

    tick_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 2},
    )
    assert tick_response.status_code == 200
    locked_before = [
        state
        for state in tick_response.json()["taskStates"]
        if state["locked"] and state["assignedRobotId"] is not None and state["status"] != "completed"
    ]
    assert locked_before
    failed_robot_id = locked_before[0]["assignedRobotId"]
    task_ids_locked_to_failed_robot = {
        state["taskId"]
        for state in locked_before
        if state["assignedRobotId"] == failed_robot_id
    }
    assert task_ids_locked_to_failed_robot

    fail_response = client.post(
        f"/api/sessions/{session_id}/failed-robots",
        json={"robotId": failed_robot_id, "currentTime": 2},
    )

    assert fail_response.status_code == 200
    payload = fail_response.json()
    task_states = {state["taskId"]: state for state in payload["taskStates"]}
    for task_id in task_ids_locked_to_failed_robot:
        state = task_states[task_id]
        assert state["assignedRobotId"] != failed_robot_id
        assert state["status"] != "unassigned"
    assert any("释放锁定任务" in event["text"] for event in payload["result"]["eventLog"])


def test_session_charge_events_and_post_charge_energy_are_tick_accurate() -> None:
    client = TestClient(app)
    scenario = {
        "id": "charge-session", "name": "charge-session", "description": "charge", "width": 4, "height": 1,
        "obstacles": [], "zones": {"warehouse": [], "inspection": [[3, 0]], "delivery": [], "charging": [[0, 0]]},
        "chargeTime": 2,
        "robots": [{"id": "R1", "name": "R1", "start": [1, 0], "battery": 1, "batteryCapacity": 8, "load": 1}],
        "tasks": [{"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[3, 0]]}],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    created = client.post("/api/sessions", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}})
    assert created.status_code == 200
    payload = client.post(f"/api/sessions/{created.json()['sessionId']}/tick", json={"currentTime": 6}).json()
    state = payload["robotStates"][0]
    assert state["battery"] == 5
    assert [event["text"] for event in payload["result"]["eventLog"] if "充电" in event["text"]] == ["R1 前往充电桩", "R1 开始充电", "R1 完成充电"]


def test_session_does_not_assign_new_task_to_charging_robot() -> None:
    client = TestClient(app)
    scenario = {
        "id": "charging-busy-session",
        "name": "charging-busy-session",
        "description": "charging robot must stay unavailable",
        "width": 5,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [], "inspection": [[3, 0], [4, 0]], "delivery": [], "charging": [[0, 0]]},
        "chargeTime": 3,
        "robots": [{"id": "R1", "name": "R1", "start": [1, 0], "battery": 1, "batteryCapacity": 10, "load": 1}],
        "tasks": [{"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[3, 0]]}],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    created = client.post("/api/sessions", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}})
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    charging = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 1})
    assert charging.status_code == 200
    assert charging.json()["robotStates"][0]["status"] == "charging"

    response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "T2",
                "type": "inspection",
                "title": "T2",
                "priority": 5,
                "releaseTime": 1,
                "targets": [[4, 0]],
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    states = {state["taskId"]: state for state in payload["taskStates"]}
    assert payload["robotStates"][0]["status"] == "charging"
    assert payload["robotStates"][0]["currentTaskId"] == "T1"
    assert states["T1"]["assignedRobotId"] == "R1"
    assert states["T1"]["status"] == "running"
    assert states["T2"]["assignedRobotId"] is None
    assert states["T2"]["status"] == "pending"


def test_session_reports_runtime_blocked_charge_route_as_clearable_failure() -> None:
    client = TestClient(app)
    scenario = {
        "id": "blocked-charge-route-session",
        "name": "blocked-charge-route-session",
        "description": "runtime block prevents reaching charger",
        "width": 5,
        "height": 2,
        "obstacles": [],
        "zones": {"warehouse": [], "inspection": [[4, 0]], "delivery": [], "charging": [[0, 0]]},
        "chargeTime": 2,
        "robots": [{"id": "R1", "name": "R1", "start": [2, 0], "battery": 2, "batteryCapacity": 10, "load": 1}],
        "tasks": [{"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[4, 0]]}],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    created = client.post("/api/sessions", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}})
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    blocked = client.post(f"/api/sessions/{session_id}/blocked-cells", json={"cell": [1, 0], "currentTime": 0})

    assert blocked.status_code == 200
    blocked_payload = blocked.json()
    task_state = next(state for state in blocked_payload["taskStates"] if state["taskId"] == "T1")
    details = blocked_payload["result"]["failureDetails"]["T1"]
    assert task_state["status"] == "unassigned"
    assert task_state["recoveryAction"] == "clearBlockedCells"
    assert details["category"] == "temporary"
    assert details["recoveryAction"] == "clearBlockedCells"
    assert details["blockingCells"] == [[1, 0]]
    assert "充电" in details["reason"]

    recovered = client.post(f"/api/sessions/{session_id}/blocked-cells/remove", json={"cell": [1, 0], "currentTime": 0})

    assert recovered.status_code == 200
    recovered_payload = recovered.json()
    recovered_state = next(state for state in recovered_payload["taskStates"] if state["taskId"] == "T1")
    assert recovered_state["failureReason"] is None
    assert recovered_state["recoveryAction"] is None
    assert recovered_payload["result"]["chargingVisits"]
