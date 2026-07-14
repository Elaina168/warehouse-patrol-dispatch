import re
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from backend.app.main import app
from backend.app import schemas


FRONTEND_TYPES = Path("frontend/src/domain/types.ts")
FRONTEND_MAIN = Path("frontend/src/main.tsx")
BACKEND_DISPATCH = Path("backend/app/dispatch.py")


def _backend_fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


def _frontend_fields(type_name: str) -> set[str]:
    text = FRONTEND_TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export type {re.escape(type_name)} = \{{(?P<body>.*?)\n\}};", text, re.S)
    assert match is not None, f"Missing frontend type: {type_name}"
    fields: set[str] = set()
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        field_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(?:\?)?:", stripped)
        if field_match:
            fields.add(field_match.group(1))
    return fields


def _frontend_optional_fields(type_name: str) -> set[str]:
    text = FRONTEND_TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export type {re.escape(type_name)} = \{{(?P<body>.*?)\n\}};", text, re.S)
    assert match is not None, f"Missing frontend type: {type_name}"
    fields: set[str] = set()
    for line in match.group("body").splitlines():
        stripped = line.strip()
        field_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\?:", stripped)
        if field_match:
            fields.add(field_match.group(1))
    return fields


def _frontend_nullable_fields(type_name: str) -> set[str]:
    text = FRONTEND_TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export type {re.escape(type_name)} = \{{(?P<body>.*?)\n\}};", text, re.S)
    assert match is not None, f"Missing frontend type: {type_name}"
    fields: set[str] = set()
    for line in match.group("body").splitlines():
        stripped = line.strip()
        field_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(?:\?)?:.*\|\s*null", stripped)
        if field_match:
            fields.add(field_match.group(1))
    return fields


def _backend_optional_fields(model: type[BaseModel]) -> set[str]:
    return {name for name, field in model.model_fields.items() if not field.is_required()}


def _backend_nullable_fields(model: type[BaseModel]) -> set[str]:
    return {
        name
        for name, field in model.model_fields.items()
        if type(None) in get_args(field.annotation)
    }


def _frontend_string_literal_union(type_name: str) -> set[str]:
    text = FRONTEND_TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export type {re.escape(type_name)} =(?P<body>.*?);", text, re.S)
    assert match is not None, f"Missing frontend type: {type_name}"
    return set(re.findall(r'"([^"]+)"', match.group("body")))


def _frontend_numeric_constant(name: str) -> int:
    text = FRONTEND_MAIN.read_text(encoding="utf-8")
    match = re.search(rf"const {re.escape(name)} = (?P<value>\d+);", text)
    assert match is not None, f"Missing frontend numeric constant: {name}"
    return int(match.group("value"))


def _backend_string_literal_values(annotation: Any) -> set[str]:
    origin = get_origin(annotation)
    if origin is Literal:
        return {value for value in get_args(annotation) if isinstance(value, str)}
    if origin in {Union, UnionType}:
        values: set[str] = set()
        for item in get_args(annotation):
            values.update(_backend_string_literal_values(item))
        return values
    return set()


def _backend_field_string_literals(model: type[BaseModel], field_name: str) -> set[str]:
    return _backend_string_literal_values(model.model_fields[field_name].annotation)


def _dispatch_recovery_action_literals() -> set[str]:
    text = BACKEND_DISPATCH.read_text(encoding="utf-8")
    return set(re.findall(r'return "temporary", "([^"]+)"', text)) | set(
        re.findall(r'return "permanent", "([^"]+)"', text)
    )


def _request_schema_ref(openapi: dict[str, Any], path: str, method: str) -> str:
    operation = openapi["paths"][path][method]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    return schema["$ref"].rsplit("/", 1)[-1]


def _response_schema_ref(openapi: dict[str, Any], path: str, method: str) -> str:
    operation = openapi["paths"][path][method]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "array" and "$ref" in schema.get("items", {}):
        return f"list[{schema['items']['$ref'].rsplit('/', 1)[-1]}]"
    raise AssertionError(f"Unsupported response schema for {method.upper()} {path}: {schema}")


