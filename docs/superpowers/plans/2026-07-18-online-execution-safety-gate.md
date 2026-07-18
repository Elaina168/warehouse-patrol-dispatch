# Online Execution Safety Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为开启避碰的在线会话增加逐 tick 执行安全门，在首个顶点或反向边冲突 tick 全车安全等待、提前结束本次推进，并向前端返回结构化拦截状态。

**Architecture:** 保留现有优先级时空 A* 和离线实验行为，在 `backend/app/sessions.py` 的会话执行边界选择首个预测冲突。会话只把实际安全路径写入历史；前端通过 `SessionResult.safetyIntervention` 自动暂停、显示状态并复用现有冲突样式。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、pytest、React 19、TypeScript、Vitest、PowerShell 7。

## Global Constraints

- 只对 `DispatchOptions.avoidConflicts = true` 的在线会话启用执行安全门。
- `avoidConflicts = false` 的基线对比继续允许展示和执行冲突。
- `POST /api/dispatch` 与六个实验接口不增加在线拦截行为。
- 首个危险 tick 全车原地等待；不执行部分冲突动作。
- 大跨度 tick 请求在首个危险 tick 提前返回，响应 `currentTime` 小于原请求目标时间。
- 安全等待不增加移动距离、不扣电、不误完成路点；已经开始的原地作业继续累计。
- 不新增恢复动作，不自动解除锁，不增加自动放弃任务策略。
- 不引入 CBS、ECBS、完整 MAPF、新实验接口或新前端面板。
- API 字段保持 camelCase；后端和前端字段必须由契约测试保持一致。
- 中文源文件和文档保持 UTF-8；代码注释使用中文。
- 不提交 `.superpowers/`、`output/`、`frontend/dist/` 或缓存文件。

## File Structure

- `backend/app/schemas.py`：在 `SessionResult` 暴露 `safetyIntervention: Conflict | None`。
- `backend/app/sessions.py`：保存最近一次拦截、选择首个危险 tick、写入安全等待、维护会话推进和重置生命周期。
- `backend/tests/test_sessions.py`：安全门确定性场景、会话状态、任务/库存/电量/时间边界回归。
- `backend/tests/test_api_contract.py`：前后端字段、可空性和 OpenAPI 响应模型契约。
- `frontend/src/domain/types.ts`：增加必有可空的 `safetyIntervention` 字段。
- `frontend/src/main.tsx`：自动暂停、状态文字、冲突单元和机器人高亮。
- `frontend/src/main.test.ts`：结构化安全状态的纯函数和渲染回归。
- `frontend/src/styles.css`：安全提示和被拦截机器人样式。
- `docs/algorithm.md`、`docs/baseline.md`、`docs/demo.md`、`docs/testing-guide.md`、`docs/experiments.md`、`AGENTS.md`：同步已实现边界与验收口径。

---

### Task 1: Session safety contract and lifecycle state

**Files:**
- Modify: `backend/app/schemas.py:193-207,441-456`
- Modify: `backend/app/sessions.py:33-57,73-108,325-334,398-427,480-561`
- Modify: `frontend/src/domain/types.ts:117-132,279-295`
- Test: `backend/tests/test_api_contract.py:127-191`
- Test: `backend/tests/test_sessions.py`

**Interfaces:**
- Produces: `SessionResult.safetyIntervention: Conflict | None`
- Produces: frontend `SessionResult.safetyIntervention: Conflict | null`
- Produces: `DispatchSession.last_safety_intervention: Conflict | None`
- Preserves: `DispatchResult` without a safety field.

- [ ] **Step 1: Write failing contract and serialization tests**

Add to `backend/tests/test_api_contract.py`:

```python
def test_session_safety_intervention_is_required_and_nullable() -> None:
    assert "safetyIntervention" in _backend_fields(schemas.SessionResult)
    assert "safetyIntervention" in _frontend_fields("SessionResult")
    assert "safetyIntervention" in _backend_nullable_fields(schemas.SessionResult)
    assert "safetyIntervention" in _frontend_nullable_fields("SessionResult")
    assert "safetyIntervention" not in _frontend_optional_fields("SessionResult")
```

Add to `backend/tests/test_sessions.py`:

```python
def test_session_create_serializes_null_safety_intervention() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert response.status_code == 200
    assert "safetyIntervention" in response.json()
    assert response.json()["safetyIntervention"] is None
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_api_contract.py::test_session_safety_intervention_is_required_and_nullable backend\tests\test_sessions.py::test_session_create_serializes_null_safety_intervention -q
```

Expected: both tests FAIL because neither backend nor frontend exposes `safetyIntervention`.

- [ ] **Step 3: Add the exact backend and frontend fields**

Add `Conflict` to the imports from `backend.app.schemas` in `backend/app/sessions.py`.

Add to `backend/app/schemas.py` inside `SessionResult`, immediately before `result`:

```python
    safetyIntervention: Conflict | None = None
```

Add to `frontend/src/domain/types.ts` inside `SessionResult`, immediately before `result`:

```ts
  safetyIntervention: Conflict | null;
```

Add to `DispatchSession`:

```python
    last_safety_intervention: Conflict | None = None
```

Add to both `SessionResult(...)` constructors in `_build_result`:

```python
            safetyIntervention=session.last_safety_intervention,
```

and:

```python
        safetyIntervention=session.last_safety_intervention,
```

Clear the field in `_reset_session_runtime`:

```python
    session.last_safety_intervention = None
```

At the start of a real tick attempt in `tick_session`, clear the previous intervention without clearing it on reads or same-time updates:

