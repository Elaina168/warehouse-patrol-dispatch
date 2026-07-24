# Online Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 加固单进程在线会话的并发一致性、输入边界、连续安全停滞诊断、自适应窗口稳定性和浏览器 CORS 默认配置，同时保持现有调度与执行安全语义。

**Architecture:** 在 Pydantic 入口统一拒绝超出比赛范围的场景，在会话注册表与每个 `DispatchSession` 之间建立无全局等待的两阶段锁获取协议。安全停滞和自适应耗时分别作为独立会话状态机；CORS 由纯函数配置模块解析，现有 API 与前端只增加 `SessionResult.safetyStall`。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic 2、pytest、React 19、TypeScript 5、Vitest、PowerShell 7。

## Global Constraints

- 单进程内使用注册表 `RLock` 和每会话 `RLock`；同时持锁时顺序只能是 `_sessions_lock -> session.lock`。
- 不得持有 `_sessions_lock` 阻塞等待繁忙的 `session.lock`。
- 同一会话请求串行；不同会话保持并行。
- 地图单轴最大 64，总格数最大 1024，机器人最大 32。
- 初始、动态和运行时任务合计最大 128；每任务目标最大 64。
- 障碍、货架、各区域和动态封锁列表不得超过地图总格数。
- 相同规范化冲突连续三次时返回 `SessionResult.safetyStall`；仍执行全车安全等待。
- 停滞不得自动失败任务、解除任务锁、关闭会话或执行冲突动作。
- 自适应耗时只使用最近 5 个真实重规划样本，至少 3 个样本；中位数 `>= 60ms` 进入慢状态，`<= 40ms` 退出，其间保持原状态。
- 固定窗口模式不受耗时样本影响。
- CORS 环境变量名精确为 `WAREHOUSE_PATROL_CORS_ORIGINS`；默认只允许 `http://127.0.0.1:5174` 和 `http://localhost:5174`；拒绝 `*`。
- 不增加数据库、Redis、分布式锁、多 worker 会话共享、登录鉴权、限流、CBS/MAPF、局部机器人放行、新实验 API 或新前端面板。
- API 字段保持 camelCase；Python 内部状态保持 snake_case。
- 所有中文文件保持 UTF-8，代码注释使用中文。
- 不删除或提交 `.superpowers/`、`output/`、`frontend/dist/`、缓存或基准结果。

## File Structure

- `backend/app/limits.py`：唯一保存比赛范围输入限制常量。
- `backend/app/schemas.py`：Pydantic 结构长度、跨字段地图面积和任务总量验证；增加 `SafetyStall`。
- `backend/app/sessions.py`：会话锁协议、运行时任务总量、安全停滞状态、自适应样本状态和生命周期。
- `backend/app/replan_window.py`：纯函数计算耗时迟滞状态和窗口决策。
- `backend/app/config.py`：纯函数解析和验证 CORS 来源。
- `backend/app/main.py`：只消费 `cors_allowed_origins()` 配置中间件。
- `backend/tests/test_schema_constraints.py`：所有最大合法值、超一非法值和入口一致性。
- `backend/tests/test_session_concurrency.py`：注册表、会话锁、delete、list、reset、容量竞争和跨会话并行回归。
- `backend/tests/test_sessions.py`：安全停滞和自适应样本的在线生命周期。
- `backend/tests/test_replan_window.py`：中位数、最少样本、60/40ms 迟滞与窗口优先级。
- `backend/tests/test_config.py`：CORS 解析器的纯函数测试。
- `backend/tests/test_health.py`：实际 FastAPI CORS 中间件行为。
- `backend/tests/test_api_contract.py`：`SafetyStall` 前后端字段、可空性和 OpenAPI 契约。
- `backend/tests/conftest.py`：通过注册表锁隔离测试会话存储。
- `frontend/src/domain/types.ts`：增加 `SafetyStall` 和必有可空字段。
- `frontend/src/main.tsx`：在现有状态条显示连续停滞，不新增面板。
- `frontend/src/main.test.ts`：停滞状态文字、暂停和清除后的展示回归。
- `AGENTS.md`、`docs/algorithm.md`、`docs/environment.md`、`docs/testing-guide.md`：同步实现边界与验证命令。

---

### Task 1: Shared input limits

**Files:**
- Create: `backend/app/limits.py`
- Modify: `backend/app/schemas.py:1-104`
- Modify: `backend/app/sessions.py:63-68,404-419`
- Test: `backend/tests/test_schema_constraints.py`
- Test: `backend/tests/test_validation.py`
- Test: `backend/tests/test_experiments.py`

**Interfaces:**
- Produces: `MAX_SCENARIO_AXIS_LENGTH = 64`
- Produces: `MAX_SCENARIO_CELL_COUNT = 1024`
- Produces: `MAX_SCENARIO_ROBOTS = 32`
- Produces: `MAX_SCENARIO_TASKS = 128`
- Produces: `MAX_TASK_TARGETS = 64`
- Preserves: `MAX_SESSION_TASKS` as an alias of `MAX_SCENARIO_TASKS` for existing session tests.
- Errors: all size violations return HTTP `422` before `run_dispatch`.

- [ ] **Step 1: Write failing structural and cross-field tests**

Add imports and helpers to `backend/tests/test_schema_constraints.py`:

```python
from backend.app import schemas
from backend.app.limits import (
    MAX_SCENARIO_AXIS_LENGTH,
    MAX_SCENARIO_CELL_COUNT,
    MAX_SCENARIO_ROBOTS,
    MAX_SCENARIO_TASKS,
    MAX_TASK_TARGETS,
)


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
```

Add exact boundary tests:

```python
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
```

Add cross-field list helpers and tests:

```python
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
```

Add valid-edge tests:

```python
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
```

Add combined-task and entry-consistency tests:

```python
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
    payload["tasks"] = [
        _inspection_task(i)
        for i in range(MAX_SCENARIO_TASKS)
    ]
    payload["dynamic"]["tasks"] = []
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
    ("path", "body"),
    [
        ("/api/dispatch", lambda scenario: {"scenario": scenario}),
        ("/api/sessions", lambda scenario: {"scenario": scenario}),
        ("/api/experiments/conflict-avoidance", lambda scenario: {"scenario": scenario}),
        (
            "/api/experiments/scale",
            lambda scenario: {"cases": [{"label": "oversized", "scenario": scenario}]},
        ),
    ],
)
def test_all_scenario_entry_points_reject_oversized_maps(path, body) -> None:
    scenario = scenario_payload()
    scenario.update(width=64, height=64)

    response = TestClient(app).post(path, json=body(scenario))

    assert response.status_code == 422
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py -q
```

Expected: collection fails because `backend.app.limits` does not exist, or the new one-over-limit cases return `200`.

- [ ] **Step 3: Add exact constants and Pydantic limits**

Create `backend/app/limits.py`:

```python
MAX_SCENARIO_AXIS_LENGTH = 64
MAX_SCENARIO_CELL_COUNT = 1024
MAX_SCENARIO_ROBOTS = 32
MAX_SCENARIO_TASKS = 128
MAX_TASK_TARGETS = 64
```

In `backend/app/schemas.py`, import those constants and define:

```python
ScenarioAxisInt = Annotated[int, Field(gt=0, le=MAX_SCENARIO_AXIS_LENGTH)]
```

Apply exact fields:

