# 硬电量约束与充电闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让带有充电地块的在线场景按真实剩余电量规划任务、返航充电、避碰预约和运行状态，并保持旧的无充电格场景兼容。

**Architecture:** 后端在调度候选状态中维护剩余电量和待插入的充电停靠点，路径构建阶段把停靠点转换为时间路径与 `ChargingVisit` 元数据。在线会话以 `robot_battery_levels` 保存真实电量并仅在实际跨格移动或充电完成时更新；前端完全消费后端状态和访问记录，不自行推算充电行为。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、pytest、React 19、TypeScript、Vite、Vitest。

## Global Constraints

- 所有源文件保持 UTF-8；新增代码注释使用中文。
- `moveTicks` 仅改变移动耗时，每跨越相邻格子仅消耗 `1` 点电量。
- `serviceTime`、等待和充电等待不消耗电量。
- `zones.charging=[]` 是旧场景兼容模式：限制任务总移动距离不超过当前电量，但不要求返航充电，也不生成充电状态。
- 任何带充电格的场景必须在任务分配时满足“完成任务全部目标后仍可抵达一个可达充电格”。
- 充电路径与等待必须经过现有时间感知 A* 和 `Reservations`，不得由前端覆盖路径或推断状态。
- 事件日志只能记录已发生的充电状态变化，不能在未来 tick 预写预测日志。

---

## File Structure

- `backend/app/schemas.py`: 扩展场景区域、机器人容量、充电访问记录、运行状态和调度响应模型。
- `backend/app/dispatch.py`: 在波束搜索中加入电量状态和充电停靠点，生成带充电访问记录的时间路径，同时保留旧测试使用的公开辅助函数返回形状。
- `backend/app/sessions.py`: 保存真实会话电量，按 tick 更新电量/充电完成事件，并把相对充电访问时间恢复为绝对会话时间。
- `frontend/src/domain/types.ts`: 与后端模型同步场景、机器人、访问记录和运行状态类型。
- `frontend/src/domain/scenarios.json`: 为默认综合场景设置 `chargeTime`、充电格和机器人容量。
- `frontend/src/main.tsx` 与 `frontend/src/styles.css`: 渲染充电格、禁止封锁充电格、展示电量容量与充电状态、显示充电中的机器人数量。
- `backend/tests/test_algorithm.py`、`backend/tests/test_sessions.py`、`backend/tests/test_validation.py`、`backend/tests/test_api_contract.py`、`frontend/src/main.test.ts`: 覆盖电量约束、充电路径预约、在线 tick、导入兼容和契约/展示。
- `docs/algorithm.md`、`docs/testing-guide.md`: 记录电量语义和可重复手工测试流程。

### Task 1: 定义充电契约与场景兼容

**Files:**
- Modify: `backend/app/schemas.py:40-72,204-250`
- Modify: `backend/app/validation.py:9-120`
- Modify: `frontend/src/domain/types.ts:6-90,150-161`
- Modify: `backend/tests/test_api_contract.py:180-265`
- Modify: `backend/tests/test_validation.py`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Consumes: `Scenario`, `Zones`, `Robot`, `RobotRuntimeState`, `DispatchResult`。
- Produces: `Scenario.chargeTime`, `Zones.charging`, `Robot.batteryCapacity`, `ChargingVisit`, `DispatchResult.chargingVisits`，以及 `toCharge`/`charging` 运行状态。

- [ ] **Step 1: 写失败的后端模式和契约测试**

```python
def test_scenario_defaults_charge_time_and_empty_charging_zone() -> None:
    scenario = Scenario.model_validate(make_scenario())
    assert scenario.chargeTime == 4
    assert scenario.zones.charging == []


def test_robot_rejects_battery_above_capacity() -> None:
    with pytest.raises(ValidationError):
        Robot.model_validate({"id": "R1", "name": "R1", "start": [0, 0], "battery": 101, "batteryCapacity": 100, "load": 1})
```

在 `test_api_contract.py` 增加 `Zones`、`Robot`、`ChargingVisit` 和 `RobotRuntimeState` 的字段一致性断言；在 `test_validation.py` 断言充电格超出地图范围或位于固定障碍时被拒绝；前端测试断言 `toCharge`、`charging` 可被类型与状态标签接受。

