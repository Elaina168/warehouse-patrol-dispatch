# 仓库货架库存流转 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `integrated-demo` 增加后端权威货架库存状态，使入库任务只能送往空货架、出库任务只能从有货货架开始，并在前端表现放货点亮、取货熄灭。

**Architecture:** `Scenario.shelves` 保存货架格、唯一作业格和初始库存；新建 `backend/app/inventory.py` 负责取送业务识别、预订和状态转换，`sessions.py` 只编排会话生命周期。`SessionResult.shelfStates` 是前端显示和随机生成器的唯一库存来源，统一任务接口负责最终校验。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、pytest、React 19、TypeScript 5.8、Vitest 3、Vite 6、CSS Grid、PowerShell 7。

## Global Constraints

- 当前实现分支为 `codex/real-warehouse-map`，不得在 `main` 上直接实现。
- 已批准规格为 `docs/superpowers/specs/2026-07-16-warehouse-inventory-flow-design.md`。
- 货架实体格继续存在于 `obstacles`，机器人只能到达唯一相邻 `serviceCell`。
- 保留 `zones.warehouse`、`zones.delivery`、`zones.inspection` 和 `zones.charging` 现有字段。
- 手工任务和随机生成任务继续共用 `POST /api/sessions/{session_id}/tasks`，不增加任务来源字段。
- 不修改任务分配、A*、冲突避免、锁、抢占、故障恢复或充电算法。
- `Scenario.shelves` 后端默认空数组；没有货架数据的园区、压力和最小测试场景继续使用通用取送语义，不启用库存限制。
- 有货架数据的场景中，所有默认、手工和随机取送任务必须符合进货点到货架或货架到出货点规则。
- 默认任务固定使用校准坐标：`T1 [2,0] -> [2,5]`、`T2 [13,6] -> [2,15]`、`T4 [8,0] -> [12,9]`。
- 默认输入验收为：无避碰预测冲突大于0，开启避碰后预测冲突0；`T=700` 六任务完成，失败、截止超期和充电访问均为0。
- 所有文件保持 UTF-8；新增代码注释使用中文。
- 所有文件修改使用 `apply_patch`；不得暂存或提交未跟踪的 `.superpowers/`。

---

### Task 1: 建立货架契约和纯库存领域模块

**Files:**
- Create: `backend/app/inventory.py`
- Create: `backend/tests/test_inventory.py`
- Modify: `backend/app/schemas.py`
- Modify: `frontend/src/domain/types.ts`
- Modify: `backend/tests/test_api_contract.py`

**Interfaces:**
- Produces: `Shelf`、`ShelfRuntimeState`、`ShelfStatus`、`ShelfTaskBinding`。
- Produces: `initial_shelf_statuses(scenario)`、`classify_shelf_task(scenario, task)`、`reserve_shelf_task(...)`、`complete_inbound_task(...)`、`complete_outbound_pickup(...)`、`build_shelf_runtime_states(...)`。
- Consumes later: Task 2 场景校验和 Task 3 会话状态均只调用该模块，不复制库存规则。

- [ ] **Step 1: 写入失败的前后端契约测试**

在 `backend/tests/test_api_contract.py` 的模型字段对照列表加入：

```py
("Shelf", schemas.Shelf),
("ShelfRuntimeState", schemas.ShelfRuntimeState),
```

在字面量对照测试中加入：

```py
assert _frontend_string_literal_union("ShelfStatus") == set(get_args(schemas.ShelfStatus))
```

并断言：

```py
assert "shelves" in _backend_fields(schemas.Scenario)
assert "shelfStates" in _backend_fields(schemas.SessionResult)
```

- [ ] **Step 2: 运行契约测试并确认缺少类型**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_api_contract.py -q
```

Expected: FAIL，报告 `schemas.Shelf` 或前端 `ShelfStatus` 不存在。

- [ ] **Step 3: 增加 Pydantic 和 TypeScript 类型**

在 `backend/app/schemas.py` 增加：

```py
ShelfStatus = Literal["empty", "inboundReserved", "occupied", "outboundReserved"]


class Shelf(ApiModel):
    id: str
    cell: Cell
    serviceCell: Cell
    initialOccupied: bool = False


class ShelfRuntimeState(ApiModel):
    shelfId: str
    cell: Cell
    serviceCell: Cell
    status: ShelfStatus
```

在 `Scenario` 增加兼容默认值：

```py
shelves: list[Shelf] = Field(default_factory=list)
```

在 `SessionResult` 增加：

```py
shelfStates: list[ShelfRuntimeState] = Field(default_factory=list)
```

在 `frontend/src/domain/types.ts` 增加：

```ts
export type ShelfStatus = "empty" | "inboundReserved" | "occupied" | "outboundReserved";

export type Shelf = {
  id: string;
  cell: Cell;
  serviceCell: Cell;
  initialOccupied: boolean;
};

export type ShelfRuntimeState = {
  shelfId: string;
  cell: Cell;
  serviceCell: Cell;
  status: ShelfStatus;
};
```

并分别加入：

```ts
// Scenario
shelves: Shelf[];

// SessionResult
shelfStates: ShelfRuntimeState[];
```

- [ ] **Step 4: 写入纯库存规则的失败测试**

在 `backend/tests/test_inventory.py` 创建完整最小场景：

```py
from backend.app.inventory import (
    ShelfInventoryError,
    build_shelf_runtime_states,
    classify_shelf_task,
    complete_inbound_task,
    complete_outbound_pickup,
    initial_shelf_statuses,
    reserve_shelf_task,
)
from backend.app.schemas import Scenario, Task


