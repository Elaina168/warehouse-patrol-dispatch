from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.app import main, schemas
from backend.app.limits import (
    MAX_EXPERIMENT_CASES,
    MAX_SCENARIO_AXIS_LENGTH,
    MAX_SCENARIO_CELL_COUNT,
    MAX_SCENARIO_ROBOTS,
    MAX_SCENARIO_TASKS,
    MAX_SESSION_CURRENT_TIME,
    MAX_TASK_TARGETS,
)
from backend.app.main import app
from backend.app.schemas import Robot
from backend.tests.helpers import scenario_payload


def _inspection_task(index: int, targets: list[list[int]] | None = None) -> dict:
    return {
        "id": f"L{index}",
        "type": "inspection",
        "title": f"限制任务 {index}",
        "priority": 1,
        "targets": targets if targets is not None else [[0, 0]],
    }


def _robot(index: int) -> dict:
    return {
        "id": f"R{index}",
        "name": f"机器人 {index}",
        "start": [index % 32, index // 32],
        "battery": 100,
        "load": 1,
    }


def _set_axis_over_limit(data: dict) -> None:
    data["width"] = MAX_SCENARIO_AXIS_LENGTH + 1


def _set_area_over_limit(data: dict) -> None:
    data["width"] = MAX_SCENARIO_AXIS_LENGTH
    data["height"] = MAX_SCENARIO_AXIS_LENGTH


def _set_robot_count_over_limit(data: dict) -> None:
    data["robots"] = [_robot(i) for i in range(MAX_SCENARIO_ROBOTS + 1)]


def _set_task_count_over_limit(data: dict) -> None:
    data["tasks"] = [
        _inspection_task(i)
        for i in range(MAX_SCENARIO_TASKS + 1)
    ]


def _set_targets_over_limit(data: dict) -> None:
    data["tasks"] = [
        _inspection_task(0, [[0, 0]] * (MAX_TASK_TARGETS + 1))
    ]


def _set_initial_and_dynamic_tasks_over_limit(data: dict) -> None:
    data["tasks"] = [_inspection_task(i) for i in range(64)]
    data["dynamic"]["tasks"] = [_inspection_task(i + 64) for i in range(65)]


def _set_map_cell_list_over_actual_area(data: dict) -> None:
    data.update(width=4, height=4)
    data["obstacles"] = [[0, 0] for _ in range(17)]


def _forbidden_call(calls: list[str], callable_name: str):
    def forbidden(*args, **kwargs):
        calls.append(callable_name)
        raise AssertionError(f"{callable_name} must not receive an invalid Pydantic request")

    return forbidden


def _validate_scenario_string(field_name: str, value: str):
    payload = scenario_payload()
    payload[field_name] = value
    return schemas.Scenario.model_validate(payload)


def _validate_dynamic_failed_robot_id(value: str):
    payload = scenario_payload()
    payload["dynamic"]["failedRobots"] = [value]
    return schemas.Scenario.model_validate(payload)


def _validate_task_string(field_name: str, value: str):
    payload = _inspection_task(0)
    payload[field_name] = value
    return schemas.Task.model_validate(payload)


def _validate_robot_string(field_name: str, value: str):
    payload = _robot(0)
    payload[field_name] = value
    return schemas.Robot.model_validate(payload)


@pytest.mark.parametrize(
    "validator",
    [
        pytest.param(lambda value: _validate_scenario_string("id", value), id="scenario-id"),
        pytest.param(lambda value: _validate_task_string("id", value), id="task-id"),
        pytest.param(lambda value: _validate_robot_string("id", value), id="robot-id"),
        pytest.param(
            lambda value: schemas.Shelf.model_validate(
                {"id": value, "cell": [0, 0], "serviceCell": [0, 1]}
            ),
            id="shelf-id",
        ),
        pytest.param(_validate_dynamic_failed_robot_id, id="dynamic-failed-robot-id"),
        pytest.param(
            lambda value: schemas.FailRobotRequest.model_validate({"robotId": value}),
            id="fail-robot-request-id",
        ),
        pytest.param(
            lambda value: schemas.RestoreRobotRequest.model_validate({"robotId": value}),
            id="restore-robot-request-id",
        ),
    ],
)
def test_identifier_strings_accept_128_and_reject_129_characters(validator) -> None:
    assert validator("I" * 128) is not None
    with pytest.raises(ValidationError):
        validator("I" * 129)


@pytest.mark.parametrize(
    "validator",
    [
        pytest.param(lambda value: _validate_scenario_string("name", value), id="scenario-name"),
        pytest.param(lambda value: _validate_task_string("title", value), id="task-title"),
        pytest.param(lambda value: _validate_robot_string("name", value), id="robot-name"),
        pytest.param(
            lambda value: schemas.ScaleExperimentScenario.model_validate(
                {"label": value, "scenario": scenario_payload()}
            ),
            id="scale-experiment-label",
        ),
    ],
)
def test_display_strings_accept_256_and_reject_257_characters(validator) -> None:
    assert validator("N" * 256) is not None
    with pytest.raises(ValidationError):
        validator("N" * 257)


def test_scenario_description_accepts_4096_and_rejects_4097_characters() -> None:
    assert _validate_scenario_string("description", "D" * 4096) is not None
    with pytest.raises(ValidationError):
        _validate_scenario_string("description", "D" * 4097)


@pytest.mark.parametrize(
    "validator",
    [
        pytest.param(
            lambda value: schemas.Scenario.model_validate(
                {
                    **scenario_payload(),
                    "robots": [
                        {
                            **scenario_payload()["robots"][0],
                            "battery": value,
                            "batteryCapacity": value,
                        }
                    ],
                }
            ),
            id="robot-battery",
        ),
        pytest.param(
            lambda value: schemas.Robot.model_validate(
                {**_robot(0), "battery": value, "batteryCapacity": value}
            ),
            id="robot-battery-capacity",
        ),
        pytest.param(
            lambda value: schemas.Robot.model_validate({**_robot(0), "load": value}),
            id="robot-load",
        ),
        pytest.param(
            lambda value: schemas.Task.model_validate(
                {**_inspection_task(0), "deadline": value}
            ),
            id="task-deadline",
        ),
        pytest.param(
            lambda value: schemas.Task.model_validate(
                {
                    "id": "D",
                    "type": "delivery",
                    "title": "D",
                    "priority": 1,
                    "pickup": [0, 0],
                    "dropoff": [1, 0],
                    "demand": value,
                }
            ),
            id="task-demand",
        ),
    ],
)
def test_javascript_safe_integer_boundary(validator) -> None:
    assert validator(9_007_199_254_740_991) is not None
    with pytest.raises(ValidationError):
        validator(9_007_199_254_740_992)


def test_runtime_current_time_openapi_maximum_matches_session_limit() -> None:
    for model in (
        schemas.SessionTickRequest,
        schemas.AddBlockRequest,
        schemas.RemoveBlockRequest,
        schemas.FailRobotRequest,
        schemas.RestoreRobotRequest,
    ):
        current_time_schema = model.model_json_schema()["properties"]["currentTime"]
        assert current_time_schema["maximum"] == MAX_SESSION_CURRENT_TIME


def test_robot_capabilities_default_to_all_task_types() -> None:
    robot = Robot(id="R1", name="R1", start=(0, 0), battery=100, load=1)
    assert robot.capabilities == ["inspection", "delivery", "emergency"]


def test_robot_capabilities_require_a_unique_nonempty_known_subset() -> None:
    assert Robot(
        id="R1", name="R1", start=(0, 0), battery=100, load=1,
        capabilities=["inspection", "emergency"],
    ).capabilities == ["inspection", "emergency"]
    with pytest.raises(ValidationError):
        Robot(id="R1", name="R1", start=(0, 0), battery=100, load=1, capabilities=[])
    with pytest.raises(ValidationError):
        Robot(id="R1", name="R1", start=(0, 0), battery=100, load=1, capabilities=["inspection", "inspection"])
    with pytest.raises(ValidationError):
        Robot(id="R1", name="R1", start=(0, 0), battery=100, load=1, capabilities=["unknown"])


def test_task_service_time_limit_accepts_10000_and_rejects_10001() -> None:
    task_payload = _inspection_task(0)

    assert schemas.Task.model_validate(
        {**task_payload, "serviceTime": 10_000}
    ).serviceTime == 10_000
    with pytest.raises(ValidationError):
        schemas.Task.model_validate({**task_payload, "serviceTime": 10_001})


def test_scenario_charge_time_limit_accepts_10000_and_rejects_10001() -> None:
    payload = scenario_payload()

    assert schemas.Scenario.model_validate(
        {**payload, "chargeTime": 10_000}
    ).chargeTime == 10_000
    with pytest.raises(ValidationError):
        schemas.Scenario.model_validate({**payload, "chargeTime": 10_001})


def test_session_time_fields_accept_10000_and_reject_10001() -> None:
    task_payload = _inspection_task(0)
    scenario = scenario_payload()

    assert schemas.Task.model_validate(
        {**task_payload, "releaseTime": 10_000}
    ).releaseTime == 10_000
    with pytest.raises(ValidationError):
        schemas.Task.model_validate({**task_payload, "releaseTime": 10_001})

    scenario["dynamic"]["triggerTime"] = 10_000
    assert schemas.Scenario.model_validate(scenario).dynamic.triggerTime == 10_000
    scenario["dynamic"]["triggerTime"] = 10_001
    with pytest.raises(ValidationError):
        schemas.Scenario.model_validate(scenario)


def test_session_apis_reject_release_time_above_session_limit() -> None:
    client = TestClient(app)
    invalid_scenario = scenario_payload()
    invalid_scenario["tasks"][0]["releaseTime"] = 10_001

    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": invalid_scenario,
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 422

    valid_response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert valid_response.status_code == 200
    session_id = valid_response.json()["sessionId"]
    add_response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                **_inspection_task(99),
                "releaseTime": 10_001,
            }
        },
    )

    assert add_response.status_code == 422
    payload = client.get(f"/api/sessions/{session_id}").json()
    assert all(task["id"] != "L99" for task in payload["result"]["tasks"])