```python
    if request.currentTime > session.current_time:
        session.last_safety_intervention = None
        _ensure_planning_started(session)
```

- [ ] **Step 4: Run targeted contract and session tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_api_contract.py backend\tests\test_sessions.py::test_session_create_serializes_null_safety_intervention -q
```

Expected: all selected tests PASS; the existing field and nullable-field comparisons also pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/schemas.py backend/app/sessions.py backend/tests/test_api_contract.py backend/tests/test_sessions.py frontend/src/domain/types.ts
git commit -m "feat: add session safety intervention contract"
```

---

### Task 2: Deterministic first-conflict selection

**Files:**
- Modify: `backend/app/sessions.py:687-705`
- Test: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `DispatchResult.conflicts: list[Conflict]`
- Produces: `_first_execution_conflict(result: DispatchResult, current_time: int, target_time: int) -> Conflict | None`
- Sorting: `time`, vertex before edge, `tuple(robots)`, then `cell`.

- [ ] **Step 1: Add a reusable result builder and failing selector tests**

Add near the top of `backend/tests/test_sessions.py`:

```python
def _safety_test_result(
    scenario: Scenario,
    paths: dict[str, list[tuple[int, int]]],
    conflicts: list[Conflict],
    assignments: list[Assignment] | None = None,
) -> DispatchResult:
    assignments = assignments or []
    return DispatchResult(
        scenarioId=scenario.id,
        avoidConflicts=True,
        includeDynamic=False,
        dynamicTriggerTime=None,
        extraBlocked=[],
        unavailableRobotIds=[],
        assignments=assignments,
        paths=paths,
        conflicts=conflicts,
        metrics=Metrics(
            makespan=max((len(path) - 1 for path in paths.values()), default=0),
            totalDistance=sum(max(0, len(path) - 1) for path in paths.values()),
            conflictCount=len(conflicts),
            loadBalance=0,
            assignedTaskCount=sum(len(item.tasks) for item in assignments),
            deadlineMissCount=0,
            averageLateness=0,
            failureCount=0,
            replanTimeMs=0,
        ),
        eventLog=[],
        tasks=[task for assignment in assignments for task in assignment.tasks],
    )
```

Add the selector tests:

```python
def test_first_execution_conflict_selects_earliest_future_conflict_deterministically() -> None:
    scenario = Scenario.model_validate(scenario_payload())
    result = _safety_test_result(
        scenario,
        {robot.id: [robot.start] * 6 for robot in scenario.robots},
        [
            Conflict(time=1, type="vertex", robots=["OLD", "OLD2"], cell=(0, 0)),
            Conflict(time=4, type="edge", robots=["R2", "R1"], cell=(2, 0)),
            Conflict(time=3, type="edge", robots=["R2", "R1"], cell=(1, 0)),
            Conflict(time=3, type="vertex", robots=["R3", "R1"], cell=(2, 0)),
            Conflict(time=3, type="vertex", robots=["R2", "R1"], cell=(3, 0)),
        ],
    )

    selected = sessions_module._first_execution_conflict(result, current_time=1, target_time=4)

    assert selected == Conflict(time=3, type="vertex", robots=["R2", "R1"], cell=(3, 0))


def test_first_execution_conflict_ignores_past_and_out_of_range_conflicts() -> None:
    scenario = Scenario.model_validate(scenario_payload())
    result = _safety_test_result(
        scenario,
        {robot.id: [robot.start] * 6 for robot in scenario.robots},
        [
            Conflict(time=2, type="vertex", robots=["R1", "R2"], cell=(1, 0)),
            Conflict(time=6, type="edge", robots=["R1", "R2"], cell=(2, 0)),
        ],
    )

    assert sessions_module._first_execution_conflict(result, current_time=2, target_time=5) is None
```

- [ ] **Step 2: Run selector tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py::test_first_execution_conflict_selects_earliest_future_conflict_deterministically backend\tests\test_sessions.py::test_first_execution_conflict_ignores_past_and_out_of_range_conflicts -q
```

Expected: FAIL with `AttributeError` because `_first_execution_conflict` does not exist.

- [ ] **Step 3: Implement the selector**

Add before `_advance_session`:

```python
def _first_execution_conflict(
    result: DispatchResult,
    current_time: int,
    target_time: int,
) -> Conflict | None:
    conflict_type_order = {"vertex": 0, "edge": 1}
    candidates = [
        conflict
        for conflict in result.conflicts
        if current_time < conflict.time <= target_time
    ]
    return min(
        candidates,
        key=lambda conflict: (
            conflict.time,
            conflict_type_order[conflict.type],
            tuple(conflict.robots),
            conflict.cell,
        ),
        default=None,
    )
```

- [ ] **Step 4: Run selector tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py::test_first_execution_conflict_selects_earliest_future_conflict_deterministically backend\tests\test_sessions.py::test_first_execution_conflict_ignores_past_and_out_of_range_conflicts -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit Task 2**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py
git commit -m "feat: select first unsafe execution tick"
```

---

### Task 3: Vertex-conflict hold and early tick termination

**Files:**
- Modify: `backend/app/sessions.py:687-779`
- Test: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `_first_execution_conflict(...) -> Conflict | None`
- Produces: `_apply_result_through_time(session: DispatchSession, result: DispatchResult, target_time: int) -> None`
- Produces: `_safety_hold_result(session: DispatchSession, result: DispatchResult) -> DispatchResult`
- Produces: `_apply_safety_hold(session: DispatchSession, result: DispatchResult, conflict: Conflict) -> None`
- Changes: `_advance_session` returns at the first intercepted tick.