def inventory_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "inventory",
            "name": "inventory",
            "description": "inventory",
            "width": 7,
            "height": 5,
            "obstacles": [[2, 2], [4, 2]],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[3, 0]],
                "delivery": [[6, 4]],
                "charging": [],
            },
            "shelves": [
                {"id": "S01", "cell": [2, 2], "serviceCell": [2, 1], "initialOccupied": False},
                {"id": "S02", "cell": [4, 2], "serviceCell": [4, 1], "initialOccupied": True},
            ],
            "robots": [{"id": "R1", "name": "R1", "start": [0, 4], "battery": 100, "load": 1}],
            "tasks": [],
            "dynamic": {"triggerTime": 10, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )


def inbound_task(task_id: str = "IN") -> Task:
    return Task.model_validate(
        {"id": task_id, "type": "delivery", "title": task_id, "priority": 2,
         "pickup": [0, 0], "dropoff": [2, 1], "demand": 1}
    )


def outbound_task(task_id: str = "OUT") -> Task:
    return Task.model_validate(
        {"id": task_id, "type": "delivery", "title": task_id, "priority": 2,
         "pickup": [4, 1], "dropoff": [6, 4], "demand": 1}
    )
```

增加断言：

```py
def test_inventory_classifies_reserves_and_completes_shelf_tasks() -> None:
    scenario = inventory_scenario()
    statuses = initial_shelf_statuses(scenario)
    bindings = {}

    assert statuses == {"S01": "empty", "S02": "occupied"}
    assert classify_shelf_task(scenario, inbound_task()).kind == "inbound"
    assert classify_shelf_task(scenario, outbound_task()).kind == "outbound"

    reserve_shelf_task(scenario, statuses, bindings, inbound_task())
    reserve_shelf_task(scenario, statuses, bindings, outbound_task())
    assert statuses == {"S01": "inboundReserved", "S02": "outboundReserved"}

    complete_outbound_pickup(statuses, bindings, "OUT")
    complete_inbound_task(statuses, bindings, "IN")
    assert statuses == {"S01": "occupied", "S02": "empty"}

    runtime = build_shelf_runtime_states(scenario, statuses)
    assert [(item.shelfId, item.status) for item in runtime] == [("S01", "occupied"), ("S02", "empty")]


def test_inventory_rejects_wrong_direction_and_duplicate_reservation() -> None:
    scenario = inventory_scenario()
    statuses = initial_shelf_statuses(scenario)
    bindings = {}
    reserve_shelf_task(scenario, statuses, bindings, inbound_task())

    with pytest.raises(ShelfInventoryError, match="入库货架已被预订"):
        reserve_shelf_task(scenario, statuses, bindings, inbound_task("IN-2"))

    invalid = outbound_task("INVALID").model_copy(update={"pickup": [2, 1]})
    with pytest.raises(ShelfInventoryError, match="出库货架为空"):
        reserve_shelf_task(scenario, statuses, bindings, invalid)
```

另加兼容回归：没有 `shelves` 的场景调用 `reserve_shelf_task` 返回 `None`，不限制原有通用配送。

- [ ] **Step 5: 运行库存测试并确认领域模块缺失**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_inventory.py backend/tests/test_api_contract.py -q
```

Expected: FAIL，报告 `backend.app.inventory` 不存在。

- [ ] **Step 6: 实现纯库存领域模块**

在 `backend/app/inventory.py` 定义：

