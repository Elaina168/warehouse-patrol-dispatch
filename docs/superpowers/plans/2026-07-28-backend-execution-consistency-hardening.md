# Backend Execution Consistency Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate zero-battery movement, invalid T=0 robot overlap, rejected future-event partial commits, and incomplete T=0 task completion transitions.

**Architecture:** Keep the existing assignment, timed A*, safety-gate, and in-memory session architecture. Add actual-path energy accounting in `dispatch.py`, a defensive online energy gate in `sessions.py`, a detached session preview for future occupancy validation, and one shared task-completion transition used by both T=0 synchronization and normal ticks.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest.

## Global Constraints

- Do not change task priority, lock, preemption, rolling-window, adaptive-window, or inventory business rules.
- Waiting ticks do not consume battery; only cell-to-cell movement consumes one unit.
- A rejected runtime request must not mutate any authoritative session field.
- Two robots may never share a T=0 start cell.
- T=0 completion must update inventory, events, locks, preferences, and the next plan in the same create response.
- Use UTF-8 and Chinese code comments.
- Follow RED → GREEN for every production behavior.

---

### Task 1: Reject duplicate robot starts

**Files:**
- Modify: `backend/app/validation.py`
- Modify: `backend/tests/test_validation.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `validate_scenario(scenario: Scenario, options: DispatchOptions) -> list[str]`
- Produces: duplicate-start diagnostic text `机器人起点重复：(x, y)`

- [ ] **Step 1: Add definition-level validation regression**

Add a test to `backend/tests/test_validation.py` that builds a valid `2 × 2` scenario with `R1.start == R2.start == (0, 0)` and asserts:

```python
errors = validate_scenario(scenario, DispatchOptions())
assert "机器人起点重复：(0, 0)" in errors
```

- [ ] **Step 2: Add API/session rejection regression**

Add a `POST /api/sessions` test to `backend/tests/test_sessions.py` and assert:

```python
assert response.status_code == 422
assert "机器人起点重复：(0, 0)" in response.json()["detail"]
```

- [ ] **Step 3: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_validation.py backend/tests/test_sessions.py -k "duplicate_robot_start"
```

Expected: both tests fail because no start-coordinate uniqueness diagnostic exists.

- [ ] **Step 4: Implement the uniqueness diagnostic**

Add a helper that preserves exact coordinate identity:

```python
def _duplicate_robot_start_errors(robots: list[Robot]) -> list[str]:
    seen: set[Cell] = set()
    duplicates: list[Cell] = []
    for robot in robots:
        if robot.start in seen and robot.start not in duplicates:
            duplicates.append(robot.start)
        seen.add(robot.start)
    return [f"机器人起点重复：({cell[0]}, {cell[1]})" for cell in duplicates]
```

Call it from `validate_scenario()` before reachability planning.

- [ ] **Step 5: Run GREEN and focused validation**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_validation.py backend/tests/test_sessions.py -k "duplicate_robot_start or validation"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/validation.py backend/tests/test_validation.py backend/tests/test_sessions.py
git commit -m "fix: reject overlapping robot starts"
```

---

### Task 2: Account for actual timed-path battery consumption

**Files:**
- Modify: `backend/app/dispatch.py`
- Modify: `backend/tests/test_algorithm.py`

**Interfaces:**
- Produces: `path_movement_count(path: list[Cell]) -> int`
- Produces: `_join_energy_checked_segment(base, segment, battery) -> tuple[list[Cell], int, bool]`
- Consumes: `plan_robot_path(...) -> tuple[list[Cell], bool]`

- [ ] **Step 1: Add the forced timed-detour regression**

Create the exact `3 × 2` scenario from the review:

- `R1` at `(1, 0)`, emergency-only, high-priority task at `(1, 0)`, `serviceTime=5`.
- `R2` at `(0, 0)`, inspection-only, `battery=batteryCapacity=2`, target `(2, 0)`.
- `avoidConflicts=True`.

Assert the candidate must not expose the unsafe route as successful:

```python
result = run_dispatch(scenario, options)
path = result.paths["R2"]
assert not (
    path == [(0, 0), (0, 1), (1, 1), (2, 1), (2, 0)]
    and "R2" not in " ".join(result.failures)
)
```

Also assert every successful non-charging prefix consumes no more movement units than the robot battery.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm.py -k "timed_detour_battery"
```