- [ ] **Step 1: Add the deterministic forced-corridor regression**

Add to `backend/tests/test_sessions.py`:

```python
def _forced_safety_gate_scenario() -> dict[str, Any]:
    return {
        "id": "forced-safety-gate",
        "name": "forced-safety-gate",
        "description": "能力约束强制两台机器人在单通道对向执行",
        "width": 3,
        "height": 1,
        "obstacles": [],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[2, 0]],
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
        ],
        "tasks": [
            {
                "id": "T1",
                "type": "inspection",
                "title": "R1 到右端",
                "priority": 2,
                "targets": [[2, 0]],
            },
            {
                "id": "T2",
                "type": "emergency",
                "title": "R2 到左端",
                "priority": 4,
                "target": [0, 0],
            },
        ],
        "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
    }


def test_session_safety_gate_holds_fleet_at_first_vertex_conflict() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": _forced_safety_gate_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    response = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 8})
    assert response.status_code == 200
    payload = response.json()
    positions = {state["robotId"]: state["position"] for state in payload["robotStates"]}
    batteries = {state["robotId"]: state["battery"] for state in payload["robotStates"]}

    assert payload["currentTime"] == 2
    assert payload["safetyIntervention"] == {
        "time": 2,
        "type": "vertex",
        "robots": ["R1", "R2"],
        "cell": [0, 0],
    }
    assert positions == {"R1": [0, 0], "R2": [1, 0]}
    assert len({tuple(position) for position in positions.values()}) == 2
    assert payload["metricsHistory"][-1]["activeConflictCount"] == 0
    assert payload["metricsHistory"][-1]["travelledDistance"] == 1
    assert batteries == {"R1": 90, "R2": 89}
    assert any("T=2 执行安全门拦截 vertex 冲突：R1 / R2" == item["text"] for item in payload["result"]["eventLog"])
```

- [ ] **Step 2: Run the regression and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py::test_session_safety_gate_holds_fleet_at_first_vertex_conflict -q
```

Expected: FAIL because the response advances through the collision instead of stopping at `T=2`.

- [ ] **Step 3: Extract existing execution-state application without changing behavior**

Move the current robot-history, charging, lock, task-progress, inventory and completion block from `_advance_session` into this exact helper:

```python
def _apply_result_through_time(
    session: DispatchSession,
    result: DispatchResult,
    target_time: int,
) -> None:
    for robot in session.scenario.robots:
        path = result.paths.get(robot.id, [session.robot_positions.get(robot.id, robot.start)])
        history = session.robot_path_history.setdefault(robot.id, [robot.start])
        while len(history) <= target_time:
            next_position = path_at(path, len(history)) or history[-1]
            history.append(next_position)
        for tick_time in range(session.current_time + 1, target_time + 1):
            previous = history[tick_time - 1]
            current = history[tick_time]
            if current != previous:
                session.robot_travelled_distance[robot.id] = session.robot_travelled_distance.get(robot.id, 0) + 1
                session.robot_battery_levels[robot.id] = max(
                    0,
                    session.robot_battery_levels.get(robot.id, robot.battery) - 1,
                )
            for visit in result.chargingVisits:
                if visit.robotId != robot.id:
                    continue
                if visit.departureTime + 1 == tick_time:
                    _record_session_event(session, tick_time, f"{robot.id} 前往充电桩")
                if visit.arrivalTime == tick_time:
                    _record_session_event(session, tick_time, f"{robot.id} 开始充电")
                if visit.completionTime == tick_time:
                    session.robot_battery_levels[robot.id] = robot.batteryCapacity
                    _record_session_event(session, tick_time, f"{robot.id} 完成充电")
        session.robot_positions[robot.id] = history[target_time]

    _update_locked_task_assignments(session, result, target_time)
    outbound_pickup_times = _update_task_waypoint_progress(session, result, target_time)

    for task in _all_tasks(session):
        binding = session.shelf_task_bindings.get(task.id)
        if binding is None or binding.kind != "outbound":
            continue
        pickup_time = outbound_pickup_times.get(task.id)
        if pickup_time is not None and session.task_waypoint_progress.get(task.id, 0) >= 1:
            if complete_outbound_pickup(session.shelf_statuses, session.shelf_task_bindings, task.id):
                _record_session_event(session, pickup_time, f"货架 {binding.shelf_id} 已取货")

    completions = _session_task_completion_times(session, result)
    completed_task = False
    for task_id, completion_time in completions.items():
        if completion_time > target_time:
            continue
        newly_completed = task_id not in session.completed_task_ids
        session.completed_task_ids.add(task_id)
        session.task_completion_times[task_id] = completion_time
        session.task_payload_positions.pop(task_id, None)
        session.preferred_task_robot_ids.pop(task_id, None)
        session.locked_task_robot_ids.pop(task_id, None)
        if newly_completed:
            completed_task = True
            _record_session_event(session, completion_time, f"任务 {task_id} 已完成")
            binding = session.shelf_task_bindings.get(task_id)
            if binding is not None and binding.kind == "inbound":
                if complete_inbound_task(session.shelf_statuses, session.shelf_task_bindings, task_id):
                    _record_session_event(session, completion_time, f"货架 {binding.shelf_id} 已放货")

    session.current_time = target_time
    if completed_task:
        _invalidate_plan(session)