```py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.schemas import Scenario, ShelfRuntimeState, ShelfStatus, Task

ShelfTaskKind = Literal["inbound", "outbound"]


@dataclass(frozen=True)
class ShelfTaskBinding:
    kind: ShelfTaskKind
    shelf_id: str


class ShelfInventoryError(ValueError):
    pass


def initial_shelf_statuses(scenario: Scenario) -> dict[str, ShelfStatus]:
    return {
        shelf.id: "occupied" if shelf.initialOccupied else "empty"
        for shelf in scenario.shelves
    }


def classify_shelf_task(scenario: Scenario, task: Task) -> ShelfTaskBinding | None:
    if not scenario.shelves or task.type != "delivery":
        return None
    shelves_by_service = {tuple(shelf.serviceCell): shelf for shelf in scenario.shelves}
    pickup = tuple(task.pickup) if task.pickup is not None else None
    dropoff = tuple(task.dropoff) if task.dropoff is not None else None
    inbound_cells = {tuple(cell) for cell in scenario.zones.warehouse}
    outbound_cells = {tuple(cell) for cell in scenario.zones.delivery}
    if pickup in inbound_cells:
        shelf = shelves_by_service.get(dropoff)
        if shelf is None:
            raise ShelfInventoryError(f"进货任务的放货作业格没有对应货架：{task.id}")
        return ShelfTaskBinding("inbound", shelf.id)
    if dropoff in outbound_cells:
        shelf = shelves_by_service.get(pickup)
        if shelf is None:
            raise ShelfInventoryError(f"出货任务的取货作业格没有对应货架：{task.id}")
        return ShelfTaskBinding("outbound", shelf.id)
    raise ShelfInventoryError(f"取送任务不是合法的进货到货架或货架到出货组合：{task.id}")


def reserve_shelf_task(
    scenario: Scenario,
    statuses: dict[str, ShelfStatus],
    bindings: dict[str, ShelfTaskBinding],
    task: Task,
) -> ShelfTaskBinding | None:
    binding = classify_shelf_task(scenario, task)
    if binding is None:
        return None
    status = statuses[binding.shelf_id]
    if binding.kind == "inbound" and status != "empty":
        reason = "已有货物" if status == "occupied" else "已被预订"
        raise ShelfInventoryError(f"入库货架{reason}：{binding.shelf_id}")
    if binding.kind == "outbound" and status != "occupied":
        reason = "为空" if status == "empty" else "已被预订"
        raise ShelfInventoryError(f"出库货架{reason}：{binding.shelf_id}")
    statuses[binding.shelf_id] = "inboundReserved" if binding.kind == "inbound" else "outboundReserved"
    bindings[task.id] = binding
    return binding


def complete_inbound_task(
    statuses: dict[str, ShelfStatus],
    bindings: dict[str, ShelfTaskBinding],
    task_id: str,
) -> bool:
    binding = bindings.get(task_id)
    if binding is None or binding.kind != "inbound":
        return False
    statuses[binding.shelf_id] = "occupied"
    bindings.pop(task_id, None)
    return True


def complete_outbound_pickup(
    statuses: dict[str, ShelfStatus],
    bindings: dict[str, ShelfTaskBinding],
    task_id: str,
) -> bool:
    binding = bindings.get(task_id)
    if binding is None or binding.kind != "outbound":
        return False
    statuses[binding.shelf_id] = "empty"
    bindings.pop(task_id, None)
    return True


def build_shelf_runtime_states(scenario: Scenario, statuses: dict[str, ShelfStatus]) -> list[ShelfRuntimeState]:
    return [
        ShelfRuntimeState(
            shelfId=shelf.id,
            cell=shelf.cell,
            serviceCell=shelf.serviceCell,
            status=statuses[shelf.id],
        )
        for shelf in scenario.shelves
    ]
```

- [ ] **Step 7: 运行库存与契约测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_inventory.py backend/tests/test_api_contract.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交领域模型**

```powershell
git add backend/app/inventory.py backend/app/schemas.py backend/tests/test_inventory.py backend/tests/test_api_contract.py frontend/src/domain/types.ts
git commit -m "feat: add shelf inventory domain model"
```

---

### Task 2: 固化货架几何、场景数据和业务校验

**Files:**
- Modify: `backend/app/validation.py`
- Modify: `backend/tests/test_validation.py`
- Modify: `frontend/src/domain/scenarios.json`
- Modify: `frontend/src/domain/view.test.ts`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: Task 1 的 `Shelf`、`ShelfInventoryError`、`initial_shelf_statuses()` 和 `reserve_shelf_task()`。
- Produces: 72个稳定货架定义、12个初始库存、经过库存校验的默认任务。

- [ ] **Step 1: 写入失败的货架几何和默认任务测试**

在 `frontend/src/domain/view.test.ts` 增加生成期望货架的辅助函数：

```ts
function expectedShelves(): Scenario["shelves"] {
  const xs = [2, 3, 4, 7, 8, 9, 12, 13, 14, 17, 18, 19];
  const rows = [
    { y: 3, serviceY: 2 },
    { y: 4, serviceY: 5 },
    { y: 7, serviceY: 6 },
    { y: 8, serviceY: 9 },
    { y: 11, serviceY: 10 },
    { y: 12, serviceY: 13 }
  ];
  const occupied = new Set(["3,3", "8,3", "13,3", "18,3", "3,7", "8,7", "13,7", "18,7", "3,11", "8,11", "13,11", "18,11"]);
  return rows.flatMap(({ y, serviceY }, rowIndex) => xs.map((x, columnIndex) => ({
    id: `S${String(rowIndex * xs.length + columnIndex + 1).padStart(2, "0")}`,
    cell: [x, y] as Cell,
    serviceCell: [x, serviceY] as Cell,
    initialOccupied: occupied.has(`${x},${y}`)
  })));
}
```

新增断言：

```ts
expect(integrated.shelves).toEqual(expectedShelves());
expect(integrated.shelves).toHaveLength(72);
expect(integrated.shelves.filter((shelf) => shelf.initialOccupied)).toHaveLength(12);
expect(new Set(integrated.shelves.map((shelf) => cellKey(shelf.serviceCell))).size).toBe(72);
expect(tasks.get("T1")).toMatchObject({ pickup: [2, 0], dropoff: [2, 5] });
expect(tasks.get("T2")).toMatchObject({ pickup: [13, 6], dropoff: [2, 15] });
expect(tasks.get("T4")).toMatchObject({ pickup: [8, 0], dropoff: [12, 9] });
```

在 `backend/tests/test_validation.py` 增加失败用例：货架 ID 重复、货架格重复、货架格不在障碍中、货架格或作业格越界、作业格不相邻、作业格重复、作业格位于固定障碍、`triggerTime=0` 时作业格位于生效动态封锁、默认入库指向有货或预订货架、默认出库从空货架或预订货架开始，以及进货/出货坐标组合不合法；断言响应为 `422` 且 `detail` 包含对应中文原因。

