# Robot Speed Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each robot's discrete per-cell movement duration affect online assignment, time-aware paths, collision avoidance, session progress, and displayed runtime information.

**Architecture:** Add required `moveTicks` data to scenario and runtime robot contracts. Keep grid connectivity distance separate from movement duration, then use `distance * moveTicks` for assignment timing and a duration-aware A* expansion for actual paths. Session replay and the frontend continue to consume absolute-time paths, so no new timeline protocol is required.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, pytest, React 19, TypeScript, Vitest, Vite.

## Global Constraints

- Keep the discrete grid model; robots never traverse more than one grid edge in a tick.
- `moveTicks` is a required integer from `1` through `3` and means ticks per moved grid cell.
- `serviceTime` remains independent from `moveTicks` and is added only after the final task waypoint is reached.
- Frontend playback rate changes request cadence only; it must not change dispatch time or `moveTicks`.
- Runtime block, failure, restore, manual-task and random-task flows must continue to trigger backend replanning.
- Work only on branch `agent/robot-speed-dispatch`; do not modify `main`.

---

### Task 1: Add Robot Speed to Shared Contracts

**Files:**
- Modify: `backend/app/schemas.py:40-46,203-211`
- Modify: `frontend/src/domain/types.ts:57-63,151-159`
- Modify: `frontend/src/domain/scenarios.json`
- Modify: `backend/tests/test_api_contract.py`
- Modify: `backend/tests/test_validation.py`

**Interfaces:**
- Produces: `Robot.moveTicks: int` and `RobotRuntimeState.moveTicks: int` in backend responses.
- Produces: matching TypeScript `Robot.moveTicks: number` and `RobotRuntimeState.moveTicks: number`.
- Consumes: Pydantic `Field(ge=1, le=3)` validation and existing API contract field comparison helpers.

- [ ] **Step 1: Write failing schema and validation tests**

```python
def test_scenario_rejects_robot_move_ticks_outside_supported_range() -> None:
    response = client.post("/api/dispatch", json={"scenario": scenario_with_robot(moveTicks=4)})
    assert response.status_code == 422

def test_session_result_exposes_robot_move_ticks() -> None:
    payload = create_session_with_robot(moveTicks=2)
    assert payload["robotStates"][0]["moveTicks"] == 2
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_validation.py backend/tests/test_api_contract.py -q`

Expected: failures because `moveTicks` is absent from the models and API contract.

- [ ] **Step 3: Implement the shared fields and default scenario data**

```python
class Robot(ApiModel):
    id: str
    name: str
    start: Cell
    battery: NonNegativeInt
    load: NonNegativeInt
    moveTicks: int = Field(ge=1, le=3)

class RobotRuntimeState(ApiModel):
    robotId: str
    name: str
    position: Cell
    status: Literal["idle", "waiting", "toPickup", "delivering", "inspecting", "failed"]
    battery: int
    load: int
    moveTicks: int
    currentTaskId: str | None = None
```

Add a distinct `moveTicks` value in the default integrated scenario for every robot, and update TypeScript types with the same required fields.

- [ ] **Step 4: Run focused contract and validation tests**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_validation.py backend/tests/test_api_contract.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the contract change**

```powershell
git add backend/app/schemas.py frontend/src/domain/types.ts frontend/src/domain/scenarios.json backend/tests/test_api_contract.py backend/tests/test_validation.py
git commit -m "Add robot movement duration contract"
```

### Task 2: Make Assignment Timing Speed-Aware

**Files:**
- Modify: `backend/app/dispatch.py:248-263,322-335,354-438,630-646`
- Modify: `backend/tests/test_algorithm.py`

**Interfaces:**
- Produces: `robot_task_travel_time(scenario, robot, start, task, extra_blocked, distance_cache) -> float`.
- Consumes: existing `task_distance(...) -> float`; returns `math.inf` for unreachable tasks and otherwise `distance * robot.moveTicks`.
- Produces: beam-search candidate times and path-planning ordering that compare elapsed movement ticks rather than raw grid distance.

- [ ] **Step 1: Write failing assignment tests**

```python
def test_assignment_prefers_faster_robot_when_grid_distance_is_equal() -> None:
    result = run_dispatch(speed_scenario({"R1": 3, "R2": 1}), DispatchOptions())
    assert assigned_robot_id(result, "T1") == "R2"

def test_service_time_is_added_after_speed_adjusted_travel_time() -> None:
    result = run_dispatch(speed_scenario({"R1": 2}, service_time=3), DispatchOptions())
    assert task_completion_times(result.assignments, result.paths)["T1"] == 7
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_algorithm.py -k "faster_robot or speed_adjusted" -q`