- [ ] **Step 2: 运行失败测试确认缺少字段**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_validation.py backend/tests/test_api_contract.py -q`

Expected: FAIL，因为 `chargeTime`、`charging`、`batteryCapacity`、`ChargingVisit` 尚未定义。

- [ ] **Step 3: 实现 Pydantic 与 TypeScript 契约**

```python
class Zones(ApiModel):
    warehouse: list[Cell]
    inspection: list[Cell]
    delivery: list[Cell]
    charging: list[Cell] = Field(default_factory=list)


class Robot(ApiModel):
    id: str
    name: str
    start: Cell
    battery: NonNegativeInt
    batteryCapacity: PositiveInt = 100
    load: NonNegativeInt
    moveTicks: int = Field(default=1, ge=1, le=4)

    @model_validator(mode="after")
    def validate_battery_capacity(self) -> Robot:
        if self.battery > self.batteryCapacity:
            raise ValueError("battery must be <= batteryCapacity")
        return self
```

从 `pydantic` 导入 `model_validator`，在 `Scenario` 添加 `chargeTime: PositiveInt = 4`；创建 `ChargingVisit(robotId, station, departureTime, arrivalTime, completionTime)`；在 `DispatchResult` 添加默认空列表 `chargingVisits`。将前端 `Zones` 的 `charging` 设为可选字段、`Scenario.chargeTime` 和 `Robot.batteryCapacity` 设为可选字段，并把运行状态联合类型扩展为 `"toCharge" | "charging"`。在 `validation.py` 的命名坐标、固定障碍检查和场景可达性输入中纳入 `zones.charging`，保证它既不越界也不位于障碍。

- [ ] **Step 4: 更新前端导入校验与运行时默认值**

```ts
const chargingCells = scenario.zones.charging ?? [];
const chargeTime = scenario.chargeTime ?? 4;
const batteryCapacity = runtimeState?.batteryCapacity ?? robot.batteryCapacity ?? 100;
```

在 `isScenario()`、`assertScenarioCellsInside()` 和前端场景归一化路径中接受缺失的 `charging`/`chargeTime`，但校验显式充电格均为地图内坐标；不得为旧导入场景补写机器人起点作为充电格。

- [ ] **Step 5: 运行目标测试并提交**

Run: `npm --prefix frontend run test -- main.test.ts; .\.venv\Scripts\python.exe -m pytest backend/tests/test_validation.py backend/tests/test_api_contract.py -q`

Expected: PASS，旧场景缺失新字段按默认值解析，非法电量容量被拒绝。

```powershell
git add backend/app/schemas.py backend/app/validation.py frontend/src/domain/types.ts backend/tests/test_api_contract.py backend/tests/test_validation.py frontend/src/main.test.ts
git commit -m "feat: add charging data contracts"
```

### Task 2: 在分配和路径中规划充电停靠点

**Files:**
- Modify: `backend/app/dispatch.py:34-60,381-489,525-572,778-915,1090-1360,1485-1618`
- Modify: `backend/tests/test_algorithm.py`

**Interfaces:**
- Consumes: `Scenario.zones.charging`, `Scenario.chargeTime`, `Robot.battery`, `Robot.batteryCapacity`。
- Produces: 内部 `ChargeStop(station, beforeTaskId)`、`AssignmentPlan(assignments, chargeStops)`、`plan_dispatch_with_charging()`，以及 `DispatchResult.chargingVisits`。

- [ ] **Step 1: 写失败的算法测试**

```python
def test_dispatch_sends_low_battery_robot_to_charge_before_assigning_task() -> None:
    result = run_dispatch(charging_scenario(battery=2, capacity=8), DispatchOptions())
    visit = next(item for item in result.chargingVisits if item.robotId == "R1")
    assert visit.station == (0, 0)
    assert result.assignments[0].tasks[0].id == "T1"
    assert visit.completionTime < task_completion_times(result.assignments, result.paths)["T1"]


def test_dispatch_rejects_task_that_cannot_finish_and_return_to_charge() -> None:
    result = run_dispatch(charging_scenario(battery=8, capacity=8, target=(7, 0)), DispatchOptions())
    assert result.assignments == []
    assert "电量" in result.failureReasons["T1"]