def test_replan_window_experiment_batch_limit_accepts_1_and_32() -> None:
    payload = {
        "scenario": scenario_payload(),
        "options": {},
    }

    valid_one = schemas.ReplanWindowExperimentRequest.model_validate(
        {**payload, "windows": [4]}
    )
    valid_maximum = schemas.ReplanWindowExperimentRequest.model_validate(
        {**payload, "windows": list(range(MAX_EXPERIMENT_CASES))}
    )

    assert len(valid_one.windows) == 1
    assert len(valid_maximum.windows) == MAX_EXPERIMENT_CASES


def test_replan_window_experiment_batch_limit_rejects_0_and_33() -> None:
    payload = {
        "scenario": scenario_payload(),
        "options": {},
    }

    with pytest.raises(ValidationError):
        schemas.ReplanWindowExperimentRequest.model_validate(
            {**payload, "windows": []}
        )
    with pytest.raises(ValidationError):
        schemas.ReplanWindowExperimentRequest.model_validate(
            {
                **payload,
                "windows": list(range(MAX_EXPERIMENT_CASES + 1)),
            }
        )


def test_replan_window_experiment_duplicate_window_rejected() -> None:
    payload = {
        "scenario": scenario_payload(),
        "options": {},
        "windows": [4, 4],
    }

    with pytest.raises(ValidationError, match="windows must be unique"):
        schemas.ReplanWindowExperimentRequest.model_validate(payload)


