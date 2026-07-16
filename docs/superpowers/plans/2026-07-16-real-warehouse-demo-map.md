# 真实仓库综合演示地图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将唯一的 `integrated-demo` 升级为经过回归保护的 `26 × 16` 真实仓库地图，并保持默认在线任务闭环、单格区域显示和现有前后端契约稳定。

**Architecture:** 继续以 `frontend/src/domain/scenarios.json` 作为唯一共享场景来源，后端测试通过现有 `frontend_demo_scenario()` 读取同一份数据。地图展示只扩展现有 CSS Grid 的单元格语义，不增加 API 字段或跨格图层；算法代码保持不变，所有适配通过场景数据、视图辅助函数和回归测试完成。

**Tech Stack:** React 19、TypeScript 5.8、Vite 6、Vitest 3、FastAPI、Pydantic、pytest、PowerShell 7、JSON。

## Global Constraints

- 当前实现分支固定为 `codex/real-warehouse-map`，不得在 `main` 上修改功能代码。
- 场景 ID 必须保持 `integrated-demo`，前端固定场景仍然只有这一项。
- 地图必须是 `26 × 16`；每个货架、进货点、出货点、充电口和机器人各占一个单元。
- 每组货架为 `3 × 2`，共 `4 × 3 = 12` 组、72个障碍单元。
- 任意相邻对象或对象组之间保留两个完整非障碍单元，不能用两条边界线代替两格。
- 默认流程只执行 `T1`、`T2`、`T3`、`T4`、`T5`、`E1`；不得自动追加任务、封锁单元或标记机器人故障。
- 手工任务与定时生成任务继续共用现有运行时任务接口；运行时封锁、故障和恢复 API 保留。
- 不新增或删除 API 字段，不修改任务分配、A*、冲突避免、锁、抢占、恢复或充电算法。
- 不宣称已经实现第三级避碰、全规划时域零冲突保证或独立执行安全门。
- 所有文件保持 UTF-8，新增代码注释使用中文。
- 使用测试先行；每个任务只提交列出的文件，不得暂存 `.superpowers/` 视觉草图。

---

### Task 1: 固化 `26 × 16` 场景数据与几何约束

**Files:**
- Modify: `frontend/src/domain/view.test.ts`
- Modify: `frontend/src/domain/scenarios.json`
- Modify: `backend/tests/test_experiments.py`

**Interfaces:**
- Consumes: `Scenario`、`Task`、`cellKey()` 和当前 `scenarios.json` 加载方式。
- Produces: 精确的 `integrated-demo` 场景数据；后续前端视图和后端固定回归均依赖该 JSON。

- [ ] **Step 1: 在场景数据测试中写入失败的几何和默认流程断言**

在 `frontend/src/domain/view.test.ts` 增加以下辅助函数：

```ts
function expectedShelfCells(): Cell[] {
  const xGroups = [[2, 3, 4], [7, 8, 9], [12, 13, 14], [17, 18, 19]];
  const yGroups = [[3, 4], [7, 8], [11, 12]];
  return yGroups.flatMap((ys) => ys.flatMap((y) => xGroups.flatMap((xs) => xs.map((x) => [x, y] as Cell))));
}

function cellSet(cells: Cell[]): Set<string> {
  return new Set(cells.map(cellKey));
}

const HORIZONTAL_INSPECTION_TARGETS: Cell[] = [
  [0, 1], [24, 1], [24, 2], [0, 2], [0, 5], [24, 5], [24, 6], [0, 6],
  [0, 9], [24, 9], [24, 10], [0, 10], [0, 13], [24, 13], [24, 14], [0, 14]
];

const VERTICAL_INSPECTION_TARGETS: Cell[] = [
  [0, 1], [0, 14], [1, 14], [1, 1], [5, 1], [5, 14], [6, 14], [6, 1],
  [10, 1], [10, 14], [11, 14], [11, 1], [15, 1], [15, 14], [16, 14], [16, 1],
  [20, 1], [20, 14], [21, 14], [21, 1], [22, 1], [22, 14], [23, 14], [23, 1],
  [24, 1], [24, 14]
];

function reachableCellKeys(scenario: Scenario, start: Cell): Set<string> {
  const obstacles = cellSet(scenario.obstacles);
  const visited = new Set<string>([cellKey(start)]);
  const queue: Cell[] = [start];
  for (let index = 0; index < queue.length; index += 1) {
    const [x, y] = queue[index];
    const neighbours: Cell[] = [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]];
    for (const neighbour of neighbours) {
      const key = cellKey(neighbour);
      if (
        neighbour[0] < 0 || neighbour[0] >= scenario.width
        || neighbour[1] < 0 || neighbour[1] >= scenario.height
        || obstacles.has(key) || visited.has(key)
      ) continue;
      visited.add(key);
      queue.push(neighbour);
    }
  }
  return visited;
}
```

