# Robot Task Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为巡检、配送和突发任务增加机器人类型能力硬约束，并保持默认四台机器人全能力与现有载重行为不变。

**Architecture:** 能力作为 `Robot.capabilities` 静态场景字段，缺失时默认支持全部任务类型。后端通过统一资格判断接入验证、Beam Search、锁定、故障恢复和货物接管；前端只负责导入校验、随机候选过滤和 tooltip 展示。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、pytest、React 19、TypeScript、Vitest、Playwright CLI。

## Global Constraints

- `capabilities` 只允许 `inspection`、`delivery`、`emergency`。
- 字段缺失时默认全能力；显式数组不得为空或包含重复值。
- 默认 `integrated-demo` 四台机器人显式全能力。
- 保留 `robot.load >= task.demand` 现有配送载重约束，不扩展载重模型。
- 手工和随机任务继续使用 `POST /api/sessions/{session_id}/tasks`。
- 不新增主界面面板、实验接口、能力等级或运行时能力修改 API。
- 所有中文源文件保持 UTF-8，代码注释使用中文。

---

### Task 1: 能力模型、默认值与 API 契约

**Files:**
- Modify: `backend/app/schemas.py:1-55`
- Modify: `frontend/src/domain/types.ts:1-66`
- Modify: `backend/tests/test_schema_constraints.py`
- Modify: `backend/tests/test_api_contract.py:227-250`

**Interfaces:**
- Produces: `TaskType = Literal["inspection", "delivery", "emergency"]`
- Produces: `ALL_TASK_TYPES: tuple[TaskType, ...]`
- Produces: `Robot.capabilities: list[TaskType]`
- Produces: frontend `Robot.capabilities?: TaskType[]`

- [ ] **Step 1: Write failing backend schema tests**

Add tests that construct `Robot` with an omitted field, a valid subset, an empty list, duplicates, and an unknown value:

```python
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
```

- [ ] **Step 2: Run schema tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_schema_constraints.py -q`

Expected: FAIL because `Robot` does not expose `capabilities` and currently accepts the extra value only as a forbidden field.

- [ ] **Step 3: Implement backend model**

In `schemas.py`, introduce the shared literal and default:

```python
TaskType = Literal["inspection", "delivery", "emergency"]
ALL_TASK_TYPES: tuple[TaskType, ...] = ("inspection", "delivery", "emergency")
```

Change `Task.type` to `TaskType` and add to `Robot`:

```python
capabilities: list[TaskType] = Field(default_factory=lambda: list(ALL_TASK_TYPES), min_length=1)
```

Extend the existing `Robot` model validator:

```python
if len(set(self.capabilities)) != len(self.capabilities):
    raise ValueError("robot capabilities must be unique")
```

- [ ] **Step 4: Add frontend type and contract assertions**

Add to frontend `Robot`:

```ts
capabilities?: TaskType[];
```

Extend the API contract test so `TaskType` equals both `Task.type` literals and `Robot.capabilities` item literals, and assert `capabilities` is optional in TypeScript because the backend supplies a default.

- [ ] **Step 5: Run schema and contract tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_schema_constraints.py backend/tests/test_api_contract.py -q`

Expected: both files PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/app/schemas.py frontend/src/domain/types.ts backend/tests/test_schema_constraints.py backend/tests/test_api_contract.py
git commit -m "feat: add robot task capability model"
```

### Task 2: 前端导入验证与默认场景

**Files:**
- Modify: `frontend/src/main.tsx:2359-2372`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/domain/scenarios.json:98-133`
- Modify: `frontend/src/domain/view.test.ts:200-250`

**Interfaces:**
- Consumes: `Robot.capabilities?: TaskType[]`
- Produces: `isTaskType(value: unknown): value is TaskType`
- Produces: imported robots accept omission or a unique nonempty task-type subset.

- [ ] **Step 1: Write failing import and scenario tests**

Add parse tests for omitted, valid, empty, duplicate, and unknown capabilities. Extend the persisted scenario test:

```ts
for (const robot of scenario.robots) {
  expect(robot.capabilities).toEqual(["inspection", "delivery", "emergency"]);
}
```

- [ ] **Step 2: Run frontend tests and verify RED**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts domain/view.test.ts`

Expected: FAIL because import validation ignores the new array semantics and the persisted robots do not declare it.

- [ ] **Step 3: Implement import validation**

Add:

```ts
function isTaskType(value: unknown): value is TaskType {
  return value === "inspection" || value === "delivery" || value === "emergency";
}

function isCapabilityList(value: unknown): value is TaskType[] {
  return Array.isArray(value)
    && value.length > 0
    && value.every(isTaskType)
    && new Set(value).size === value.length;
}
```

Extend `isRobot` with:

```ts
&& (value.capabilities === undefined || isCapabilityList(value.capabilities))
```

- [ ] **Step 4: Set all four persisted demo robots to all capabilities**

Add exactly this array to R1, R2, R3, and R4:

```json
"capabilities": ["inspection", "delivery", "emergency"]
```

- [ ] **Step 5: Run frontend tests and verify GREEN**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test`