Expected: failures because assignment currently compares only raw grid distance.

- [ ] **Step 3: Implement speed-aware movement-time helpers and scoring**

```python
def robot_task_travel_time(
    scenario: Scenario,
    robot: Robot,
    start: Cell,
    task: Task,
    extra_blocked: list[Cell],
    distance_cache: DistanceCache | None = None,
) -> float:
    distance = task_distance(scenario, start, task, extra_blocked, distance_cache)
    return distance * robot.moveTicks if math.isfinite(distance) else math.inf
```

Use this helper for `finish_time`, candidate distance-cost fields, and static path-order estimates. Preserve raw geometric distance only where the metric is explicitly a travelled-grid-cell count.

- [ ] **Step 4: Run focused algorithm tests**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_algorithm.py -q`

Expected: all algorithm tests pass, including the two new speed tests.

- [ ] **Step 5: Commit assignment timing**

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py
git commit -m "Use robot speed in task assignment timing"
```

### Task 3: Build Duration-Aware Time Paths and Reservations

**Files:**
- Modify: `backend/app/dispatch.py:158-217,463-501,504-579,694-828,959-983`
- Modify: `backend/tests/test_algorithm.py`

**Interfaces:**
- Produces: `astar_timed(..., move_ticks: int = 1) -> list[Cell]`.
- Produces: `movement_is_reserved(start, goal, start_time, move_ticks, reservations) -> bool`.
- Consumes: `Reservations.vertices`, `Reservations.edges`, `reserve_path`, `detect_conflicts`, and the explicit repeated-cell path representation.

- [ ] **Step 1: Write failing time-path tests**

```python
def test_slow_robot_path_keeps_the_start_cell_reserved_until_arrival() -> None:
    path = astar_timed(scenario, (0, 0), (1, 0), 0, Reservations(), move_ticks=3)
    assert path == [(0, 0), (0, 0), (0, 0), (1, 0)]

def test_avoidance_waits_when_a_slow_robot_occupies_the_shared_start_cell() -> None:
    result = run_dispatch(two_robot_speed_conflict_scenario(), DispatchOptions(avoidConflicts=True))
    assert result.conflicts == []
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_algorithm.py -k "slow_robot_path or slow_robot_occupies" -q`

Expected: path duration is one tick per edge and the reservation test fails.

- [ ] **Step 3: Implement duration-aware A* transitions**

```python
def movement_is_reserved(
    start: Cell,
    goal: Cell,
    start_time: int,
    move_ticks: int,
    reservations: Reservations,
) -> bool:
    for time_index in range(start_time + 1, start_time + move_ticks):
        if f"{cell_key(start)}@{time_index}" in reservations.vertices:
            return True
    arrival_time = start_time + move_ticks
    return is_reserved(goal, arrival_time, start, reservations)
```

For a neighboring move, append `move_ticks - 1` copies of the source cell before the destination cell. For a wait move, retain the existing one-tick transition. Pass each robot's `moveTicks` through `plan_robot_path`, idle parking planning, and all timed A* calls.

- [ ] **Step 4: Run focused path and conflict tests**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_algorithm.py -q`

Expected: expanded paths preserve zero conflicts when avoidance is enabled.

- [ ] **Step 5: Commit duration-aware path planning**

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py
git commit -m "Plan speed-aware time paths"
```

### Task 4: Preserve Speed Semantics in Online Sessions

**Files:**
- Modify: `backend/app/sessions.py:901-926,1059-1136,1139-1228,1395-1403`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: expanded `DispatchResult.paths` and `Robot.moveTicks`.
- Produces: `RobotRuntimeState.moveTicks` and session task completions based on expanded absolute-time paths.
- Produces: runtime replan results with the same speed semantics after blocked-cell, robot-failure, recovery, and task-insertion APIs.

- [ ] **Step 1: Write failing online-session tests**

```python
def test_online_session_keeps_slow_robot_at_start_during_move_duration() -> None:
    payload = create_speed_session(move_ticks=3)
    payload = tick_session(payload["sessionId"], 2)
    assert robot_state(payload, "R1")["position"] == [0, 0]
    assert robot_state(payload, "R1")["moveTicks"] == 3

def test_runtime_block_replans_using_speed_adjusted_positions() -> None:
    session_id = create_speed_session(move_ticks=2)["sessionId"]
    tick_session(session_id, 1)
    payload = add_block(session_id, [2, 0], current_time=1)
    assert payload["result"]["conflicts"] == []
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sessions.py -k "slow_robot_at_start or speed_adjusted_positions" -q`