- [ ] **Step 2: 运行场景与校验测试并确认失败**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/view.test.ts
.\.venv\Scripts\python.exe -m pytest backend/tests/test_validation.py -q
```

Expected: FAIL；前端报告 `shelves` 缺失，后端仍接受无效货架几何。

- [ ] **Step 3: 在后端场景校验中加入货架规则**

在 `backend/app/validation.py` 增加 `_shelf_errors(scenario)`：

```py
def _shelf_errors(scenario: Scenario, options: DispatchOptions) -> list[str]:
    errors: list[str] = []
    obstacle_keys = {cell_key(cell) for cell in scenario.obstacles}
    shelf_ids: set[str] = set()
    shelf_cells: set[str] = set()
    service_cells: set[str] = set()
    active_dynamic_blocked = {
        cell_key(cell)
        for cell in scenario.dynamic.blockedCells
    } if options.includeDynamic and scenario.dynamic.triggerTime == 0 else set()
    for shelf in scenario.shelves:
        shelf_key = cell_key(shelf.cell)
        service_key = cell_key(shelf.serviceCell)
        if shelf.id in shelf_ids:
            errors.append(f"货架 ID 重复：{shelf.id}")
        if shelf_key in shelf_cells:
            errors.append(f"货架坐标重复：{shelf_key}")
        if service_key in service_cells:
            errors.append(f"货架作业格重复：{service_key}")
        if shelf_key not in obstacle_keys:
            errors.append(f"货架格不在固定障碍中：{shelf.id} {shelf_key}")
        if abs(shelf.cell[0] - shelf.serviceCell[0]) + abs(shelf.cell[1] - shelf.serviceCell[1]) != 1:
            errors.append(f"货架作业格不相邻：{shelf.id}")
        if service_key in obstacle_keys:
            errors.append(f"货架作业格位于固定障碍：{shelf.id} {service_key}")
        if service_key in active_dynamic_blocked:
            errors.append(f"货架作业格位于当前生效的动态封锁：{shelf.id} {service_key}")
        shelf_ids.add(shelf.id)
        shelf_cells.add(shelf_key)
        service_cells.add(service_key)
    return errors
```

将 `_shelf_errors(scenario, options)` 接入 `validate_scenario()`，并把货架格、作业格加入 `_named_cells()`，由现有边界检查覆盖二者越界。使用 Task 1 的库存函数在副本状态中依次预订 `scenario.tasks` 和 `scenario.dynamic.tasks`；捕获 `ShelfInventoryError` 并加入诊断列表。没有货架的场景跳过库存校验。

- [ ] **Step 4: 更新前端导入校验**

在 `isScenario()` 中接受后端兼容格式：

```ts
&& (value.shelves === undefined || (
  Array.isArray(value.shelves)
  && value.shelves.every(isShelf)
))
```

新增：

```ts
function isShelf(value: unknown): value is Scenario["shelves"][number] {
  return isRecord(value)
    && isString(value.id)
    && isCell(value.cell)
    && isCell(value.serviceCell)
    && typeof value.initialOccupied === "boolean";
}
```

将 `scenario.shelves ?? []` 的货架格和作业格加入 `assertScenarioCellsInside()`；导入旧场景时在返回前规范化为 `shelves: []`，保证应用内部的 `Scenario` 始终完整。

- [ ] **Step 5: 更新共享场景 JSON**

在 `frontend/src/domain/scenarios.json` 增加72项 `shelves`。编号和映射必须严格为：

```text
S01..S12: y=3,  x=[2,3,4,7,8,9,12,13,14,17,18,19], serviceY=2
S13..S24: y=4,  同一 x 顺序, serviceY=5
S25..S36: y=7,  同一 x 顺序, serviceY=6
S37..S48: y=8,  同一 x 顺序, serviceY=9
S49..S60: y=11, 同一 x 顺序, serviceY=10
S61..S72: y=12, 同一 x 顺序, serviceY=13
```

`initialOccupied: true` 只用于：

```text
S02, S05, S08, S11, S26, S29, S32, S35, S50, S53, S56, S59
```

更新默认任务：

```json
{"id":"T1","pickup":[2,0],"dropoff":[2,5]}
{"id":"T2","pickup":[13,6],"dropoff":[2,15]}
{"id":"T4","pickup":[8,0],"dropoff":[12,9]}
```

只替换坐标，保留每个任务现有的其他字段。

- [ ] **Step 6: 运行场景和校验测试**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/view.test.ts src/main.test.ts
.\.venv\Scripts\python.exe -m pytest backend/tests/test_inventory.py backend/tests/test_validation.py backend/tests/test_api_contract.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交场景与校验**

```powershell
git add backend/app/validation.py backend/tests/test_validation.py frontend/src/domain/scenarios.json frontend/src/domain/view.test.ts frontend/src/main.tsx
git commit -m "feat: model warehouse shelves and service cells"
```

---

### Task 3: 将库存预订和取放转换接入在线会话

**Files:**
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `reserve_shelf_task()` 和 Task 1 的状态转换函数。
- Produces: 每个 `SessionResult.shelfStates` 都与当前 tick 一致；统一任务接口具备事务式库存校验。

- [ ] **Step 1: 写入失败的会话库存闭环测试**

在 `backend/tests/test_sessions.py` 增加最小货架会话辅助场景，包含一个空货架 `S01`、一个初始有货货架 `S02`、一台机器人和空任务列表。测试以下流程：

```py
def shelf_states(payload: dict) -> dict[str, str]:
    return {item["shelfId"]: item["status"] for item in payload["shelfStates"]}