def test_scale_experiment_batch_limit_accepts_1_and_32() -> None:
    scenario = scenario_payload()
    one_case = [{"label": "case-0", "scenario": scenario}]
    maximum_cases = [
        {"label": f"case-{index}", "scenario": scenario}
        for index in range(MAX_EXPERIMENT_CASES)
    ]

    valid_one = schemas.ScaleExperimentRequest.model_validate({"cases": one_case})
    valid_maximum = schemas.ScaleExperimentRequest.model_validate(
        {"cases": maximum_cases}
    )

    assert len(valid_one.cases) == 1
    assert len(valid_maximum.cases) == MAX_EXPERIMENT_CASES


def test_scale_experiment_batch_limit_rejects_0_and_33() -> None:
    scenario = scenario_payload()
    excessive_cases = [
        {"label": f"case-{index}", "scenario": scenario}
        for index in range(MAX_EXPERIMENT_CASES + 1)
    ]

    with pytest.raises(ValidationError):
        schemas.ScaleExperimentRequest.model_validate({"cases": []})
    with pytest.raises(ValidationError):
        schemas.ScaleExperimentRequest.model_validate({"cases": excessive_cases})


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
    assert "streamTaskCount" not in payload
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


def test_task_priority_accepts_zero_and_rejects_values_outside_zero_to_five() -> None:
    client = TestClient(app)

    zero_priority_scenario = scenario_payload()
    zero_priority_scenario["tasks"][0]["priority"] = 0
    assert client.post(
        "/api/dispatch",
        json={"scenario": zero_priority_scenario},
    ).status_code == 200

    negative_priority_scenario = scenario_payload()
    negative_priority_scenario["tasks"][0]["priority"] = -1
    assert client.post(
        "/api/dispatch",
        json={"scenario": negative_priority_scenario},
    ).status_code == 422

    excessive_priority_scenario = scenario_payload()
    excessive_priority_scenario["tasks"][0]["priority"] = 6
    assert client.post(
        "/api/dispatch",
        json={"scenario": excessive_priority_scenario},
    ).status_code == 422