```

再加入两个机器人竞争同一充电格的场景，断言 `detect_conflicts(result.paths) == []`，且两个 `ChargingVisit` 的充电区间不重叠。

- [ ] **Step 2: 运行失败测试确认当前调度不维护电量或访问记录**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py -k "charge or battery" -q`

Expected: FAIL，因为结果没有 `chargingVisits` 且现有波束搜索不会插入充电路径。

- [ ] **Step 3: 在波束搜索中维护候选电量与充电停靠点**

```python
@dataclass
class ChargeStop:
    station: Cell
    beforeTaskId: str


@dataclass
class RobotAssignmentState:
    robot: Robot
    cursor: Cell
    battery: int
    time: int = 0
    distance: int = 0
    penalty: float = 0
    tasks: list[Task] = field(default_factory=list)
    chargeStops: list[ChargeStop] = field(default_factory=list)
```

实现 `task_energy_cost()` 计算当前位置经过全部 `task_waypoints()` 的跨格距离；实现 `nearest_reachable_charge_station()` 在固定障碍和运行时封锁下返回可达充电格及路径距离。实现 `assign_tasks_with_charging()`：

```python
if not scenario.zones.charging:
    if robot_state.battery < task_energy_cost(...):
        continue
elif robot_state.battery < task_energy_cost(...) + return_charge_distance:
    charge = nearest_reachable_charge_station(scenario, robot_state.cursor, extra_blocked, distance_cache)
    if charge is None or robot_state.battery < charge.distance:
        continue
    robot_state.chargeStops.append(ChargeStop(station=charge.cell, beforeTaskId=task.id))
    robot_state.cursor = charge.cell
    robot_state.time += charge.distance * robot.moveTicks + scenario.chargeTime
    robot_state.battery = robot.batteryCapacity
```

随后按完整任务距离扣减电量、更新完成时间和候选代价。保留 `assign_tasks_beam_search()` 作为返回 `AssignmentPlan.assignments` 的兼容包装器；`run_dispatch()` 使用新入口获取充电停靠点。

- [ ] **Step 4: 将停靠点编入时间路径和访问记录**

实现 `plan_robot_path_with_charging()`，在处理 `ChargeStop.beforeTaskId` 对应任务前：使用现有 `astar_timed()` 或 `astar()` 规划至充电格；追加 `scenario.chargeTime` 个充电格等待 tick；创建：

```python
ChargingVisit(
    robotId=robot.id,
    station=stop.station,
    departureTime=departure_time,
    arrivalTime=arrival_time,
    completionTime=arrival_time + scenario.chargeTime,
)
```

实现 `build_paths_with_charging()` 返回 `(paths, failures, charging_visits)`；保留现有 `build_paths()` 作为忽略第三项的兼容包装器。`run_dispatch()` 将 `chargingVisits=charging_visits` 填入 `DispatchResult`。充电等待必须在 `reserve_path()` 前纳入完整路径。

- [ ] **Step 5: 为无充电格场景和失败原因补齐回归**

实现 `battery_failure_detail()` 并在 `build_failure_details()` 先于通用可达性恢复分类调用它：`zones.charging=[]` 时短路径任务保持既有分配，任务总移动距离超过剩余电量时返回“剩余电量不足且无可达充电桩”；带充电格但满电后仍无法完成任务并返航时返回“电池容量不足以完成任务并到达充电桩”。这两种原因均为永久约束，不生成解除封锁或恢复机器人按钮。新增断言覆盖上述三种结果。

- [ ] **Step 6: 运行算法测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py -q`

Expected: PASS，充电停靠点改变路径与时间，但不引入顶点或边冲突。

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py
git commit -m "feat: plan charging with battery constraints"
```

### Task 3: 使在线会话按实际 tick 保存电量和充电状态

**Files:**
- Modify: `backend/app/sessions.py:60-89,518-554,569-621,624-684,795-835,901-927,1360-1404`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `DispatchResult.chargingVisits`、`Robot.batteryCapacity`、完整时间路径。
- Produces: `DispatchSession.robot_battery_levels`、实际充电事件、绝对时间的 `ChargingVisit` 与 `RobotRuntimeState.status`。

- [ ] **Step 1: 写失败的在线会话测试**

