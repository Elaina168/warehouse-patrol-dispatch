# Input and Resource Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound all user-controlled duration, path, and experiment-batch dimensions before they can create unbounded planning work.

**Architecture:** Centralize exact numeric limits in `backend/app/limits.py`, enforce field limits through Pydantic, enforce cumulative path limits before list growth in `dispatch.py`, and mirror only the user-facing duration limit in the frontend. Keep experiment endpoints and result schemas unchanged except for stricter request validation.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- `MAX_PLANNED_PATH_TICKS = 10_000`.
- `MAX_TASK_SERVICE_TIME = 10_000`.
- `MAX_SCENARIO_CHARGE_TIME = 10_000`.
- `MAX_EXPERIMENT_CASES = 32`.
- A path with 10,000 ticks contains at most 10,001 position nodes.
- Limits must reject or stop work before allocating an oversized list.
- Empty experiment batches and duplicate replan windows are invalid.
- Existing standard/extended fixed experiment endpoints keep their current cases and response contracts.
- Follow RED → GREEN for every production behavior.

---

### Task 1: Define and expose exact resource constants

**Files:**
- Modify: `backend/app/limits.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_schema_constraints.py`

**Interfaces:**
- Produces: `MAX_PLANNED_PATH_TICKS`
- Produces: `MAX_TASK_SERVICE_TIME`
- Produces: `MAX_SCENARIO_CHARGE_TIME`
- Produces: `MAX_EXPERIMENT_CASES`

- [ ] **Step 1: Add duration boundary tests**

Add model-validation tests:

```python
assert Task.model_validate({**task_payload, "serviceTime": 10_000}).serviceTime == 10_000
with pytest.raises(ValidationError):
    Task.model_validate({**task_payload, "serviceTime": 10_001})

assert Scenario.model_validate({**scenario_payload, "chargeTime": 10_000}).chargeTime == 10_000
with pytest.raises(ValidationError):
    Scenario.model_validate({**scenario_payload, "chargeTime": 10_001})
```

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_schema_constraints.py -k "service_time_limit or charge_time_limit"
```

Expected: 10,001 is accepted.

- [ ] **Step 3: Add constants and bounded annotated types**

Add to `limits.py`:

```python
MAX_PLANNED_PATH_TICKS = 10_000
MAX_TASK_SERVICE_TIME = MAX_PLANNED_PATH_TICKS
MAX_SCENARIO_CHARGE_TIME = MAX_PLANNED_PATH_TICKS
MAX_EXPERIMENT_CASES = 32
```

In `schemas.py` define exact annotations:

```python
TaskServiceTimeInt = Annotated[int, Field(ge=0, le=MAX_TASK_SERVICE_TIME)]
ChargeTimeInt = Annotated[int, Field(ge=1, le=MAX_SCENARIO_CHARGE_TIME)]
```

Use them for `Task.serviceTime` and `Scenario.chargeTime`.

- [ ] **Step 4: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_schema_constraints.py -k "service_time or charge_time"
```

Expected: selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/limits.py backend/app/schemas.py backend/tests/test_schema_constraints.py
git commit -m "fix: bound task and charge durations"
```

---

### Task 2: Stop cumulative path expansion at the planning budget

**Files:**
- Modify: `backend/app/dispatch.py`
- Modify: `backend/tests/test_algorithm.py`

**Interfaces:**
- Produces: `PathPlanFailure(taskId: str, detail: TaskFailureDetail)`
- Produces: `_can_append_ticks(path, tick_count) -> bool`
- Changes: `plan_robot_path(...) -> tuple[list[Cell], bool, PathPlanFailure | None]`
- Changes: `PathPlanningCandidate` carries task failure overrides

- [ ] **Step 1: Add service accumulation overflow regression**

Create one robot and two reachable tasks whose individual service times are valid but whose cumulative path would exceed 10,000 ticks. Assert:

```python
result = run_dispatch(scenario, options)
assert len(result.paths["R1"]) <= 10_001
assert result.failureDetails["T2"].category == "permanent"
assert result.failureDetails["T2"].recoveryAction == "fixTaskDefinition"
assert result.failureDetails["T2"].reason == "规划路径超过最大时域 10000 tick"
```

- [ ] **Step 2: Add release-wait and charge overflow regressions**

Cover both loops that currently append one cell per tick:

- a task with `releaseTime=10_000` plus one move;
- a charging visit whose append would exceed the remaining path budget.

Assert no returned path exceeds 10,001 nodes and no intermediate oversized list is created.

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm.py -k "path_tick_budget"
```

Expected: paths exceed the desired limit or return generic failure details.

- [ ] **Step 4: Add a constant-time append guard**

Implement:

```python
def _can_append_ticks(path: list[Cell], tick_count: int) -> bool:
    return tick_count >= 0 and len(path) - 1 + tick_count <= MAX_PLANNED_PATH_TICKS
```

Check before:

- release-time waiting;
- charge-time waiting;
- service-time waiting;
- joining a movement segment.

For a movement segment, calculate the number of new nodes as `max(0, len(segment) - 1)` before `join_paths`.

- [ ] **Step 5: Carry exact path-budget failure details**

Add:

```python
@dataclass(frozen=True)
class PathPlanFailure:
    taskId: str
    detail: TaskFailureDetail
```

The detail is:

```python
TaskFailureDetail(
    reason="规划路径超过最大时域 10000 tick",
    category="permanent",
    recoveryAction="fixTaskDefinition",
)
```

Extend `PathPlanningCandidate` with `failureDetails: dict[str, TaskFailureDetail]`. Merge the selected candidate overrides after normal `build_failure_details()` so the exact path-budget reason wins for the affected task.

- [ ] **Step 6: Preserve score and API semantics**