在 `describe("scenario data")` 中增加精确回归：

```ts
it("protects the realistic warehouse grid and two-cell spacing", () => {
  const integrated = scenarios[0];
  expect(integrated.id).toBe("integrated-demo");
  expect([integrated.width, integrated.height]).toEqual([26, 16]);
  expect(cellSet(integrated.obstacles)).toEqual(cellSet(expectedShelfCells()));
  expect(integrated.obstacles).toHaveLength(72);

  expect(integrated.zones.warehouse).toEqual([[2, 0], [5, 0], [8, 0], [11, 0], [14, 0], [17, 0]]);
  expect(integrated.zones.delivery).toEqual([[2, 15], [5, 15], [8, 15], [11, 15], [14, 15], [17, 15]]);
  expect(integrated.zones.charging).toEqual([[25, 3], [25, 6], [25, 9], [25, 12]]);
  expect(cellSet(integrated.zones.inspection)).toEqual(
    cellSet([...HORIZONTAL_INSPECTION_TARGETS, ...VERTICAL_INSPECTION_TARGETS])
  );
  expect(integrated.zones.inspection).toHaveLength(38);
  expect(integrated.robots.map((robot) => robot.start)).toEqual([[22, 3], [22, 6], [22, 9], [22, 12]]);

  const obstacleKeys = cellSet(integrated.obstacles);
  const specialCells = [
    ...integrated.zones.warehouse,
    ...integrated.zones.inspection,
    ...integrated.zones.delivery,
    ...(integrated.zones.charging ?? []),
    ...integrated.robots.map((robot) => robot.start)
  ];
  expect(specialCells.every((cell) => !obstacleKeys.has(cellKey(cell)))).toBe(true);

  const reachable = reachableCellKeys(integrated, [0, 0]);
  expect(reachable.size).toBe(integrated.width * integrated.height - integrated.obstacles.length);
});

it("protects the six default tasks and calibrated warehouse timing", () => {
  const integrated = scenarios[0];
  const tasks = new Map(integrated.tasks.map((task) => [task.id, task]));
  expect([...tasks.keys()]).toEqual(["T1", "T2", "T3", "T4", "T5", "E1"]);

  expect(tasks.get("T1")).toMatchObject({ pickup: [2, 0], dropoff: [17, 15], deadline: 500 });
  expect(tasks.get("T2")).toMatchObject({ pickup: [17, 0], dropoff: [2, 15], deadline: 500 });
  expect(tasks.get("T4")).toMatchObject({ pickup: [8, 0], dropoff: [11, 15], releaseTime: 4, deadline: 500 });
  expect(tasks.get("E1")).toMatchObject({ target: [11, 10], releaseTime: 12, deadline: 300 });
  expect(tasks.get("T3")).toMatchObject({ targets: HORIZONTAL_INSPECTION_TARGETS, deadline: 400 });
  expect(tasks.get("T5")).toMatchObject({
    targets: VERTICAL_INSPECTION_TARGETS,
    releaseTime: 8,
    deadline: 700
  });

  expect(integrated.robots.map((robot) => [robot.battery, robot.batteryCapacity])).toEqual([
    [512, 512], [510, 512], [508, 512], [506, 512]
  ]);
  expect(integrated.dynamic).toEqual({ triggerTime: 12, blockedCells: [], failedRobots: [], tasks: [] });
});
```

