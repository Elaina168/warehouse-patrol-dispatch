from fastapi.testclient import TestClient

import backend.app.dispatch as dispatch_module
from backend.app.dispatch import build_paths, build_paths_for_order, path_planning_candidate_score, task_completion_times
from backend.app.main import app
from backend.app.schemas import Assignment, DispatchOptions, Scenario
from backend.tests.helpers import scenario_payload, seeded_pressure_scenario


def test_assignment_prefers_faster_robot_when_grid_distance_is_equal() -> None:
    client = TestClient(app)
    scenario = {
        "id": "speed-aware-assignment",
        "name": "speed-aware-assignment",
        "description": "equal grid distance should prefer the faster robot",
        "width": 5,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [], "inspection": [[2, 0]], "delivery": []},
        "robots": [
            {"id": "R1", "name": "slow", "start": [0, 0], "battery": 90, "load": 1, "moveTicks": 4},
            {"id": "R2", "name": "fast", "start": [4, 0], "battery": 90, "load": 1, "moveTicks": 1},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "equal distance", "priority": 2, "targets": [[2, 0]]},
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }

    response = client.post(
        "/api/dispatch",
        json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}},
    )

    assert response.status_code == 200
    assignment = next(item for item in response.json()["assignments"] if item["tasks"])
    assert assignment["robotId"] == "R2"


def test_timed_path_expands_each_move_by_robot_duration() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "timed-speed-path",
            "name": "timed-speed-path",
            "description": "robot movement duration should expand the timed path",
            "width": 2,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [[1, 0]], "delivery": []},
            "robots": [],
            "tasks": [],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )

    path = dispatch_module.astar_timed(
        scenario,
        (0, 0),
        (1, 0),
        0,
        dispatch_module.Reservations(),
        move_ticks=3,
    )

    assert path == [(0, 0), (0, 0), (0, 0), (1, 0)]


def test_avoidance_respects_slow_robot_intermediate_start_cell_occupancy() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "slow-start-occupancy",
            "name": "slow-start-occupancy",
            "description": "another robot must wait while the slow robot still occupies its start cell",
            "width": 2,
            "height": 2,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [[0, 0], [1, 0]], "delivery": []},
            "robots": [
                {"id": "R1", "name": "slow", "start": [0, 0], "battery": 90, "load": 1, "moveTicks": 3},
                {"id": "R2", "name": "fast", "start": [0, 1], "battery": 90, "load": 1, "moveTicks": 1},
            ],
            "tasks": [
                {"id": "T1", "type": "inspection", "title": "slow move", "priority": 2, "targets": [[1, 0]]},
                {"id": "T2", "type": "inspection", "title": "shared start", "priority": 2, "targets": [[0, 0]]},
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )
    tasks = {task.id: task for task in scenario.tasks}
    assignments = [Assignment(robotId="R1", tasks=[tasks["T1"]]), Assignment(robotId="R2", tasks=[tasks["T2"]])]

    paths, failures = build_paths(scenario, scenario.robots, assignments, True, [], [])

    assert failures == []
    assert paths["R1"][:4] == [(0, 0), (0, 0), (0, 0), (1, 0)]
    assert paths["R2"][1] != (0, 0)
    assert dispatch_module.detect_conflicts(paths) == []


def test_dispatch_api_returns_schedulable_result() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenarioId"] == "test-map"
    assert payload["metrics"]["assignedTaskCount"] >= 2
    assert payload["metrics"]["totalDistance"] > 0
    assert set(payload["paths"].keys()) == {"R1", "R2"}
    assert len(payload["tasks"]) == 3
    assert payload["eventLog"][0]["text"].startswith("加载场景")
    assert all(event["time"] == 0 for event in payload["eventLog"])
    assert not any("未检测到时空路径冲突" in event["text"] for event in payload["eventLog"])
    assert not any("均按时完成" in event["text"] for event in payload["eventLog"])

def test_dispatch_api_can_disable_dynamic_tasks() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": False, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["includeDynamic"] is False
    assert payload["dynamicTriggerTime"] is None
    assert len(payload["tasks"]) == 2


def test_dispatch_dynamic_task_release_time_is_not_before_dynamic_trigger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dispatch-dynamic-release-floor",
        "name": "dispatch-dynamic-release-floor",
        "description": "dynamic dispatch task should not start before trigger",
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
    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assignments"][0]["tasks"][0]["id"] == "DYN-EARLY"
    assert payload["assignments"][0]["tasks"][0]["releaseTime"] == 5
    assert payload["paths"]["R1"][:6] == [[0, 0]] * 6
    assert payload["paths"]["R1"][6:8] == [[1, 0], [2, 0]]


def test_dispatch_does_not_apply_future_dynamic_robot_failure_at_start() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dispatch-future-dynamic-failure",
        "name": "dispatch-future-dynamic-failure",
        "description": "future dynamic failed robot should not block initial base task planning",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[1, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "T1", "priority": 2, "targets": [[1, 0]]},
        ],
        "dynamic": {
            "triggerTime": 10,
            "blockedCells": [],
            "failedRobots": ["R1"],
            "tasks": [],
        },
    }

    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assigned_task_ids = [task["id"] for assignment in payload["assignments"] for task in assignment["tasks"]]
    assert "T1" in assigned_task_ids
    assert payload["unavailableRobotIds"] == []
    assert "T1" not in payload["failureReasons"]


def test_dispatch_future_dynamic_task_avoids_robot_failed_at_trigger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dispatch-future-dynamic-task-failed-robot",
        "name": "dispatch-future-dynamic-task-failed-robot",
        "description": "future dynamic task should not be assigned to robot failed at trigger",
        "width": 5,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0], [4, 0]],
            "inspection": [[1, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [4, 0], "battery": 90, "load": 1},
        ],
        "tasks": [],
        "dynamic": {
            "triggerTime": 5,
            "blockedCells": [],
            "failedRobots": ["R1"],
            "tasks": [
                {"id": "DYN-FAIL", "type": "inspection", "title": "DYN-FAIL", "priority": 3, "targets": [[1, 0]]},
            ],
        },
    }

    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    dynamic_assignment = next(
        assignment for assignment in payload["assignments"] if any(task["id"] == "DYN-FAIL" for task in assignment["tasks"])
    )
    assigned_task = next(task for task in dynamic_assignment["tasks"] if task["id"] == "DYN-FAIL")
    assert payload["unavailableRobotIds"] == []
    assert dynamic_assignment["robotId"] == "R2"
    assert assigned_task["releaseTime"] == 5
    assert "DYN-FAIL" not in payload["failureReasons"]


def test_dispatch_future_dynamic_task_path_avoids_dynamic_block_after_trigger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "dispatch-future-dynamic-block-path",
        "name": "dispatch-future-dynamic-block-path",
        "description": "future dynamic task path should avoid dynamic block after trigger",
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
        "tasks": [],
        "dynamic": {
            "triggerTime": 5,
            "blockedCells": [[1, 0]],
            "failedRobots": [],
            "tasks": [
                {"id": "DYN-BLOCK", "type": "inspection", "title": "DYN-BLOCK", "priority": 3, "targets": [[2, 0]]},
            ],
        },
    }

    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assigned_task = next(task for assignment in payload["assignments"] for task in assignment["tasks"] if task["id"] == "DYN-BLOCK")
    path_after_trigger = payload["paths"]["R1"][payload["dynamicTriggerTime"]:]
    assert payload["extraBlocked"] == []
    assert assigned_task["releaseTime"] == 5
    assert payload["paths"]["R1"][:6] == [[0, 0]] * 6
    assert [1, 0] not in path_after_trigger
    assert payload["paths"]["R1"][-1] == [2, 0]
    assert "DYN-BLOCK" not in payload["failureReasons"]