def test_session_reserves_completes_and_resets_shelf_inventory() -> None:
    client = TestClient(app)
    created = client.post("/api/sessions", json={"scenario": shelf_session_scenario(), "options": {"avoidConflicts": True, "includeDynamic": False}})
    session_id = created.json()["sessionId"]
    assert shelf_states(created.json()) == {"S01": "empty", "S02": "occupied"}

    inbound = client.post(f"/api/sessions/{session_id}/tasks", json={"task": inbound_runtime_task()})
    assert shelf_states(inbound.json())["S01"] == "inboundReserved"

    outbound = client.post(f"/api/sessions/{session_id}/tasks", json={"task": outbound_runtime_task()})
    assert shelf_states(outbound.json())["S02"] == "outboundReserved"

    completed = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 40})
    assert shelf_states(completed.json()) == {"S01": "occupied", "S02": "empty"}

    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert shelf_states(reset.json()) == {"S01": "empty", "S02": "occupied"}
```

增加出库中间 tick 测试：从任务路径找到第一次到达 `pickup` 的绝对索引，在该 tick 前 `S02` 为 `outboundReserved`，到达该 tick 后为 `empty`，不等待出货任务完成。

将入库任务固定为 `serviceTime=3`，从任务路径找到第一次到达 `dropoff` 的绝对索引 `dropoff_tick`；分别推进并断言：

```py
at_dropoff = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": dropoff_tick})
before_service_done = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": dropoff_tick + 2})
after_service_done = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": dropoff_tick + 3})
assert shelf_states(at_dropoff.json())["S01"] == "inboundReserved"
assert shelf_states(before_service_done.json())["S01"] == "inboundReserved"
assert shelf_states(after_service_done.json())["S01"] == "occupied"
```

由此固定“到达放货作业格后仍等待完整 `serviceTime`，完成时才点亮”的语义。

增加拒绝无副作用测试：重复入库和空货架出库返回 `422`，并逐项比较响应前后的 `currentTime`、`runtimeTaskCount`、`metricsHistory`、任务 ID、库存状态和已有结果。

- [ ] **Step 2: 运行会话定向测试并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py -k "shelf_inventory or shelf_task" -q
```

Expected: FAIL；创建响应没有 `shelfStates`，运行时任务没有建立预订。

- [ ] **Step 3: 在会话对象保存库存状态和绑定**

在 `DispatchSession` 增加：

```py
shelf_statuses: dict[str, ShelfStatus] = field(default_factory=dict)
shelf_task_bindings: dict[str, ShelfTaskBinding] = field(default_factory=dict)
```

新增初始化函数：

```py
def _initialize_shelf_inventory(session: DispatchSession) -> None:
    statuses = initial_shelf_statuses(session.scenario)
    bindings: dict[str, ShelfTaskBinding] = {}
    for task in [*session.scenario.tasks, *session.scenario.dynamic.tasks]:
        reserve_shelf_task(session.scenario, statuses, bindings, task)
    session.shelf_statuses = statuses
    session.shelf_task_bindings = bindings
```

在 `create_session()` 构造 `DispatchSession` 后、写入 `_sessions` 前调用；在 `_reset_session_runtime()` 恢复初始场景并清空运行时状态后再次调用。

- [ ] **Step 4: 让统一任务接口事务式预订货架**

在 `add_task()` 中，普通场景验证通过后复制库存状态：

```py
next_statuses = dict(session.shelf_statuses)
next_bindings = dict(session.shelf_task_bindings)
try:
    reserve_shelf_task(session.scenario, next_statuses, next_bindings, task)
except ShelfInventoryError as error:
    raise HTTPException(status_code=422, detail=[str(error)]) from error
```

只有在所有验证成功后才追加任务，并同时提交副本：

```py
session.scenario.tasks.append(task)
session.shelf_statuses = next_statuses
session.shelf_task_bindings = next_bindings
```

错误分支不得调用 `_invalidate_plan()`、`_touch_session(updated=True)` 或写事件日志。

- [ ] **Step 5: 在 tick 推进中同步实际取放**

在 `_advance_session()` 调用 `_update_task_waypoint_progress()` 后，遍历仍有绑定的任务：

```py
for task in _all_tasks(session):
    binding = session.shelf_task_bindings.get(task.id)
    if binding is None or binding.kind != "outbound":
        continue
    if session.task_waypoint_progress.get(task.id, 0) >= 1:
        if complete_outbound_pickup(session.shelf_statuses, session.shelf_task_bindings, task.id):
            _record_session_event(session, target_time, f"货架 {binding.shelf_id} 已取货")
```

在完成时间循环中，仅对第一次完成的入库任务调用：

```py
binding = session.shelf_task_bindings.get(task_id)
if newly_completed and binding is not None and binding.kind == "inbound":
    if complete_inbound_task(session.shelf_statuses, session.shelf_task_bindings, task_id):
        _record_session_event(session, completion_time, f"货架 {binding.shelf_id} 已放货")
```

若一次 `tick` 跨越取货时刻，事件时间必须使用任务路径第一次到达 `pickup` 的绝对索引，而不是目标 `target_time`。复用现有 `_find_next_visit_until()` 获取该索引，并在测试中断言事件时间。

- [ ] **Step 6: 在所有会话响应暴露货架状态**

在 `_build_result()` 的空闲结果和正常结果两个 `SessionResult(...)` 构造位置加入：