- [ ] **Step 2: 运行场景测试并确认旧地图导致失败**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/view.test.ts
```

Expected: FAIL；至少包含 `expected [14, 10] to deeply equal [26, 16]` 或障碍数量不等于72。

- [ ] **Step 3: 用精确坐标替换共享场景 JSON**

在 `frontend/src/domain/scenarios.json` 保留单个对象并写入：

```json
{
  "id": "integrated-demo",
  "name": "综合调度演示",
  "description": "真实仓库双格通道中展示巡检、取送、避碰、默认在线任务、作业时间和充电基础设施。",
  "width": 26,
  "height": 16,
  "zones": {
    "warehouse": [[2, 0], [5, 0], [8, 0], [11, 0], [14, 0], [17, 0]],
    "inspection": [[0, 1], [24, 1], [24, 2], [0, 2], [0, 5], [24, 5], [24, 6], [0, 6], [0, 9], [24, 9], [24, 10], [0, 10], [0, 13], [24, 13], [24, 14], [0, 14], [1, 14], [1, 1], [5, 1], [5, 14], [6, 14], [6, 1], [10, 1], [10, 14], [11, 14], [11, 1], [15, 1], [15, 14], [16, 14], [16, 1], [20, 1], [20, 14], [21, 14], [21, 1], [22, 1], [22, 14], [23, 14], [23, 1]],
    "delivery": [[2, 15], [5, 15], [8, 15], [11, 15], [14, 15], [17, 15]],
    "charging": [[25, 3], [25, 6], [25, 9], [25, 12]]
  },
  "chargeTime": 4,
  "dynamic": { "triggerTime": 12, "blockedCells": [], "failedRobots": [], "tasks": [] }
}
```

`obstacles` 必须枚举以下集合：

```ts
[[3, 4], [7, 8], [11, 12]].flatMap(([fromY, toY]) =>
  Array.from({ length: toY - fromY + 1 }, (_, offset) => fromY + offset).flatMap((y) =>
    [[2, 4], [7, 9], [12, 14], [17, 19]].flatMap(([fromX, toX]) =>
      Array.from({ length: toX - fromX + 1 }, (_, offset) => [fromX + offset, y])
    )
  )
)
```

JSON 中必须保存该表达式展开后的72个坐标，不能把表达式写进 JSON。

机器人保留现有 `id`、`name`、`load` 和 `moveTicks`，只替换：

```json
[
  { "id": "R1", "start": [22, 3], "battery": 512, "batteryCapacity": 512 },
  { "id": "R2", "start": [22, 6], "battery": 510, "batteryCapacity": 512 },
  { "id": "R3", "start": [22, 9], "battery": 508, "batteryCapacity": 512 },
  { "id": "R4", "start": [22, 12], "battery": 506, "batteryCapacity": 512 }
]
```

`T3.targets` 精确写为：

```json
[[0, 1], [24, 1], [24, 2], [0, 2], [0, 5], [24, 5], [24, 6], [0, 6], [0, 9], [24, 9], [24, 10], [0, 10], [0, 13], [24, 13], [24, 14], [0, 14]]
```

`T5.targets` 精确写为：

```json
[[0, 1], [0, 14], [1, 14], [1, 1], [5, 1], [5, 14], [6, 14], [6, 1], [10, 1], [10, 14], [11, 14], [11, 1], [15, 1], [15, 14], [16, 14], [16, 1], [20, 1], [20, 14], [21, 14], [21, 1], [22, 1], [22, 14], [23, 14], [23, 1], [24, 1], [24, 14]]
```

其余任务坐标和截止时间按 Step 1 的断言写入；保留现有任务类型、标题、优先级、`serviceTime`、`demand` 和 `releaseTime`。

- [ ] **Step 4: 同步真实场景实验的稳定断言**

在 `backend/tests/test_experiments.py` 的两个真实场景避碰测试中使用：

```py
assert without_avoidance["metrics"]["conflictCount"] > 0
assert with_avoidance["metrics"]["conflictCount"] == 0
assert without_avoidance["metrics"]["assignedTaskCount"] == 6
assert with_avoidance["metrics"]["assignedTaskCount"] == 6
assert without_avoidance["metrics"]["failureCount"] == 0
assert with_avoidance["metrics"]["failureCount"] == 0
```

在 `test_replan_window_experiment_uses_integrated_demo_task_timing` 中使用：

```py
assert window_4["metrics"]["assignedTaskCount"] == 4
assert window_24["metrics"]["assignedTaskCount"] == 6
assert window_4["metrics"]["conflictCount"] == 0
assert window_24["metrics"]["conflictCount"] == 0
assert window_4["metrics"]["failureCount"] == 0
assert window_24["metrics"]["failureCount"] == 0
```

- [ ] **Step 5: 运行场景数据和实验测试**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/view.test.ts
.\.venv\Scripts\python.exe -m pytest backend/tests/test_experiments.py -q
```