```

- [ ] **Step 4: Implement safe-hold result construction and intervention**

Add immediately after `_apply_result_through_time`:

```python
def _safety_hold_result(session: DispatchSession, result: DispatchResult) -> DispatchResult:
    hold_paths: dict[str, list[Cell]] = {}
    for robot in session.scenario.robots:
        position = session.robot_positions.get(robot.id, robot.start)
        history = session.robot_path_history.get(robot.id, [position])
        prefix = _history_prefix(history, position, session.current_time)
        hold_paths[robot.id] = [*prefix, position]

    active_charging_visits = [
        visit
        for visit in result.chargingVisits
        if visit.arrivalTime <= session.current_time
    ]
    return result.model_copy(
        update={
            "paths": hold_paths,
            "chargingVisits": active_charging_visits,
        }
    )


def _apply_safety_hold(
    session: DispatchSession,
    result: DispatchResult,
    conflict: Conflict,
) -> None:
    if conflict.time != session.current_time + 1:
        raise ValueError("safety hold must apply to the next session tick")
    hold_result = _safety_hold_result(session, result)
    _apply_result_through_time(session, hold_result, conflict.time)
    session.last_safety_intervention = conflict
    robot_ids = " / ".join(conflict.robots)
    _record_session_event(
        session,
        conflict.time,
        f"T={conflict.time} 执行安全门拦截 {conflict.type} 冲突：{robot_ids}",
    )
    _invalidate_plan(session)
```

- [ ] **Step 5: Replace `_advance_session` with boundary-aware control flow**

Use this implementation, retaining the existing dynamic/window helpers:

```python
def _advance_session(session: DispatchSession, target_time: int) -> None:
    if target_time <= session.current_time:
        return

    result = session.last_result
    if result is None:
        result = _build_result(session).result

    dynamic_trigger_time = _next_scenario_dynamic_trigger_time(session, target_time)
    window_trigger_time = _next_rolling_window_trigger_time(session, target_time)
    active_completion_time = _next_active_task_completion_time(session, result, target_time)
    safety_conflict = (
        _first_execution_conflict(result, session.current_time, target_time)
        if session.options.avoidConflicts
        else None
    )
    next_trigger_time = _first_crossed_time(
        session.current_time,
        target_time,
        [
            dynamic_trigger_time,
            window_trigger_time,
            active_completion_time,
            safety_conflict.time if safety_conflict is not None else None,
        ],
    )

    if next_trigger_time is not None and next_trigger_time < target_time:
        _advance_session(session, next_trigger_time)
        if session.last_safety_intervention is not None:
            return
        _advance_session(session, target_time)
        return

    if safety_conflict is not None and safety_conflict.time == target_time:
        if target_time > session.current_time + 1:
            _advance_session(session, target_time - 1)
            if session.last_safety_intervention is not None:
                return
            _advance_session(session, target_time)
            return

        _apply_safety_hold(session, result, safety_conflict)
        if dynamic_trigger_time == target_time:
            _activate_scenario_dynamic(session, target_time, result)
        if window_trigger_time == target_time:
            _activate_rolling_window(session, target_time)
        return

    _apply_result_through_time(session, result, target_time)
    if dynamic_trigger_time == target_time:
        _activate_scenario_dynamic(session, target_time, result)
    if window_trigger_time == target_time:
        _activate_rolling_window(session, target_time)
```

- [ ] **Step 6: Run the vertex regression and nearby session tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py::test_session_safety_gate_holds_fleet_at_first_vertex_conflict backend\tests\test_sessions.py::test_session_tick_tracks_runtime_state backend\tests\test_sessions.py::test_session_tick_reuses_plan_between_runtime_changes -q
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py
git commit -m "feat: stop online execution before vertex conflicts"
```

---

### Task 4: Edge conflicts, business semantics, repeated holds, and baseline compatibility

**Files:**
- Modify: `backend/app/sessions.py:687-779,1224-1434`
- Test: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `_apply_safety_hold(...)` and `_safety_test_result(...)`.
- Verifies: actual path history, battery, task progress, service time, reset/read lifecycle, repeated no-solution behavior, dynamic timing and baseline comparison.

- [ ] **Step 1: Add a direct edge-swap safety test**

Add to `backend/tests/test_sessions.py`:

```python
def test_session_safety_gate_blocks_reverse_edge_swap() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "edge-safety",
            "name": "edge-safety",
            "description": "反向边交换安全门测试",
            "width": 3,
            "height": 2,
            "obstacles": [],
            "zones": {"warehouse": [[0, 0]], "inspection": [], "delivery": [], "charging": []},
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 0], "battery": 80, "load": 1},
                {"id": "R2", "name": "R2", "start": [1, 0], "battery": 80, "load": 1},
            ],
            "tasks": [],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )
    conflict = Conflict(time=1, type="edge", robots=["R1", "R2"], cell=(1, 0))
    result = _safety_test_result(
        scenario,
        {"R1": [(0, 0), (1, 0)], "R2": [(1, 0), (0, 0)]},
        [conflict],
    )
    session = sessions_module.DispatchSession(
        session_id="edge-safety",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
        robot_positions={"R1": (0, 0), "R2": (1, 0)},
        robot_path_history={"R1": [(0, 0)], "R2": [(1, 0)]},
        robot_travelled_distance={"R1": 0, "R2": 0},
        robot_battery_levels={"R1": 80, "R2": 80},
        last_result=result,
        planning_started=True,
    )

    sessions_module._advance_session(session, 1)

    assert session.current_time == 1
    assert session.robot_path_history == {
        "R1": [(0, 0), (0, 0)],
        "R2": [(1, 0), (1, 0)],
    }
    assert session.robot_travelled_distance == {"R1": 0, "R2": 0}
    assert session.robot_battery_levels == {"R1": 80, "R2": 80}
    assert session.last_safety_intervention == conflict
```