def test_frontend_types_match_backend_api_model_fields() -> None:
    model_pairs: list[tuple[str, type[BaseModel]]] = [
        ("Robot", schemas.Robot),
        ("ChargingVisit", schemas.ChargingVisit),
        ("DynamicEvent", schemas.DynamicEvent),
        ("Zones", schemas.Zones),
        ("Scenario", schemas.Scenario),
        ("Assignment", schemas.Assignment),
        ("Conflict", schemas.Conflict),
        ("ConflictState", schemas.ConflictState),
        ("Metrics", schemas.Metrics),
        ("EventItem", schemas.EventItem),
        ("DispatchOptions", schemas.DispatchOptions),
        ("DispatchRequest", schemas.DispatchRequest),
        ("CreateSessionRequest", schemas.CreateSessionRequest),
        ("AddTaskRequest", schemas.AddTaskRequest),
        ("SessionTickRequest", schemas.SessionTickRequest),
        ("AddBlockRequest", schemas.AddBlockRequest),
        ("RemoveBlockRequest", schemas.RemoveBlockRequest),
        ("FailRobotRequest", schemas.FailRobotRequest),
        ("RestoreRobotRequest", schemas.RestoreRobotRequest),
        ("DispatchResult", schemas.DispatchResult),
        ("SessionResult", schemas.SessionResult),
        ("SessionSummary", schemas.SessionSummary),
        ("RobotRuntimeState", schemas.RobotRuntimeState),
        ("TaskRuntimeState", schemas.TaskRuntimeState),
        ("TaskFailureDetail", schemas.TaskFailureDetail),
        ("ConflictState", schemas.ConflictState),
        ("MetricSnapshot", schemas.MetricSnapshot),
        ("DeleteSessionResult", schemas.DeleteSessionResult),
    ]

    for type_name, model in model_pairs:
        assert _frontend_fields(type_name) == _backend_fields(model), type_name


def test_frontend_task_union_covers_backend_task_fields() -> None:
    frontend_task_fields = (
        _frontend_fields("PatrolTask")
        | _frontend_fields("DeliveryTask")
        | _frontend_fields("EmergencyTask")
    )

    assert frontend_task_fields == _backend_fields(schemas.Task)


def test_frontend_response_nullable_fields_match_backend_schema() -> None:
    response_model_pairs: list[tuple[str, type[BaseModel]]] = [
        ("DispatchResult", schemas.DispatchResult),
        ("RobotRuntimeState", schemas.RobotRuntimeState),
        ("TaskRuntimeState", schemas.TaskRuntimeState),
        ("SessionResult", schemas.SessionResult),
        ("SessionSummary", schemas.SessionSummary),
        ("TaskFailureDetail", schemas.TaskFailureDetail),
        ("MetricSnapshot", schemas.MetricSnapshot),
        ("DeleteSessionResult", schemas.DeleteSessionResult),
    ]

    for type_name, model in response_model_pairs:
        assert _frontend_nullable_fields(type_name) == _backend_nullable_fields(model), type_name


def test_frontend_task_variants_include_backend_serialized_nullable_fields() -> None:
    expected_nullable_fields = {
        "PatrolTask": {"releaseTime", "deadline", "serviceTime", "pickup", "dropoff", "demand", "target"},
        "DeliveryTask": {"releaseTime", "deadline", "serviceTime", "targets", "target"},
        "EmergencyTask": {"releaseTime", "deadline", "serviceTime", "targets", "pickup", "dropoff", "demand"},
    }

    for type_name, fields in expected_nullable_fields.items():
        assert _frontend_nullable_fields(type_name) == fields, type_name


def test_frontend_request_optional_fields_match_backend_defaults() -> None:
    request_model_pairs: list[tuple[str, type[BaseModel]]] = [
        ("DispatchRequest", schemas.DispatchRequest),
        ("CreateSessionRequest", schemas.CreateSessionRequest),
        ("AddBlockRequest", schemas.AddBlockRequest),
        ("RemoveBlockRequest", schemas.RemoveBlockRequest),
        ("FailRobotRequest", schemas.FailRobotRequest),
        ("RestoreRobotRequest", schemas.RestoreRobotRequest),
    ]

    for type_name, model in request_model_pairs:
        assert _frontend_optional_fields(type_name) == _backend_optional_fields(model), type_name


def test_frontend_assignment_replan_window_constants_match_backend_schema() -> None:
    schema = schemas.DispatchOptions.model_json_schema()["properties"]["assignmentReplanWindow"]

    assert schema["minimum"] == 0
    assert schema["default"] == _frontend_numeric_constant("DEFAULT_ASSIGNMENT_REPLAN_WINDOW")
    assert schema["maximum"] == _frontend_numeric_constant("MAX_ASSIGNMENT_REPLAN_WINDOW")


def test_frontend_recovery_action_union_matches_backend_schema() -> None:
    assert _frontend_string_literal_union("RecoveryAction") == set(get_args(schemas.RecoveryAction))


def test_backend_recovery_action_schema_matches_dispatch_literals() -> None:
    assert set(get_args(schemas.RecoveryAction)) == _dispatch_recovery_action_literals()