Expected: failure because the four-move timed path is accepted with battery two.

- [ ] **Step 3: Add actual movement accounting**

Implement:

```python
def path_movement_count(path: list[Cell]) -> int:
    return sum(
        1
        for previous, current in zip(path, path[1:])
        if previous != current
    )


def _join_energy_checked_segment(
    base: list[Cell],
    segment: list[Cell],
    battery: int,
) -> tuple[list[Cell], int, bool]:
    movement_cost = path_movement_count(segment)
    if movement_cost > battery:
        return base, battery, False
    return join_paths(base, segment), battery - movement_cost, True
```

Use this helper for:

- the route to a selected charging station;
- every task waypoint route.

Ignore the static `next_battery` returned by `task_charge_decision()` after the final timed route exists. After a charging visit completes, set battery to `batteryCapacity`.

- [ ] **Step 4: Preserve the return-to-charge invariant**

After all task waypoints are joined, if the scenario has charging cells, calculate the existing static nearest-station distance from the actual endpoint. Reject the candidate when remaining battery is below that distance.

Do not add a new charging heuristic in this task; safe candidate failure is preferred to an unsafe path.

- [ ] **Step 5: Run GREEN and charging regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm.py -k "battery or charge or charging or timed_detour"
```

Expected: new regression and all existing charging tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py
git commit -m "fix: validate battery against timed paths"
```

---

### Task 3: Add an online zero-battery execution guard

**Files:**
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Produces: `_first_energy_violation(session, result, target_time) -> tuple[int, str] | None`
- Produces: `_apply_energy_hold(session, result, event_time, robot_id) -> None`
- Consumes: `_advance_session(session, target_time) -> bool`

- [ ] **Step 1: Add a defensive invalid-cache regression**

Construct a session object with:

- `R1` battery zero at `(0, 0)`;
- a cached path `[(0, 0), (1, 0)]`;
- no charging visit at T=1.

Advance to T=1 and assert:

```python
assert session.robot_positions["R1"] == (0, 0)
assert session.robot_path_history["R1"][1] == (0, 0)
assert session.robot_battery_levels["R1"] == 0
assert session.last_result is None
assert any("电量不足" in event.text for event in session.event_notes)
```

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py -k "zero_battery_cached_path"
```

Expected: robot moves to `(1, 0)` with battery clamped at zero.

- [ ] **Step 3: Detect the first energy violation before history writes**

Scan each robot path from `session.current_time + 1` through `target_time`, tracking:

- current authoritative battery;
- cell changes;
- charging visit completion ticks.

Return the earliest `(tick, robot_id)` where a movement would begin with zero battery.

- [ ] **Step 4: Apply a safe hold at the violation tick**

Reuse the safety-hold path construction so all robots remain at their authoritative positions for that tick. Record:

```python
f"T={event_time} 电量安全门拦截：{robot_id} 电量不足，保持原位并重新规划"
```

Invalidate the plan. Do not populate `safetyIntervention`, because that field is reserved for vertex/edge conflicts.

- [ ] **Step 5: Integrate into trigger ordering**

Include the first energy-violation tick in `_advance_session()` alongside dynamic, rolling-window, completion, and conflict trigger times. If energy and collision triggers share a tick, apply one full-fleet hold and record both applicable explanations without double-advancing time.

- [ ] **Step 6: Run GREEN and session safety suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py -k "zero_battery or execution_safety or charging"
```