def test_dispatch_assignment_balances_clustered_tasks() -> None:
    client = TestClient(app)
    scenario = {
        "id": "clustered-line",
        "name": "clustered-line",
        "description": "clustered assignment regression",
        "width": 11,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[4, 0], [5, 0], [6, 0]],
            "delivery": [],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [10, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[4, 0]]},
            {"id": "T2", "type": "inspection", "title": "T2", "priority": 1, "targets": [[5, 0]]},
            {"id": "T3", "type": "inspection", "title": "T3", "priority": 1, "targets": [[6, 0]]},
        ],
        "dynamic": {
            "triggerTime": 0,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }

    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": False, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    tasks_by_robot = {assignment["robotId"]: assignment["tasks"] for assignment in payload["assignments"]}
    assert len(tasks_by_robot["R2"]) >= 1
    assert payload["metrics"]["makespan"] <= 5

def test_assignment_keeps_locked_tasks_as_robot_prefix() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "locked-prefix",
            "name": "locked-prefix",
            "description": "locked task prefix regression",
            "width": 6,
            "height": 1,
            "obstacles": [],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[1, 0], [5, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "LOW", "type": "inspection", "title": "LOW", "priority": 1, "targets": [[5, 0]]},
                {"id": "URGENT", "type": "inspection", "title": "URGENT", "priority": 5, "targets": [[1, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    assignments = dispatch_module.assign_tasks_beam_search(
        scenario,
        scenario.robots,
        scenario.tasks,
        [],
        [],
        {"LOW": "R1"},
    )

    assert [task.id for task in assignments[0].tasks] == ["LOW", "URGENT"]

def test_assignment_defers_far_future_tasks_outside_replan_window() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "rolling-window",
            "name": "rolling-window",
            "description": "rolling assignment window regression",
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
                    "id": "FUTURE",
                    "type": "inspection",
                    "title": "FUTURE",
                    "priority": 5,
                    "releaseTime": 99,
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

    assignments = dispatch_module.assign_tasks_beam_search(
        scenario,
        scenario.robots,
        scenario.tasks,
        [],
        [],
        {},
    )

    assert [task.id for task in assignments[0].tasks] == ["NOW", "FUTURE"]


def test_dispatch_keeps_far_future_tasks_visible_without_current_assignment_or_failure() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "rolling-window-dispatch",
            "name": "rolling-window-dispatch",
            "description": "far future tasks should stay visible without current assignment pressure",
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
                    "id": "FUTURE",
                    "type": "inspection",
                    "title": "FUTURE",
                    "priority": 5,
                    "releaseTime": 99,
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

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=False))

    assigned_task_ids = [task.id for assignment in result.assignments for task in assignment.tasks]
    assert assigned_task_ids == ["NOW"]
    assert [task.id for task in result.tasks] == ["NOW", "FUTURE"]
    assert result.metrics.assignedTaskCount == 1
    assert result.metrics.failureCount == 0
    assert result.failureDetails == {}
    assert any(event.text == "1 个远期任务等待滚动窗口调度" for event in result.eventLog)


def test_dispatch_uses_configured_assignment_replan_window() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "configured-rolling-window",
            "name": "configured-rolling-window",
            "description": "configured rolling assignment window regression",
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
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=False, assignmentReplanWindow=4),
    )

    assigned_task_ids = [task.id for assignment in result.assignments for task in assignment.tasks]
    assert assigned_task_ids == ["NOW"]
    assert [task.id for task in result.tasks] == ["NOW", "SOON"]
    assert result.metrics.assignedTaskCount == 1
    assert result.metrics.failureCount == 0
    assert result.failureDetails == {}
    assert any(event.text == "1 个远期任务等待滚动窗口调度" for event in result.eventLog)


def test_dispatch_adaptive_window_expands_to_include_future_task() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "adaptive-future-window",
            "name": "adaptive-future-window",
            "description": "adaptive low-load window should include future work",
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

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(
            avoidConflicts=True,
            includeDynamic=False,
            assignmentReplanWindow=24,
            adaptiveReplanWindow=True,
        ),
    )

    assert result.effectiveAssignmentReplanWindow == 48
    assert result.replanWindowReason == "当前负载较低且存在远期任务，扩大窗口"
    assert [task.id for assignment in result.assignments for task in assignment.tasks] == ["FUTURE"]


def test_dispatch_keeps_locked_far_future_task_inside_current_planning_window() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "locked-future-window",
            "name": "locked-future-window",
            "description": "locked far future tasks must stay in the current replan set",
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
                    "id": "LOCKED-FUTURE",
                    "type": "inspection",
                    "title": "LOCKED-FUTURE",
                    "priority": 5,
                    "releaseTime": 99,
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

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=False),
        locked_task_robot_ids={"LOCKED-FUTURE": "R1"},
    )

    assigned_task_ids = [task.id for assignment in result.assignments for task in assignment.tasks]
    assert assigned_task_ids == ["LOCKED-FUTURE"]
    assert result.metrics.assignedTaskCount == 1
    assert result.metrics.failureCount == 0
    assert result.failureDetails == {}
    assert not any("远期任务等待滚动窗口调度" in event.text for event in result.eventLog)