Expected: runtime state does not expose `moveTicks` and slow movement timing is not represented.

- [ ] **Step 3: Implement runtime propagation and completion checks**

```python
RobotRuntimeState(
    robotId=robot.id,
    name=robot.name,
    position=position,
    status=status,
    battery=remaining_battery,
    load=robot.load,
    moveTicks=robot.moveTicks,
    currentTaskId=current_task_id,
)
```

Keep task waypoint detection based on `path_at(path, absolute_time)`. Do not multiply `serviceTime`; the expanded path alone accounts for movement duration.

- [ ] **Step 4: Run focused session tests**

Run: `./.venv/Scripts/python.exe -m pytest backend/tests/test_sessions.py -q`

Expected: all session regressions pass, including block, failure, restore, manual task, and stream task flows.

- [ ] **Step 5: Commit online-session integration**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py
git commit -m "Preserve robot speed in online sessions"
```

### Task 5: Render Robot Movement Duration in the Frontend

**Files:**
- Modify: `frontend/src/main.tsx:1127-1315,1347-1365,2072-2080,2642-2655`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/domain/sessionApi.test.ts`

**Interfaces:**
- Consumes: `Robot.moveTicks` and `RobotRuntimeState.moveTicks` from Task 1.
- Produces: `robotMoveDurationLabel(moveTicks: number): string` returning `每格耗时 ${moveTicks} tick`.
- Produces: map hover/runtime information that shows backend-provided speed without recomputing movement or completion time on the frontend.

- [ ] **Step 1: Write failing frontend tests**

```ts
it("formats robot movement duration for runtime information", () => {
  expect(robotMoveDurationLabel(2)).toBe("每格耗时 2 tick");
});

it("keeps moveTicks aligned with the backend runtime robot state contract", () => {
  expect(runtimeRobotState.moveTicks).toBe(3);
});
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `npm --prefix frontend run test -- main.test.ts sessionApi.test.ts`

Expected: the display helper and runtime state field are absent.

- [ ] **Step 3: Implement the label and display fields**

```ts
export function robotMoveDurationLabel(moveTicks: number): string {
  return `每格耗时 ${moveTicks} tick`;
}
```

Display this label in the existing robot map information using the runtime state when it is available and the scenario robot otherwise. Extend scenario import validation so `moveTicks` must be a finite integer in the supported range.

- [ ] **Step 4: Run focused frontend tests and build**

Run: `npm --prefix frontend run test -- main.test.ts sessionApi.test.ts`

Run: `npm --prefix frontend run build`

Expected: all selected tests and TypeScript build pass.

- [ ] **Step 5: Commit frontend support**

```powershell
git add frontend/src/main.tsx frontend/src/main.test.ts frontend/src/domain/sessionApi.test.ts
git commit -m "Display robot movement duration"
```

### Task 6: Update Documentation and Run End-to-End Verification

**Files:**
- Modify: `docs/algorithm.md`
- Modify: `docs/testing-guide.md`
- Modify: `docs/superpowers/plans/2026-07-12-robot-speed-dispatch.md`

**Interfaces:**
- Consumes: final API schema, default scenario, backend tests, frontend tests, and running local platform.
- Produces: documented `moveTicks` semantics and repeatable manual verification steps.

- [ ] **Step 1: Add documentation assertions to the testing guide**

```markdown
1. Start the default integrated scenario and inspect each robot's “每格耗时 N tick” information.
2. Add a task equidistant from a fast and slow robot; confirm the faster robot is assigned.
3. Trigger a block while a slow robot is in an intermediate occupancy tick; confirm replanning preserves a conflict-free result.
4. Confirm a task's configured service time begins only after the final target is reached.
```

- [ ] **Step 2: Run the full automated check**

Run: `npm run check`

Expected: frontend build, frontend tests, and backend tests all pass.

- [ ] **Step 3: Run browser verification on the local platform**

Run: `npm run dev:no-browser`

Verify: default scenario lists distinct robot movement durations; playback keeps a slow robot on its departure cell for the configured intermediate tick; manual task insertion and a runtime block keep backend status online and no active conflict marker remains after replanning.

- [ ] **Step 4: Commit documentation and verification updates**

```powershell
git add docs/algorithm.md docs/testing-guide.md docs/superpowers/plans/2026-07-12-robot-speed-dispatch.md
git commit -m "Document robot speed dispatch behavior"
```