Expected: all selected tests pass and no unsafe history entry exists.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py
git commit -m "fix: stop zero battery online movement"
```

---

### Task 4: Preview future runtime state without mutating the session

**Files:**
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Produces: `_clone_session_for_preview(session: DispatchSession) -> DispatchSession`
- Produces: `_preview_session_at_time(session, target_time) -> DispatchSession`
- Consumes: `add_blocked_cell(session_id, request)`

- [ ] **Step 1: Add the rejected future-block transaction regression**

Use the exact `3 × 1` review scenario:

- `R1` starts `(0, 0)`.
- high-priority `T1` targets `(1, 0)`;
- low-priority `T2` targets `(2, 0)`;
- request block `(2, 0)` at T=2.

Snapshot the complete public session payload before the request. Assert:

```python
assert response.status_code == 409
after = client.get(f"/api/sessions/{session_id}").json()
assert after == before
```

Normalize only access timestamps if GET intentionally touches them; all runtime fields must compare exactly:

- `currentTime`;
- robot states and paths;
- task states and completion count;
- shelf states;
- runtime event count;
- metrics history;
- event log;
- `last_result` through the public result.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py -k "future_block_replan_transaction"
```

Expected: response is 409 but the session has advanced to T=2 and completed both tasks.

- [ ] **Step 3: Implement detached session cloning**

Use `copy.copy()` for the dataclass shell, replace `lock` with a new `RLock`, set `replan_observer=None`, and `deepcopy()` every other dataclass field:

```python
def _clone_session_for_preview(session: DispatchSession) -> DispatchSession:
    preview = copy.copy(session)
    for item in dataclasses.fields(DispatchSession):
        if item.name in {"lock", "replan_observer"}:
            continue
        setattr(preview, item.name, copy.deepcopy(getattr(session, item.name)))
    preview.lock = RLock()
    preview.replan_observer = None
    return preview
```

Do not publish the clone or touch `_sessions`.

- [ ] **Step 4: Implement exact future preview**

```python
def _preview_session_at_time(
    session: DispatchSession,
    target_time: int,
) -> DispatchSession:
    preview = _clone_session_for_preview(session)
    _advance_runtime_event(preview, target_time)
    return preview
```

The preview must execute the same dynamic triggers, completions, rolling-window transitions and replans as the real session.

- [ ] **Step 5: Validate block occupancy against the preview**

For `current_time > session.current_time`:

1. build the preview;
2. inspect `preview.robot_positions`;
3. reject without touching the real session if occupied;
4. otherwise advance and mutate the real session.

Keep the second real-session occupancy assertion as an internal invariant; it must not be the first point at which a user-visible rejection occurs.

- [ ] **Step 6: Prove observer and global state isolation**

Add tests asserting preview:

- does not call `replan_observer`;
- does not change session count or identity;
- does not append metrics/events to the real session;
- produces the same target positions as a separately created control session advanced normally.

- [ ] **Step 7: Run GREEN and runtime-event regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py backend/tests/test_session_concurrency.py -k "future or block or preview or runtime_event"
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py backend/tests/test_session_concurrency.py
git commit -m "fix: preview future blocks transactionally"
```

---

### Task 5: Unify T=0 and tick task completion transitions

**Files:**
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/tests/test_inventory.py`

**Interfaces:**
- Produces: `_complete_session_task(session, task_id, completion_time) -> bool`
- Changes: `_sync_completed_task_states(...) -> set[str]`
- Consumes: `_apply_result_through_time`, `_build_result`

- [ ] **Step 1: Add the T=0 queued-task regression**

Create one robot at `(0, 0)` with:

- `T0`, priority five, inspection target `(0, 0)`;
- `T1`, priority one, inspection target `(2, 0)`.

Assert the create response:

```python
assert state_by_id["T0"]["status"] == "completed"
assert state_by_id["T1"]["assignedRobotId"] == "R1"
assert response["result"]["assignments"][0]["tasks"][0]["id"] == "T1"
```

- [ ] **Step 2: Add T=0 inbound and outbound inventory regressions**

Inbound:

```python
assert task_state["status"] == "completed"
assert shelf_state["status"] == "occupied"
```