Expected: all frontend tests PASS.

- [ ] **Step 6: Commit Task 2**

```powershell
git add frontend/src/main.tsx frontend/src/main.test.ts frontend/src/domain/scenarios.json frontend/src/domain/view.test.ts
git commit -m "feat(frontend): validate robot task capabilities"
```

### Task 3: 统一能力判断、验证与任务分配

**Files:**
- Modify: `backend/app/dispatch.py:456-510,1209-1210`
- Modify: `backend/app/validation.py:147-180`
- Modify: `backend/app/sessions.py:1645-1678`
- Modify: `backend/tests/test_algorithm.py`
- Modify: `backend/tests/test_validation.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Produces: `robot_supports_task_type(robot: Robot, task: Task) -> bool`
- Produces: `robot_has_required_load(robot: Robot, task: Task) -> bool`
- Produces: `robot_can_handle_task(robot: Robot, task: Task) -> bool`

- [ ] **Step 1: Write failing assignment tests**

Create a mixed fleet where the nearer robot lacks the task capability and assert all three task types go only to compatible robots. Add a delivery case proving both `delivery` capability and the existing load threshold are required.

```python
assert assigned_robot_by_task["I1"] == "R-INSPECTION"
assert assigned_robot_by_task["D1"] == "R-DELIVERY"
assert assigned_robot_by_task["E1"] == "R-EMERGENCY"
```

- [ ] **Step 2: Write failing validation tests**

Add scenario and runtime insertion cases:

- a task with no robot supporting its type is rejected;
- an omitted capability field preserves legacy acceptance;
- a compatible robot that is currently failed or dynamically blocked remains a recoverable runtime condition rather than a definition error.

- [ ] **Step 3: Run targeted backend tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py backend/tests/test_validation.py backend/tests/test_sessions.py -q`

Expected: capability-specific assertions FAIL because selection currently checks only delivery load.

- [ ] **Step 4: Implement the three predicates**

```python
def robot_supports_task_type(robot: Robot, task: Task) -> bool:
    return task.type in robot.capabilities


def robot_has_required_load(robot: Robot, task: Task) -> bool:
    return task.type != "delivery" or robot.load >= (task.demand or 1)


def robot_can_handle_task(robot: Robot, task: Task) -> bool:
    return robot_supports_task_type(robot, task) and robot_has_required_load(robot, task)
```

Use `robot_can_handle_task` before charge, distance, score, lock preference, validation reachability, and runtime task-definition reachability. Remove equivalent direct load-only filters from these paths.

- [ ] **Step 5: Run targeted backend tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py backend/tests/test_validation.py backend/tests/test_sessions.py -q`

Expected: all targeted files PASS, including existing load regressions.

- [ ] **Step 6: Commit Task 3**

```powershell
git add backend/app/dispatch.py backend/app/validation.py backend/app/sessions.py backend/tests/test_algorithm.py backend/tests/test_validation.py backend/tests/test_sessions.py
git commit -m "feat: enforce robot task capabilities"
```

### Task 4: 失败分类、锁定与故障恢复

**Files:**
- Modify: `backend/app/schemas.py:10-19`
- Modify: `frontend/src/domain/types.ts:169-177`
- Modify: `backend/app/dispatch.py:1213-1305,1423-1470`
- Modify: `frontend/src/main.tsx:1703-1712`
- Modify: `backend/tests/test_algorithm.py`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/tests/test_api_contract.py`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Produces: recovery action `addCapableRobotOrChangeTaskType`
- Preserves: recovery action `addCapableRobotOrReduceDemand` for delivery load only.

- [ ] **Step 1: Write failing failure-semantics tests**

Cover these exact outcomes:

```python
assert detail.category == "permanent"
assert detail.recoveryAction == "addCapableRobotOrChangeTaskType"
```

for no type-compatible robot;

```python
assert detail.category == "temporary"
assert detail.recoveryAction == "restoreRobot"
assert detail.blockingRobotIds == ["R-CAPABLE"]
```

when only the compatible robot is failed; and `relaxLocksOrReplan` when an incompatible locked robot has a reachable compatible alternative. Assert incompatible failed robots never enter `blockingRobotIds`.

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py backend/tests/test_sessions.py backend/tests/test_api_contract.py -q`

Expected: FAIL because the new recovery action and capability-aware classification do not exist.

- [ ] **Step 3: Add recovery action to backend and frontend unions**

Add exactly:

```text
addCapableRobotOrChangeTaskType
```

and map it in the frontend to:

```text
恢复：增加兼容机器人或修改任务类型
```

- [ ] **Step 4: Refactor failure classification by ordered eligibility**

For each task, derive these sets in order:

```python
type_compatible = [robot for robot in scoped_robots if robot_supports_task_type(robot, task)]
fully_capable = [robot for robot in type_compatible if robot_has_required_load(robot, task)]
```

Return type-capability permanent failure before load failure; then evaluate failed robots, blocked reachability, battery feasibility, static reachability, and lock relaxation using only `fully_capable` robots.

- [ ] **Step 5: Run backend and frontend targeted tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py backend/tests/test_sessions.py backend/tests/test_api_contract.py -q`

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: all targeted tests PASS and existing load-specific recovery assertions remain unchanged.

