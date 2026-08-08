from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import cors_allowed_origins
from backend.app.dispatch import run_dispatch
from backend.app.experiments import (
    compare_conflict_avoidance,
    compare_dynamic_replanning,
    compare_replan_windows,
    compare_scale_cases,
    run_online_pressure_experiment,
    run_seeded_pressure_experiment,
)
from backend.app.schemas import (
    AddBlockRequest,
    AddTaskRequest,
    ConflictAvoidanceExperimentRequest,
    ConflictAvoidanceExperimentResult,
    CreateSessionRequest,
    DeleteSessionResult,
    DispatchRequest,
    DispatchResult,
    DynamicReplanningExperimentRequest,
    DynamicReplanningExperimentResult,
    FailRobotRequest,
    OnlinePressureExperimentRequest,
    OnlinePressureExperimentResult,
    ReplanWindowExperimentRequest,
    ReplanWindowExperimentResult,
    RemoveBlockRequest,
    RestoreRobotRequest,
    ScaleExperimentRequest,
    ScaleExperimentResult,
    SeededPressureExperimentRequest,
    SeededPressureExperimentResult,
    SessionResult,
    SessionSummary,
    SessionTickRequest,
)
from backend.app.sessions import (
    add_blocked_cell,
    add_task,
    create_session,
    delete_session,
    fail_robot,
    get_session,
    list_sessions,
    remove_blocked_cell,
    reset_session,
    restore_robot,
    tick_session,
)
from backend.app.limits import MAX_REQUEST_BODY_BYTES
from backend.app.request_limits import RequestBodyLimitMiddleware
from backend.app.validation import validate_scenario

app = FastAPI(title="Warehouse Patrol Dispatch API", version="0.1.0")

app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_size=MAX_REQUEST_BODY_BYTES,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/project")
def project() -> dict[str, str]:
    return {
        "name": "warehouse-patrol-dispatch",
        "description": "Multi-robot task allocation, path planning, and dynamic replanning platform.",
    }


@app.post("/api/dispatch", response_model=DispatchResult)
def dispatch(request: DispatchRequest) -> DispatchResult:
    diagnostics = validate_scenario(request.scenario, request.options)
    if diagnostics:
        raise HTTPException(status_code=422, detail=diagnostics)
    return run_dispatch(request.scenario, request.options)


@app.post("/api/experiments/conflict-avoidance", response_model=ConflictAvoidanceExperimentResult)
def experiment_conflict_avoidance(request: ConflictAvoidanceExperimentRequest) -> ConflictAvoidanceExperimentResult:
    diagnostics = validate_scenario(request.scenario, request.options)
    if diagnostics:
        raise HTTPException(status_code=422, detail=diagnostics)
    return compare_conflict_avoidance(request)


@app.post("/api/experiments/dynamic-replanning", response_model=DynamicReplanningExperimentResult)
def experiment_dynamic_replanning(request: DynamicReplanningExperimentRequest) -> DynamicReplanningExperimentResult:
    validation_options = request.options.model_copy(update={"includeDynamic": True})
    diagnostics = validate_scenario(request.scenario, validation_options)
    if diagnostics:
        raise HTTPException(status_code=422, detail=diagnostics)
    return compare_dynamic_replanning(request)


@app.post("/api/experiments/replan-window", response_model=ReplanWindowExperimentResult)
def experiment_replan_window(request: ReplanWindowExperimentRequest) -> ReplanWindowExperimentResult:
    diagnostics = validate_scenario(request.scenario, request.options)
    if diagnostics:
        raise HTTPException(status_code=422, detail=diagnostics)
    return compare_replan_windows(request)


@app.post("/api/experiments/scale", response_model=ScaleExperimentResult)
def experiment_scale(request: ScaleExperimentRequest) -> ScaleExperimentResult:
    diagnostics: list[str] = []
    for case in request.cases:
        case_diagnostics = validate_scenario(case.scenario, request.options)
        diagnostics.extend(f"{case.label}: {diagnostic}" for diagnostic in case_diagnostics)
    if diagnostics:
        raise HTTPException(status_code=422, detail=diagnostics)
    return compare_scale_cases(request)


@app.post("/api/experiments/seeded-pressure", response_model=SeededPressureExperimentResult)
def experiment_seeded_pressure(request: SeededPressureExperimentRequest) -> SeededPressureExperimentResult:
    return run_seeded_pressure_experiment(request)


@app.post("/api/experiments/online-pressure", response_model=OnlinePressureExperimentResult)
def experiment_online_pressure(request: OnlinePressureExperimentRequest) -> OnlinePressureExperimentResult:
    return run_online_pressure_experiment(request)


@app.post("/api/sessions", response_model=SessionResult)
def session_create(request: CreateSessionRequest) -> SessionResult:
    return create_session(request)


@app.get("/api/sessions", response_model=list[SessionSummary])
def session_list() -> list[SessionSummary]:
    return list_sessions()


@app.get("/api/sessions/{session_id}", response_model=SessionResult)
def session_get(session_id: str) -> SessionResult:
    return get_session(session_id)


@app.delete("/api/sessions/{session_id}", response_model=DeleteSessionResult)
def session_delete(session_id: str) -> DeleteSessionResult:
    return delete_session(session_id)


@app.post("/api/sessions/{session_id}/reset", response_model=SessionResult)
def session_reset(session_id: str) -> SessionResult:
    return reset_session(session_id)


@app.post("/api/sessions/{session_id}/tasks", response_model=SessionResult)
def session_add_task(session_id: str, request: AddTaskRequest) -> SessionResult:
    return add_task(session_id, request)


@app.post("/api/sessions/{session_id}/tick", response_model=SessionResult)
def session_tick(session_id: str, request: SessionTickRequest) -> SessionResult:
    return tick_session(session_id, request)


@app.post("/api/sessions/{session_id}/blocked-cells", response_model=SessionResult)
def session_add_blocked_cell(session_id: str, request: AddBlockRequest) -> SessionResult:
    return add_blocked_cell(session_id, request)


@app.post("/api/sessions/{session_id}/blocked-cells/remove", response_model=SessionResult)
def session_remove_blocked_cell(session_id: str, request: RemoveBlockRequest) -> SessionResult:
    return remove_blocked_cell(session_id, request)


@app.post("/api/sessions/{session_id}/failed-robots", response_model=SessionResult)
def session_fail_robot(session_id: str, request: FailRobotRequest) -> SessionResult:
    return fail_robot(session_id, request)


@app.post("/api/sessions/{session_id}/failed-robots/restore", response_model=SessionResult)
def session_restore_robot(session_id: str, request: RestoreRobotRequest) -> SessionResult:
    return restore_robot(session_id, request)