Expected: 前端场景数据测试和全部实验测试 PASS；真实场景无避碰基线冲突大于0，开启避碰后冲突为0。

- [ ] **Step 6: 提交场景数据和实验基线**

```powershell
git add frontend/src/domain/scenarios.json frontend/src/domain/view.test.ts backend/tests/test_experiments.py
git commit -m "feat: replace integrated demo with warehouse grid"
```

---

### Task 2: 按单格显示进货、出货、巡检和充电语义

**Files:**
- Modify: `frontend/src/domain/view.ts`
- Modify: `frontend/src/domain/view.test.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `Scenario["zones"]` 和 `cellKey(Cell)`。
- Produces: `buildZoneCellPresentations(zones): Map<string, ZoneCellPresentation>`，供 `MapBoard` 渲染单格类名和回退文字。

- [ ] **Step 1: 为单格区域表现写失败的纯函数测试**

将 `buildZoneCellPresentations` 加入 `frontend/src/domain/view.test.ts` 的导入，并增加：

```ts
it("builds single-cell zone classes and labels with stable overlap priority", () => {
  const presentations = buildZoneCellPresentations({
    warehouse: [[2, 0]],
    inspection: [[3, 1], [4, 1]],
    delivery: [[5, 15]],
    charging: [[4, 1], [25, 3]]
  });

  expect(presentations.get("2,0")).toEqual({ classNames: ["warehouse-cell"], label: "进" });
  expect(presentations.get("3,1")).toEqual({ classNames: ["inspection-cell"], label: "巡" });
  expect(presentations.get("5,15")).toEqual({ classNames: ["delivery-cell"], label: "出" });
  expect(presentations.get("25,3")).toEqual({ classNames: ["charging-cell"], label: "充" });
  expect(presentations.get("4,1")).toEqual({
    classNames: ["charging-cell", "inspection-cell"],
    label: "充"
  });
});
```

- [ ] **Step 2: 运行测试并确认缺少导出函数**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/view.test.ts
```

Expected: FAIL，TypeScript/Vitest 报告 `buildZoneCellPresentations` 未导出。

- [ ] **Step 3: 在视图领域模块实现单格区域映射**

将 `frontend/src/domain/view.ts` 的类型导入改为包含 `Scenario`，并增加：

```ts
export type ZoneCellPresentation = {
  classNames: string[];
  label: string;
};

const ZONE_CELL_STYLES = [
  ["charging", "charging-cell", "充"],
  ["warehouse", "warehouse-cell", "进"],
  ["delivery", "delivery-cell", "出"],
  ["inspection", "inspection-cell", "巡"]
] as const;

export function buildZoneCellPresentations(zones: Scenario["zones"]): Map<string, ZoneCellPresentation> {
  const presentations = new Map<string, ZoneCellPresentation>();
  for (const [zoneName, className, label] of ZONE_CELL_STYLES) {
    for (const cell of zones[zoneName] ?? []) {
      const key = cellKey(cell);
      const current = presentations.get(key);
      presentations.set(key, {
        classNames: [...(current?.classNames ?? []), className],
        label: current?.label ?? label
      });
    }
  }
  return presentations;
}
```

