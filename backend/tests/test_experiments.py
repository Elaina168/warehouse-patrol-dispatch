from fastapi.testclient import TestClient

import backend.app.experiments as experiments_module
import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.app.limits import MAX_EXPERIMENT_CASES
from backend.app.schemas import Scenario
from backend.tests.helpers import frontend_demo_scenario


def crossing_delivery_scenario() -> dict:
    return {
        "id": "crossing-delivery",
        "name": "crossing-delivery",
        "description": "two robots crossing in one aisle with a bypass row",
        "width": 5,
        "height": 2,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0], [4, 0]],
            "inspection": [],
            "delivery": [[0, 0], [4, 0]],
        },
        "robots": [
            {"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1},
            {"id": "R2", "name": "R2", "start": [4, 0], "battery": 90, "load": 1},
        ],
        "tasks": [
            {
                "id": "D1",
                "type": "delivery",
                "title": "D1",
                "priority": 2,
                "pickup": [0, 0],
                "dropoff": [4, 0],
                "demand": 1,
            },
            {
                "id": "D2",
                "type": "delivery",
                "title": "D2",
                "priority": 2,
                "pickup": [4, 0],
                "dropoff": [0, 0],
                "demand": 1,
            },
        ],
        "dynamic": {
            "triggerTime": 0,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }


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


def forced_online_experiment_conflict_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "forced-online-experiment-conflict",
            "name": "forced-online-experiment-conflict",
            "description": "实验会话保留冲突执行语义",
            "width": 4,
            "height": 10,
            "obstacles": [[x, y] for y in range(1, 9) for x in range(4)],
            "zones": {
                "warehouse": [],
                "inspection": [[2, 9]],
                "delivery": [],
                "charging": [],
            },
            "robots": [
                {
                    "id": "R1",
                    "name": "R1",
                    "start": [0, 0],
                    "battery": 90,
                    "batteryCapacity": 100,
                    "load": 1,
                    "capabilities": ["inspection"],
                },
                {
                    "id": "R2",
                    "name": "R2",
                    "start": [2, 0],
                    "battery": 90,
                    "batteryCapacity": 100,
                    "load": 1,
                    "capabilities": ["emergency"],
                },
                {
                    "id": "R3",
                    "name": "R3",
                    "start": [0, 9],
                    "battery": 90,
                    "batteryCapacity": 100,
                    "load": 1,
                },
                {
                    "id": "R4",
                    "name": "R4",
                    "start": [1, 9],
                    "battery": 90,
                    "batteryCapacity": 100,
                    "load": 1,
                },
            ],
            "tasks": [
                {
                    "id": "T1",
                    "type": "inspection",
                    "title": "R1 保持左端",
                    "priority": 2,
                    "targets": [[2, 0]],
                },
                {
                    "id": "T2",
                    "type": "emergency",
                    "title": "R2 前往左端",
                    "priority": 4,
                    "target": [0, 0],
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


def test_invalid_experiment_batch_limits_and_duplicate_windows_never_run_dispatch(
    monkeypatch,
) -> None:
    client = TestClient(app)
    scenario = crossing_delivery_scenario()
    calls: list[tuple] = []

    def forbidden_run_dispatch(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("run_dispatch must not receive an invalid experiment request")

    monkeypatch.setattr(experiments_module, "run_dispatch", forbidden_run_dispatch)
    excessive_cases = [
        {"label": f"case-{index}", "scenario": scenario}
        for index in range(MAX_EXPERIMENT_CASES + 1)
    ]
    requests = [
        ("/api/experiments/replan-window", {"scenario": scenario, "windows": []}),
        (
            "/api/experiments/replan-window",
            {"scenario": scenario, "windows": [4, 4]},
        ),
        (
            "/api/experiments/replan-window",
            {
                "scenario": scenario,
                "windows": list(range(MAX_EXPERIMENT_CASES + 1)),
            },
        ),
        ("/api/experiments/scale", {"cases": []}),
        ("/api/experiments/scale", {"cases": excessive_cases}),
    ]

    for path, body in requests:
        response = client.post(path, json=body)
        assert response.status_code == 422
        assert calls == []


def test_conflict_avoidance_experiment_returns_baseline_and_avoidance_cases() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/conflict-avoidance",
        json={
            "scenario": crossing_delivery_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}

    assert payload["scenarioId"] == "crossing-delivery"
    assert set(cases) == {"withoutConflictAvoidance", "withConflictAvoidance"}
    assert cases["withoutConflictAvoidance"]["options"]["avoidConflicts"] is False
    assert cases["withConflictAvoidance"]["options"]["avoidConflicts"] is True
    assert cases["withoutConflictAvoidance"]["result"]["metrics"]["conflictCount"] > 0
    assert cases["withConflictAvoidance"]["result"]["metrics"]["conflictCount"] == 0
    assert cases["withoutConflictAvoidance"]["result"]["metrics"]["assignedTaskCount"] == 2
    assert cases["withConflictAvoidance"]["result"]["metrics"]["assignedTaskCount"] == 2


def test_conflict_avoidance_experiment_uses_integrated_demo_scenario() -> None:
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
    without_task_ids = {task["id"] for task in without_avoidance["tasks"]}
    with_task_ids = {task["id"] for task in with_avoidance["tasks"]}
    without_assigned_ids = {
        task["id"] for assignment in without_avoidance["assignments"] for task in assignment["tasks"]
    }
    with_assigned_ids = {task["id"] for assignment in with_avoidance["assignments"] for task in assignment["tasks"]}

    assert payload["scenarioId"] == "integrated-demo"
    assert without_avoidance["dynamicTriggerTime"] is None
    assert with_avoidance["dynamicTriggerTime"] is None
    assert without_task_ids == {"T1", "T2", "T3", "T4", "T5", "E1"}
    assert with_task_ids == without_task_ids
    assert without_assigned_ids == without_task_ids
    assert with_assigned_ids == without_task_ids
    assert without_avoidance["metrics"]["assignedTaskCount"] == 6
    assert with_avoidance["metrics"]["assignedTaskCount"] == 6
    assert without_avoidance["metrics"]["conflictCount"] > 0
    assert with_avoidance["metrics"]["conflictCount"] == 0
    assert len(without_avoidance["conflicts"]) == without_avoidance["metrics"]["conflictCount"]
    assert with_avoidance["conflicts"] == []
    assert without_avoidance["metrics"]["failureCount"] == 0
    assert with_avoidance["metrics"]["failureCount"] == 0


def dynamic_replanning_scenario() -> dict:
    scenario = crossing_delivery_scenario()
    scenario["id"] = "dynamic-replanning"
    scenario["name"] = "dynamic-replanning"
    scenario["description"] = "dynamic emergency task comparison"
    scenario["zones"]["inspection"] = [[2, 1]]
    scenario["dynamic"] = {
        "triggerTime": 3,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [
            {
                "id": "E-DYN",
                "type": "emergency",
                "title": "E-DYN",
                "priority": 5,
                "target": [2, 1],
            }
        ],
    }
    return scenario


def test_dynamic_replanning_experiment_returns_static_and_dynamic_cases() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/dynamic-replanning",
        json={
            "scenario": dynamic_replanning_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}

    assert payload["scenarioId"] == "dynamic-replanning"
    assert set(cases) == {"withoutDynamicReplanning", "withDynamicReplanning"}
    assert cases["withoutDynamicReplanning"]["options"]["includeDynamic"] is False
    assert cases["withDynamicReplanning"]["options"]["includeDynamic"] is True
    without_task_ids = {task["id"] for task in cases["withoutDynamicReplanning"]["result"]["tasks"]}
    with_task_ids = {task["id"] for task in cases["withDynamicReplanning"]["result"]["tasks"]}
    assert "E-DYN" not in without_task_ids
    assert "E-DYN" in with_task_ids
    assert cases["withoutDynamicReplanning"]["result"]["dynamicTriggerTime"] is None
    assert cases["withDynamicReplanning"]["result"]["dynamicTriggerTime"] == 3
    assert cases["withDynamicReplanning"]["result"]["metrics"]["assignedTaskCount"] > cases[
        "withoutDynamicReplanning"
    ]["result"]["metrics"]["assignedTaskCount"]


def test_dynamic_replanning_experiment_uses_integrated_demo_variant() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/dynamic-replanning",
        json={
            "scenario": integrated_dynamic_event_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}
    without_dynamic = cases["withoutDynamicReplanning"]["result"]
    with_dynamic = cases["withDynamicReplanning"]["result"]
    without_task_ids = {task["id"] for task in without_dynamic["tasks"]}
    with_task_ids = {task["id"] for task in with_dynamic["tasks"]}
    without_assigned_ids = {
        task["id"] for assignment in without_dynamic["assignments"] for task in assignment["tasks"]
    }
    with_assigned_ids = {task["id"] for assignment in with_dynamic["assignments"] for task in assignment["tasks"]}

    assert payload["scenarioId"] == "integrated-demo"
    assert without_dynamic["dynamicTriggerTime"] is None
    assert with_dynamic["dynamicTriggerTime"] == 12
    assert "E1" not in without_task_ids
    assert "E1" in with_task_ids
    assert "E1" not in without_assigned_ids
    assert "E1" in with_assigned_ids
    assert without_dynamic["metrics"]["assignedTaskCount"] == 5
    assert with_dynamic["metrics"]["assignedTaskCount"] == 6
    assert without_dynamic["metrics"]["conflictCount"] == 0
    assert with_dynamic["metrics"]["conflictCount"] == 0
    assert without_dynamic["metrics"]["failureCount"] == 0
    assert with_dynamic["metrics"]["failureCount"] == 0
    assert without_dynamic["metrics"]["totalDistance"] > 0
    assert with_dynamic["metrics"]["totalDistance"] > 0


def replan_window_scenario() -> dict:
    scenario = crossing_delivery_scenario()
    scenario["id"] = "replan-window"
    scenario["name"] = "replan-window"
    scenario["description"] = "rolling assignment window comparison"
    scenario["zones"]["inspection"] = [[1, 1], [2, 1], [3, 1]]
    scenario["tasks"].append(
        {
            "id": "FUTURE-12",
            "type": "inspection",
            "title": "FUTURE-12",
            "priority": 1,
            "releaseTime": 12,
            "deadline": 40,
            "targets": [[2, 1]],
        }
    )
    return scenario


def test_replan_window_experiment_returns_one_case_per_window() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/replan-window",
        json={
            "scenario": replan_window_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
            "windows": [4, 24],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}

    assert payload["scenarioId"] == "replan-window"
    assert set(cases) == {"window-4", "window-24"}
    assert cases["window-4"]["options"]["assignmentReplanWindow"] == 4
    assert cases["window-24"]["options"]["assignmentReplanWindow"] == 24
    window_4_task_ids = {
        task["id"]
        for assignment in cases["window-4"]["result"]["assignments"]
        for task in assignment["tasks"]
    }
    window_24_task_ids = {
        task["id"]
        for assignment in cases["window-24"]["result"]["assignments"]
        for task in assignment["tasks"]
    }
    assert "FUTURE-12" not in window_4_task_ids
    assert "FUTURE-12" in window_24_task_ids
    assert cases["window-4"]["result"]["metrics"]["assignedTaskCount"] < cases["window-24"]["result"]["metrics"][
        "assignedTaskCount"
    ]


def test_replan_window_experiment_can_include_adaptive_case() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/replan-window",
        json={
            "scenario": replan_window_scenario(),
            "options": {
                "avoidConflicts": True,
                "includeDynamic": False,
                "assignmentReplanWindow": 4,
            },
            "windows": [4],
            "includeAdaptive": True,
        },
    )

    assert response.status_code == 200
    cases = {case["label"]: case for case in response.json()["cases"]}
    assert set(cases) == {"window-4", "adaptive-window-4"}
    assert cases["window-4"]["options"]["adaptiveReplanWindow"] is False
    adaptive = cases["adaptive-window-4"]
    assert adaptive["options"]["adaptiveReplanWindow"] is True
    assert adaptive["result"]["effectiveAssignmentReplanWindow"] == 4
    assert adaptive["result"]["replanWindowReason"] == "当前负载适中，保持基准窗口"


def test_replan_window_experiment_uses_integrated_demo_task_timing() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/replan-window",
        json={
            "scenario": frontend_demo_scenario("integrated-demo"),
            "options": {"avoidConflicts": True, "includeDynamic": True},
            "windows": [4, 24],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}
    window_4 = cases["window-4"]["result"]
    window_24 = cases["window-24"]["result"]
    window_4_assigned_ids = {task["id"] for assignment in window_4["assignments"] for task in assignment["tasks"]}
    window_24_assigned_ids = {task["id"] for assignment in window_24["assignments"] for task in assignment["tasks"]}

    assert payload["scenarioId"] == "integrated-demo"
    assert window_4["dynamicTriggerTime"] is None
    assert window_24["dynamicTriggerTime"] is None
    assert "E1" in {task["id"] for task in window_4["tasks"]}
    assert "E1" not in window_4_assigned_ids
    assert "E1" in window_24_assigned_ids
    assert window_4["metrics"]["assignedTaskCount"] == 4
    assert window_24["metrics"]["assignedTaskCount"] == 6
    assert window_4["metrics"]["conflictCount"] == 0
    assert window_24["metrics"]["conflictCount"] == 0
    assert window_4["metrics"]["failureCount"] == 0
    assert window_24["metrics"]["failureCount"] == 0


def scaled_scenario(label: str, robot_count: int, task_count: int) -> dict:
    width = 8
    robots = [
        {
            "id": f"R{index + 1}",
            "name": f"R{index + 1}",
            "start": [0, index],
            "battery": 90,
            "load": 1,
        }
        for index in range(robot_count)
    ]
    inspection_cells = [[width - 1, index] for index in range(task_count)]
    return {
        "id": f"scale-{label}",
        "name": f"scale-{label}",
        "description": f"{robot_count} robots and {task_count} tasks",
        "width": width,
        "height": max(robot_count, task_count),
        "obstacles": [],
        "zones": {
            "warehouse": [robot["start"] for robot in robots],
            "inspection": inspection_cells,
            "delivery": [],
        },
        "robots": robots,
        "tasks": [
            {
                "id": f"T{index + 1}",
                "type": "inspection",
                "title": f"T{index + 1}",
                "priority": 1 + (index % 3),
                "releaseTime": 0,
                "deadline": 40,
                "targets": [cell],
            }
            for index, cell in enumerate(inspection_cells)
        ],
        "dynamic": {
            "triggerTime": 0,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }


def capability_scale_scenario(label: str) -> dict:
    scenario = {
        "id": label,
        "name": label,
        "description": "机器人任务类型能力规模对比。",
        "width": 8,
        "height": 3,
        "obstacles": [],
        "zones": {
            "warehouse": [[7, 1]],
            "inspection": [[7, 0]],
            "delivery": [[6, 1]],
            "charging": [],
        },
        "robots": [
            {
                "id": "R-INSPECTION",
                "name": "巡检机器人",
                "start": [0, 0],
                "battery": 100,
                "load": 1,
                "capabilities": ["inspection", "delivery", "emergency"],
            },
            {
                "id": "R-DELIVERY",
                "name": "取送机器人",
                "start": [0, 1],
                "battery": 100,
                "load": 2,
                "capabilities": ["inspection", "delivery", "emergency"],
            },
            {
                "id": "R-EMERGENCY",
                "name": "突发机器人",
                "start": [0, 2],
                "battery": 100,
                "load": 1,
                "capabilities": ["inspection", "delivery", "emergency"],
            },
        ],
        "tasks": [
            {
                "id": "I1",
                "type": "inspection",
                "title": "巡检任务",
                "priority": 1,
                "releaseTime": 0,
                "deadline": 40,
                "targets": [[7, 0]],
            },
            {
                "id": "D1",
                "type": "delivery",
                "title": "重载取送任务",
                "priority": 2,
                "releaseTime": 0,
                "deadline": 40,
                "pickup": [7, 1],
                "dropoff": [6, 1],
                "demand": 2,
            },
            {
                "id": "E1",
                "type": "emergency",
                "title": "突发任务",
                "priority": 4,
                "releaseTime": 0,
                "deadline": 40,
                "target": [7, 2],
            },
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    if label == "specialized-fleet":
        for robot, capability in zip(
            scenario["robots"],
            ("inspection", "delivery", "emergency"),
            strict=True,
        ):
            robot["capabilities"] = [capability]
    return scenario


def assert_assignments_respect_capabilities(case: dict, scenario: dict) -> None:
    robot_by_id = {robot["id"]: robot for robot in scenario["robots"]}
    for assignment in case["result"]["assignments"]:
        robot = robot_by_id[assignment["robotId"]]
        for task in assignment["tasks"]:
            assert task["type"] in robot["capabilities"]
            if task["type"] == "delivery":
                assert robot["load"] >= task["demand"]


def test_scale_experiment_compares_homogeneous_and_specialized_capability_fleets() -> None:
    client = TestClient(app)
    homogeneous = capability_scale_scenario("homogeneous-fleet")
    specialized = capability_scale_scenario("specialized-fleet")

    assert {robot["id"]: robot["capabilities"] for robot in specialized["robots"]} == {
        "R-INSPECTION": ["inspection"],
        "R-DELIVERY": ["delivery"],
        "R-EMERGENCY": ["emergency"],
    }

    response = client.post(
        "/api/experiments/scale",
        json={
            "cases": [
                {"label": "homogeneous-fleet", "scenario": homogeneous},
                {"label": "specialized-fleet", "scenario": specialized},
            ],
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    cases = {case["label"]: case for case in response.json()["cases"]}
    assert set(cases) == {"homogeneous-fleet", "specialized-fleet"}
    for label, scenario in (("homogeneous-fleet", homogeneous), ("specialized-fleet", specialized)):
        case = cases[label]
        assert case["result"]["metrics"]["assignedTaskCount"] == 3
        assert case["result"]["metrics"]["conflictCount"] == 0
        assert case["result"]["metrics"]["failureCount"] == 0
        assert case["result"]["metrics"]["deadlineMissCount"] == 0
        assert case["result"]["chargingVisits"] == []
        assert_assignments_respect_capabilities(case, scenario)


def test_scale_experiment_returns_one_case_per_supplied_scenario() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/scale",
        json={
            "cases": [
                {"label": "small", "scenario": scaled_scenario("small", robot_count=2, task_count=2)},
                {"label": "medium", "scenario": scaled_scenario("medium", robot_count=4, task_count=4)},
            ],
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}

    assert set(cases) == {"small", "medium"}
    assert cases["small"]["scenarioId"] == "scale-small"
    assert cases["medium"]["scenarioId"] == "scale-medium"
    assert cases["small"]["options"]["avoidConflicts"] is True
    assert cases["medium"]["options"]["includeDynamic"] is False
    assert cases["small"]["result"]["metrics"]["assignedTaskCount"] == 2
    assert cases["medium"]["result"]["metrics"]["assignedTaskCount"] == 4
    assert cases["medium"]["result"]["metrics"]["totalDistance"] >= cases["small"]["result"]["metrics"][
        "totalDistance"
    ]


def test_scale_experiment_accepts_integrated_demo_scenario() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/scale",
        json={
            "cases": [
                {"label": "integrated-demo", "scenario": frontend_demo_scenario("integrated-demo")}
            ],
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}

    assert list(cases) == ["integrated-demo"]
    result = cases["integrated-demo"]["result"]
    assert cases["integrated-demo"]["scenarioId"] == "integrated-demo"
    assert cases["integrated-demo"]["options"]["avoidConflicts"] is True
    assert cases["integrated-demo"]["options"]["includeDynamic"] is False
    assert result["dynamicTriggerTime"] is None
    assert result["metrics"]["assignedTaskCount"] == len(result["tasks"])
    assert result["metrics"]["failureCount"] == 0
    assert result["failureDetails"] == {}


def test_seeded_pressure_experiment_returns_compact_performance_cases() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/seeded-pressure",
        json={"options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 120}},
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}
    summary = payload["summary"]

    assert list(cases) == ["seed-17", "seed-29", "seed-31"]
    assert summary["caseCount"] == 3
    assert summary["largestRobotCount"] == 8
    assert summary["largestTaskCount"] == 27
    assert summary["totalTaskCount"] == 65
    assert summary["totalAssignedTaskCount"] == 65
    assert summary["stableCaseCount"] == 3
    assert summary["stableRatePercent"] == 100
    assert summary["assignmentRatePercent"] == 100
    assert "completionRatePercent" not in summary
    assert summary["planningTimeBudgetMs"] == 2000
    assert summary["maxConflictCount"] == 0
    assert summary["totalDeadlineMissCount"] == 0
    assert summary["totalFailureCount"] == 0
    assert summary["totalDistance"] == sum(case["totalDistance"] for case in cases.values())
    assert summary["maxMakespan"] == max(case["makespan"] for case in cases.values())
    assert summary["averageDistancePerTask"] == round(summary["totalDistance"] / summary["totalTaskCount"], 1)
    assert isinstance(summary["maxReplanTimeMs"], float)
    assert summary["maxReplanTimeMs"] >= 0
    assert cases["seed-17"]["seed"] == 17
    assert cases["seed-29"]["robotCount"] == 6
    assert cases["seed-31"]["robotCount"] == 8
    assert cases["seed-17"]["taskCount"] == 15
    assert cases["seed-29"]["taskCount"] == 23
    assert cases["seed-31"]["taskCount"] == 27
    for case in cases.values():
        assert case["options"]["avoidConflicts"] is True
        assert case["options"]["includeDynamic"] is True
        assert case["options"]["assignmentReplanWindow"] == 120
        assert case["scenarioId"].startswith("seeded-pressure-")
        assert case["dynamicTaskCount"] == 3
        assert case["obstacleCount"] >= 6
        assert case["assignedTaskCount"] == case["taskCount"]
        assert case["stable"] is True
        assert case["assignmentRatePercent"] == 100
        assert "completionRatePercent" not in case
        assert case["conflictCount"] == 0
        assert case["deadlineMissCount"] == 0
        assert case["failureCount"] == 0
        assert case["totalDistance"] > 0
        assert case["averageDistancePerTask"] == round(case["totalDistance"] / case["taskCount"], 1)
        assert case["makespan"] > 0
        assert isinstance(case["replanTimeMs"], float)
        assert case["replanTimeMs"] >= 0

    assert cases["seed-17"]["assignedTaskCount"] < cases["seed-29"]["assignedTaskCount"]
    assert cases["seed-29"]["assignedTaskCount"] < cases["seed-31"]["assignedTaskCount"]


def test_seeded_pressure_experiment_can_run_extended_stability_cases() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/seeded-pressure",
        json={
            "caseSet": "extended",
            "options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 120},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}
    summary = payload["summary"]

    assert list(cases) == ["seed-17", "seed-29", "seed-31", "seed-37", "seed-43", "seed-53", "seed-67"]
    assert summary["caseCount"] == 7
    assert summary["largestRobotCount"] == 8
    assert summary["largestTaskCount"] == 33
    assert summary["totalTaskCount"] == 189
    assert summary["totalAssignedTaskCount"] == 189
    assert summary["stableCaseCount"] == 7
    assert summary["stableRatePercent"] == 100
    assert summary["assignmentRatePercent"] == 100
    assert "completionRatePercent" not in summary
    assert summary["planningTimeBudgetMs"] == 2000
    assert summary["maxConflictCount"] == 0
    assert summary["totalDeadlineMissCount"] == 0
    assert summary["totalFailureCount"] == 0
    assert summary["totalDistance"] == sum(case["totalDistance"] for case in cases.values())
    assert summary["maxMakespan"] == max(case["makespan"] for case in cases.values())
    assert summary["averageDistancePerTask"] == round(summary["totalDistance"] / summary["totalTaskCount"], 1)
    assert isinstance(summary["maxReplanTimeMs"], float)
    assert summary["maxReplanTimeMs"] >= 0
    assert cases["seed-37"]["taskCount"] == 29
    assert cases["seed-43"]["taskCount"] == 31
    assert cases["seed-53"]["taskCount"] == 31
    assert cases["seed-67"]["taskCount"] == 33
    for case in cases.values():
        assert case["assignedTaskCount"] == case["taskCount"]
        assert case["stable"] is True
        assert case["assignmentRatePercent"] == 100
        assert "completionRatePercent" not in case
        assert case["conflictCount"] == 0
        assert case["deadlineMissCount"] == 0
        assert case["failureCount"] == 0
        assert case["averageDistancePerTask"] == round(case["totalDistance"] / case["taskCount"], 1)
        assert isinstance(case["replanTimeMs"], float)
        assert case["replanTimeMs"] >= 0


def test_online_pressure_experiment_returns_runtime_flow_summary() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/experiments/online-pressure",
        json={"options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 120}},
    )

    assert response.status_code == 200
    payload = response.json()
    cases = {case["label"]: case for case in payload["cases"]}
    summary = payload["summary"]

    assert list(cases) == ["seed-17-online-flow"]
    assert summary["caseCount"] == 1
    assert summary["stableCaseCount"] == 1
    assert summary["stableRatePercent"] == 100
    assert summary["coverageRatePercent"] == 100
    assert summary["actualCompletionRatePercent"] == round(
        summary["totalCompletedTaskCount"] / summary["totalReleasedTaskCount"] * 100,
        1,
    )
    assert "completionRatePercent" not in summary
    assert summary["maxConflictCount"] == 0
    assert summary["totalFailureCount"] == 0
    assert summary["totalRuntimeEventCount"] == 6
    assert summary["totalReleasedTaskCount"] == 17
    assert summary["totalRuntimeTaskCount"] == 2
    assert "totalManualTaskCount" not in summary
    assert "totalStreamTaskCount" not in summary
    assert summary["totalCoveredTaskCount"] == summary["totalTaskCount"]
    assert summary["totalCompletedTaskCount"] > 0
    assert summary["maxReplanTimeMs"] > 0

    case = cases["seed-17-online-flow"]
    assert case["seed"] == 17
    assert case["scenarioId"] == "seeded-pressure-seed-17"
    assert case["options"]["avoidConflicts"] is True
    assert case["options"]["includeDynamic"] is True
    assert case["options"]["assignmentReplanWindow"] == 120
    assert case["robotCount"] == 4
    assert case["baseTaskCount"] == 12
    assert case["scenarioDynamicTaskCount"] == 3
    assert case["releasedTaskCount"] == 17
    assert case["runtimeTaskCount"] == 2
    assert "manualTaskCount" not in case
    assert "streamTaskCount" not in case
    assert case["runtimeEventCount"] == 6
    assert case["runtimeEventEvidence"] == [
        "manualTask",
        "blockedCell",
        "failedRobot",
        "restoredRobot",
        "clearedBlockedCell",
        "generatedTask",
    ]
    assert case["tickCount"] == 20
    assert case["coveredTaskCount"] == case["taskCount"]
    assert case["completedTaskCount"] > 0
    assert case["assignedTaskCount"] > 0
    assert case["stable"] is True
    assert case["coverageRatePercent"] == 100
    assert case["actualCompletionRatePercent"] == round(
        case["completedTaskCount"] / case["releasedTaskCount"] * 100,
        1,
    )
    assert "completionRatePercent" not in case
    assert case["conflictCount"] == 0
    assert case["deadlineMissCount"] == 0
    assert case["failureCount"] == 0
    assert case["totalDistance"] > 0
    assert case["averageDistancePerTask"] == round(case["totalDistance"] / case["taskCount"], 1)
    assert case["makespan"] > 0
    assert case["replanTimeMs"] > 0
    assert case["metricsHistoryCount"] >= 10
    assert case["eventLogCount"] >= 6


def test_online_pressure_experiment_bypasses_execution_interception(monkeypatch) -> None:
    client = TestClient(app)
    scenario = forced_online_experiment_conflict_scenario()
    observed_ticks: list[tuple[int, int, object]] = []
    real_tick_session = sessions_module.tick_session

    def record_experiment_tick(session_id, request):
        result = real_tick_session(session_id, request)
        observed_ticks.append((request.currentTime, result.currentTime, result.safetyIntervention))
        return result

    monkeypatch.setattr(experiments_module, "seeded_pressure_scenario", lambda *_: scenario.model_copy(deep=True))
    monkeypatch.setattr(experiments_module, "tick_session", record_experiment_tick)

    response = client.post(
        "/api/experiments/online-pressure",
        json={"options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 120}},
    )

    assert response.status_code == 200
    assert response.json()["cases"][0]["tickCount"] == 20
    assert observed_ticks
    assert all(reached_time == requested_time for requested_time, reached_time, _ in observed_ticks)
    assert all(intervention is None for _, _, intervention in observed_ticks)

    ordinary = client.post(
        "/api/sessions",
        json={
            "scenario": scenario.model_dump(mode="json"),
            "options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 120},
        },
    )
    assert ordinary.status_code == 200
    ordinary_tick = client.post(
        f"/api/sessions/{ordinary.json()['sessionId']}/tick",
        json={"currentTime": 8},
    )
    assert ordinary_tick.status_code == 200
    assert ordinary_tick.json()["currentTime"] == 2
    assert ordinary_tick.json()["safetyIntervention"] is not None