```python
class Task(ApiModel):
    id: str
    type: TaskType
    title: str
    priority: PriorityInt
    releaseTime: NonNegativeInt | None = None
    deadline: NonNegativeInt | None = None
    serviceTime: NonNegativeInt | None = None
    targets: list[Cell] | None = Field(default=None, max_length=MAX_TASK_TARGETS)
    pickup: Cell | None = None
    dropoff: Cell | None = None
    demand: PositiveInt | None = None
    target: Cell | None = None


class DynamicEvent(ApiModel):
    triggerTime: NonNegativeInt
    blockedCells: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    failedRobots: list[str] = Field(max_length=MAX_SCENARIO_ROBOTS)
    tasks: list[Task] = Field(max_length=MAX_SCENARIO_TASKS)


class Zones(ApiModel):
    warehouse: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    inspection: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    delivery: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    charging: list[Cell] = Field(default_factory=list, max_length=MAX_SCENARIO_CELL_COUNT)


class Scenario(ApiModel):
    id: str
    name: str
    description: str
    width: ScenarioAxisInt
    height: ScenarioAxisInt
    obstacles: list[Cell] = Field(max_length=MAX_SCENARIO_CELL_COUNT)
    zones: Zones
    shelves: list[Shelf] = Field(default_factory=list, max_length=MAX_SCENARIO_CELL_COUNT)
    robots: list[Robot] = Field(max_length=MAX_SCENARIO_ROBOTS)
    tasks: list[Task] = Field(max_length=MAX_SCENARIO_TASKS)
    dynamic: DynamicEvent
    chargeTime: PositiveInt = 4

    @model_validator(mode="after")
    def validate_size_limits(self) -> "Scenario":
        cell_count = self.width * self.height
        if cell_count > MAX_SCENARIO_CELL_COUNT:
            raise ValueError(
                f"map cell count must be <= {MAX_SCENARIO_CELL_COUNT}: {cell_count}"
            )
        if len(self.tasks) + len(self.dynamic.tasks) > MAX_SCENARIO_TASKS:
            raise ValueError(
                "initial and dynamic task count must be "
                f"<= {MAX_SCENARIO_TASKS}: "
                f"{len(self.tasks) + len(self.dynamic.tasks)}"
            )
        cell_lists = {
            "obstacles": self.obstacles,
            "shelves": self.shelves,
            "zones.warehouse": self.zones.warehouse,
            "zones.inspection": self.zones.inspection,
            "zones.delivery": self.zones.delivery,
            "zones.charging": self.zones.charging,
            "dynamic.blockedCells": self.dynamic.blockedCells,
        }
        for field_name, values in cell_lists.items():
            if len(values) > cell_count:
                raise ValueError(
                    f"{field_name} count must be <= map cell count: "
                    f"{len(values)} > {cell_count}"
                )
        return self
```

In `backend/app/sessions.py`, import `MAX_SCENARIO_TASKS` and replace the standalone `500`:

```python
MAX_SESSION_TASKS = MAX_SCENARIO_TASKS
```

Change `_require_task_capacity` and `_require_initial_task_capacity` size errors to `status_code=422`. Keep the duplicate task ID error at `409`.

- [ ] **Step 4: Run boundary, validation and experiment tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py backend\tests\test_validation.py backend\tests\test_experiments.py -q
```

Expected: all selected tests PASS. If a generated existing fixture exceeds a new limit, inspect that exact fixture; do not widen the approved limits.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/limits.py backend/app/schemas.py backend/app/sessions.py backend/tests/test_schema_constraints.py backend/tests/test_validation.py backend/tests/test_experiments.py
git commit -m "feat: enforce competition input limits"
```

---

### Task 2: Per-session concurrency and registry lifecycle

**Files:**
- Modify: `backend/app/sessions.py:1-180,351-370,422-515`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/test_session_concurrency.py`
- Test: `backend/tests/test_sessions.py:6658-6885`

**Interfaces:**
- Produces: `_sessions_lock: RLock`
- Produces: `DispatchSession.lock: RLock`
- Produces: `DispatchSession.closing: bool`
- Produces: `_locked_session(session_id: str, *, touch_access: bool = True) -> Iterator[DispatchSession]`
- Produces: `_publish_session(session: DispatchSession) -> None`, which prunes and inserts in one registry transaction/retry loop.
- Lock ownership: `_sessions` membership, `closing` and `last_accessed_at` use `_sessions_lock`; remaining mutable session state uses `session.lock`.
- Lock order: simultaneous acquisition is `_sessions_lock -> session.lock`; blocking waits happen outside `_sessions_lock`.

- [ ] **Step 1: Write deterministic concurrency tests**

Create `backend/tests/test_session_concurrency.py` with imports:

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.app.schemas import AddTaskRequest, SessionTickRequest, Task
from backend.tests.helpers import scenario_payload
```

Add helpers:

```python
def _create_session() -> str:
    response = TestClient(app).post("/api/sessions", json={"scenario": scenario_payload()})
    assert response.status_code == 200
    return response.json()["sessionId"]


def _task(task_id: str) -> AddTaskRequest:
    return AddTaskRequest(
        task=Task(
            id=task_id,
            type="inspection",
            title=task_id,
            priority=1,
            targets=[(1, 4)],
        )
    )


def _status(callable_) -> int:
    try:
        callable_()
    except HTTPException as error:
        return error.status_code
    return 200
```

Add tests with no `sleep`:

```python
def test_same_session_duplicate_task_requests_are_serialized() -> None:
    session_id = _create_session()
    with sessions_module._sessions_lock:
        session = sessions_module._sessions[session_id]
    start = Barrier(3)
    lock_held = True
    session.lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    lambda: (
                        start.wait(),
                        _status(lambda: sessions_module.add_task(session_id, _task("DUP"))),
                    )[1]
                )
                for _ in range(2)
            ]
            start.wait()
            session.lock.release()
            lock_held = False
            statuses = sorted(future.result(timeout=5) for future in futures)
    finally:
        if lock_held:
            session.lock.release()

    assert statuses == [200, 409]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert [task.id for task in session.scenario.tasks].count("DUP") == 1
```

Add a capacity race:

```python
def test_session_capacity_check_and_append_are_atomic(monkeypatch) -> None:
    session_id = _create_session()
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        current_count = len(sessions_module._all_known_tasks(session))
    monkeypatch.setattr(
        sessions_module,
        "MAX_SESSION_TASKS",
        current_count + 1,
    )
    start = Barrier(3)
    with sessions_module._sessions_lock:
        session = sessions_module._sessions[session_id]
    lock_held = True
    session.lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                lambda: (
                    start.wait(),
                    _status(
                        lambda: sessions_module.add_task(
                            session_id,
                            _task("CAP-A"),
                        )
                    ),
                )[1]
            )
            second = pool.submit(
                lambda: (
                    start.wait(),
                    _status(
                        lambda: sessions_module.add_task(
                            session_id,
                            _task("CAP-B"),
                        )
                    ),
                )[1]
            )
            start.wait()
            session.lock.release()
            lock_held = False
            statuses = sorted(
                [first.result(timeout=10), second.result(timeout=10)]
            )
    finally:
        if lock_held:
            session.lock.release()

    assert statuses == [200, 422]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert len(sessions_module._all_known_tasks(session)) == current_count + 1
```

Add tick monotonicity:

```python
def test_concurrent_ticks_never_move_time_backward() -> None:
    session_id = _create_session()
    start = Barrier(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            lambda: (start.wait(), _status(
                lambda: sessions_module.tick_session(
                    session_id, SessionTickRequest(currentTime=1)
                )
            ))[1]
        )
        second = pool.submit(
            lambda: (start.wait(), _status(
                lambda: sessions_module.tick_session(
                    session_id, SessionTickRequest(currentTime=2)
                )
            ))[1]
        )
        start.wait()
        statuses = sorted([first.result(timeout=10), second.result(timeout=10)])

    assert statuses in ([200, 200], [200, 422])
    assert sessions_module.get_session(session_id).currentTime == 2
```

Add cross-session progress:

```python
def test_waiting_for_session_a_does_not_block_session_b() -> None:
    session_a_id = _create_session()
    session_b_id = _create_session()
    with sessions_module._sessions_lock:
        session_a = sessions_module._sessions[session_a_id]
    started = Barrier(2)
    lock_held = True
    session_a.lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            waiting_a = pool.submit(
                lambda: (started.wait(), sessions_module.get_session(session_a_id))[1]
            )
            started.wait()
            completed_b = pool.submit(sessions_module.get_session, session_b_id)
            assert completed_b.result(timeout=5).sessionId == session_b_id
            session_a.lock.release()
            lock_held = False
            assert waiting_a.result(timeout=5).sessionId == session_a_id
    finally:
        if lock_held:
            session_a.lock.release()
```

Add tick/reset coherence:

```python
def test_tick_and_reset_leave_one_complete_state() -> None:
    session_id = _create_session()
    start = Barrier(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tick = pool.submit(
            lambda: (
                start.wait(),
                sessions_module.tick_session(
                    session_id,
                    SessionTickRequest(currentTime=1),
                ),
            )[1]
        )
        reset = pool.submit(
            lambda: (start.wait(), sessions_module.reset_session(session_id))[1]
        )
        start.wait()
        tick.result(timeout=10)
        reset.result(timeout=10)

    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert all(
            len(path) == session.current_time + 1
            for path in session.robot_path_history.values()
        )
        if session.current_time == 0:
            assert session.runtime_task_count == 0
            assert session.runtime_blocked_cells == []
            assert session.runtime_failed_robot_ids == []
        else:
            assert session.current_time == 1
```

Add delete ordering:

```python
def test_delete_waits_for_an_already_running_tick(monkeypatch) -> None:
    session_id = _create_session()
    entered = Barrier(2)
    release = Barrier(2)
    real_advance = sessions_module._advance_session

    def blocking_advance(session, target_time):
        entered.wait()
        release.wait()
        return real_advance(session, target_time)

    monkeypatch.setattr(sessions_module, "_advance_session", blocking_advance)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tick = pool.submit(
            sessions_module.tick_session,
            session_id,
            SessionTickRequest(currentTime=1),
        )
        entered.wait()
        delete = pool.submit(sessions_module.delete_session, session_id)
        release.wait()
        assert tick.result(timeout=10).currentTime == 1
        assert delete.result(timeout=10).deleted is True

    with pytest.raises(HTTPException) as error:
        sessions_module.get_session(session_id)
    assert error.value.status_code == 404
```

Add atomic list observation:

```python
def test_list_waits_for_complete_task_append(monkeypatch) -> None:
    session_id = _create_session()
    entered = Barrier(2)
    release = Barrier(2)
    real_invalidate = sessions_module._invalidate_plan

    def blocking_invalidate(session):
        real_invalidate(session)
        entered.wait()
        release.wait()

    monkeypatch.setattr(sessions_module, "_invalidate_plan", blocking_invalidate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        add = pool.submit(sessions_module.add_task, session_id, _task("ATOMIC"))
        entered.wait()
        listed = pool.submit(sessions_module.list_sessions)
        release.wait()
        assert add.result(timeout=10).runtimeTaskCount == 1
        summary = next(
            item
            for item in listed.result(timeout=10)
            if item.sessionId == session_id
        )
    assert summary.runtimeTaskCount == 1
```

Add an observed lock helper for identity rechecks:

```python
from threading import RLock


class ObservedRLock:
    def __init__(self) -> None:
        self._lock = RLock()
        self.blocking_wait_started = Barrier(2)

    def acquire(self, blocking: bool = True) -> bool:
        if not blocking:
            return self._lock.acquire(blocking=False)
        if self._lock.acquire(blocking=False):
            return True
        self.blocking_wait_started.wait()
        return self._lock.acquire()

    def release(self) -> None:
        self._lock.release()
```

Add delete identity:

```python
def test_delete_never_removes_replacement_with_same_id() -> None:
    session_id = _create_session()
    with sessions_module._sessions_lock:
        original = sessions_module._sessions[session_id]
        observed_lock = ObservedRLock()
        original.lock = observed_lock
    observed_lock.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        deletion = pool.submit(sessions_module.delete_session, session_id)
        observed_lock.blocking_wait_started.wait()
        replacement = sessions_module.DispatchSession(
            session_id=session_id,
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
        )
        with sessions_module._sessions_lock:
            sessions_module._sessions[session_id] = replacement
        observed_lock.release()
        with pytest.raises(HTTPException) as error:
            deletion.result(timeout=5)
    assert error.value.status_code == 404
    with sessions_module._sessions_lock:
        assert sessions_module._sessions[session_id] is replacement
```

Add capacity identity and fresh-condition recalculation:

```python
def test_capacity_publish_rechecks_identity_and_capacity(monkeypatch) -> None:
    session_id = _create_session()
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    with sessions_module._sessions_lock:
        original = sessions_module._sessions[session_id]
        observed_lock = ObservedRLock()
        original.lock = observed_lock
        original.last_accessed_at = 0
    incoming = sessions_module.DispatchSession(
        session_id="incoming",
        scenario=original.scenario.model_copy(deep=True),
        options=original.options.model_copy(deep=True),
    )
    observed_lock.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        publishing = pool.submit(sessions_module._publish_session, incoming)
        observed_lock.blocking_wait_started.wait()
        replacement = sessions_module.DispatchSession(
            session_id=session_id,
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
            last_accessed_at=1,
        )
        with sessions_module._sessions_lock:
            sessions_module._sessions[session_id] = replacement
        monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 2)
        observed_lock.release()
        publishing.result(timeout=5)

    with sessions_module._sessions_lock:
        assert sessions_module._sessions[session_id] is replacement
        assert sessions_module._sessions["incoming"] is incoming
```

Add busy-expiration behavior separately:

```python
def test_ttl_cleanup_skips_busy_expired_session_until_next_cleanup() -> None:
    session_id = _create_session()
    with sessions_module._sessions_lock:
        session = sessions_module._sessions[session_id]
        session.last_accessed_at = 0
    session.lock.acquire()
    try:
        sessions_module._cleanup_sessions(sessions_module.SESSION_TTL_SECONDS + 1)
        with sessions_module._sessions_lock:
            assert sessions_module._sessions[session_id] is session
    finally:
        session.lock.release()
    sessions_module._cleanup_sessions(sessions_module.SESSION_TTL_SECONDS + 1)
    with sessions_module._sessions_lock:
        assert session_id not in sessions_module._sessions
```

- [ ] **Step 2: Run concurrency tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_session_concurrency.py -q
```

Expected: tests fail because `DispatchSession.lock`, `closing` and `_sessions_lock` do not exist.

- [ ] **Step 3: Add lock state and the two-phase session context manager**

In `backend/app/sessions.py`, add:

```python
from contextlib import contextmanager
from threading import RLock
from collections.abc import Iterator
```

Add fields:

```python
lock: RLock = field(default_factory=RLock, repr=False, compare=False)
closing: bool = field(default=False, repr=False, compare=False)
```

Place both declarations at the end of `DispatchSession`; do not copy them into Pydantic models or snapshots.

Add registry lock:

```python
_sessions: dict[str, DispatchSession] = {}
_sessions_lock = RLock()
```

Implement the exact two-phase acquisition:

```python
@contextmanager
def _locked_session(
    session_id: str,
    *,
    touch_access: bool = True,
) -> Iterator[DispatchSession]:
    while True:
        waited_session: DispatchSession | None = None
        with _sessions_lock:
            _cleanup_sessions_locked()
            session = _sessions.get(session_id)
            if session is None or session.closing:
                raise HTTPException(
                    status_code=404,
                    detail=f"调度会话不存在：{session_id}",
                )
            if session.lock.acquire(blocking=False):
                if touch_access:
                    session.last_accessed_at = _session_now()
                break
            waited_session = session
        waited_session.lock.acquire()
        waited_session.lock.release()

    try:
        yield session
    finally:
        session.lock.release()