```py
shelfStates=build_shelf_runtime_states(session.scenario, session.shelf_statuses),
```

确保 `get_session`、tick、重置、运行时任务、封锁、故障和恢复返回同一字段。

- [ ] **Step 7: 运行会话和契约回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py backend/tests/test_api_contract.py -q
```

Expected: PASS；现有无货架场景回归不受影响。

- [ ] **Step 8: 提交会话库存闭环**

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py
git commit -m "feat: track shelf inventory in online sessions"
```

---

### Task 4: 显示库存亮灯并约束随机生成任务

**Files:**
- Create: `frontend/src/domain/inventory.ts`
- Create: `frontend/src/domain/inventory.test.ts`
- Modify: `frontend/src/domain/view.ts`
- Modify: `frontend/src/domain/view.test.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `Scenario.shelves`、`SessionResult.shelfStates`。
- Produces: `buildShelfCellPresentations()`、`buildWarehouseDeliveryCandidates()`。
- Changes: `buildRandomGeneratedTask(tasks, currentTime, scenario, sequence, shelfStates)`。

- [ ] **Step 1: 写入失败的库存显示纯函数测试**

在 `frontend/src/domain/inventory.test.ts` 增加：

```ts
import { describe, expect, it } from "vitest";
import { buildShelfCellPresentations, buildWarehouseDeliveryCandidates } from "./inventory";
import type { Scenario, ShelfRuntimeState } from "./types";

it("highlights only occupied and outbound-reserved shelves", () => {
  const presentations = buildShelfCellPresentations(
    [
      { id: "S01", cell: [2, 2], serviceCell: [2, 1], initialOccupied: false },
      { id: "S02", cell: [4, 2], serviceCell: [4, 1], initialOccupied: true }
    ],
    [
      { shelfId: "S01", cell: [2, 2], serviceCell: [2, 1], status: "inboundReserved" },
      { shelfId: "S02", cell: [4, 2], serviceCell: [4, 1], status: "outboundReserved" }
    ]
  );
  expect(presentations.get("2,2")?.classNames).toEqual(["shelf-cell"]);
  expect(presentations.get("4,2")?.classNames).toEqual(["shelf-cell", "shelf-stocked"]);
  expect(presentations.get("4,2")?.label).toBe("货架 S02 · 出库已预订");
});
```

增加候选测试：`empty` 只产生进货点到作业格，`occupied` 只产生作业格到出货点，两个预订状态均不产生候选。

- [ ] **Step 2: 运行纯函数测试并确认模块缺失**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/inventory.test.ts
```

Expected: FAIL，报告 `./inventory` 不存在。

- [ ] **Step 3: 实现前端库存纯函数**

在 `frontend/src/domain/inventory.ts` 定义：

```ts
import { cellKey } from "./view";
import type { Cell, Scenario, Shelf, ShelfRuntimeState } from "./types";

export type ShelfCellPresentation = { classNames: string[]; label: string };
export type WarehouseDeliveryCandidate = {
  kind: "inbound" | "outbound";
  pickup: Cell;
  dropoff: Cell;
  signature: string;
};

export function buildShelfCellPresentations(
  shelves: Shelf[],
  states: ShelfRuntimeState[]
): Map<string, ShelfCellPresentation> {
  const stateById = new Map(states.map((state) => [state.shelfId, state]));
  return new Map(shelves.map((shelf) => {
    const status = stateById.get(shelf.id)?.status ?? (shelf.initialOccupied ? "occupied" : "empty");
    const stocked = status === "occupied" || status === "outboundReserved";
    const statusLabel = {
      empty: "空",
      inboundReserved: "入库已预订",
      occupied: "已有货物",
      outboundReserved: "出库已预订"
    }[status];
    return [cellKey(shelf.cell), {
      classNames: stocked ? ["shelf-cell", "shelf-stocked"] : ["shelf-cell"],
      label: `货架 ${shelf.id} · ${statusLabel}`
    }];
  }));
}

export function buildWarehouseDeliveryCandidates(
  scenario: Scenario,
  states: ShelfRuntimeState[]
): WarehouseDeliveryCandidate[] {
  const stateById = new Map(states.map((state) => [state.shelfId, state.status]));
  return (scenario.shelves ?? []).flatMap((shelf) => {
    const status = stateById.get(shelf.id) ?? (shelf.initialOccupied ? "occupied" : "empty");
    if (status === "empty") {
      return scenario.zones.warehouse.map((pickup) => ({
        kind: "inbound" as const,
        pickup,
        dropoff: shelf.serviceCell,
        signature: `delivery:${cellKey(pickup)}>${cellKey(shelf.serviceCell)}`
      }));
    }
    if (status === "occupied") {
      return scenario.zones.delivery.map((dropoff) => ({
        kind: "outbound" as const,
        pickup: shelf.serviceCell,
        dropoff,
        signature: `delivery:${cellKey(shelf.serviceCell)}>${cellKey(dropoff)}`
      }));
    }
    return [];
  });
}
```

- [ ] **Step 4: 接入地图货架亮灯**

给 `MapBoard` 增加 `shelfStates: ShelfRuntimeState[]` 参数，调用：

```ts
const shelfCellPresentations = useMemo(
  () => buildShelfCellPresentations(scenario.shelves ?? [], shelfStates),
  [scenario.shelves, shelfStates]
);
```