Outbound with an initially occupied shelf:

```python
assert task_state["status"] == "completed"
assert shelf_state["status"] == "empty"
```

For both, assert one task-completed event and one inventory event, with no duplicates after ticking to T=1.

- [ ] **Step 3: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py backend/tests/test_inventory.py -k "immediate_completion or t0_inventory"
```

Expected: queued task remains pending and shelf stays reserved.

- [ ] **Step 4: Extract the authoritative completion transition**

Implement:

```python
def _complete_session_task(
    session: DispatchSession,
    task_id: str,
    completion_time: int,
) -> bool:
    if task_id in session.completed_task_ids:
        return False
    session.completed_task_ids.add(task_id)
    session.task_completion_times[task_id] = completion_time
    session.task_payload_positions.pop(task_id, None)
    session.preferred_task_robot_ids.pop(task_id, None)
    session.locked_task_robot_ids.pop(task_id, None)
    _record_session_event(session, completion_time, f"任务 {task_id} 已完成")
    return True
```

Move inbound completion and outbound pickup side effects into shared helpers invoked from both the normal progress path and T=0 synchronization. Inventory bindings make these calls idempotent.

- [ ] **Step 5: Return newly completed IDs from synchronization**

Change `_sync_completed_task_states` to return the set of tasks newly transitioned during the call. It must use `_complete_session_task` rather than duplicating state writes.

- [ ] **Step 6: Add bounded immediate-replan convergence**

In `_build_result`, when `session.current_time == 0` and synchronization returns new completions:

1. invalidate `session.last_result`;
2. rebuild the effective dispatch input;
3. repeat until no new T=0 completion occurs.

Set the maximum number of iterations to `len(_all_tasks(session)) + 1`; exceeding it raises an internal `RuntimeError` with an exact convergence message.

Do not record duplicate metric snapshots for intermediate internal iterations. Only the final response state contributes the T=0 metric snapshot.

- [ ] **Step 7: Replace the normal tick completion duplication**

Use `_complete_session_task` from `_apply_result_through_time`. Preserve exact completion timestamps and existing event ordering.

- [ ] **Step 8: Run GREEN and full session/inventory suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py backend/tests/test_inventory.py
```

Expected: all session and inventory tests pass.

- [ ] **Step 9: Commit**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py backend/tests/test_inventory.py
git commit -m "fix: unify immediate task completion"
```

---

### Task 6: Backend consistency review and verification

**Files:**
- Review: `backend/app/dispatch.py`
- Review: `backend/app/sessions.py`
- Review: `backend/app/validation.py`
- Review: changed backend tests

**Interfaces:**
- Verifies all outputs produced by Tasks 1–5.

- [ ] **Step 1: Run the exact six review reproductions**

Run the new focused tests for:

- timed battery detour;
- cached zero-battery movement;
- future block transaction;
- duplicate starts;
- queued T=0 completion;
- T=0 inventory transition.

Expected: all pass.

- [ ] **Step 2: Run backend tests with warnings as errors**

Use a unique temp directory:

```powershell
$reviewTemp = Join-Path $env:TEMP "warehouse-patrol-backend-hardening-$PID"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -W error --basetemp $reviewTemp backend/tests
```

Expected: all tests pass without warnings.

- [ ] **Step 3: Inspect invariants**

Review the net diff and confirm:

- no path accepted as successful can require movement after battery zero;
- no preview object shares mutable state with the real session;
- T=0 convergence is bounded;
- inventory transitions remain idempotent;
- no API contract field was added.

- [ ] **Step 4: Run diff checks**

```powershell
git diff --check
git status --short
```

Expected: only planned tracked changes plus pre-existing untracked `.superpowers/` and `output/`.

- [ ] **Step 5: Commit review fixes if needed**

If review requires corrections, add a focused regression before each correction, then commit:

```powershell
git add backend/app backend/tests
git commit -m "fix: close backend hardening review gaps"
```