def test_frontend_runtime_literal_unions_match_backend_schema() -> None:
    assert _frontend_string_literal_union("TaskType") == _backend_field_string_literals(schemas.Task, "type")
    assert _frontend_string_literal_union("ConflictType") == _backend_field_string_literals(schemas.Conflict, "type")
    assert _frontend_string_literal_union("RobotRuntimeStatus") == _backend_field_string_literals(
        schemas.RobotRuntimeState,
        "status",
    )
    assert _frontend_string_literal_union("TaskRuntimeStatus") == _backend_field_string_literals(
        schemas.TaskRuntimeState,
        "status",
    )
    assert _frontend_string_literal_union("TaskFailureCategory") == _backend_field_string_literals(
        schemas.TaskRuntimeState,
        "failureCategory",
    )


def test_openapi_session_routes_use_expected_request_models() -> None:
    openapi = app.openapi()
    expected_request_refs = {
        ("/api/experiments/conflict-avoidance", "post"): "ConflictAvoidanceExperimentRequest",
        ("/api/experiments/dynamic-replanning", "post"): "DynamicReplanningExperimentRequest",
        ("/api/experiments/replan-window", "post"): "ReplanWindowExperimentRequest",
        ("/api/experiments/scale", "post"): "ScaleExperimentRequest",
        ("/api/experiments/seeded-pressure", "post"): "SeededPressureExperimentRequest",
        ("/api/experiments/online-pressure", "post"): "OnlinePressureExperimentRequest",
        ("/api/sessions", "post"): "CreateSessionRequest",
        ("/api/sessions/{session_id}/tasks", "post"): "AddTaskRequest",
        ("/api/sessions/{session_id}/tick", "post"): "SessionTickRequest",
        ("/api/sessions/{session_id}/blocked-cells", "post"): "AddBlockRequest",
        ("/api/sessions/{session_id}/blocked-cells/remove", "post"): "RemoveBlockRequest",
        ("/api/sessions/{session_id}/failed-robots", "post"): "FailRobotRequest",
        ("/api/sessions/{session_id}/failed-robots/restore", "post"): "RestoreRobotRequest",
    }

    for (path, method), schema_name in expected_request_refs.items():
        assert _request_schema_ref(openapi, path, method) == schema_name


def test_openapi_dispatch_and_session_routes_use_expected_response_models() -> None:
    openapi = app.openapi()
    expected_response_refs = {
        ("/api/dispatch", "post"): "DispatchResult",
        ("/api/experiments/conflict-avoidance", "post"): "ConflictAvoidanceExperimentResult",
        ("/api/experiments/dynamic-replanning", "post"): "DynamicReplanningExperimentResult",
        ("/api/experiments/replan-window", "post"): "ReplanWindowExperimentResult",
        ("/api/experiments/scale", "post"): "ScaleExperimentResult",
        ("/api/experiments/seeded-pressure", "post"): "SeededPressureExperimentResult",
        ("/api/experiments/online-pressure", "post"): "OnlinePressureExperimentResult",
        ("/api/sessions", "get"): "list[SessionSummary]",
        ("/api/sessions", "post"): "SessionResult",
        ("/api/sessions/{session_id}", "get"): "SessionResult",
        ("/api/sessions/{session_id}", "delete"): "DeleteSessionResult",
        ("/api/sessions/{session_id}/reset", "post"): "SessionResult",
        ("/api/sessions/{session_id}/tasks", "post"): "SessionResult",
        ("/api/sessions/{session_id}/tick", "post"): "SessionResult",
        ("/api/sessions/{session_id}/blocked-cells", "post"): "SessionResult",
        ("/api/sessions/{session_id}/blocked-cells/remove", "post"): "SessionResult",
        ("/api/sessions/{session_id}/failed-robots", "post"): "SessionResult",
        ("/api/sessions/{session_id}/failed-robots/restore", "post"): "SessionResult",
    }

    for (path, method), schema_name in expected_response_refs.items():
        assert _response_schema_ref(openapi, path, method) == schema_name


def test_openapi_exposes_expected_session_routes() -> None:
    openapi = app.openapi()
    expected_routes = {
        ("/api/experiments/conflict-avoidance", "post"),
        ("/api/experiments/dynamic-replanning", "post"),
        ("/api/experiments/replan-window", "post"),
        ("/api/experiments/scale", "post"),
        ("/api/experiments/seeded-pressure", "post"),
        ("/api/experiments/online-pressure", "post"),
        ("/api/sessions", "get"),
        ("/api/sessions", "post"),
        ("/api/sessions/{session_id}", "get"),
        ("/api/sessions/{session_id}", "delete"),
        ("/api/sessions/{session_id}/reset", "post"),
        ("/api/sessions/{session_id}/tasks", "post"),
        ("/api/sessions/{session_id}/tick", "post"),
        ("/api/sessions/{session_id}/blocked-cells", "post"),
        ("/api/sessions/{session_id}/blocked-cells/remove", "post"),
        ("/api/sessions/{session_id}/failed-robots", "post"),
        ("/api/sessions/{session_id}/failed-robots/restore", "post"),
    }

    for path, method in expected_routes:
        assert method in openapi["paths"][path]