def test_dispatch_handles_medium_online_planning_pressure() -> None:
    tasks = [
        {
            "id": f"T{index}",
            "type": "inspection",
            "title": f"T{index}",
            "priority": 1 + (index % 5),
            "releaseTime": index % 6,
            "deadline": 40 + index,
            "targets": [[2 + (index % 8), 2 + (index // 8)]],
        }
        for index in range(16)
    ]
    scenario = Scenario.model_validate(
        {
            "id": "medium-pressure",
            "name": "medium-pressure",
            "description": "medium pressure dispatch regression",
            "width": 12,
            "height": 8,
            "obstacles": [[5, 1], [5, 4], [6, 5], [7, 6]],
            "zones": {
                "warehouse": [[0, 0], [11, 0], [0, 7], [11, 7]],
                "inspection": [[2 + (index % 8), 2 + (index // 8)] for index in range(16)],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 2},
                {"id": "R2", "name": "R2", "start": [11, 0], "battery": 90, "load": 2},
                {"id": "R3", "name": "R3", "start": [0, 7], "battery": 90, "load": 2},
                {"id": "R4", "name": "R4", "start": [11, 7], "battery": 90, "load": 2},
            ],
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=False))

    assert result.metrics.assignedTaskCount == 16
    assert result.metrics.failureCount == 0
    assert set(result.paths) == {"R1", "R2", "R3", "R4"}
    assert result.metrics.replanTimeMs >= 0


def test_dispatch_handles_eight_robot_mixed_task_pressure() -> None:
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
        [4, 10],
        [13, 10],
        [4, 1],
        [13, 1],
    ]
    delivery_pairs = [
        ([1, 1], [16, 10]),
        ([16, 1], [1, 10]),
        ([1, 10], [16, 1]),
        ([16, 10], [1, 1]),
        ([1, 5], [16, 5]),
        ([16, 5], [1, 5]),
        ([8, 1], [8, 10]),
        ([9, 10], [9, 1]),
    ]
    tasks = [
        {
            "id": f"I{index + 1}",
            "type": "inspection",
            "title": f"巡检 {index + 1}",
            "priority": 1 + (index % 5),
            "releaseTime": index % 8,
            "deadline": 70 + index,
            "targets": [cell],
        }
        for index, cell in enumerate(inspection_cells)
    ]
    tasks.extend(
        {
            "id": f"D{index + 1}",
            "type": "delivery",
            "title": f"配送 {index + 1}",
            "priority": 2 + (index % 3),
            "releaseTime": index % 6,
            "deadline": 80 + index,
            "pickup": pickup,
            "dropoff": dropoff,
            "demand": 1 + (index % 2),
        }
        for index, (pickup, dropoff) in enumerate(delivery_pairs)
    )
    scenario = Scenario.model_validate(
        {
            "id": "eight-robot-mixed-pressure",
            "name": "eight-robot-mixed-pressure",
            "description": "8 robot mixed task pressure dispatch regression",
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
                "delivery": [[16, 10], [1, 10], [16, 1], [1, 1], [16, 5], [1, 5], [8, 10], [9, 1]],
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
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=False))

    assert result.metrics.assignedTaskCount == 24
    assert result.metrics.failureCount == 0
    assert result.conflicts == []
    assert len(result.paths) >= 6
    assert result.metrics.replanTimeMs >= 0


def _lane_scale_scenario(label: str, robot_count: int, tasks_per_robot: int) -> Scenario:
    width = 14
    height = robot_count
    robots = []
    tasks = []
    inspection_cells = []
    delivery_cells = []
    for row in range(robot_count):
        robots.append({"id": f"R{row + 1}", "name": f"R{row + 1}", "start": [0, row], "battery": 90, "load": 2})
        for step in range(tasks_per_robot):
            cell = [4 + step * 3, row]
            inspection_cells.append(cell)
            tasks.append(
                {
                    "id": f"I{row + 1}-{step + 1}",
                    "type": "inspection",
                    "title": f"I{row + 1}-{step + 1}",
                    "priority": 1 + (step % 4),
                    "releaseTime": step % 4,
                    "deadline": 60 + step + row,
                    "targets": [cell],
                }
            )
        pickup = [1, row]
        dropoff = [12, row]
        delivery_cells.append(dropoff)
        tasks.append(
            {
                "id": f"D{row + 1}",
                "type": "delivery",
                "title": f"D{row + 1}",
                "priority": 3,
                "releaseTime": row % 3,
                "deadline": 90 + row,
                "pickup": pickup,
                "dropoff": dropoff,
                "demand": 1,
            }
        )

    dynamic_tasks = []
    for row in range(min(3, robot_count)):
        cell = [10, row]
        inspection_cells.append(cell)
        dynamic_tasks.append(
            {"id": f"E{row + 1}", "type": "emergency", "title": f"E{row + 1}", "priority": 5, "target": cell}
        )

    return Scenario.model_validate(
        {
            "id": f"lane-scale-{label}",
            "name": f"lane-scale-{label}",
            "description": "deterministic scale pressure regression",
            "width": width,
            "height": height,
            "obstacles": [],
            "zones": {
                "warehouse": [robot["start"] for robot in robots],
                "inspection": inspection_cells,
                "delivery": delivery_cells,
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 6,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": dynamic_tasks,
            },
        }
    )


def test_dispatch_handles_deterministic_scale_pressure_family() -> None:
    cases = [
        ("small", 3, 2, 12),
        ("medium", 5, 2, 18),
        ("large", 8, 2, 27),
    ]
    previous_distance = 0
    previous_task_count = 0

    for label, robot_count, tasks_per_robot, expected_task_count in cases:
        scenario = _lane_scale_scenario(label, robot_count, tasks_per_robot)
        result = dispatch_module.run_dispatch(
            scenario,
            DispatchOptions(avoidConflicts=True, includeDynamic=True, assignmentReplanWindow=120),
        )

        assert len(result.tasks) == expected_task_count
        assert result.metrics.assignedTaskCount == expected_task_count
        assert result.metrics.failureCount == 0
        assert result.failureDetails == {}
        assert result.conflicts == []
        assert result.metrics.conflictCount == 0
        assert result.metrics.totalDistance > previous_distance
        assert result.metrics.assignedTaskCount > previous_task_count
        assert result.metrics.replanTimeMs < 1000
        assert set(result.paths) == {robot.id for robot in scenario.robots}
        assert {task.id for task in scenario.dynamic.tasks}.issubset(
            {task.id for assignment in result.assignments for task in assignment.tasks}
        )
        previous_distance = result.metrics.totalDistance
        previous_task_count = result.metrics.assignedTaskCount


def test_dispatch_handles_fixed_seed_pressure_family() -> None:
    cases = [
        ("seed-17", 17, 4, 12),
        ("seed-29", 29, 6, 20),
        ("seed-31", 31, 8, 24),
    ]
    previous_task_count = 0

    for label, seed, robot_count, task_count in cases:
        scenario = seeded_pressure_scenario(label, seed, robot_count, task_count)
        result = dispatch_module.run_dispatch(
            scenario,
            DispatchOptions(avoidConflicts=True, includeDynamic=True, assignmentReplanWindow=120),
        )
        expected_task_count = task_count + len(scenario.dynamic.tasks)
        assigned_task_ids = {task.id for assignment in result.assignments for task in assignment.tasks}

        assert len(result.tasks) == expected_task_count
        assert result.metrics.assignedTaskCount == expected_task_count
        assert result.metrics.assignedTaskCount > previous_task_count
        assert result.metrics.failureCount == 0
        assert result.failureDetails == {}
        assert result.conflicts == []
        assert result.metrics.conflictCount == 0
        assert result.metrics.totalDistance > 0
        assert result.metrics.replanTimeMs < 2000
        assert result.dynamicTriggerTime == 8
        assert result.extraBlocked == []
        assert set(result.paths) == {robot.id for robot in scenario.robots}
        assert {task.id for task in scenario.dynamic.tasks}.issubset(assigned_task_ids)
        previous_task_count = result.metrics.assignedTaskCount


def test_dispatch_handles_seed_43_pressure_boundary_without_late_goal_conflict() -> None:
    scenario = seeded_pressure_scenario("seed-43", seed=43, robot_count=8, task_count=28)

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True, assignmentReplanWindow=120),
    )

    assert len(result.tasks) == 31
    assert result.metrics.assignedTaskCount == 31
    assert result.metrics.failureCount == 0
    assert result.failureDetails == {}
    assert result.conflicts == []
    assert result.metrics.conflictCount == 0
    assert set(result.paths) == {robot.id for robot in scenario.robots}


def test_dispatch_reports_unassigned_tasks_as_failures() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "unassigned-failure",
            "name": "unassigned-failure",
            "description": "unassigned task failure regression",
            "width": 4,
            "height": 2,
            "obstacles": [],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[1, 0]],
                "delivery": [[3, 1]],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "OK", "type": "inspection", "title": "OK", "priority": 1, "targets": [[1, 0]]},
                {
                    "id": "HEAVY",
                    "type": "delivery",
                    "title": "HEAVY",
                    "priority": 2,
                    "pickup": [0, 0],
                    "dropoff": [3, 1],
                    "demand": 2,
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

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=False))

    assert result.metrics.assignedTaskCount == 1
    assert result.metrics.failureCount == 1
    assert result.failureDetails["HEAVY"].reason == result.failureReasons["HEAVY"]
    assert result.failureDetails["HEAVY"].category == "permanent"
    assert result.failureDetails["HEAVY"].recoveryAction == "addCapableRobotOrReduceDemand"
    assert result.failureDetails["HEAVY"].blockingCells == []
    assert result.failureDetails["HEAVY"].blockingRobotIds == []
    assert any(event.text == "任务 HEAVY 未分配" for event in result.eventLog)