优先级固定为充电、进货、出货、巡检；重叠时保留所有 CSS 类，只使用第一个文字。

- [ ] **Step 4: 接入 `MapBoard` 并保持操作保护逻辑**

在 `frontend/src/main.tsx` 从 `./domain/view` 导入 `buildZoneCellPresentations`。在 `MapBoard` 中保留现有 `chargingCells` 集合供 `mapContextAction()` 使用，并增加：

```ts
const zoneCellPresentations = useMemo(
  () => buildZoneCellPresentations(scenario.zones),
  [scenario.zones]
);
```

在单元格循环中增加：

```ts
const zonePresentation = zoneCellPresentations.get(key);
```

将类名数组中的单一充电判断替换为：

```ts
...(zonePresentation?.classNames ?? []),
```

将单元格回退内容替换为：

```tsx
{robotId ? (
  // 保留现有 robot-marker 内容
) : (taskCells.get(key) ?? zonePresentation?.label ?? null)}
```

任务文字继续使用现有 `collectTaskCells()` 的“巡/取/送/急”，因此不会在38个巡检点重复显示任务 ID。

- [ ] **Step 5: 添加区域配色并让网格按场景比例显示**

在 `frontend/src/styles.css` 中删除旧的单独 `.cell.charging-cell` 规则，并将以下区域规则放在 `.cell.obstacle` 之前，使后面的障碍和封锁状态继续覆盖区域底色：

```css
.cell.warehouse-cell {
  background: #2f8f5b;
  border-color: #237247;
}

.cell.delivery-cell {
  background: #d48a1f;
  border-color: #a86713;
}

.cell.inspection-cell {
  background: #7654a6;
  border-color: #5d3d89;
}

.cell.charging-cell {
  background: #39a8a2;
  border-color: #237d78;
  color: #ffffff;
  font-weight: 700;
}
```

将 `.cell.task-cell` 改为任务内框，并只给没有功能区类别的普通任务格使用默认绿色背景：

```css
.cell.task-cell {
  border-color: #5e8b2f;
  box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.72);
}

.cell.task-cell:not(.warehouse-cell):not(.delivery-cell):not(.inspection-cell):not(.charging-cell) {
  background: #6f9d3f;
}
```

保持 `.cell.obstacle`、`.cell.blocked` 位于区域规则之后，保持 `.cell.conflict-cell` 位于任务和区域规则之后。这样障碍、运行时封锁和冲突视觉不会被进货、出货、巡检或充电底色遮蔽。

将 `.map-wrap` 的固定高度替换为动态网格比例：

```css
.map-wrap {
  position: relative;
  width: min(100%, 720px);
  height: auto;
  aspect-ratio: var(--cols) / var(--rows);
  margin: 0 auto;
  padding: 0;
}

.active-route-layer {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: grid;
  gap: 3px;
  pointer-events: none;
}
```

同时用 `inset: 0` 替换 `.active-route-layer` 原来的 `inset: 16px`。这使 `26 × 16` 地图内容区保持 `26:16`，网格单元为正方形，路径箭头层与 CSS Grid 使用完全相同的边界。

- [ ] **Step 6: 运行前端测试和构建**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/domain/view.test.ts src/main.test.ts
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

Expected: 两个测试文件全部 PASS；TypeScript 编译和 Vite 正式构建 PASS。

- [ ] **Step 7: 提交单格区域显示**

```powershell
git add frontend/src/domain/view.ts frontend/src/domain/view.test.ts frontend/src/main.tsx frontend/src/styles.css
git commit -m "feat: render warehouse map zones by cell"
```