```python
def test_session_decrements_energy_only_for_actual_cell_transitions_and_restores_after_charge() -> None:
    payload = create_charging_session_and_tick_to_completion(client)
    state = next(item for item in payload["robotStates"] if item["robotId"] == "R1")
    assert state["battery"] == state["batteryCapacity"]
    assert any("开始充电" in event["text"] for event in payload["result"]["eventLog"])
    assert any("完成充电" in event["text"] for event in payload["result"]["eventLog"])


def test_session_reports_to_charge_then_charging_and_does_not_assign_task_during_charge() -> None:
    assert tick_payload_at(1)["robotStates"][0]["status"] == "toCharge"
    assert tick_payload_at(2)["robotStates"][0]["status"] == "charging"
    assert tick_payload_at(2)["robotStates"][0]["currentTaskId"] is None
```

再加入运行时封锁唯一充电路径的用例，断言任务保持待分配、日志在实际重规划 tick 出现电量不可达说明，且无机器人穿过封锁格。

- [ ] **Step 2: 运行失败测试确认当前会话从总距离派生电量**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py -k "charge or battery" -q`

Expected: FAIL，因为 `DispatchSession` 不保存电量，运行状态不存在充电状态和事件。

- [ ] **Step 3: 保存并推进真实电量**

在 `DispatchSession` 添加：

```python
robot_battery_levels: dict[str, int] = field(default_factory=dict)
emitted_charge_event_keys: set[str] = field(default_factory=set)
```

在 `create_session()` 和 `_reset_session_runtime()` 用每台机器人的 `battery` 初始化 `robot_battery_levels`。在 `_build_effective_dispatch_input()` 用当前 `robot_positions` 和 `robot_battery_levels` 重建 `scenario.robots`。将 `_advance_session()` 的机器人推进改为逐 tick 处理：相邻格变化时扣 `1`；命中 `ChargingVisit.arrivalTime` 记录“R<n> 开始充电”；命中 `completionTime` 将电量恢复到 `batteryCapacity` 并记录“R<n> 完成充电”。以 `emitted_charge_event_keys` 去重，确保同一实际状态变化只记一次。

- [ ] **Step 4: 恢复绝对访问时间并构建运行状态**

在 `_restore_absolute_result()` 为每个 `ChargingVisit` 的 `departureTime`、`arrivalTime`、`completionTime` 加 `current_time`。在 `_build_idle_result()` 填入 `chargingVisits=[]`。在 `_build_robot_states()` 优先判断：故障、当前充电访问的 `charging`、当前充电访问的 `toCharge`、任务状态、等待、空闲；电量读取 `robot_battery_levels`，运行时状态返回 `batteryCapacity`。

- [ ] **Step 5: 运行会话测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py -q`

Expected: PASS，跳 tick、手动任务、随机任务、封锁和重置均保持实际电量与充电事件一致。

```powershell
git add backend/app/sessions.py backend/tests/test_sessions.py
git commit -m "feat: track charging in online sessions"
```

### Task 4: 渲染充电地图与后端运行状态

**Files:**
- Modify: `frontend/src/main.tsx:1128-1327,1347-1365,2077-2084,2615-2714`
- Modify: `frontend/src/styles.css:865-946`
- Modify: `frontend/src/domain/scenarios.json:34-64`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Consumes: `Scenario.zones.charging`, `Scenario.chargeTime`, `RobotRuntimeState.batteryCapacity`, `DispatchResult.chargingVisits`。
- Produces: 充电格地图标识、不可封锁规则、中文状态标签和“当前/容量”电量展示。

- [ ] **Step 1: 写失败的前端纯函数和场景数据测试**

```ts
it("labels charging robot runtime states", () => {
  expect(robotRuntimeStatusLabel("toCharge")).toBe("前往充电");
  expect(robotRuntimeStatusLabel("charging")).toBe("充电中");
});

it("keeps charging cells out of block actions", () => {
  expect(mapContextAction([1, 1], new Set(), new Set(), new Map(), new Set(), new Set(["1,1"]))).toBeNull();
});
```

增加默认综合场景断言：`zones.charging` 非空、`chargeTime === 4`、机器人显式含 `batteryCapacity`。

- [ ] **Step 2: 运行失败测试确认新状态和区域尚未渲染**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: FAIL，因为状态标签、地图区域和默认场景数据尚未存在。