- [ ] **Step 6: Commit Task 4**

```powershell
git add backend/app/schemas.py backend/app/dispatch.py frontend/src/domain/types.ts frontend/src/main.tsx backend/tests/test_algorithm.py backend/tests/test_sessions.py backend/tests/test_api_contract.py frontend/src/main.test.ts
git commit -m "feat: explain task capability failures"
```

### Task 5: 随机任务候选与机器人展示

**Files:**
- Modify: `frontend/src/main.tsx:1265-1271,2158-2210`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Produces: `robotCapabilityLabels(robot: Robot): string[]`
- Produces: `supportedTaskTypes(robots: Robot[]): Set<TaskType>`
- Changes: `buildRandomGeneratedTask` only selects task types in the fleet capability union.

- [ ] **Step 1: Write failing frontend tests**

Add assertions that:

- omitted capabilities display all three Chinese labels;
- an explicit subset displays only its labels;
- a delivery-incapable fleet never generates delivery;
- an inspection-only fleet generates inspection and does not fabricate unsupported emergency or delivery work;
- default all-capability generation keeps existing delivery alternation tests passing.

- [ ] **Step 2: Run frontend tests and verify RED**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL because tooltip and generator do not inspect robot capabilities.

- [ ] **Step 3: Implement display and generator filtering**

Use an explicit label map:

```ts
const taskTypeLabels: Record<TaskType, string> = {
  inspection: "巡检",
  delivery: "取送",
  emergency: "突发"
};
```

Treat omitted capabilities as all three task types. Build the random candidate list only from supported types, while leaving submission through the existing unified endpoint unchanged.

- [ ] **Step 4: Add tooltip line**

Render:

```tsx
<span>能力 {robotCapabilityLabels(robot).join(" / ")}</span>
```

next to the existing battery, load, and move-time information.

- [ ] **Step 5: Run frontend tests and verify GREEN**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test`

Expected: all frontend tests PASS.

- [ ] **Step 6: Commit Task 5**

```powershell
git add frontend/src/main.tsx frontend/src/main.test.ts
git commit -m "feat(frontend): show and honor robot capabilities"
```

### Task 6: 基线、压力证据、文档与真实浏览器验收

**Files:**
- Modify: `backend/tests/test_experiments.py`
- Modify: `backend/tests/test_e2e_demo.py`
- Modify: `docs/algorithm.md`
- Modify: `docs/baseline.md`
- Modify: `docs/demo.md`
- Modify: `docs/testing-guide.md`
- Modify: `docs/experiments.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: existing `POST /api/experiments/scale` endpoint.
- Produces: labeled homogeneous, specialized, and specialized-with-failure evidence without a new API.

- [ ] **Step 1: Add experiment and fixed-demo regressions**

Add a scale experiment request containing:

- `homogeneous-fleet`: all robots support all task types;
- `specialized-fleet`: each task has at least one compatible specialist;
- a focused online/session failure case where only one compatible robot fails and later recovers.

Assert assignments never violate capabilities, default six tasks still complete by T=700, final occupied shelf count remains 13, and conflicts/failures/deadline misses/charging visits remain zero.

- [ ] **Step 2: Run experiment and e2e tests**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_experiments.py backend/tests/test_e2e_demo.py -q`

Expected: PASS after Tasks 1-5.

- [ ] **Step 3: Synchronize documentation**

Document the exact `capabilities` values, omitted-field compatibility, default all-capability demo, existing load constraint boundary, failure recovery ordering, unified task endpoint, and the fact that this feature is unrelated to the unimplemented full-horizon safety gate.

- [ ] **Step 4: Run complete automated verification**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' run check`

Expected: frontend production build PASS, all frontend tests PASS, all backend tests PASS.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 5: Verify real browser behavior**

Start the platform with `& 'C:\nvm4w\nodejs\npm.cmd' run dev:no-browser`, wait for the backend health check and frontend URL printed by the project script, then use Playwright at `1280 x 720` to verify:

- each default robot tooltip shows `能力 巡检 / 取送 / 突发`;
- a manual task still reaches the unified endpoint;
- random generation still inserts legal tasks;
- playback, event log, failure/recovery controls, and console remain functional;
- browser console has zero errors and zero warnings.

After acceptance, stop both services with `& 'C:\nvm4w\nodejs\npm.cmd' run dev:stop` and confirm the development ports are no longer listening.

- [ ] **Step 6: Commit Task 6**

```powershell
git add AGENTS.md docs/algorithm.md docs/baseline.md docs/demo.md docs/testing-guide.md docs/experiments.md backend/tests/test_experiments.py backend/tests/test_e2e_demo.py
git commit -m "docs: document robot task capabilities"
```

- [ ] **Step 7: Request final code review**

Review the full branch diff against `docs/superpowers/specs/2026-07-17-robot-task-capabilities-design.md`, fix all Critical and Important findings, rerun `npm run check`, and only then offer merge/push options.