---

### Task 3: 将固定演示回归对齐到默认任务流程

**Files:**
- Modify: `backend/tests/test_e2e_demo.py`

**Interfaces:**
- Consumes: `frontend_demo_scenario("integrated-demo")` 和现有 session API。
- Produces: 默认流程在 `T=700` 完成、无运行时事件、无充电访问的固定回归。

- [ ] **Step 1: 将固定 demo 测试改成默认任务闭环断言**

用以下测试替换 `test_integrated_demo_runs_online_dispatch_flow` 中手工任务、封锁、故障和恢复步骤：

```py
def test_integrated_demo_runs_default_online_dispatch_flow() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sessions",
        json={
            "scenario": frontend_demo_scenario("integrated-demo"),
            "options": {"avoidConflicts": True, "includeDynamic": True},
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["sessionId"]

    release_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 12},
    )
    assert release_response.status_code == 200
    release_payload = release_response.json()
    states_at_release = {state["taskId"]: state["status"] for state in release_payload["taskStates"]}
    assert states_at_release["E1"] in {"running", "completed"}
    assert release_payload["runtimeTaskCount"] == 0
    assert release_payload["runtimeEventCount"] == 0
    assert release_payload["result"]["extraBlocked"] == []
    assert release_payload["result"]["unavailableRobotIds"] == []

    completion_response = client.post(
        f"/api/sessions/{session_id}/tick",
        json={"currentTime": 700},
    )
    assert completion_response.status_code == 200
    payload = completion_response.json()
    assert payload["scenarioId"] == "integrated-demo"
    assert payload["completedTaskCount"] == 6
    assert {state["status"] for state in payload["taskStates"]} == {"completed"}
    assert payload["runtimeTaskCount"] == 0
    assert payload["runtimeEventCount"] == 0
    assert payload["result"]["metrics"]["conflictCount"] == 0
    assert payload["result"]["metrics"]["deadlineMissCount"] == 0
    assert payload["result"]["metrics"]["failureCount"] == 0
    assert payload["result"]["chargingVisits"] == []
    assert payload["metricsHistory"][-1]["completedTaskCount"] == 6
```

该测试只验证默认场景任务。运行时任务、封锁、故障和恢复继续由 `backend/tests/test_sessions.py` 的独立回归保护。

- [ ] **Step 2: 运行固定 demo 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_e2e_demo.py -q
```

Expected: 固定 demo 测试全部 PASS；默认流程在 `T=700` 完成6个任务且运行时计数保持0。

- [ ] **Step 3: 运行会话与契约回归，确认共享场景没有破坏其他链路**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py backend/tests/test_api_contract.py -q
```

Expected: `backend/tests/test_sessions.py` 和 `backend/tests/test_api_contract.py` 全部 PASS。

- [ ] **Step 4: 提交回归适配**

```powershell
git add backend/tests/test_e2e_demo.py
git commit -m "test: realign integrated demo regressions"
```

---

### Task 4: 同步基线、演示、测试和实验文档

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/baseline.md`
- Modify: `docs/demo.md`
- Modify: `docs/testing-guide.md`
- Modify: `docs/experiments.md`

**Interfaces:**
- Consumes: 已通过的场景数据、固定 demo 回归和实验结果。
- Produces: 与代码一致的项目基线、默认流程和人工测试说明。

- [ ] **Step 1: 更新基线和演示说明**

在 `docs/baseline.md` 和 `docs/demo.md` 明确写入以下事实：

```markdown
`integrated-demo` 使用 `26 × 16` 网格，包含12组 `3 × 2` 单格货架。货架组、顶部进货点、底部出货点、右侧机器人起点和充电口之间统一保留两个完整可走格；所有非障碍单元均可通行。

默认演示只运行 `T1`、`T2`、`T3`、`T4`、`T5` 和 `E1`。`T3`、`T5` 分别覆盖横向和纵向通道，`E1` 在 `T=12` 释放。默认流程不追加运行时任务，不自动封锁单元，也不自动标记机器人故障。