- [ ] **Step 3: 实现地图和机器人信息展示**

在 `MapBoard()` 预计算 `chargingCells`，为充电格添加 `charging-cell` 类，且在任务字样之前输出 `充`。更新 `mapContextAction()` 的参数和调用处，充电格直接返回 `null`，因此右键不显示封锁/解锁操作。新增 `.cell.charging-cell` 的独立蓝绿色外观，并保持 `.cell.robot-cell` 与 `.robot-marker` 的层级高于充电地块与路径箭头。

```tsx
<span>电量 {robotState.battery}/{robotState.batteryCapacity} · 载重 {robotState.load}</span>
```

更新 `getDisplayRobotState()`、`robotRuntimeStatusLabel()` 和机器人悬浮信息。重规划解释区在已有指标网格增加“充电机器人”指标，值由 `robotStates.filter((item) => item.status === "charging").length` 计算；任务队列保持后端 `pending` 语义，不将充电等待显示为永久失败。

- [ ] **Step 4: 更新导入验证与默认综合场景**

在 `isScenario()` 接受 `zones.charging` 缺失、在存在时要求其为 `Cell[]`；在 `assertScenarioCellsInside()` 加入充电格。默认综合场景添加两个不与初始任务字样重叠的通行充电格、`chargeTime: 4` 和每台机器人的 `batteryCapacity: 100`，但不改变地图宽高或障碍布局。

- [ ] **Step 5: 运行前端测试、构建并提交**

Run: `npm --prefix frontend run test; npm --prefix frontend run build`

Expected: PASS，默认地图稳定显示充电格，充电状态与容量字段在类型和界面中一致。

```powershell
git add frontend/src/main.tsx frontend/src/styles.css frontend/src/domain/scenarios.json frontend/src/main.test.ts
git commit -m "feat: show charging stations and states"
```

### Task 5: 端到端校验、文档和浏览器复测

**Files:**
- Modify: `backend/tests/test_e2e_demo.py`
- Modify: `docs/algorithm.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Consumes: 默认综合场景、在线会话 API、充电事件日志、地图充电格和任务队列。
- Produces: 固定演示场景的充电回归与用户可执行测试步骤。

- [ ] **Step 1: 写失败的默认场景端到端用例**

```python
def test_integrated_demo_exposes_charge_stations_and_runtime_battery_state(client: TestClient) -> None:
    scenario = load_frontend_scenario("integrated-demo")
    assert len(scenario["zones"]["charging"]) >= 2
    response = client.post("/api/sessions", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": True}})
    assert response.status_code == 200
    assert all("batteryCapacity" in state for state in response.json()["robotStates"])
```

补充一个低初始电量的临时任务流，断言事件日志顺序为“前往充电、开始充电、完成充电”，而非在会话创建时预写。

- [ ] **Step 2: 运行失败用例确认默认场景缺少充电配置或运行字段**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_e2e_demo.py -q`

Expected: FAIL，直到默认场景和会话响应完成更新。

- [ ] **Step 3: 更新说明文档**

在 `docs/algorithm.md` 说明：每格耗电、`moveTicks` 与能耗分离、`serviceTime` 不耗电、带充电格场景的返航安全条件、空充电格旧场景兼容模式和 `chargeTime=4` 默认值。在 `docs/testing-guide.md` 增加手工用例：观察充电格、低电量机器人先充电、充电期间不可派发、运行时封锁充电路线后任务等待分配、充电完成后任务恢复。

- [ ] **Step 4: 运行完整自动校验**

Run: `npm run check`

Expected: 前端构建成功、全部 Vitest 通过、全部 pytest 通过。

- [ ] **Step 5: 浏览器进行最小真实流程复测**

使用 `http://127.0.0.1:5174`：启动综合场景，确认充电地块可见；创建或使用低电量场景，推进 tick，确认 `toCharge`、`charging`、电量恢复和事件顺序；右键充电格不出现封锁操作；确认浏览器控制台没有 error。

- [ ] **Step 6: 提交文档与回归测试**

```powershell
git add backend/tests/test_e2e_demo.py docs/algorithm.md docs/testing-guide.md
git commit -m "test: cover charging dispatch flow"
```