Path-budget failure must:

- count once in `metrics.failureCount`;
- appear once in `failures`;
- appear in `failureReasons` and `failureDetails`;
- not remove unrelated assignments or deferred tasks;
- not create conflict nodes beyond the capped path.

- [ ] **Step 7: Run GREEN and algorithm regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm.py -k "path_tick_budget or service_time or charging"
```

Expected: selected tests pass.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py
git commit -m "fix: cap planned path expansion"
```

---

### Task 3: Bound experiment batch requests

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_schema_constraints.py`
- Modify: `backend/tests/test_experiments.py`

**Interfaces:**
- Changes: `ReplanWindowExperimentRequest.windows`
- Changes: `ScaleExperimentRequest.cases`
- Produces: duplicate-window validation

- [ ] **Step 1: Add 1/32/33 element boundary tests**

For both request models assert:

```python
assert len(valid_one.windows) == 1
assert len(valid_32.windows) == 32
with pytest.raises(ValidationError):
    ReplanWindowExperimentRequest.model_validate({**payload, "windows": []})
with pytest.raises(ValidationError):
    ReplanWindowExperimentRequest.model_validate({**payload, "windows": list(range(33))})
```

Repeat equivalent coverage for `cases`.

- [ ] **Step 2: Add duplicate-window rejection test**

```python
with pytest.raises(ValidationError, match="windows must be unique"):
    ReplanWindowExperimentRequest.model_validate(
        {**payload, "windows": [4, 4]}
    )
```

- [ ] **Step 3: Prove no planning begins for invalid requests**

At the API layer, monkeypatch `backend.app.experiments.run_dispatch` and assert its call count remains zero for empty, duplicate, and 33-element requests.

- [ ] **Step 4: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_schema_constraints.py backend/tests/test_experiments.py -k "experiment_batch_limit or duplicate_window"
```

Expected: invalid lists are accepted.

- [ ] **Step 5: Add exact Pydantic list constraints**

```python
windows: list[AssignmentReplanWindowInt] = Field(
    min_length=1,
    max_length=MAX_EXPERIMENT_CASES,
)

cases: list[ScaleExperimentScenario] = Field(
    min_length=1,
    max_length=MAX_EXPERIMENT_CASES,
)
```

Add an `after` validator for unique windows that raises exactly `windows must be unique`.

- [ ] **Step 6: Run GREEN and experiment suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_schema_constraints.py backend/tests/test_experiments.py
```

Expected: all schema and experiment tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/schemas.py backend/tests/test_schema_constraints.py backend/tests/test_experiments.py
git commit -m "fix: bound experiment batch sizes"
```

---

### Task 4: Mirror service-time bounds in manual task entry

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`
- Modify: `backend/tests/test_api_contract.py`

**Interfaces:**
- Produces frontend constant: `MAX_TASK_SERVICE_TIME = 10_000`
- Changes: `buildManualTask(...)`

- [ ] **Step 1: Add frontend boundary tests**

Add:

```typescript
expect(buildManualTask({ ...form, serviceTime: 10_001 }, tasks, 0, scenario)?.serviceTime)
  .toBe(10_000);
expect(buildManualTask({ ...form, serviceTime: -1 }, tasks, 0, scenario)?.serviceTime)
  .toBe(0);
```

Also assert fractional values are truncated before clamping.

- [ ] **Step 2: Add API constant alignment regression**

Extend `test_api_contract.py` to read the exact frontend constant and compare it with `backend.app.limits.MAX_TASK_SERVICE_TIME`. Do not use case-insensitive or approximate matching.

- [ ] **Step 3: Run RED**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_api_contract.py -k "service_time_limit"
```

Expected: frontend returns 10,001 and the constant is absent.

- [ ] **Step 4: Implement the exact frontend bound**

Add:

```typescript
const MAX_TASK_SERVICE_TIME = 10_000;
```

Update the number input with `max={MAX_TASK_SERVICE_TIME}` and construct:

```typescript
serviceTime: clamp(
  Math.floor(Number.isFinite(form.serviceTime) ? form.serviceTime : 0),
  0,
  MAX_TASK_SERVICE_TIME
)
```

- [ ] **Step 5: Run GREEN and build**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_api_contract.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/main.tsx frontend/src/main.test.ts backend/tests/test_api_contract.py
git commit -m "fix: align manual task duration limits"
```

---

### Task 5: Resource-boundary verification

**Files:**
- Review: `backend/app/limits.py`
- Review: `backend/app/schemas.py`
- Review: `backend/app/dispatch.py`
- Review: `frontend/src/main.tsx`
- Review: relevant tests

**Interfaces:**
- Verifies all constants and path-budget behavior produced by Tasks 1–4.

- [ ] **Step 1: Run the complete boundary suites**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_schema_constraints.py backend/tests/test_algorithm.py backend/tests/test_experiments.py backend/tests/test_api_contract.py
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts
```

- [ ] **Step 2: Run a safe large-value probe**

Submit `serviceTime=10_001` to `/api/dispatch` and assert HTTP 422 without invoking `run_dispatch`. Submit a cumulative valid-field scenario and assert every returned path contains at most 10,001 nodes.

- [ ] **Step 3: Review allocation sites**

Search:

```powershell
rg -n "range\\(.*serviceTime|range\\(scenario\\.chargeTime|while len\\(path\\).*release" backend/app
```

Confirm every user-controlled tick append checks the budget first.

- [ ] **Step 4: Run diff checks**

```powershell
git diff --check
git status --short
```

- [ ] **Step 5: Commit review corrections if needed**

Add a regression before any correction and commit:

```powershell
git add backend frontend
git commit -m "fix: close resource boundary review gaps"
```