地图显示充电口、电量和容量，但默认流程不要求进入充电状态；完整充电闭环继续由低电量专用回归验证。
```

从 `docs/baseline.md` 的“下一阶段入口”删除“重做更真实的仓库地图”，因为该项在本分支完成。不得删除异构能力和第三级避碰仍未实现的说明。

- [ ] **Step 2: 更新人工测试和实验口径**

在 `docs/testing-guide.md` 的固定场景章节增加：

```markdown
- 地图尺寸为 `26 × 16`，货架为12组 `3 × 2` 单格障碍。
- 任意货架组或功能点之间至少能看到两个完整可走格。
- 进货点位于顶部、出货点位于底部、机器人和充电口位于右侧。
- 默认播放到 `T=700` 时，6个场景任务应全部完成；冲突、失败、截止超期和充电访问均为0。
```

将默认演示的手工封锁、故障和任务追加移到独立人工测试小节，不再把它们写成默认步骤。

在 `docs/experiments.md` 将真实场景避碰结论更新为：

```markdown
`integrated-demo` 的当前固定输入中，无避碰基线存在预测冲突；开启优先级避碰后6个任务仍全部分配，预测冲突降为0，失败和截止超期保持为0。该结果属于当前场景和回归覆盖，不等于已经实现第三级全规划时域安全门。
```

- [ ] **Step 3: 更新 README 和 AGENTS 当前状态**

在 `README.md` 的项目概述补充一条真实仓库地图能力：

```markdown
- 唯一固定场景采用 `26 × 16` 真实仓库网格，包含双格通道、12组 `3 × 2` 货架、顶部进货点、底部出货点和右侧充电区。
```

在 `AGENTS.md` 的完成项和路线图中记录：

```markdown
- The sole `integrated-demo` baseline now uses a `26 × 16` realistic warehouse grid with twelve `3 × 2` shelf groups, two-cell horizontal and vertical clearances, top inbound cells, bottom outbound cells, right-side robot starts, and right-side charging cells.
- The fixed default demo regression now uses only the six scenario tasks and completes by T=700 with zero active conflicts, failures, deadline misses, or charging visits; runtime task, block, failure, and recovery behavior remains covered by focused session regressions.
```

删除或改写 `AGENTS.md` 中“固定 demo 通过手工任务、封锁、故障和恢复验证”的过期描述，但保留这些运行时能力已由其他测试覆盖的事实。

- [ ] **Step 4: 运行文档搜索和完整检查**

Run:

```powershell
rg -n "14 × 10|14x10|重做更真实的仓库地图|固定 demo.*手工|默认流程.*手动封锁" README.md AGENTS.md docs
& 'C:\nvm4w\nodejs\npm.cmd' run check
git diff --check
```

Expected: 搜索不再命中过期地图尺寸或“地图仍待重做”的表述；允许设计文档在背景中说明旧地图是 `14 × 10`。完整检查通过，前端测试和后端测试均无失败，`git diff --check` 无输出。

- [ ] **Step 5: 提交文档并确认分支状态**

```powershell
git add README.md AGENTS.md docs/baseline.md docs/demo.md docs/testing-guide.md docs/experiments.md
git commit -m "docs: document realistic warehouse demo"
git status --short
```

Expected: 提交成功；`git status --short` 最多只显示未跟踪的 `.superpowers/` 视觉草图，不显示任何未提交的产品代码、测试或项目文档。

---

## Final Verification

- [ ] 确认当前分支为 `codex/real-warehouse-map`。
- [ ] 确认 `git log --oneline -5` 包含场景、单格显示、回归和文档提交。
- [ ] 再次运行 `& 'C:\nvm4w\nodejs\npm.cmd' run check`，预期全部通过。
- [ ] 运行 `git diff main...HEAD --check`，预期无输出。
- [ ] 检查 `git diff --stat main...HEAD`，确认没有算法实现、API schema、实验面板、竞赛材料或3D文件进入分支。