```

The local `session` yielded after the loop must be the exact object successfully locked while the registry lock was held.

Change all public session operations to use one context:

```python
def get_session(session_id: str) -> SessionResult:
    with _locked_session(session_id) as session:
        return _build_result(session)


def reset_session(session_id: str) -> SessionResult:
    with _locked_session(session_id) as session:
        if session.initial_scenario is None or session.initial_options is None:
            raise HTTPException(
                status_code=409,
                detail=f"调度会话缺少初始快照，无法重置：{session_id}",
            )
        _reset_session_runtime(session, updated=True)
        return _build_result(session)
```

Apply that pattern to `reset_session`, `add_task`, `tick_session`, `add_blocked_cell`, `remove_blocked_cell`, `fail_robot` and `restore_robot`. Remove `_require_session`; no public operation may receive an unlocked mutable session.

Split timestamp responsibilities:

```python
def _touch_session_updated(session: DispatchSession) -> None:
    session.updated_at = _session_now()
```

Replace every session-operation call to `_touch_session` whose `updated` argument is `True` with `_touch_session_updated(session)`. Access time is updated only inside `_locked_session` while both locks are held.

- [ ] **Step 4: Implement create, list, cleanup, pruning and delete without global blocking**

Create the initial `SessionResult` before publishing the object:

```python
_initialize_shelf_inventory(session)
initial_result = _build_result(session)
_publish_session(session)
return initial_result
```

`_cleanup_sessions_locked` may remove only sessions whose lock is acquired non-blocking under `_sessions_lock`; busy expired sessions remain for the next cleanup.

Implement it exactly around identity and time rechecks:

```python
def _cleanup_sessions_locked(now: float | None = None) -> None:
    cleanup_time = _session_now() if now is None else now
    candidates = sorted(
        (
            item
            for item in _sessions.values()
            if not item.closing
            and cleanup_time - item.last_accessed_at > SESSION_TTL_SECONDS
        ),
        key=lambda item: item.last_accessed_at,
    )
    for candidate in candidates:
        if not candidate.lock.acquire(blocking=False):
            continue
        try:
            if (
                _sessions.get(candidate.session_id) is candidate
                and not candidate.closing
                and cleanup_time - candidate.last_accessed_at > SESSION_TTL_SECONDS
            ):
                candidate.closing = True
                _sessions.pop(candidate.session_id)
        finally:
            candidate.lock.release()


def _cleanup_sessions(now: float | None = None) -> None:
    with _sessions_lock:
        _cleanup_sessions_locked(now)
```

Implement atomic capacity pruning and publication as one retry loop:

```python
def _publish_session(session: DispatchSession) -> None:
    while True:
        waited_session: DispatchSession | None = None
        with _sessions_lock:
            _cleanup_sessions_locked()
            if len(_sessions) < MAX_SESSIONS:
                session.last_accessed_at = _session_now()
                _sessions[session.session_id] = session
                return
            candidates = sorted(
                (item for item in _sessions.values() if not item.closing),
                key=lambda item: item.last_accessed_at,
            )
            for candidate in candidates:
                if candidate.lock.acquire(blocking=False):
                    try:
                        if _sessions.get(candidate.session_id) is candidate:
                            candidate.closing = True
                            _sessions.pop(candidate.session_id)
                    finally:
                        candidate.lock.release()
                    break
            else:
                if candidates:
                    candidate = candidates[0]
                    candidate.closing = True
                    waited_session = candidate
                else:
                    waited_session = min(
                        _sessions.values(),
                        key=lambda item: item.last_accessed_at,
                    )
        if waited_session is not None:
            waited_session.lock.acquire()
            waited_session.lock.release()
            with _sessions_lock:
                if _sessions.get(waited_session.session_id) is waited_session:
                    waited_session.closing = False
```

After every removal or wait, loop and recompute capacity, identity, access time and ordering. The insertion occurs only while `_sessions_lock` still protects the successful capacity check.

Implement delete with a local target identity:

```python
def delete_session(session_id: str) -> DeleteSessionResult:
    target: DispatchSession | None = None
    while True:
        with _sessions_lock:
            current = _sessions.get(session_id)
            if current is None or (current.closing and current is not target):
                raise HTTPException(
                    status_code=404,
                    detail=f"调度会话不存在：{session_id}",
                )
            if target is None:
                target = current
                target.closing = True
            if current is not target:
                raise HTTPException(
                    status_code=404,
                    detail=f"调度会话不存在：{session_id}",
                )
            if target.lock.acquire(blocking=False):
                try:
                    if _sessions.get(session_id) is target:
                        _sessions.pop(session_id)
                        return DeleteSessionResult(sessionId=session_id, deleted=True)
                finally:
                    target.lock.release()
        target.lock.acquire()
        target.lock.release()
```

Implement list from an ordered ID snapshot, acquiring each summary with `touch_access=False`; skip `404` caused by concurrent deletion and do not include sessions created after the snapshot.

```python
def list_sessions() -> list[SessionSummary]:
    with _sessions_lock:
        _cleanup_sessions_locked()
        session_ids = [
            item.session_id
            for item in sorted(
                (session for session in _sessions.values() if not session.closing),
                key=lambda session: session.last_accessed_at,
                reverse=True,
            )
        ]

    summaries: list[SessionSummary] = []
    for session_id in session_ids:
        try:
            with _locked_session(session_id, touch_access=False) as session:
                summaries.append(_build_session_summary(session))
        except HTTPException as error:
            if error.status_code != 404:
                raise
    return summaries
```

Update `backend/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def clear_session_store() -> Iterator[None]:
    with sessions_module._sessions_lock:
        sessions_module._sessions.clear()
    yield
    with sessions_module._sessions_lock:
        sessions_module._sessions.clear()
```

- [ ] **Step 5: Run concurrency and existing lifecycle tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_session_concurrency.py backend\tests\test_sessions.py -q
```

Expected: all selected tests PASS without deadlock. After pytest exits, this command must print no lingering pytest worker:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*online-runtime-hardening*' }
```

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/app/sessions.py backend/tests/conftest.py backend/tests/test_session_concurrency.py backend/tests/test_sessions.py
git commit -m "fix: serialize online session operations"
```

---

### Task 3: Structured consecutive safety stall

**Files:**
- Modify: `backend/app/schemas.py:193-207,441-457`
- Modify: `backend/app/sessions.py:75-114,179-348,440-464,519-607,867-943`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/tests/test_api_contract.py:130-199`
- Modify: `frontend/src/domain/types.ts:117-132,279-296`
- Modify: `frontend/src/main.tsx:201-218,618-625,2120-2129`
- Modify: `frontend/src/main.test.ts:113-170`

**Interfaces:**
- Produces backend:

```python
class SafetyStall(ApiModel):
    conflict: Conflict
    consecutiveCount: PositiveInt
    firstInterventionTime: NonNegativeInt
    latestInterventionTime: NonNegativeInt