在单元格循环中取出 `shelfPresentation`，把类名加入现有数组。按钮保留原有 `aria-label` 行为，并增加：

```tsx
title={shelfPresentation?.label}
```

`App` 调用 `MapBoard` 时传入：

```tsx
shelfStates={session?.shelfStates ?? []}
```

在 `frontend/src/styles.css` 的障碍规则之后增加：

```css
.cell.shelf-cell {
  background: #38434a;
  border-color: #263138;
}

.cell.shelf-stocked {
  background: #d8a11f;
  border-color: #fff0a6;
  box-shadow: inset 0 0 0 2px rgba(255, 244, 185, 0.9), 0 0 8px rgba(230, 171, 39, 0.85);
}
```

保持 `.cell.blocked` 和 `.cell.conflict-cell` 的覆盖顺序不变。

- [ ] **Step 5: 写入随机生成器失败测试**

修改 `frontend/src/main.test.ts` 中对 `buildRandomGeneratedTask` 的调用，传入 `ShelfRuntimeState[]`。新增：

```ts
it("generates outbound deliveries only from occupied unreserved shelves", () => {
  const task = buildRandomGeneratedTask([], 8, scenario, 1, [
    { shelfId: "S01", cell: [2, 3], serviceCell: [2, 2], status: "empty" },
    { shelfId: "S02", cell: [3, 3], serviceCell: [3, 2], status: "occupied" }
  ]);
  if (task?.type === "delivery" && task.dropoff[1] === 15) {
    expect(task.pickup).toEqual([3, 2]);
  }
});
```

增加多序列测试：同时保留一个 `empty` 和一个 `occupied` 货架，在固定 `currentTime` 下生成20个不同 `sequence` 的任务，断言10个入库、10个出库，且全部为 `delivery`。收集所有出库任务并断言其 `pickup` 从不等于 `empty`、`inboundReserved` 或 `outboundReserved` 的作业格。构造所有货架为预订状态时，断言返回任务类型为 `inspection` 或 `emergency`。

- [ ] **Step 6: 修改随机生成器使用库存候选**

将签名改为：

```ts
export function buildRandomGeneratedTask(
  tasks: Task[],
  currentTime: number,
  scenario: Scenario,
  sequence: number,
  shelfStates: ShelfRuntimeState[]
): Task | null
```

`buildGeneratedTaskCandidates()` 继续构造巡检和应急候选。`buildRandomGeneratedTask()` 在 `scenario.shelves.length > 0` 时先调用 `buildWarehouseDeliveryCandidates()`：若入库和出库候选同时存在，用 `seed % 2` 选定方向，并只从该方向候选中选择；只有一种方向时只从该方向选择；没有任何合法取送候选时才改从巡检和应急候选中选择。没有货架时保留当前通用配送候选，保护园区和旧导入场景。

在 `pushGeneratedTask()` 传入：

```ts
session?.shelfStates ?? []
```

把 `createManualTaskForm()` 在货架场景中的默认配送终点改为第一个 `initialOccupied === false` 的 `serviceCell`；没有货架时保持当前 `zones.delivery[0]` 回退。

- [ ] **Step 7: 运行前端测试和构建**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/inventory.test.ts src/domain/view.test.ts src/main.test.ts
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

Expected: PASS。

- [ ] **Step 8: 提交前端库存显示和生成器**

```powershell
git add frontend/src/domain/inventory.ts frontend/src/domain/inventory.test.ts frontend/src/domain/view.ts frontend/src/domain/view.test.ts frontend/src/main.tsx frontend/src/main.test.ts frontend/src/styles.css
git commit -m "feat: render and schedule shelf inventory"
```

---

### Task 5: 校准默认闭环和实验回归

**Files:**
- Modify: `backend/tests/test_e2e_demo.py`
- Modify: `backend/tests/test_experiments.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: 完整 `integrated-demo` 和 `SessionResult.shelfStates`。
- Produces: 默认库存变化、避碰对比和 `T=700` 闭环的固定证据。

- [ ] **Step 1: 扩展固定 demo 端到端断言**

在 `test_integrated_demo_runs_default_online_dispatch_flow` 创建会话后断言：

```py
initial_shelves = {item["shelfId"]: item["status"] for item in create_response.json()["shelfStates"]}
assert sum(status in {"occupied", "outboundReserved"} for status in initial_shelves.values()) == 12
assert initial_shelves["S32"] == "outboundReserved"
assert initial_shelves["S13"] == "inboundReserved"
assert initial_shelves["S43"] == "inboundReserved"
```

其中 `T1` 目标货架 `[2,4]` 为 `S13`，`T2` 取货货架 `[13,7]` 为 `S32`，`T4` 目标货架 `[12,8]` 为 `S43`。

从创建响应的 `result.assignments` 找到 `T2` 的机器人和路径，计算路径第一次出现 `[13,6]` 的索引；推进到该 tick 前后并断言 `S32` 从 `outboundReserved` 变为 `empty`。

最终 `T=700` 追加：

```py
final_shelves = {item["shelfId"]: item["status"] for item in payload["shelfStates"]}
assert sum(status == "occupied" for status in final_shelves.values()) == 13
assert final_shelves["S13"] == "occupied"
assert final_shelves["S32"] == "empty"
assert final_shelves["S43"] == "occupied"
```

- [ ] **Step 2: 扩展故障后载荷连续性库存断言**

在现有“配送取货后载荷机器人故障并交接”会话测试中加入货架数据，使取货点成为初始有货货架作业格。推进到取货后标记机器人故障，断言：

```py
assert shelf_states(failed_payload)["S01"] == "empty"
```

现有测试已经验证故障后的货物位置连续性，不重复增加路径断言。恢复机器人后追加：

```py
assert shelf_states(restored_payload)["S01"] == "empty"
```

- [ ] **Step 3: 运行固定 demo 和实验测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_e2e_demo.py backend/tests/test_experiments.py -q
```