- [ ] **Step 2: Add task-progress, service-time, and charging tests using actual held paths**

Add one delivery test where a planned pickup at the conflict tick must not complete:

```python
def test_safety_hold_does_not_complete_unexecuted_delivery_pickup() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "held-pickup",
            "name": "held-pickup",
            "description": "安全等待不得完成未执行取货",
            "width": 3,
            "height": 2,
            "obstacles": [],
            "zones": {"warehouse": [[0, 1]], "inspection": [], "delivery": [[2, 1]], "charging": []},
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 1], "battery": 80, "load": 1},
                {"id": "R2", "name": "R2", "start": [0, 0], "battery": 80, "load": 1},
                {"id": "R3", "name": "R3", "start": [1, 0], "battery": 80, "load": 1},
            ],
            "tasks": [
                {
                    "id": "D1",
                    "type": "delivery",
                    "title": "等待中的取货",
                    "priority": 2,
                    "pickup": [1, 1],
                    "dropoff": [2, 1],
                    "demand": 1,
                }
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )
    task = scenario.tasks[0]
    conflict = Conflict(time=1, type="edge", robots=["R2", "R3"], cell=(1, 0))
    result = _safety_test_result(
        scenario,
        {
            "R1": [(0, 1), (1, 1), (2, 1)],
            "R2": [(0, 0), (1, 0)],
            "R3": [(1, 0), (0, 0)],
        },
        [conflict],
        [Assignment(robotId="R1", tasks=[task])],
    )
    session = sessions_module.DispatchSession(
        session_id="held-pickup",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
        robot_positions={robot.id: robot.start for robot in scenario.robots},
        robot_path_history={robot.id: [robot.start] for robot in scenario.robots},
        robot_travelled_distance={robot.id: 0 for robot in scenario.robots},
        robot_battery_levels={robot.id: robot.battery for robot in scenario.robots},
        last_result=result,
        planning_started=True,
    )

    sessions_module._advance_session(session, 1)

    assert session.task_waypoint_progress.get("D1", 0) == 0
    assert "D1" not in session.task_payload_positions
    assert "D1" not in session.completed_task_ids
```

Add a service test where the task target was already reached before the hold:

```python
def test_safety_hold_counts_service_time_already_started_at_target() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "held-service",
            "name": "held-service",
            "description": "安全等待继续累计已开始作业",
            "width": 3,
            "height": 2,
            "obstacles": [],
            "zones": {"warehouse": [[0, 1]], "inspection": [[0, 1]], "delivery": [], "charging": []},
            "robots": [
                {"id": "R1", "name": "R1", "start": [0, 1], "battery": 80, "load": 1},
                {"id": "R2", "name": "R2", "start": [0, 0], "battery": 80, "load": 1},
                {"id": "R3", "name": "R3", "start": [1, 0], "battery": 80, "load": 1},
            ],
            "tasks": [
                {
                    "id": "I1",
                    "type": "inspection",
                    "title": "原地作业",
                    "priority": 2,
                    "targets": [[0, 1]],
                    "serviceTime": 1,
                }
            ],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )
    task = scenario.tasks[0]
    conflict = Conflict(time=1, type="edge", robots=["R2", "R3"], cell=(1, 0))
    result = _safety_test_result(
        scenario,
        {
            "R1": [(0, 1), (0, 1)],
            "R2": [(0, 0), (1, 0)],
            "R3": [(1, 0), (0, 0)],
        },
        [conflict],
        [Assignment(robotId="R1", tasks=[task])],
    )
    session = sessions_module.DispatchSession(
        session_id="held-service",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
        robot_positions={robot.id: robot.start for robot in scenario.robots},
        robot_path_history={robot.id: [robot.start] for robot in scenario.robots},
        robot_travelled_distance={robot.id: 0 for robot in scenario.robots},
        robot_battery_levels={robot.id: robot.battery for robot in scenario.robots},
        last_result=result,
        planning_started=True,
    )

    sessions_module._advance_session(session, 1)

    assert session.task_waypoint_progress["I1"] == 1
    assert session.task_completion_times["I1"] == 1
    assert "I1" in session.completed_task_ids
```

Add `ChargingVisit` to the imports from `backend.app.schemas`, then add a charging-continuity test:

```python
def test_safety_hold_keeps_an_active_charge_completion() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "held-charge",
            "name": "held-charge",
            "description": "安全等待保持已开始充电的完成语义",
            "width": 3,
            "height": 2,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": [], "charging": [[0, 1]]},
            "robots": [
                {
                    "id": "R1",
                    "name": "R1",
                    "start": [0, 1],
                    "battery": 10,
                    "batteryCapacity": 100,
                    "load": 1,
                },
                {"id": "R2", "name": "R2", "start": [0, 0], "battery": 80, "load": 1},
                {"id": "R3", "name": "R3", "start": [1, 0], "battery": 80, "load": 1},
            ],
            "tasks": [],
            "dynamic": {"triggerTime": 0, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )
    conflict = Conflict(time=2, type="edge", robots=["R2", "R3"], cell=(1, 0))
    result = _safety_test_result(
        scenario,
        {
            "R1": [(0, 1), (0, 1), (0, 1)],
            "R2": [(0, 0), (0, 0), (1, 0)],
            "R3": [(1, 0), (1, 0), (0, 0)],
        },
        [conflict],
    ).model_copy(
        update={
            "chargingVisits": [
                ChargingVisit(
                    robotId="R1",
                    station=(0, 1),
                    departureTime=0,
                    arrivalTime=1,
                    completionTime=2,
                )
            ]
        }
    )
    session = sessions_module.DispatchSession(
        session_id="held-charge",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
        current_time=1,
        robot_positions={robot.id: robot.start for robot in scenario.robots},
        robot_path_history={robot.id: [robot.start, robot.start] for robot in scenario.robots},
        robot_travelled_distance={robot.id: 0 for robot in scenario.robots},
        robot_battery_levels={"R1": 10, "R2": 80, "R3": 80},
        last_result=result,
        planning_started=True,
    )

    sessions_module._advance_session(session, 2)

    assert session.robot_battery_levels["R1"] == 100
    assert any(event.text == "R1 完成充电" for event in session.event_notes)
```