```

- Produces frontend:

```ts
export type SafetyStall = {
  conflict: Conflict;
  consecutiveCount: number;
  firstInterventionTime: number;
  latestInterventionTime: number;
};
```

- Produces: `SessionResult.safetyStall: SafetyStall | None` and TypeScript `SafetyStall | null`.
- Produces: `_clear_safety_stall(session)` and `_track_safety_stall(session, conflict)`.
- Signature: `(Conflict.type, tuple(sorted(Conflict.robots)), Conflict.cell)`; conflict time is excluded.

- [ ] **Step 1: Write failing backend contract and lifecycle tests**

Extend `backend/tests/test_api_contract.py`:

```python
def test_session_safety_stall_is_required_nullable_and_aligned() -> None:
    assert _frontend_fields("SafetyStall") == _backend_fields(schemas.SafetyStall)
    assert "safetyStall" in _backend_fields(schemas.SessionResult)
    assert "safetyStall" in _frontend_fields("SessionResult")
    assert "safetyStall" in _backend_nullable_fields(schemas.SessionResult)
    assert "safetyStall" in _frontend_nullable_fields("SessionResult")
    assert "safetyStall" not in _frontend_optional_fields("SessionResult")
    assert "safetyStall" not in _backend_fields(schemas.DispatchResult)
```

Add `("SafetyStall", schemas.SafetyStall)` to the `model_pairs` list in the existing full frontend/backend field-alignment test.

In `backend/tests/test_sessions.py`, reuse the existing forced-conflict scenario and add:

```python
def _create_forced_conflict_session(client: TestClient) -> str:
    response = client.post(
        "/api/sessions",
        json={
            "scenario": _forced_safety_gate_scenario(),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )
    assert response.status_code == 200
    return response.json()["sessionId"]


def test_third_same_safety_intervention_exposes_stall() -> None:
    client = TestClient(app)
    session_id = _create_forced_conflict_session(client)

    first = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 2}).json()
    second = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 3}).json()
    third = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 4}).json()

    assert first["safetyStall"] is None
    assert second["safetyStall"] is None
    assert third["safetyStall"] == {
        "conflict": third["safetyIntervention"],
        "consecutiveCount": 3,
        "firstInterventionTime": first["safetyIntervention"]["time"],
        "latestInterventionTime": third["safetyIntervention"]["time"],
    }
```

Add deterministic signature and fourth-intervention tests:

```python
def test_stall_signature_normalizes_robots_and_restarts_for_type_or_cell() -> None:
    scenario = Scenario.model_validate(_forced_safety_gate_scenario())
    session = sessions_module.DispatchSession(
        session_id="stall-signature",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
    )
    sessions_module._track_safety_stall(
        session,
        Conflict(time=1, type="vertex", robots=["R2", "R1"], cell=(0, 0)),
    )
    sessions_module._track_safety_stall(
        session,
        Conflict(time=2, type="vertex", robots=["R1", "R2"], cell=(0, 0)),
    )
    sessions_module._track_safety_stall(
        session,
        Conflict(time=3, type="vertex", robots=["R2", "R1"], cell=(0, 0)),
    )
    assert session.last_safety_stall is not None
    assert session.last_safety_stall.consecutiveCount == 3

    sessions_module._track_safety_stall(
        session,
        Conflict(time=4, type="edge", robots=["R1", "R2"], cell=(0, 0)),
    )
    assert session.consecutive_safety_intervention_count == 1
    assert session.last_safety_stall is None
    sessions_module._track_safety_stall(
        session,
        Conflict(time=5, type="edge", robots=["R2", "R1"], cell=(1, 0)),
    )
    assert session.consecutive_safety_intervention_count == 1


def test_fourth_same_intervention_updates_without_duplicate_threshold_event() -> None:
    scenario = Scenario.model_validate(_forced_safety_gate_scenario())
    session = sessions_module.DispatchSession(
        session_id="stall-fourth",
        scenario=scenario,
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
    )
    for time in range(1, 5):
        sessions_module._track_safety_stall(
            session,
            Conflict(
                time=time,
                type="vertex",
                robots=["R1", "R2"],
                cell=(0, 0),
            ),
        )

    assert session.last_safety_stall is not None
    assert session.last_safety_stall.consecutiveCount == 4
    assert session.last_safety_stall.latestInterventionTime == 4
    assert sum(
        "连续安全停滞" in event.text
        for event in session.event_notes
    ) == 1
```

Add lifecycle helpers:

```python
def _seed_safety_stall(session_id: str) -> None:
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        for time in range(1, 4):
            sessions_module._track_safety_stall(
                session,
                Conflict(
                    time=time,
                    type="vertex",
                    robots=["R1", "R2"],
                    cell=(1, 1),
                ),
            )


def _create_normal_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={"scenario": scenario_payload()})
    assert response.status_code == 200
    return response.json()["sessionId"]
```

Add safe-progress and reset tests:

```python
def test_safe_tick_and_reset_clear_stall() -> None:
    client = TestClient(app)
    session_id = _create_normal_session(client)
    _seed_safety_stall(session_id)
    advanced = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 1},
    )
    assert advanced.status_code == 200
    assert advanced.json()["safetyStall"] is None

    _seed_safety_stall(session_id)
    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200
    assert reset.json()["safetyStall"] is None
```

Add successful runtime-change coverage:

```python
@pytest.mark.parametrize(
    ("prepare_path", "prepare_body", "action_path", "action_body"),
    [
        (
            None,
            None,
            "tasks",
            {
                "task": {
                    "id": "CLEAR-STALL",
                    "type": "inspection",
                    "title": "清除停滞",
                    "priority": 1,
                    "targets": [[1, 4]],
                }
            },
        ),
        (None, None, "blocked-cells", {"cell": [1, 1]}),
        (
            "blocked-cells",
            {"cell": [1, 1]},
            "blocked-cells/remove",
            {"cell": [1, 1]},
        ),
        (None, None, "failed-robots", {"robotId": "R1"}),
        (
            "failed-robots",
            {"robotId": "R1"},
            "failed-robots/restore",
            {"robotId": "R1"},
        ),
    ],
)
def test_successful_runtime_changes_clear_stall(
    prepare_path,
    prepare_body,
    action_path,
    action_body,
) -> None:
    client = TestClient(app)
    session_id = _create_normal_session(client)
    if prepare_path is not None:
        prepared = client.post(
            f"/api/sessions/{session_id}/{prepare_path}",
            json=prepare_body,
        )
        assert prepared.status_code == 200
    _seed_safety_stall(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/{action_path}",
        json=action_body,
    )

    assert response.status_code == 200
    assert response.json()["safetyStall"] is None
```

Add idempotent/rejected behavior:

```python
def test_idempotent_and_rejected_runtime_updates_keep_stall() -> None:
    client = TestClient(app)
    session_id = _create_normal_session(client)
    assert client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1]},
    ).status_code == 200
    _seed_safety_stall(session_id)

    duplicate = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [1, 1]},
    )
    rejected = client.post(
        f"/api/sessions/{session_id}/blocked-cells",
        json={"cell": [2, 1]},
    )
    read_back = client.get(f"/api/sessions/{session_id}")

    assert duplicate.status_code == 200
    assert duplicate.json()["safetyStall"]["consecutiveCount"] == 3
    assert rejected.status_code == 409
    assert read_back.json()["safetyStall"]["consecutiveCount"] == 3
```

Extend `test_repeated_unsolved_plans_continue_to_hold_without_executed_conflicts` to make a third tick request, assert its `safetyStall.consecutiveCount == 3`, and retain `_assert_executed_history_is_collision_free`. Keep the existing service, charging and inventory hold tests unchanged and include them in the Task 3 test command as regression evidence.

- [ ] **Step 2: Run backend tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_api_contract.py::test_session_safety_stall_is_required_nullable_and_aligned backend\tests\test_sessions.py -k "safety_stall or same_safety_intervention or stall_holds" -q
```

Expected: contract test fails because `SafetyStall` is absent; lifecycle payloads have no `safetyStall`.

- [ ] **Step 3: Implement backend contract and state machine**

Add `SafetyStall` immediately after `Conflict` in `backend/app/schemas.py`:

```python
class SafetyStall(ApiModel):
    conflict: Conflict
    consecutiveCount: PositiveInt
    firstInterventionTime: NonNegativeInt
    latestInterventionTime: NonNegativeInt
```

Add this field immediately after `SessionResult.safetyIntervention`:

```python
safetyStall: SafetyStall | None = None
```

Add session state:

```python
safety_stall_signature: tuple[str, tuple[str, ...], Cell] | None = None
consecutive_safety_intervention_count: int = 0
safety_stall_first_time: int | None = None
last_safety_stall: SafetyStall | None = None
```

Implement:

```python
def _clear_safety_stall(session: DispatchSession) -> None:
    session.safety_stall_signature = None
    session.consecutive_safety_intervention_count = 0
    session.safety_stall_first_time = None
    session.last_safety_stall = None


def _track_safety_stall(session: DispatchSession, conflict: Conflict) -> None:
    signature = (
        conflict.type,
        tuple(sorted(conflict.robots)),
        conflict.cell,
    )
    if signature == session.safety_stall_signature:
        session.consecutive_safety_intervention_count += 1
    else:
        session.safety_stall_signature = signature
        session.consecutive_safety_intervention_count = 1
        session.safety_stall_first_time = conflict.time
    count = session.consecutive_safety_intervention_count
    if count < 3:
        session.last_safety_stall = None
        return
    first_time = session.safety_stall_first_time
    if first_time is None:
        raise AssertionError("safety stall first time must be set")
    session.last_safety_stall = SafetyStall(
        conflict=conflict,
        consecutiveCount=count,
        firstInterventionTime=first_time,
        latestInterventionTime=conflict.time,
    )
    if count == 3:
        _record_session_event(
            session,
            conflict.time,
            f"T={conflict.time} 连续安全停滞：同一冲突已拦截 3 次",
        )
```

Call `_track_safety_stall(session, conflict)` from `_apply_safety_hold` after setting `last_safety_intervention`.

Call `_clear_safety_stall(session)`:

- in `_reset_session_runtime`;
- immediately before a non-hold `_apply_result_through_time` that advances at least one tick;
- after a task is successfully appended;
- after a blocked cell is actually added or removed;
- after a robot is actually failed or restored.

Do not call it for idempotent branches or before validations that may raise.

Add `safetyStall=session.last_safety_stall` to both `SessionResult` constructor call sites in `_build_result`.

- [ ] **Step 4: Add frontend type and status helper tests**

Add `SafetyStall` to `frontend/src/domain/types.ts` and the required field:

```ts
export type SafetyStall = {
  conflict: Conflict;
  consecutiveCount: number;
  firstInterventionTime: number;
  latestInterventionTime: number;
};

safetyStall: SafetyStall | null;
```

Place `safetyStall` immediately after `SessionResult.safetyIntervention`.

Extend the import list and `execution safety intervention` tests in `frontend/src/main.test.ts`:

```ts
it("formats a structured consecutive stall without parsing event text", () => {
  const stall = {
    conflict: intervention,
    consecutiveCount: 3,
    firstInterventionTime: 7,
    latestInterventionTime: 9
  };

  expect(safetyStallLabel(stall)).toBe("连续拦截 3 次，当前调度停滞");
  expect(safetyStallLabel(null)).toBeNull();
});
```

Update every `SessionResult` fixture in frontend tests to include `safetyStall: null`.

Run frontend tests to verify RED before editing `main.tsx`:

```powershell
npm --prefix frontend run test -- main.test.ts
```

Expected: FAIL because `safetyStallLabel` is not exported.

- [ ] **Step 5: Wire the existing frontend status strip**

In `frontend/src/main.tsx`, import `SafetyStall` and add:

```ts
export function safetyStallLabel(stall: SafetyStall | null): string | null {
  if (!stall) return null;
  return `连续拦截 ${stall.consecutiveCount} 次，当前调度停滞`;
}
```

Choose the status without changing pause behavior:

```ts
const safetyStatus = safetyStallLabel(session?.safetyStall ?? null)
  ?? safetyInterventionLabel(session?.safetyIntervention ?? null);
```

Keep `shouldPauseForSafetyIntervention(payload.safetyIntervention)` unchanged. Reuse the existing `<span className="safety-status">`; do not add a panel, popup or new CSS section.

- [ ] **Step 6: Run backend, contract and frontend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_api_contract.py backend\tests\test_sessions.py -q
npm --prefix frontend run test -- main.test.ts
npm --prefix frontend run build
```

Expected: all selected backend tests and frontend tests PASS; TypeScript build succeeds.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/app/schemas.py backend/app/sessions.py backend/tests/test_api_contract.py backend/tests/test_sessions.py frontend/src/domain/types.ts frontend/src/main.tsx frontend/src/main.test.ts
git commit -m "feat: report consecutive safety stalls"
```

---

### Task 4: Adaptive replan-window hysteresis

**Files:**
- Modify: `backend/app/replan_window.py`
- Modify: `backend/app/dispatch.py:1943-1956`
- Modify: `backend/app/sessions.py:75-114,440-464,547-569,1007-1034`
- Modify: `backend/tests/test_replan_window.py`
- Modify: `backend/tests/test_algorithm.py:820-930`
- Modify: `backend/tests/test_sessions.py:2234-2445`

**Interfaces:**
- Produces:

```python
REPLAN_TIME_SAMPLE_WINDOW = 5
MIN_REPLAN_TIME_SAMPLES = 3
SLOW_REPLAN_ENTER_THRESHOLD_MS = 60
SLOW_REPLAN_EXIT_THRESHOLD_MS = 40
```

- Produces: `update_latency_slow_state(samples_ms: list[float], current_slow: bool) -> bool`
- Produces: `_record_replan_latency(session: DispatchSession, replan_time_ms: float) -> None`
- Changes:

```python
def decide_replan_window(
    *,
    configured_window: int,
    adaptive: bool,
    released_task_count: int,
    future_task_count: int,
    active_robot_count: int,
    latency_slow: bool,
) -> ReplanWindowDecision
```
- Removes: `SLOW_REPLAN_THRESHOLD_MS`, `recent_replan_time_ms` argument and `DispatchSession.last_replan_time_ms`.
- Adds: `DispatchSession.recent_replan_times_ms` and `DispatchSession.adaptive_latency_slow`.

- [ ] **Step 1: Replace single-sample tests with deterministic hysteresis tests**

In `backend/tests/test_replan_window.py`, import all new constants and `update_latency_slow_state`. Add:

```python
@pytest.mark.parametrize("samples", [[], [100], [100, 100]])
def test_latency_state_requires_three_samples(samples: list[float]) -> None:
    assert update_latency_slow_state(samples, False) is False


def test_latency_state_uses_only_latest_five_sample_median() -> None:
    samples = [1000, 10, 60, 70, 80, 90]
    assert update_latency_slow_state(samples, False) is True


def test_latency_state_enters_at_60_and_exits_at_40() -> None:
    assert update_latency_slow_state([60, 60, 60], False) is True
    assert update_latency_slow_state([40, 40, 40], True) is False


def test_latency_state_is_sticky_between_40_and_60() -> None:
    samples = [50, 50, 50]
    assert update_latency_slow_state(samples, False) is False
    assert update_latency_slow_state(samples, True) is True
```

Update every `decide_replan_window` call to pass `latency_slow`. Verify fixed mode with `latency_slow=True` still returns the configured window. Verify the slow reason is exactly:

```text
近期规划耗时中位数较高，收缩窗口
```

- [ ] **Step 2: Run pure policy tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py -q
```

Expected: import or signature failures because the new state function and argument do not exist.

- [ ] **Step 3: Implement pure state and decision functions**

Replace the single threshold in `backend/app/replan_window.py`:

```python
from statistics import median