Expected:

```text
withoutConflictAvoidance: assignedTaskCount=6, conflictCount>0, failureCount=0
withConflictAvoidance: assignedTaskCount=6, conflictCount=0, failureCount=0
T=700: completedTaskCount=6, deadlineMissCount=0, chargingVisits=[]
```

若实际结果不满足，停止实现并报告具体指标；不得通过放宽断言或修改算法继续。

- [ ] **Step 4: 运行会话、验证和契约回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py backend/tests/test_validation.py backend/tests/test_api_contract.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交端到端证据**

```powershell
git add backend/tests/test_e2e_demo.py backend/tests/test_experiments.py backend/tests/test_sessions.py
git commit -m "test: verify warehouse inventory flow"
```

---

### Task 6: 同步项目文档并完成全量和浏览器验收

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/baseline.md`
- Modify: `docs/demo.md`
- Modify: `docs/testing-guide.md`
- Modify: `docs/experiments.md`

**Interfaces:**
- Consumes: 已通过的库存状态、默认任务和实验结果。
- Produces: 与代码一致的运行说明、人工验收步骤和后续路线。

- [ ] **Step 1: 更新项目和基线说明**

文档统一写明：

```markdown
货架实体格不可通行，每个货架有一个唯一相邻作业格。入库任务从进货点取货并送到空货架作业格；出库任务从已有货物货架作业格取货并送到出货点。后端会话维护权威库存与预订状态，手工任务和随机任务执行同一库存校验。
```

在默认流程中明确 `T1`、`T4` 为入库，`T2` 为出库；默认12个货架亮起，`T2` 取货后对应货架熄灭，`T1`、`T4` 放货后对应货架亮起，最终有货货架为13个。

- [ ] **Step 2: 更新人工测试和实验边界**

在 `docs/testing-guide.md` 增加：

```markdown
1. 初始确认12个货架显示库存高亮。
2. 播放到 `T2` 机器人到达 `[13,6]`，确认货架 `S32` 熄灭。
3. 继续播放，确认 `T1`、`T4` 完成后 `S13`、`S43` 亮起。
4. 播放到 `T=700`，确认6个任务完成且最终13个货架有货。
5. 手工尝试从空货架出库或向有货货架入库，确认请求被拒绝且现有会话不变化。
6. 开启随机生成器，确认出库任务只从当前有货且未预订的货架开始。
```

在 `docs/experiments.md` 保留第三级避碰未实现说明，并记录当前固定输入为无避碰预测冲突大于0、开启避碰后0。

- [ ] **Step 3: 搜索过期业务描述**

Run:

```powershell
rg -n "进货点.*出货点|直接.*出货|货架.*可通行|机器人.*进入货架|随机配送.*warehouse.*delivery" README.md AGENTS.md docs frontend/src backend/app
```

Expected: 产品文档和代码注释不再把仓库场景描述为进货点直接送到出货点；设计规格背景中的旧行为说明允许保留。

- [ ] **Step 4: 运行完整检查**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
git diff --check
```

Expected: 前端构建通过，全部前端测试和全部后端测试通过，`git diff --check` 无输出。

- [ ] **Step 5: 启动平台并执行浏览器验收**

Run:

```powershell
Start-Process -FilePath powershell.exe -ArgumentList '-ExecutionPolicy','Bypass','-File','D:\codex\summer\scripts\start-dev.ps1','-NoBrowser' -WorkingDirectory 'D:\codex\summer' -WindowStyle Hidden
```

打开 `http://127.0.0.1:5174`，确认：

```text
后端在线
416个地图单元
72个货架
初始12个 shelf-stocked
无浏览器控制台错误
```

播放并观察 `S32` 熄灭、`S13` 与 `S43` 亮起。若人工播放到状态转换耗时过长，可通过现有 tick API推进到自动化测试已记录的精确转换 tick，再刷新页面确认；不得通过前端伪造库存状态。

- [ ] **Step 6: 提交文档**

```powershell
git add README.md AGENTS.md docs/baseline.md docs/demo.md docs/testing-guide.md docs/experiments.md
git commit -m "docs: document warehouse inventory operations"
```

---

## Final Verification

- [ ] 确认当前分支为 `codex/real-warehouse-map`。
- [ ] 确认 `git status --short` 最多只显示未跟踪 `.superpowers/`。
- [ ] 运行 `& 'C:\nvm4w\nodejs\npm.cmd' run check` 并读取完整成功输出。
- [ ] 运行 `git diff main...HEAD --check`，预期无输出。
- [ ] 运行 `git diff --name-only main...HEAD`，确认没有核心算法实现、3D、实验面板或竞赛材料文件进入分支。
- [ ] 确认默认任务与库存自动化结果：初始12、最终13、`T=700` 六任务完成、预测冲突从大于0降为0。
- [ ] 确认平台仍在浏览器中可见，并把当前页面保留给用户检查。