@pytest.mark.parametrize(
    ("mutate", "expected_fragment"),
    [
        (_set_axis_over_limit, "less than or equal to 64"),
        (_set_area_over_limit, "map cell count must be <= 1024"),
        (_set_robot_count_over_limit, "at most 32"),
        (_set_task_count_over_limit, "at most 128"),
        (_set_targets_over_limit, "at most 64"),
    ],
)
def test_scenario_size_limits_reject_one_over_limit(mutate, expected_fragment: str) -> None:
    payload = scenario_payload()
    payload["obstacles"] = []
    payload["tasks"] = []
    payload["dynamic"]["tasks"] = []
    mutate(payload)

    response = TestClient(app).post("/api/dispatch", json={"scenario": payload})

    assert response.status_code == 422
    assert expected_fragment in response.text


def _set_map_cell_list(data: dict, field_name: str, count: int) -> None:
    cells = [[0, 0] for _ in range(count)]
    if field_name == "obstacles":
        data["obstacles"] = cells
    elif field_name == "shelves":
        data["shelves"] = [
            {
                "id": f"S{index}",
                "cell": [0, 0],
                "serviceCell": [1, 0],
                "initialOccupied": False,
            }
            for index in range(count)
        ]
    elif field_name.startswith("zones."):
        data["zones"][field_name.split(".", 1)[1]] = cells
    elif field_name == "dynamic.blockedCells":
        data["dynamic"]["blockedCells"] = cells
    else:
        raise AssertionError(f"unexpected field: {field_name}")


@pytest.mark.parametrize(
    "field_name",
    [
        "obstacles",
        "shelves",
        "zones.warehouse",
        "zones.inspection",
        "zones.delivery",
        "zones.charging",
        "dynamic.blockedCells",
    ],
)
def test_map_cell_lists_cannot_exceed_actual_map_area(field_name: str) -> None:
    payload = scenario_payload()
    payload.update(width=4, height=4)
    payload["obstacles"] = []
    payload["shelves"] = []
    payload["zones"] = {
        "warehouse": [],
        "inspection": [],
        "delivery": [],
        "charging": [],
    }
    payload["tasks"] = []
    payload["dynamic"] = {
        "triggerTime": 0,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [],
    }
    _set_map_cell_list(payload, field_name, 17)

    response = TestClient(app).post("/api/dispatch", json={"scenario": payload})

    assert response.status_code == 422
    assert field_name in response.text