- [ ] **Step 3: Add intervention lifecycle tests**

Add focused assertions:

```python
def test_safety_intervention_persists_on_read_and_reset_clears_it() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": _forced_safety_gate_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    ).json()
    session_id = created["sessionId"]
    held = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 8}).json()
    read_back = client.get(f"/api/sessions/{session_id}").json()
    runtime_update = client.post(
        f"/api/sessions/{session_id}/tasks",
        json={
            "task": {
                "id": "M1",
                "type": "inspection",
                "title": "拦截后的运行时任务",
                "priority": 2,
                "releaseTime": held["currentTime"],
                "targets": [[0, 0]],
            }
        },
    ).json()
    reset = client.post(f"/api/sessions/{session_id}/reset").json()

    assert held["safetyIntervention"] is not None
    assert read_back["safetyIntervention"] == held["safetyIntervention"]
    assert runtime_update["safetyIntervention"] == held["safetyIntervention"]
    assert reset["safetyIntervention"] is None


def test_next_safe_tick_clears_the_previous_safety_intervention() -> None:
    client = TestClient(app)
    safe_scenario = scenario_payload()
    safe_scenario["robots"] = safe_scenario["robots"][:1]
    safe_scenario["tasks"] = []
    safe_scenario["dynamic"] = {
        "triggerTime": 0,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [],
    }
    created = client.post(
        "/api/sessions",
        json={
            "scenario": safe_scenario,
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    ).json()
    session_id = created["sessionId"]
    sessions_module._sessions[session_id].last_safety_intervention = Conflict(
        time=0,
        type="vertex",
        robots=["R1", "R2"],
        cell=(0, 0),
    )

    response = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 1})

    assert response.status_code == 200
    assert response.json()["currentTime"] == 1
    assert response.json()["safetyIntervention"] is None
```

- [ ] **Step 4: Add baseline-mode compatibility test**

```python


def test_baseline_mode_keeps_existing_conflict_execution_behavior() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": _forced_safety_gate_scenario(),
            "options": {"avoidConflicts": False, "includeDynamic": False},
        },
    ).json()
    response = client.post(
        f"/api/sessions/{created['sessionId']}/tick",
        json={"currentTime": 1},
    ).json()

    positions = [tuple(state["position"]) for state in response["robotStates"]]
    assert response["currentTime"] == 1
    assert response["safetyIntervention"] is None
    assert len(set(positions)) < len(positions)
    assert response["metricsHistory"][-1]["activeConflictCount"] == 1
```

- [ ] **Step 5: Add same-tick dynamic activation test**

Add an internal same-tick dynamic test so the dynamic event cannot disappear behind the intervention:

```python
def test_safety_hold_preserves_dynamic_trigger_on_the_same_absolute_tick() -> None:
    scenario_payload_value = _forced_safety_gate_scenario()
    scenario_payload_value["dynamic"] = {
        "triggerTime": 1,
        "blockedCells": [],
        "failedRobots": [],
        "tasks": [
            {
                "id": "E1",
                "type": "emergency",
                "title": "同 tick 动态任务",
                "priority": 4,
                "releaseTime": 1,
                "target": [2, 0],
            }
        ],
    }
    scenario = Scenario.model_validate(scenario_payload_value)
    conflict = Conflict(time=1, type="edge", robots=["R1", "R2"], cell=(1, 0))
    result = _safety_test_result(
        scenario,
        {"R1": [(0, 0), (1, 0)], "R2": [(2, 0), (0, 0)]},
        [conflict],
    )
    session = sessions_module.DispatchSession(
        session_id="same-tick-dynamic-safety",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=True),
        robot_positions={robot.id: robot.start for robot in scenario.robots},
        robot_path_history={robot.id: [robot.start] for robot in scenario.robots},
        robot_travelled_distance={robot.id: 0 for robot in scenario.robots},
        robot_battery_levels={robot.id: robot.battery for robot in scenario.robots},
        last_result=result,
        planning_started=True,
    )

    sessions_module._advance_session(session, 1)

    texts = [event.text for event in session.event_notes]
    assert session.current_time == 1
    assert any(text.startswith("T=1 执行安全门拦截") for text in texts)
    assert "T=1 场景动态事件触发" in texts
```

- [ ] **Step 6: Add repeated no-solution history invariant test**

Add an invariant helper and a repeated no-solution API test:

```python
def _assert_executed_history_is_collision_free(histories: dict[str, list[tuple[int, int]]]) -> None:
    horizon = max((len(path) for path in histories.values()), default=0)
    for tick in range(horizon):
        positions = {
            robot_id: path[min(tick, len(path) - 1)]
            for robot_id, path in histories.items()
        }
        assert len(set(positions.values())) == len(positions)
        if tick == 0:
            continue
        robot_ids = sorted(histories)
        for first_index, first_id in enumerate(robot_ids):
            for second_id in robot_ids[first_index + 1 :]:
                first_previous = histories[first_id][min(tick - 1, len(histories[first_id]) - 1)]
                first_current = positions[first_id]
                second_previous = histories[second_id][min(tick - 1, len(histories[second_id]) - 1)]
                second_current = positions[second_id]
                assert not (
                    first_previous == second_current
                    and second_previous == first_current
                    and first_previous != first_current
                )


def test_repeated_unsolved_plans_continue_to_hold_without_executed_conflicts() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": _forced_safety_gate_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    ).json()
    session_id = created["sessionId"]
    first = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 8}).json()
    second = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": first["currentTime"] + 8},
    ).json()

    session = sessions_module._sessions[session_id]
    assert first["safetyIntervention"] is not None
    assert second["safetyIntervention"] is not None
    _assert_executed_history_is_collision_free(session.robot_path_history)
```

- [ ] **Step 7: Run the complete safety-focused session subset**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -k "safety or conflict_states or integrated_demo_avoidance" -q
```

Expected: all selected tests PASS.

- [ ] **Step 8: Run all backend session tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -q
```

Expected: all session tests PASS.

- [ ] **Step 9: Commit Task 4**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py
git commit -m "test: close online safety gate edge cases"
```

---

### Task 5: Frontend pause, explanation, and map highlighting

**Files:**
- Modify: `frontend/src/main.tsx:201-212,613-628,705-723,1163-1199,1217-1270,2050-2130`
- Modify: `frontend/src/main.test.ts:7-61,940-990`
- Modify: `frontend/src/styles.css:93-111,779-790`

**Interfaces:**
- Consumes: `SessionResult.safetyIntervention: Conflict | null`
- Produces: `shouldPauseForSafetyIntervention(intervention: Conflict | null) -> boolean`
- Produces: `safetyInterventionLabel(intervention: Conflict | null) -> string | null`
- Produces: `mergeSafetyInterventionMarker(markers: Conflict[], intervention: Conflict | null, currentTime: number) -> Conflict[]`
- Produces: `isSafetyInterventionRobot(robotId: string | null, intervention: Conflict | null, currentTime: number) -> boolean`

- [ ] **Step 1: Write failing pure frontend tests**

Import the four new helpers in `frontend/src/main.test.ts`, then add:

```ts
describe("execution safety intervention", () => {
  const intervention = {
    time: 7,
    type: "vertex" as const,
    robots: ["R1", "R2"],
    cell: [3, 2] as [number, number]
  };

  it("pauses only for a structured safety intervention", () => {
    expect(shouldPauseForSafetyIntervention(intervention)).toBe(true);
    expect(shouldPauseForSafetyIntervention(null)).toBe(false);
  });

  it("formats the status without parsing event text", () => {
    expect(safetyInterventionLabel(intervention)).toBe("T=7 安全门已拦截顶点冲突：R1 / R2");
    expect(safetyInterventionLabel({ ...intervention, type: "edge" })).toBe(
      "T=7 安全门已拦截边交换冲突：R1 / R2"
    );
    expect(safetyInterventionLabel(null)).toBeNull();
  });

  it("adds the intercepted cell and robots only at the intervention tick", () => {
    expect(mergeSafetyInterventionMarker([], intervention, 7)).toEqual([intervention]);
    expect(mergeSafetyInterventionMarker([intervention], intervention, 7)).toEqual([intervention]);
    expect(mergeSafetyInterventionMarker([], intervention, 8)).toEqual([]);
    expect(isSafetyInterventionRobot("R1", intervention, 7)).toBe(true);
    expect(isSafetyInterventionRobot("R3", intervention, 7)).toBe(false);
    expect(isSafetyInterventionRobot("R1", intervention, 8)).toBe(false);
  });

  it("does not apply a safety intervention from an invalidated request generation", async () => {
    const coordinator = createSessionRequestCoordinator();
    const staleGeneration = coordinator.currentGeneration();
    const stalePayload = Promise.resolve(intervention);
    coordinator.invalidate();
    let playing = true;

    const payload = await stalePayload;
    if (coordinator.isCurrent(staleGeneration) && shouldPauseForSafetyIntervention(payload)) {
      playing = false;
    }

    expect(playing).toBe(true);
  });
});
```

- [ ] **Step 2: Run frontend tests and verify RED**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts
```

Expected: FAIL because the four helpers do not exist.

- [ ] **Step 3: Implement the pure helpers**

Add to `frontend/src/main.tsx` near the existing conflict helpers:

```ts
export function shouldPauseForSafetyIntervention(intervention: Conflict | null): boolean {
  return intervention !== null;
}

export function safetyInterventionLabel(intervention: Conflict | null): string | null {
  if (!intervention) return null;
  const conflictType = intervention.type === "vertex" ? "顶点冲突" : "边交换冲突";
  return `T=${intervention.time} 安全门已拦截${conflictType}：${intervention.robots.join(" / ")}`;
}

export function mergeSafetyInterventionMarker(
  markers: Conflict[],
  intervention: Conflict | null,
  currentTime: number
): Conflict[] {
  if (!intervention || intervention.time !== currentTime) return markers;
  if (markers.some((marker) => sameConflictAlert(marker, intervention))) return markers;
  return [...markers, intervention];
}

export function isSafetyInterventionRobot(
  robotId: string | null,
  intervention: Conflict | null,
  currentTime: number
): boolean {
  return robotId !== null
    && intervention !== null
    && intervention.time === currentTime
    && intervention.robots.includes(robotId);
}
```

- [ ] **Step 4: Wire automatic pause and status strip**

In `applySessionPayload`, after applying the response time:

```ts
    if (shouldPauseForSafetyIntervention(payload.safetyIntervention)) {
      setPlaying(false);
    }
```

Before rendering, derive:

```ts
  const safetyStatus = safetyInterventionLabel(session?.safetyIntervention ?? null);
```

Add to the status strip after the strategy label:

```tsx
          {safetyStatus ? (
            <span className="safety-status" title={safetyStatus}>{safetyStatus}</span>
          ) : null}
```

- [ ] **Step 5: Wire map conflict-cell and robot highlighting**

Pass the field to `MapBoard`:

```tsx
                    safetyIntervention={session?.safetyIntervention ?? null}
```

Add the following property to each of the three existing `createElement(MapBoard, { ... })` objects in `frontend/src/main.test.ts`, immediately after `unresolvedConflictAlert`:

```ts
      safetyIntervention: null,
```

Add the prop type:

```ts
  safetyIntervention: Conflict | null;
```

Update the active conflict calculation:

```ts
  const activeConflicts = useMemo(() => {
    const plannedMarkers = selectMapConflictMarkers(
      result.conflicts,
      time,
      unresolvedConflictAlert,
      result.paths,
      result.conflictStates
    );
    return new Map(
      mergeSafetyInterventionMarker(plannedMarkers, safetyIntervention, time)
        .map((conflict) => [cellKey(conflict.cell), conflict])
    );
  }, [result.conflicts, result.conflictStates, result.paths, safetyIntervention, time, unresolvedConflictAlert]);
```

Add to each robot cell class list:

```ts
        isSafetyInterventionRobot(robotId, safetyIntervention, time) ? "safety-intervention-robot-cell" : "",
```

- [ ] **Step 6: Add scoped styles**

Add to `frontend/src/styles.css`:

```css
.status-strip .safety-status {
  border-color: rgba(255, 166, 77, 0.65);
  color: #ffd4a3;
}

.cell.safety-intervention-robot-cell .robot-marker {
  box-shadow: 0 0 0 3px rgba(255, 157, 66, 0.9), 0 0 16px rgba(255, 120, 45, 0.7);
}
```

- [ ] **Step 7: Run frontend tests and build**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

Expected: all frontend tests PASS and the Vite production build succeeds.

- [ ] **Step 8: Commit Task 5**

```powershell
git add frontend/src/domain/types.ts frontend/src/main.tsx frontend/src/main.test.ts frontend/src/styles.css
git commit -m "feat(frontend): explain execution safety holds"
```

---

### Task 6: Documentation, full regression, and review

**Files:**
- Modify: `docs/algorithm.md:216-225`
- Modify: `docs/baseline.md:88-96`
- Modify: `docs/demo.md:91-95`
- Modify: `docs/testing-guide.md:375-390,490-505,539`
- Modify: `docs/experiments.md:239-253`
- Modify: `AGENTS.md:180-245`
- Verify: all source and test files changed in Tasks 1-5

**Interfaces:**
- Documents: `SessionResult.safetyIntervention`
- Documents: full-fleet hold, first-dangerous-tick early return, structured frontend pause, baseline exception, and remaining non-MAPF boundary.

- [ ] **Step 1: Update the algorithm and API boundary wording**

Use these exact claims consistently:

```text
- 已实现执行安全门：开启避碰的在线会话会在首个预测顶点或反向边冲突 tick 让全车安全等待，不把冲突动作写入实际路径历史。
- 大跨度 tick 请求在首个危险 tick 提前返回；SessionResult.safetyIntervention 提供绝对时间、冲突类型、机器人和单元格。
- 基线对比、直接调度和实验接口仍可返回或执行预测冲突，用于对照；安全门只属于开启避碰的在线执行链路。
- 当前仍不是完整 MAPF/CBS 求解器；安全门保证不执行不安全动作，不保证任意输入都能找到零冲突路线。
```

Update the three-level conflict wording so the first level is now a code-enforced invariant, while full-horizon solvability remains unimplemented.

- [ ] **Step 2: Update demo and testing instructions**

Add the operator behavior:

```text
当安全门拦截冲突时，播放自动暂停，顶部显示结构化拦截状态，地图高亮冲突单元和涉及机器人。检查事件后再次播放会从实际等待位置重新规划；系统不会降级执行带冲突路径。
```

Keep `integrated-demo` default flow unchanged and do not add the forced one-dimensional case to the persisted frontend scenarios.

- [ ] **Step 3: Run targeted backend suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py backend\tests\test_api_contract.py backend\tests\test_e2e_demo.py backend\tests\test_experiments.py -q
```

Expected: all selected backend tests PASS.

- [ ] **Step 4: Run complete project verification**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: frontend production build PASS, all frontend tests PASS, and all backend tests PASS.

- [ ] **Step 5: Check repository cleanliness and encoding-sensitive diff**

Run:

```powershell
git diff --check
git status --short
```

Expected: `git diff --check` has no output; only intended source/docs changes plus the pre-existing untracked `.superpowers/` and `output/` appear.

- [ ] **Step 6: Commit documentation and verification updates**

```powershell
git add AGENTS.md docs/algorithm.md docs/baseline.md docs/demo.md docs/testing-guide.md docs/experiments.md
git commit -m "docs: document online execution safety gate"
```

- [ ] **Step 7: Request final code review**

Review the complete branch diff against `docs/superpowers/specs/2026-07-18-online-execution-safety-gate-design.md`. Treat any actual executed vertex or reverse-edge conflict, false task completion, battery change during a hold, baseline-mode regression, stale-response regression, or contract mismatch as blocking. Fix all blocking findings, rerun `npm run check`, and only then offer merge or push options.