REPLAN_TIME_SAMPLE_WINDOW = 5
MIN_REPLAN_TIME_SAMPLES = 3
SLOW_REPLAN_ENTER_THRESHOLD_MS = 60
SLOW_REPLAN_EXIT_THRESHOLD_MS = 40


def update_latency_slow_state(
    samples_ms: list[float],
    current_slow: bool,
) -> bool:
    recent = samples_ms[-REPLAN_TIME_SAMPLE_WINDOW:]
    if len(recent) < MIN_REPLAN_TIME_SAMPLES:
        return current_slow
    value = median(recent)
    if not current_slow and value >= SLOW_REPLAN_ENTER_THRESHOLD_MS:
        return True
    if current_slow and value <= SLOW_REPLAN_EXIT_THRESHOLD_MS:
        return False
    return current_slow
```

Change the decision signature:

```python
def decide_replan_window(
    *,
    configured_window: int,
    adaptive: bool,
    released_task_count: int,
    future_task_count: int,
    active_robot_count: int,
    latency_slow: bool,
) -> ReplanWindowDecision:
```

Use `latency_slow` before task pressure and replace the reason text. In direct dispatch, pass `latency_slow=False`.

- [ ] **Step 4: Add session sample lifecycle tests**

Add to `backend/tests/test_sessions.py`:

```python
def test_session_latency_recorder_keeps_latest_five_samples() -> None:
    scenario = Scenario.model_validate(scenario_payload())
    session = sessions_module.DispatchSession(
        session_id="latency-samples",
        scenario=scenario,
        options=DispatchOptions(
            avoidConflicts=True,
            includeDynamic=False,
            adaptiveReplanWindow=True,
        ),
    )

    for value in [10, 20, 30, 40, 50, 60]:
        sessions_module._record_replan_latency(session, value)

    assert session.recent_replan_times_ms == [20, 30, 40, 50, 60]
```

Add cached-read behavior:

```python
def test_cached_get_does_not_append_replan_latency_sample() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {
                "avoidConflicts": True,
                "includeDynamic": False,
                "adaptiveReplanWindow": True,
            },
        },
    )
    assert created.status_code == 200
    session_id = created.json()["sessionId"]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        before = list(session.recent_replan_times_ms)

    assert client.get(f"/api/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 200

    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert session.recent_replan_times_ms == before
```

Add reset behavior with the delayed-planning fixed demo:

```python
def test_reset_clears_adaptive_latency_history_and_state() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": frontend_demo_scenario("integrated-demo"),
            "options": {
                "avoidConflicts": True,
                "includeDynamic": True,
                "adaptiveReplanWindow": True,
            },
        },
    )
    assert created.status_code == 200
    session_id = created.json()["sessionId"]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        session.recent_replan_times_ms = [100, 100, 100]
        session.adaptive_latency_slow = True

    reset = client.post(f"/api/sessions/{session_id}/reset")

    assert reset.status_code == 200
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert session.recent_replan_times_ms == []
        assert session.adaptive_latency_slow is False
```

Add ordering of decision-before-sample:

```python
def test_replan_sample_changes_only_the_next_window_decision(monkeypatch) -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": scenario_payload(),
            "options": {
                "avoidConflicts": True,
                "includeDynamic": False,
                "assignmentReplanWindow": 24,
                "adaptiveReplanWindow": True,
            },
        },
    )
    session_id = created.json()["sessionId"]
    real_run_dispatch = sessions_module.run_dispatch
    decisions = []

    def deterministic_run_dispatch(*args, **kwargs):
        decisions.append(kwargs["replan_window_decision"])
        result = real_run_dispatch(*args, **kwargs)
        return result.model_copy(
            update={
                "metrics": result.metrics.model_copy(
                    update={"replanTimeMs": 100}
                )
            }
        )

    monkeypatch.setattr(
        sessions_module,
        "run_dispatch",
        deterministic_run_dispatch,
    )
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        session.recent_replan_times_ms = [100, 100]
        session.adaptive_latency_slow = False
        sessions_module._invalidate_plan(session)
    first = client.get(f"/api/sessions/{session_id}").json()

    first_decision = decisions[-1]
    assert first_decision.reason != "近期规划耗时中位数较高，收缩窗口"
    assert first["result"]["replanWindowReason"] == first_decision.reason
    assert not any(
        "近期规划耗时中位数较高" in event["text"]
        for event in first["result"]["eventLog"]
    )
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert session.adaptive_latency_slow is True
        sessions_module._invalidate_plan(session)
    second = client.get(f"/api/sessions/{session_id}").json()

    assert decisions[-1].reason == "近期规划耗时中位数较高，收缩窗口"
    assert second["result"]["replanWindowReason"] == decisions[-1].reason
```

- [ ] **Step 5: Replace session single-value state**

In `DispatchSession`, replace:

```python
last_replan_time_ms: float | None = None
```

with:

```python
recent_replan_times_ms: list[float] = field(default_factory=list)
adaptive_latency_slow: bool = False
```

Reset both:

```python
session.recent_replan_times_ms.clear()
session.adaptive_latency_slow = False
```

Change `_session_replan_window_decision` to pass:

```python
latency_slow=session.adaptive_latency_slow
```

After `run_dispatch` returns and after recording the decision actually used, append and update:

```python
def _record_replan_latency(
    session: DispatchSession,
    replan_time_ms: float,
) -> None:
    session.recent_replan_times_ms.append(replan_time_ms)
    session.recent_replan_times_ms = session.recent_replan_times_ms[
        -REPLAN_TIME_SAMPLE_WINDOW:
    ]
    session.adaptive_latency_slow = update_latency_slow_state(
        session.recent_replan_times_ms,
        session.adaptive_latency_slow,
    )
```

Call it only in the `result is None` planning branch:

```python
_record_replan_window_decision(session, replan_window_decision)
_record_replan_latency(session, result.metrics.replanTimeMs)
```

Do not add samples in `_build_idle_result`, cached `_build_result`, get/list or passive tick reuse.

- [ ] **Step 6: Run policy, algorithm and session tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py backend\tests\test_algorithm.py backend\tests\test_sessions.py -q
```

Expected: all selected tests PASS. No test may assert uncontrolled wall-clock milliseconds.

- [ ] **Step 7: Commit Task 4**

```powershell
git add backend/app/replan_window.py backend/app/dispatch.py backend/app/sessions.py backend/tests/test_replan_window.py backend/tests/test_algorithm.py backend/tests/test_sessions.py
git commit -m "fix: stabilize adaptive replan latency"
```

---

### Task 5: Explicit CORS origins

**Files:**
- Create: `backend/app/config.py`
- Modify: `backend/app/main.py:1-62`
- Create: `backend/tests/test_config.py`
- Modify: `backend/tests/test_health.py`

**Interfaces:**
- Produces: `CORS_ORIGINS_ENV = "WAREHOUSE_PATROL_CORS_ORIGINS"`
- Produces: `DEFAULT_CORS_ORIGINS = ("http://127.0.0.1:5174", "http://localhost:5174")`
- Produces: `cors_allowed_origins(environ: Mapping[str, str] | None = None) -> list[str]`
- Startup behavior: invalid configured origins raise `ValueError` when `backend.app.main` constructs middleware.

- [ ] **Step 1: Write failing pure parser tests**

Create `backend/tests/test_config.py`:

```python
import pytest

from backend.app.config import cors_allowed_origins


def test_cors_defaults_to_two_local_frontend_origins() -> None:
    assert cors_allowed_origins({}) == [
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ]


def test_cors_parses_trims_and_deduplicates_explicit_origins() -> None:
    assert cors_allowed_origins({
        "WAREHOUSE_PATROL_CORS_ORIGINS": (
            " http://192.168.1.10:5174,"
            "https://demo.example.com,"
            "http://192.168.1.10:5174 "
        )
    }) == [
        "http://192.168.1.10:5174",
        "https://demo.example.com",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "",
        " , ",
        "*",
        "ftp://example.com",
        "http://",
        "http://example.com/path",
        "http://example.com?x=1",
        "http://example.com#fragment",
        "http://user:password@example.com",
    ],
)
def test_cors_rejects_invalid_or_non_origin_values(value: str) -> None:
    with pytest.raises(ValueError, match="WAREHOUSE_PATROL_CORS_ORIGINS"):
        cors_allowed_origins({"WAREHOUSE_PATROL_CORS_ORIGINS": value})
```

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_config.py -q
```

Expected: collection fails because `backend.app.config` does not exist.

- [ ] **Step 3: Implement the pure parser**

Create `backend/app/config.py`:

```python
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

CORS_ORIGINS_ENV = "WAREHOUSE_PATROL_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5174",
    "http://localhost:5174",
)


def cors_allowed_origins(
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    source = os.environ if environ is None else environ
    if CORS_ORIGINS_ENV not in source:
        return list(DEFAULT_CORS_ORIGINS)
    values = [item.strip() for item in source[CORS_ORIGINS_ENV].split(",")]
    values = [item for item in values if item]
    if not values:
        raise ValueError(f"{CORS_ORIGINS_ENV} must contain at least one origin")

    origins: list[str] = []
    for value in values:
        parsed = urlsplit(value)
        try:
            parsed.port
        except ValueError as error:
            raise ValueError(f"{CORS_ORIGINS_ENV} contains invalid origin: {value}") from error
        invalid = (
            value == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != ""
            or parsed.query != ""
            or parsed.fragment != ""
        )
        if invalid:
            raise ValueError(f"{CORS_ORIGINS_ENV} contains invalid origin: {value}")
        if value not in origins:
            origins.append(value)
    return origins
```

- [ ] **Step 4: Write and run failing middleware tests**

Replace the wildcard assertion in `backend/tests/test_health.py`:

```python
def test_default_cors_allows_local_frontend_origin() -> None:
    response = TestClient(app).get(
        "/health",
        headers={"Origin": "http://127.0.0.1:5174"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"


def test_default_cors_rejects_unlisted_browser_origin() -> None:
    response = TestClient(app).get(
        "/health",
        headers={"Origin": "https://unlisted.example.com"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allows_only_configured_method_and_header() -> None:
    response = TestClient(app).options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_health.py -q
```

Expected: the first test fails because the current middleware returns `*`.

- [ ] **Step 5: Wire exact middleware settings**

In `backend/app/main.py`, import `cors_allowed_origins` and use:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
```

Do not catch `ValueError`; invalid configuration must fail application import/startup.

- [ ] **Step 6: Run configuration and health tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_config.py backend\tests\test_health.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit Task 5**

```powershell
git add backend/app/config.py backend/app/main.py backend/tests/test_config.py backend/tests/test_health.py
git commit -m "fix: restrict configurable cors origins"
```

---

### Task 6: Documentation, benchmark smoke and full verification

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/algorithm.md`
- Modify: `docs/environment.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Documents: single-process lock boundary, safety-stall meaning, exact limits, adaptive hysteresis, CORS environment variable and verification commands.
- Preserves: no claim of multi-worker session safety, complete MAPF, authentication or public-network security.

- [ ] **Step 1: Update documentation with exact implemented behavior**

Add to `AGENTS.md` completed progress:

```markdown
- Online sessions serialize same-session operations with a registry/per-session lock protocol while allowing different sessions to plan in parallel; the guarantee is process-local and does not cover multiple Uvicorn workers.
- Repeated identical execution-safety holds expose nullable `SessionResult.safetyStall` after three consecutive interventions without weakening full-fleet safe waiting or automatically failing tasks.
- Scenario inputs are bounded to 64 cells per axis, 1024 total cells, 32 robots, 128 total initial/dynamic/runtime tasks, and 64 targets per task.
- Adaptive rolling-window latency uses the median of the latest five real replans after at least three samples, entering slow state at 60ms and leaving it at 40ms; fixed mode remains unchanged.
- Browser CORS defaults to the two local Vite origins and accepts explicit comma-separated origins through `WAREHOUSE_PATROL_CORS_ORIGINS`; wildcard origins remain rejected.
```

In `docs/algorithm.md`, state:

```markdown
连续安全停滞是活性诊断，不是新的寻路器。相同规范化冲突连续拦截三次后，系统仍保持全车等待并继续允许重新规划；该状态不证明场景无解，也不提供完整 MAPF 或全时域可达性保证。
```

In `docs/environment.md`, document:

```powershell
$env:WAREHOUSE_PATROL_CORS_ORIGINS='http://192.168.1.10:5174,https://demo.example.com'
npm run backend:dev
```

State that missing configuration uses the two exact local origins; empty values, wildcard, paths, queries, fragments and non-HTTP(S) schemes fail startup. State explicitly that CORS is not authentication and non-browser clients are unaffected.

In `docs/testing-guide.md`, add focused commands:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_session_concurrency.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -k "safety_stall" -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_config.py backend\tests\test_health.py -q
```

- [ ] **Step 2: Run all focused backend and frontend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py backend\tests\test_session_concurrency.py backend\tests\test_sessions.py backend\tests\test_replan_window.py backend\tests\test_config.py backend\tests\test_health.py backend\tests\test_api_contract.py -q
npm --prefix frontend run test
npm --prefix frontend run build
```

Expected: every selected backend test passes, all frontend tests pass, and production build succeeds.

- [ ] **Step 3: Run algorithm-boundary smoke without committing output**

Run:

```powershell
npm run benchmark:algorithm -- --families scale --repetitions 1 --timeout-seconds 30
```

Expected: three scale runs complete; generated JSON/CSV remain under ignored `output/`. Inspect the newest JSON and assert all three records have `status = "completed"`; do not interpret wall-clock median or P95 as pass/fail.

- [ ] **Step 4: Run complete project verification**

Run:

```powershell
npm run check
```

Expected: frontend build, complete frontend tests and complete backend tests all exit `0`.

- [ ] **Step 5: Check UTF-8, diff scope and repository cleanliness**

Run:

```powershell
git diff --check
git status --short
git diff --stat b0c53b8..HEAD
```

Expected:

- no whitespace errors;
- only planned source, test and documentation files are tracked changes;
- `.superpowers/` and `output/` remain untracked or ignored and unstaged;
- no generated benchmark reports, frontend build output or cache files are staged.

- [ ] **Step 6: Commit Task 6**

```powershell
git add AGENTS.md docs/algorithm.md docs/environment.md docs/testing-guide.md
git commit -m "docs: document online runtime hardening"
```

- [ ] **Step 7: Request final branch review**

Review the complete range:

```powershell
git diff --check b0c53b8..HEAD
git log --oneline --decorate b0c53b8..HEAD
```

Use `superpowers:requesting-code-review`. Treat these as merge blockers:

- any same-session race, deadlock, registry lock held during a blocking session-lock wait, or orphan mutation after delete;
- any route that reaches planning with an oversized request;
- any executed vertex or reverse-edge conflict while `avoidConflicts=true`;
- any stall lifecycle mismatch or automatic task failure/unlock;
- any uncontrolled wall-clock assertion in adaptive policy tests;
- any wildcard CORS origin or claim that CORS supplies authentication;
- any regression in the fixed demo, charging, inventory, capabilities, recovery, experiments or algorithm-boundary smoke.

After review fixes, rerun `npm run check` and the scale smoke before using `superpowers:finishing-a-development-branch`.