def test_scenario_size_limits_accept_exact_boundaries() -> None:
    payload = scenario_payload()
    payload.update(width=64, height=16)
    payload["obstacles"] = []
    payload["shelves"] = []
    payload["zones"] = {
        "warehouse": [],
        "inspection": [],
        "delivery": [],
        "charging": [],
    }
    payload["robots"] = [_robot(i) for i in range(MAX_SCENARIO_ROBOTS)]
    payload["tasks"] = [
        _inspection_task(i, [[0, 0]] * MAX_TASK_TARGETS)
        for i in range(MAX_SCENARIO_TASKS)
    ]
    payload["dynamic"] = {
        "triggerTime": 0,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [],
    }

    scenario = schemas.Scenario.model_validate(payload)

    assert scenario.width * scenario.height == MAX_SCENARIO_CELL_COUNT
    assert len(scenario.robots) == MAX_SCENARIO_ROBOTS
    assert len(scenario.tasks) == MAX_SCENARIO_TASKS
    assert len(scenario.tasks[0].targets or []) == MAX_TASK_TARGETS


def test_initial_and_dynamic_tasks_share_128_limit() -> None:
    payload = scenario_payload()
    payload["tasks"] = [_inspection_task(i) for i in range(64)]
    payload["dynamic"]["tasks"] = [_inspection_task(i + 64) for i in range(65)]

    response = TestClient(app).post("/api/sessions", json={"scenario": payload})

    assert response.status_code == 422


def test_initial_and_dynamic_tasks_accept_exact_combined_limit() -> None:
    payload = scenario_payload()
    payload["tasks"] = [_inspection_task(i) for i in range(64)]
    payload["dynamic"]["tasks"] = [
        _inspection_task(i + 64)
        for i in range(64)
    ]

    scenario = schemas.Scenario.model_validate(payload)

    assert len(scenario.tasks) + len(scenario.dynamic.tasks) == 128


def test_runtime_task_uses_the_same_total_128_limit() -> None:
    payload = scenario_payload()
    payload["id"] = "integrated-demo"
    payload["tasks"] = [_inspection_task(i) for i in range(64)]
    payload["dynamic"]["tasks"] = [_inspection_task(i + 64) for i in range(64)]
    client = TestClient(app)
    created = client.post("/api/sessions", json={"scenario": payload})
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    response = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={"task": _inspection_task(MAX_SCENARIO_TASKS)},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "调度会话任务数已达上限："
        f"{MAX_SCENARIO_TASKS} + 1 > {MAX_SCENARIO_TASKS}"
    )


@pytest.mark.parametrize(
    ("path", "body", "callable_name"),
    [
        ("/api/dispatch", lambda scenario: {"scenario": scenario}, "run_dispatch"),
        ("/api/sessions", lambda scenario: {"scenario": scenario}, "create_session"),
        (
            "/api/experiments/conflict-avoidance",
            lambda scenario: {"scenario": scenario},
            "compare_conflict_avoidance",
        ),
        (
            "/api/experiments/dynamic-replanning",
            lambda scenario: {"scenario": scenario},
            "compare_dynamic_replanning",
        ),
        (
            "/api/experiments/replan-window",
            lambda scenario: {"scenario": scenario, "windows": [4]},
            "compare_replan_windows",
        ),
        (
            "/api/experiments/scale",
            lambda scenario: {"cases": [{"label": "oversized", "scenario": scenario}]},
            "compare_scale_cases",
        ),
    ],
)
@pytest.mark.parametrize(
    "mutate",
    [
        _set_area_over_limit,
        _set_initial_and_dynamic_tasks_over_limit,
        _set_map_cell_list_over_actual_area,
    ],
)
def test_invalid_scenarios_never_call_route_or_planning_functions(
    path,
    body,
    callable_name: str,
    mutate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_payload()
    mutate(scenario)
    calls: list[str] = []
    monkeypatch.setattr(main, callable_name, _forbidden_call(calls, callable_name))

    response = TestClient(app).post(path, json=body(scenario))

    assert response.status_code == 422
    assert calls == []