def test_dispatch_marks_blocked_unreachable_task_as_temporary_failure() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-blocked-failure",
            "name": "temporary-blocked-failure",
            "description": "temporary blocked failure detail regression",
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
                {"id": "R2", "name": "R2", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "BLOCKED", "type": "inspection", "title": "BLOCKED", "priority": 1, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [[1, 0]],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureDetails["BLOCKED"].reason == result.failureReasons["BLOCKED"]
    assert result.failureDetails["BLOCKED"].category == "temporary"
    assert result.failureDetails["BLOCKED"].recoveryAction == "clearBlockedCells"
    assert result.failureDetails["BLOCKED"].blockingCells == [(1, 0)]
    assert result.failureDetails["BLOCKED"].blockingRobotIds == []


def test_dispatch_reports_only_recovering_blocked_cells_in_failure_detail() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "specific-blocked-cell-detail",
            "name": "specific-blocked-cell-detail",
            "description": "recovering blocked cell detail regression",
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
            ],
            "tasks": [
                {"id": "BLOCKED", "type": "inspection", "title": "BLOCKED", "priority": 1, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [[1, 0], [0, 1]],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.failureCount == 1
    assert result.failureDetails["BLOCKED"].category == "temporary"
    assert result.failureDetails["BLOCKED"].recoveryAction == "clearBlockedCells"
    assert result.failureDetails["BLOCKED"].blockingCells == [(1, 0)]
    assert result.failureDetails["BLOCKED"].blockingRobotIds == []


def test_dispatch_reports_joint_recovering_blocked_cells_without_unrelated_blocks() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "joint-blocked-cell-detail",
            "name": "joint-blocked-cell-detail",
            "description": "joint recovering blocked cell detail regression",
            "width": 4,
            "height": 2,
            "obstacles": [[1, 1], [2, 1], [3, 1]],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[3, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "BLOCKED", "type": "inspection", "title": "BLOCKED", "priority": 1, "targets": [[3, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [[1, 0], [2, 0], [0, 1]],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.failureCount == 1
    assert result.failureDetails["BLOCKED"].category == "temporary"
    assert result.failureDetails["BLOCKED"].recoveryAction == "clearBlockedCells"
    assert result.failureDetails["BLOCKED"].blockingCells == [(1, 0), (2, 0)]
    assert result.failureDetails["BLOCKED"].blockingRobotIds == []


def test_dispatch_marks_unavailable_robots_in_failure_detail() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-unavailable-robot",
            "name": "temporary-unavailable-robot",
            "description": "temporary robot failure detail regression",
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
                {"id": "WAITING", "type": "inspection", "title": "WAITING", "priority": 1, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R1"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureDetails["WAITING"].reason == result.failureReasons["WAITING"]
    assert result.failureDetails["WAITING"].category == "temporary"
    assert result.failureDetails["WAITING"].recoveryAction == "restoreRobot"
    assert result.failureDetails["WAITING"].blockingCells == []
    assert result.failureDetails["WAITING"].blockingRobotIds == ["R1"]


def test_dispatch_marks_unavailable_capable_robot_as_temporary_recovery() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-capable-unavailable-robot",
            "name": "temporary-capable-unavailable-robot",
            "description": "capable failed robot recovery regression",
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
                    "id": "LOAD",
                    "type": "delivery",
                    "title": "LOAD",
                    "priority": 2,
                    "pickup": [0, 0],
                    "dropoff": [2, 0],
                    "demand": 2,
                },
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R2"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureDetails["LOAD"].category == "temporary"
    assert result.failureDetails["LOAD"].recoveryAction == "restoreRobot"
    assert result.failureDetails["LOAD"].blockingCells == []
    assert result.failureDetails["LOAD"].blockingRobotIds == ["R2"]


def test_dispatch_marks_missing_task_type_capability_as_permanent_failure() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "permanent-missing-task-type-capability",
            "name": "permanent-missing-task-type-capability",
            "description": "no robot supports the emergency task type",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {
                    "id": "R-INSPECTION",
                    "name": "R-INSPECTION",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
            ],
            "tasks": [
                {
                    "id": "EMERGENCY",
                    "type": "emergency",
                    "title": "EMERGENCY",
                    "priority": 5,
                    "target": [2, 0],
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=False),
    )

    detail = result.failureDetails["EMERGENCY"]
    assert "没有机器人兼容任务类型 emergency" in result.failureReasons["EMERGENCY"]
    assert detail.category == "permanent"
    assert detail.recoveryAction == "addCapableRobotOrChangeTaskType"
    assert detail.blockingCells == []
    assert detail.blockingRobotIds == []


def test_dispatch_only_reports_failed_type_compatible_robot_as_restore_target() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-compatible-failed-robot",
            "name": "temporary-compatible-failed-robot",
            "description": "only the failed emergency robot can execute the task",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {
                    "id": "R-CAPABLE",
                    "name": "R-CAPABLE",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
                {
                    "id": "R-INCOMPATIBLE",
                    "name": "R-INCOMPATIBLE",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
            ],
            "tasks": [
                {
                    "id": "EMERGENCY",
                    "type": "emergency",
                    "title": "EMERGENCY",
                    "priority": 5,
                    "target": [2, 0],
                },
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R-CAPABLE", "R-INCOMPATIBLE"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
    )

    detail = result.failureDetails["EMERGENCY"]
    assert detail.category == "temporary"
    assert detail.recoveryAction == "restoreRobot"
    assert detail.blockingCells == []
    assert detail.blockingRobotIds == ["R-CAPABLE"]


def test_dispatch_relaxes_lock_when_locked_robot_has_wrong_task_type() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-incompatible-task-type-lock",
            "name": "temporary-incompatible-task-type-lock",
            "description": "a reachable compatible robot can replace the incompatible locked robot",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {
                    "id": "R-INCOMPATIBLE",
                    "name": "R-INCOMPATIBLE",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R-CAPABLE",
                    "name": "R-CAPABLE",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            "tasks": [
                {
                    "id": "EMERGENCY",
                    "type": "emergency",
                    "title": "EMERGENCY",
                    "priority": 5,
                    "target": [2, 0],
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=False),
        locked_task_robot_ids={"EMERGENCY": "R-INCOMPATIBLE"},
    )

    detail = result.failureDetails["EMERGENCY"]
    assert result.failureReasons["EMERGENCY"] == (
        "任务锁定机器人 R-INCOMPATIBLE 不兼容任务类型 emergency，释放锁定后可改派"
    )
    assert detail.category == "temporary"
    assert detail.recoveryAction == "relaxLocksOrReplan"
    assert detail.blockingCells == []
    assert detail.blockingRobotIds == []


def test_dispatch_reports_failed_alternative_before_relaxing_incompatible_task_type_lock() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "failed-alternative-for-incompatible-lock",
            "name": "failed-alternative-for-incompatible-lock",
            "description": "the compatible alternative must be restored before the invalid lock can be relaxed",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {
                    "id": "R-LOCKED",
                    "name": "R-LOCKED",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R-CAPABLE",
                    "name": "R-CAPABLE",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            "tasks": [
                {
                    "id": "EMERGENCY",
                    "type": "emergency",
                    "title": "EMERGENCY",
                    "priority": 5,
                    "target": [2, 0],
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": ["R-CAPABLE"], "tasks": []},
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
        locked_task_robot_ids={"EMERGENCY": "R-LOCKED"},
    )

    detail = result.failureDetails["EMERGENCY"]
    assert result.failureReasons["EMERGENCY"] == "没有可用机器人"
    assert detail.category == "temporary"
    assert detail.recoveryAction == "restoreRobot"
    assert detail.blockingCells == []
    assert detail.blockingRobotIds == ["R-CAPABLE"]


def test_dispatch_reports_blocked_alternative_before_relaxing_insufficient_load_lock() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "blocked-alternative-for-insufficient-load-lock",
            "name": "blocked-alternative-for-insufficient-load-lock",
            "description": "the capable delivery alternative is blocked at runtime",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [[0, 0]], "inspection": [], "delivery": [[2, 0]], "charging": []},
            "robots": [
                {
                    "id": "R-LOCKED",
                    "name": "R-LOCKED",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["delivery"],
                },
                {
                    "id": "R-CAPABLE",
                    "name": "R-CAPABLE",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 2,
                    "capabilities": ["delivery"],
                },
            ],
            "tasks": [
                {
                    "id": "DELIVERY",
                    "type": "delivery",
                    "title": "DELIVERY",
                    "priority": 2,
                    "pickup": [0, 0],
                    "dropoff": [2, 0],
                    "demand": 2,
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [[1, 0]], "failedRobots": [], "tasks": []},
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
        locked_task_robot_ids={"DELIVERY": "R-LOCKED"},
    )

    detail = result.failureDetails["DELIVERY"]
    assert result.failureReasons["DELIVERY"] == "所有候选机器人到剩余目标不可达，当前动态封锁 1 个单元"
    assert detail.category == "temporary"
    assert detail.recoveryAction == "clearBlockedCells"
    assert detail.blockingCells == [(1, 0)]
    assert detail.blockingRobotIds == []


def test_dispatch_reports_static_unreachable_alternative_before_relaxing_incompatible_lock() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "static-alternative-for-incompatible-lock",
            "name": "static-alternative-for-incompatible-lock",
            "description": "the compatible alternative is separated by a fixed wall",
            "width": 3,
            "height": 2,
            "obstacles": [[1, 0], [1, 1]],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {
                    "id": "R-LOCKED",
                    "name": "R-LOCKED",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R-CAPABLE",
                    "name": "R-CAPABLE",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            "tasks": [
                {
                    "id": "EMERGENCY",
                    "type": "emergency",
                    "title": "EMERGENCY",
                    "priority": 5,
                    "target": [2, 0],
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=False),
        locked_task_robot_ids={"EMERGENCY": "R-LOCKED"},
    )

    detail = result.failureDetails["EMERGENCY"]
    assert result.failureReasons["EMERGENCY"] == "所有候选机器人到剩余目标不可达"
    assert detail.category == "permanent"
    assert detail.recoveryAction == "fixMapOrTaskTarget"
    assert detail.blockingCells == []
    assert detail.blockingRobotIds == []


def test_dispatch_does_not_relax_lock_for_battery_infeasible_active_alternative() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "battery-infeasible-active-alternative",
            "name": "battery-infeasible-active-alternative",
            "description": "the geometrically reachable alternative cannot satisfy the battery constraint",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {
                    "id": "R-LOCKED",
                    "name": "R-LOCKED",
                    "start": [0, 0],
                    "battery": 90,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R-CAPABLE",
                    "name": "R-CAPABLE",
                    "start": [0, 0],
                    "battery": 1,
                    "batteryCapacity": 1,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            "tasks": [
                {
                    "id": "EMERGENCY",
                    "type": "emergency",
                    "title": "EMERGENCY",
                    "priority": 5,
                    "target": [2, 0],
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=False),
        locked_task_robot_ids={"EMERGENCY": "R-LOCKED"},
    )

    detail = result.failureDetails["EMERGENCY"]
    assert result.failureReasons["EMERGENCY"] == "剩余电量不足且无可达充电桩"
    assert detail.category == "permanent"
    assert detail.recoveryAction == "fixMapOrTaskTarget"
    assert detail.blockingCells == []
    assert detail.blockingRobotIds == []


def test_dispatch_does_not_restore_failed_robot_when_battery_remains_infeasible() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "battery-infeasible-failed-robot",
            "name": "battery-infeasible-failed-robot",
            "description": "restoring the failed robot does not replenish its battery",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {
                    "id": "R-CAPABLE",
                    "name": "R-CAPABLE",
                    "start": [0, 0],
                    "battery": 1,
                    "batteryCapacity": 1,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
            ],
            "tasks": [
                {
                    "id": "EMERGENCY",
                    "type": "emergency",
                    "title": "EMERGENCY",
                    "priority": 5,
                    "target": [2, 0],
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": ["R-CAPABLE"], "tasks": []},
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
    )

    detail = result.failureDetails["EMERGENCY"]
    assert result.failureReasons["EMERGENCY"] == "剩余电量不足且无可达充电桩"
    assert detail.category == "permanent"
    assert detail.recoveryAction == "fixMapOrTaskTarget"
    assert detail.blockingCells == []
    assert detail.blockingRobotIds == []


def test_dispatch_marks_incapable_locked_robot_as_temporary_recovery() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-incapable-lock",
            "name": "temporary-incapable-lock",
            "description": "incapable locked robot recovery regression",
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
                    "id": "LOAD",
                    "type": "delivery",
                    "title": "LOAD",
                    "priority": 2,
                    "pickup": [0, 0],
                    "dropoff": [2, 0],
                    "demand": 2,
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

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=False),
        locked_task_robot_ids={"LOAD": "R1"},
    )

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["LOAD"] == "任务锁定机器人 R1 不满足载重 2，释放锁定后可改派"
    assert result.failureDetails["LOAD"].category == "temporary"
    assert result.failureDetails["LOAD"].recoveryAction == "relaxLocksOrReplan"
    assert result.failureDetails["LOAD"].blockingCells == []
    assert result.failureDetails["LOAD"].blockingRobotIds == []


def test_dispatch_marks_reachable_alternative_robot_as_lock_replan_recovery() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-locked-blocked-robot",
            "name": "temporary-locked-blocked-robot",
            "description": "locked blocked robot alternative recovery regression",
            "width": 4,
            "height": 2,
            "obstacles": [],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[3, 1]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
                {"id": "R2", "name": "R2", "start": [3, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "INSPECT", "type": "inspection", "title": "INSPECT", "priority": 2, "targets": [[3, 1]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [[1, 0], [0, 1]],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
        locked_task_robot_ids={"INSPECT": "R1"},
    )

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["INSPECT"] == "任务锁定机器人 R1 不可达，释放锁定后可改派"
    assert result.failureDetails["INSPECT"].category == "temporary"
    assert result.failureDetails["INSPECT"].recoveryAction == "relaxLocksOrReplan"
    assert result.failureDetails["INSPECT"].blockingCells == []
    assert result.failureDetails["INSPECT"].blockingRobotIds == []


def test_dispatch_marks_failed_locked_robot_with_active_alternative_as_lock_replan_recovery() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-failed-locked-robot-alternative",
            "name": "temporary-failed-locked-robot-alternative",
            "description": "failed locked robot active alternative recovery regression",
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
                {"id": "R2", "name": "R2", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "INSPECT", "type": "inspection", "title": "INSPECT", "priority": 2, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R1"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
        locked_task_robot_ids={"INSPECT": "R1"},
    )

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["INSPECT"] == "任务锁定机器人 R1 已不可用，释放锁定后可改派"
    assert result.failureDetails["INSPECT"].category == "temporary"
    assert result.failureDetails["INSPECT"].recoveryAction == "relaxLocksOrReplan"
    assert result.failureDetails["INSPECT"].blockingCells == []
    assert result.failureDetails["INSPECT"].blockingRobotIds == []


def test_dispatch_marks_failed_locked_robot_without_alternative_as_restore_robot_recovery() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-failed-locked-robot-only-option",
            "name": "temporary-failed-locked-robot-only-option",
            "description": "failed locked robot restore-only recovery regression",
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
                {"id": "INSPECT", "type": "inspection", "title": "INSPECT", "priority": 2, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R1"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
        locked_task_robot_ids={"INSPECT": "R1"},
    )

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["INSPECT"] == "任务锁定机器人 R1 已不可用"
    assert result.failureDetails["INSPECT"].category == "temporary"
    assert result.failureDetails["INSPECT"].recoveryAction == "restoreRobot"
    assert result.failureDetails["INSPECT"].blockingCells == []
    assert result.failureDetails["INSPECT"].blockingRobotIds == ["R1"]


def test_dispatch_keeps_failed_locked_robot_static_unreachable_reason_permanent() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "permanent-failed-locked-static-wall",
            "name": "permanent-failed-locked-static-wall",
            "description": "failed locked robot static reachability regression",
            "width": 3,
            "height": 2,
            "obstacles": [[1, 0], [1, 1]],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[2, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "INSPECT", "type": "inspection", "title": "INSPECT", "priority": 2, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R1"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
        locked_task_robot_ids={"INSPECT": "R1"},
    )

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["INSPECT"] == "所有候选机器人到剩余目标不可达"
    assert result.failureDetails["INSPECT"].reason == result.failureReasons["INSPECT"]
    assert result.failureDetails["INSPECT"].category == "permanent"
    assert result.failureDetails["INSPECT"].recoveryAction == "fixMapOrTaskTarget"
    assert result.failureDetails["INSPECT"].blockingCells == []
    assert result.failureDetails["INSPECT"].blockingRobotIds == []


def test_dispatch_keeps_failed_locked_robot_capacity_failure_permanent() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "permanent-failed-locked-capacity",
            "name": "permanent-failed-locked-capacity",
            "description": "failed locked robot capacity regression",
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
            ],
            "tasks": [
                {
                    "id": "LOAD",
                    "type": "delivery",
                    "title": "LOAD",
                    "priority": 2,
                    "pickup": [0, 0],
                    "dropoff": [2, 0],
                    "demand": 2,
                },
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R1"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(
        scenario,
        DispatchOptions(avoidConflicts=True, includeDynamic=True),
        locked_task_robot_ids={"LOAD": "R1"},
    )

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["LOAD"] == "没有可用机器人满足载重 2"
    assert result.failureDetails["LOAD"].reason == result.failureReasons["LOAD"]
    assert result.failureDetails["LOAD"].category == "permanent"
    assert result.failureDetails["LOAD"].recoveryAction == "addCapableRobotOrReduceDemand"
    assert result.failureDetails["LOAD"].blockingCells == []
    assert result.failureDetails["LOAD"].blockingRobotIds == []


def test_dispatch_marks_block_and_robot_restore_as_joint_temporary_recovery() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "temporary-joint-recovery",
            "name": "temporary-joint-recovery",
            "description": "blocked path plus failed capable robot recovery regression",
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
                "triggerTime": 0,
                "blockedCells": [[1, 0]],
                "failedRobots": ["R2"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureDetails["JOINT"].category == "temporary"
    assert result.failureDetails["JOINT"].recoveryAction == "clearBlockedCellsAndRestoreRobot"
    assert result.failureDetails["JOINT"].blockingCells == [(1, 0)]
    assert result.failureDetails["JOINT"].blockingRobotIds == ["R2"]


def test_dispatch_keeps_static_unreachable_task_permanent_despite_unrelated_block() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "permanent-static-wall",
            "name": "permanent-static-wall",
            "description": "static unreachable task classification regression",
            "width": 3,
            "height": 2,
            "obstacles": [[1, 0], [1, 1]],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[2, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "WALLED", "type": "inspection", "title": "WALLED", "priority": 1, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [[0, 1]],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["WALLED"] == "所有候选机器人到剩余目标不可达"
    assert result.failureDetails["WALLED"].reason == result.failureReasons["WALLED"]
    assert result.failureDetails["WALLED"].category == "permanent"
    assert result.failureDetails["WALLED"].recoveryAction == "fixMapOrTaskTarget"
    assert result.failureDetails["WALLED"].blockingCells == []
    assert result.failureDetails["WALLED"].blockingRobotIds == []


def test_dispatch_keeps_static_unreachable_task_permanent_when_all_robots_failed() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "permanent-static-wall-all-failed",
            "name": "permanent-static-wall-all-failed",
            "description": "static unreachable task with failed robots regression",
            "width": 3,
            "height": 2,
            "obstacles": [[1, 0], [1, 1]],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[2, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "WALLED", "type": "inspection", "title": "WALLED", "priority": 1, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": ["R1"],
                "tasks": [],
            },
        }
    )

    result = dispatch_module.run_dispatch(scenario, DispatchOptions(avoidConflicts=True, includeDynamic=True))

    assert result.metrics.assignedTaskCount == 0
    assert result.metrics.failureCount == 1
    assert result.failureReasons["WALLED"] == "所有候选机器人到剩余目标不可达"
    assert result.failureDetails["WALLED"].reason == result.failureReasons["WALLED"]
    assert result.failureDetails["WALLED"].category == "permanent"
    assert result.failureDetails["WALLED"].recoveryAction == "fixMapOrTaskTarget"
    assert result.failureDetails["WALLED"].blockingCells == []
    assert result.failureDetails["WALLED"].blockingRobotIds == []


def test_assigned_path_failure_reports_clear_blocked_cells_when_block_removal_restores_path() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "assigned-temporary-path-failure",
            "name": "assigned-temporary-path-failure",
            "description": "assigned temporary path failure detail regression",
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
                {"id": "BLOCKED-PATH", "type": "inspection", "title": "BLOCKED-PATH", "priority": 1, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    task = scenario.tasks[0]

    details = dispatch_module.build_failure_details(
        scenario,
        scenario.tasks,
        [Assignment(robotId="R1", tasks=[task])],
        {"R1": [scenario.robots[0].start]},
        [(1, 0)],
        [],
        {},
    )

    assert details["BLOCKED-PATH"].category == "temporary"
    assert details["BLOCKED-PATH"].recoveryAction == "clearBlockedCells"
    assert details["BLOCKED-PATH"].blockingCells == [(1, 0)]
    assert details["BLOCKED-PATH"].blockingRobotIds == []


def test_assigned_path_failure_keeps_static_unreachable_detail_permanent() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "assigned-permanent-path-failure",
            "name": "assigned-permanent-path-failure",
            "description": "assigned permanent path failure detail regression",
            "width": 3,
            "height": 2,
            "obstacles": [[1, 0], [1, 1]],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[2, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "WALLED-PATH", "type": "inspection", "title": "WALLED-PATH", "priority": 1, "targets": [[2, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    task = scenario.tasks[0]

    details = dispatch_module.build_failure_details(
        scenario,
        scenario.tasks,
        [Assignment(robotId="R1", tasks=[task])],
        {"R1": [scenario.robots[0].start]},
        [(0, 1)],
        [],
        {},
    )

    assert details["WALLED-PATH"].category == "permanent"
    assert details["WALLED-PATH"].recoveryAction == "fixMapOrTaskTarget"
    assert details["WALLED-PATH"].blockingCells == []
    assert details["WALLED-PATH"].blockingRobotIds == []


def test_path_planning_prioritizes_high_priority_bottleneck_task() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "bottleneck-priority",
            "name": "bottleneck-priority",
            "description": "path planning priority regression",
            "width": 5,
            "height": 3,
            "obstacles": [[0, 0], [0, 2], [1, 2], [2, 0], [2, 2], [3, 2], [4, 0], [4, 2]],
            "zones": {
                "warehouse": [[0, 1], [1, 0]],
                "inspection": [[4, 1], [3, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 1], "battery": 90, "load": 1},
                {"id": "R2", "name": "R2", "start": [1, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "LOW", "type": "inspection", "title": "LOW", "priority": 1, "targets": [[4, 1]]},
                {"id": "HIGH", "type": "inspection", "title": "HIGH", "priority": 5, "targets": [[3, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    tasks = {task.id: task for task in scenario.tasks}
    assignments = [
        Assignment(robotId="R1", tasks=[tasks["LOW"]]),
        Assignment(robotId="R2", tasks=[tasks["HIGH"]]),
    ]

    paths, failures = build_paths(
        scenario,
        scenario.robots,
        assignments,
        True,
        [],
        [],
    )
    completions = task_completion_times(assignments, paths)

    assert failures == []
    assert completions["HIGH"] == 4
    assert completions["LOW"] > completions["HIGH"]


def test_path_planning_keeps_robot_at_task_endpoint_for_service_time() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "service-time-path",
            "name": "service-time-path",
            "description": "service time path regression",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [[0, 0]], "inspection": [[1, 0], [2, 0]], "delivery": []},
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {
                    "id": "T1",
                    "type": "inspection",
                    "title": "需要作业的巡检",
                    "priority": 2,
                    "targets": [[1, 0]],
                    "serviceTime": 2,
                },
                {
                    "id": "T2",
                    "type": "inspection",
                    "title": "后续巡检",
                    "priority": 1,
                    "targets": [[2, 0]],
                },
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )
    tasks = {task.id: task for task in scenario.tasks}
    assignments = [Assignment(robotId="R1", tasks=[tasks["T1"], tasks["T2"]])]

    paths, failures = build_paths(scenario, scenario.robots, assignments, True, [], [])
    completions = task_completion_times(assignments, paths)

    assert failures == []
    assert paths["R1"] == [(0, 0), (1, 0), (1, 0), (1, 0), (2, 0)]
    assert completions == {"T1": 3, "T2": 4}


def test_path_planning_portfolio_selects_lower_makespan_order() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "portfolio-bottleneck",
            "name": "portfolio-bottleneck",
            "description": "path planning portfolio regression",
            "width": 5,
            "height": 3,
            "obstacles": [[1, 0], [1, 2], [2, 0], [2, 2], [3, 0], [3, 2]],
            "zones": {
                "warehouse": [],
                "inspection": [[4, 0], [4, 1], [4, 2]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
                {"id": "R2", "name": "R2", "start": [0, 1], "battery": 90, "load": 1},
                {"id": "R3", "name": "R3", "start": [0, 2], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[4, 0]]},
                {"id": "T2", "type": "inspection", "title": "T2", "priority": 2, "targets": [[4, 1]]},
                {"id": "T3", "type": "inspection", "title": "T3", "priority": 3, "targets": [[4, 2]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    tasks = {task.id: task for task in scenario.tasks}
    assignments = [
        Assignment(robotId="R1", tasks=[tasks["T1"]]),
        Assignment(robotId="R2", tasks=[tasks["T2"]]),
        Assignment(robotId="R3", tasks=[tasks["T3"]]),
    ]

    priority_order = [scenario.robots[2], scenario.robots[1], scenario.robots[0]]
    priority_candidate = build_paths_for_order(
        scenario,
        scenario.robots,
        assignments,
        True,
        [],
        [],
        priority_order,
    )
    paths, failures = build_paths(scenario, scenario.robots, assignments, True, [], [])
    makespan = max(len(path) - 1 for path in paths.values())

    assert priority_candidate.failures == ["R1 存在不可达任务"]
    assert path_planning_candidate_score(priority_candidate, assignments)[0] == 1
    assert failures == []
    assert makespan == 7


def test_path_planning_reserves_final_cells_beyond_short_padding_window() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "late-final-cell-reservation",
            "name": "late-final-cell-reservation",
            "description": "late final-cell conflict avoidance regression",
            "width": 20,
            "height": 1,
            "obstacles": [],
            "zones": {
                "warehouse": [[0, 0], [19, 0]],
                "inspection": [[2, 0], [0, 0]],
                "delivery": [],
            },
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
                {"id": "R2", "name": "R2", "start": [19, 0], "battery": 90, "load": 1},
            ],
            "tasks": [
                {"id": "STOP", "type": "inspection", "title": "STOP", "priority": 1, "targets": [[2, 0]]},
                {"id": "PASS", "type": "inspection", "title": "PASS", "priority": 1, "targets": [[0, 0]]},
            ],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    tasks = {task.id: task for task in scenario.tasks}
    assignments = [
        Assignment(robotId="R1", tasks=[tasks["STOP"]]),
        Assignment(robotId="R2", tasks=[tasks["PASS"]]),
    ]

    candidate = build_paths_for_order(
        scenario,
        scenario.robots,
        assignments,
        True,
        [],
        [],
        scenario.robots,
    )

    assert candidate.failures == ["R2 存在不可达任务"]
    assert dispatch_module.detect_conflicts(candidate.paths) == []


def test_task_distance_reuses_segment_cache(monkeypatch) -> None:
    scenario = Scenario.model_validate(scenario_payload())
    task = next(task for task in scenario.tasks if task.id == "T2")
    original_astar = dispatch_module.astar
    astar_call_count = 0

    def counting_astar(*args, **kwargs):
        nonlocal astar_call_count
        astar_call_count += 1
        return original_astar(*args, **kwargs)

    monkeypatch.setattr(dispatch_module, "astar", counting_astar)
    distance_cache = {}

    first_distance = dispatch_module.task_distance(scenario, (0, 0), task, [], distance_cache)
    first_call_count = astar_call_count
    second_distance = dispatch_module.task_distance(scenario, (0, 0), task, [], distance_cache)

    assert first_distance == second_distance
    assert first_call_count == 2
    assert astar_call_count == first_call_count

def test_release_time_delays_path_execution() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["tasks"] = [
        {
            "id": "T1",
            "type": "inspection",
            "title": "延迟释放巡检",
            "priority": 3,
            "releaseTime": 4,
            "deadline": 10,
            "targets": [[1, 0]],
        }
    ]
    scenario["dynamic"]["tasks"] = []
    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    robot_id = payload["assignments"][0]["robotId"]
    path = payload["paths"][robot_id]
    assert path[0:5] == [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0]]
    assert payload["metrics"]["deadlineMissCount"] == 0

def test_deadline_metrics_report_late_tasks() -> None:
    client = TestClient(app)
    scenario = scenario_payload()
    scenario["tasks"] = [
        {
            "id": "T1",
            "type": "inspection",
            "title": "必然超期巡检",
            "priority": 3,
            "deadline": 0,
            "targets": [[5, 0]],
        }
    ]
    scenario["dynamic"]["tasks"] = []
    response = client.post(
        "/api/dispatch",
        json={
            "scenario": scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metrics"]["deadlineMissCount"] == 1
    assert payload["metrics"]["averageLateness"] > 0


def test_dispatch_charges_low_battery_robot_before_task() -> None:
    client = TestClient(app)
    scenario = {
        "id": "charging-before-task",
        "name": "charging-before-task",
        "description": "low battery robot must charge before assignment",
        "width": 4,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [], "inspection": [[3, 0]], "delivery": [], "charging": [[0, 0]]},
        "chargeTime": 2,
        "robots": [
            {"id": "R1", "name": "R1", "start": [1, 0], "battery": 1, "batteryCapacity": 8, "load": 1},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "inspection", "priority": 1, "targets": [[3, 0]]},
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }

    response = client.post("/api/dispatch", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}})

    assert response.status_code == 200
    payload = response.json()
    assert payload["assignments"][0]["robotId"] == "R1"
    assert payload["assignments"][0]["tasks"][0]["id"] == "T1"
    assert payload["chargingVisits"] == [
        {"robotId": "R1", "station": [0, 0], "departureTime": 0, "arrivalTime": 1, "completionTime": 3}
    ]


def test_dispatch_explains_when_battery_capacity_cannot_finish_and_return() -> None:
    client = TestClient(app)
    scenario = {
        "id": "charge-capacity-failure", "name": "charge-capacity-failure", "description": "capacity", "width": 5, "height": 1,
        "obstacles": [], "zones": {"warehouse": [], "inspection": [[4, 0]], "delivery": [], "charging": [[0, 0]]},
        "robots": [{"id": "R1", "name": "R1", "start": [1, 0], "battery": 5, "batteryCapacity": 5, "load": 1}],
        "tasks": [{"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[4, 0]]}],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    response = client.post("/api/dispatch", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}})
    assert response.status_code == 200
    assert "电池容量不足" in response.json()["failureReasons"]["T1"]


def test_charging_station_reservations_avoid_conflicts_for_two_robots() -> None:
    client = TestClient(app)
    scenario = {
        "id": "shared-charger", "name": "shared-charger", "description": "shared", "width": 5, "height": 2,
        "obstacles": [], "zones": {"warehouse": [], "inspection": [], "delivery": [[4, 0], [4, 1]], "charging": [[0, 0]]},
        "chargeTime": 2,
        "robots": [
            {"id": "R1", "name": "R1", "start": [1, 0], "battery": 1, "batteryCapacity": 8, "load": 1},
            {"id": "R2", "name": "R2", "start": [1, 1], "battery": 2, "batteryCapacity": 10, "load": 2},
        ],
        "tasks": [
            {"id": "T1", "type": "delivery", "title": "T1", "priority": 2, "pickup": [3, 0], "dropoff": [4, 0], "demand": 1},
            {"id": "T2", "type": "delivery", "title": "T2", "priority": 1, "pickup": [3, 1], "dropoff": [4, 1], "demand": 2},
        ], "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    response = client.post("/api/dispatch", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["chargingVisits"]) == 2
    assert payload["conflicts"] == []


def test_dynamic_block_reroutes_low_battery_robot_to_alternate_charger() -> None:
    client = TestClient(app)
    scenario = {
        "id": "alternate-charger", "name": "alternate-charger", "description": "dynamic charging route", "width": 5, "height": 2,
        "obstacles": [], "zones": {"warehouse": [], "inspection": [[4, 0]], "delivery": [], "charging": [[0, 0], [0, 1]]},
        "chargeTime": 2,
        "robots": [{"id": "R1", "name": "R1", "start": [2, 0], "battery": 3, "batteryCapacity": 10, "load": 1}],
        "tasks": [{"id": "T1", "type": "inspection", "title": "T1", "priority": 1, "targets": [[4, 0]]}],
        "dynamic": {"triggerTime": 0, "blockedCells": [[1, 0]], "failedRobots": [], "tasks": []},
    }

    response = client.post("/api/dispatch", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": True}})

    assert response.status_code == 200
    payload = response.json()
    assert payload["assignments"][0]["tasks"][0]["id"] == "T1"
    assert payload["chargingVisits"] == [
        {"robotId": "R1", "station": [0, 1], "departureTime": 0, "arrivalTime": 3, "completionTime": 5}
    ]
    assert payload["conflicts"] == []


def test_dispatch_assigns_each_task_type_only_to_capable_robot() -> None:
    client = TestClient(app)
    scenario = {
        "id": "task-capability-assignment",
        "name": "task-capability-assignment",
        "description": "每种任务都应由具备对应能力的机器人执行。",
        "width": 7,
        "height": 3,
        "obstacles": [],
        "zones": {
            "warehouse": [[6, 1]],
            "inspection": [[6, 0]],
            "delivery": [[5, 1]],
            "charging": [],
        },
        "robots": [
            {
                "id": "R-INSPECTION",
                "name": "巡检机器人",
                "start": [6, 2],
                "battery": 100,
                "load": 2,
                "capabilities": ["inspection"],
            },
            {
                "id": "R-DELIVERY",
                "name": "配送机器人",
                "start": [6, 0],
                "battery": 100,
                "load": 2,
                "capabilities": ["delivery"],
            },
            {
                "id": "R-EMERGENCY",
                "name": "应急机器人",
                "start": [6, 1],
                "battery": 100,
                "load": 2,
                "capabilities": ["emergency"],
            },
        ],
        "tasks": [
            {
                "id": "I1",
                "type": "inspection",
                "title": "巡检任务",
                "priority": 1,
                "targets": [[6, 0]],
            },
            {
                "id": "D1",
                "type": "delivery",
                "title": "配送任务",
                "priority": 2,
                "pickup": [6, 1],
                "dropoff": [5, 1],
                "demand": 2,
            },
            {
                "id": "E1",
                "type": "emergency",
                "title": "应急任务",
                "priority": 3,
                "target": [6, 2],
            },
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }

    response = client.post(
        "/api/dispatch",
        json={"scenario": scenario, "options": {"avoidConflicts": False, "includeDynamic": False}},
    )

    assert response.status_code == 200
    assigned_robot_by_task = {
        task["id"]: assignment["robotId"]
        for assignment in response.json()["assignments"]
        for task in assignment["tasks"]
    }
    assert assigned_robot_by_task["I1"] == "R-INSPECTION"
    assert assigned_robot_by_task["D1"] == "R-DELIVERY"
    assert assigned_robot_by_task["E1"] == "R-EMERGENCY"


def test_dispatch_delivery_requires_capability_and_sufficient_load() -> None:
    client = TestClient(app)
    scenario = {
        "id": "delivery-capability-and-load",
        "name": "delivery-capability-and-load",
        "description": "配送任务同时要求配送能力和足够载重。",
        "width": 6,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[4, 0]],
            "inspection": [],
            "delivery": [[5, 0]],
            "charging": [],
        },
        "robots": [
            {
                "id": "R-NO-DELIVERY",
                "name": "高载重巡检机器人",
                "start": [4, 0],
                "battery": 100,
                "load": 3,
                "capabilities": ["inspection"],
            },
            {
                "id": "R-LOW-LOAD",
                "name": "低载重配送机器人",
                "start": [3, 0],
                "battery": 100,
                "load": 1,
                "capabilities": ["delivery"],
            },
            {
                "id": "R-VALID",
                "name": "合格配送机器人",
                "start": [0, 0],
                "battery": 100,
                "load": 2,
                "capabilities": ["delivery"],
            },
        ],
        "tasks": [
            {
                "id": "D1",
                "type": "delivery",
                "title": "重载配送",
                "priority": 2,
                "pickup": [4, 0],
                "dropoff": [5, 0],
                "demand": 2,
            }
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }

    response = client.post(
        "/api/dispatch",
        json={"scenario": scenario, "options": {"avoidConflicts": False, "includeDynamic": False}},
    )

    assert response.status_code == 200
    assignment = next(item for item in response.json()["assignments"] if item["tasks"])
    assert assignment["robotId"] == "R-VALID"
    assert assignment["tasks"][0]["id"] == "D1"
