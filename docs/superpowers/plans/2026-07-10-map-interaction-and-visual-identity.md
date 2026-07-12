# 地图交互与视觉区分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让单场景地图中的路径、机器人、任务标签和运行时封锁操作清晰且不冲突。

**Architecture:** 前端从当前场景机器人顺序生成稳定颜色，并在地图单元和箭头层共用该颜色。路径箭头在 MapBoard 中按任务标签格过滤；右键菜单只调用现有会话封锁接口，并由返回会话结果驱动重规划后的地图。场景状态从“可选择集合”收敛为默认或已导入的单个当前场景。

**Tech Stack:** React 18、TypeScript、Vite、Vitest、Lucide React、CSS Grid。

## Global Constraints

- 只修改前端；保留后端动态触发、运行时封锁和重规划 API。
- 源文件使用 UTF-8；代码注释使用中文。
- 路径箭头不覆盖显示“巡、取、送、急”的任务标签格。
- 导入场景替换当前场景，不再提供多个场景选择。
- 固定障碍、任务标签格和机器人占用格不提供右键封锁动作。
- 当前目录不属于 Git 仓库，不创建工作树或提交。

---

### Task 1: 路径过滤、机器人颜色和单场景状态

**Files:**
- Modify: `frontend/src/main.tsx:1-170,447-465,499-515,1064-1185,1225-1255,1730-1830`
- Modify: `frontend/src/styles.css:780-995`
- Test: `frontend/src/main.test.ts:1-45,453-540`

**Interfaces:**
- Produces: `export const ROBOT_COLORS: readonly string[]`.
- Produces: `export function robotColorForIndex(index: number): string`.
- Produces: `export function filterActiveRouteArrows(arrows: ActiveRouteArrow[], taskCells: ReadonlySet<string>): ActiveRouteArrow[]`.

- [ ] **Step 1: 写入失败测试**

```ts
it("uses a stable robot palette and removes arrows from task label cells", () => {
  expect(robotColorForIndex(0)).not.toBe(robotColorForIndex(1));
  expect(robotColorForIndex(4)).toBe(robotColorForIndex(0));
  expect(filterActiveRouteArrows([
    { robotId: "R1", cell: [1, 0], angle: 90, lane: 0 },
    { robotId: "R2", cell: [2, 0], angle: 90, lane: 1 }
  ], new Set(["1,0"]))).toEqual([
    { robotId: "R2", cell: [2, 0], angle: 90, lane: 1 }
  ]);
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示新增路径过滤和颜色导出不存在。

- [ ] **Step 3: 实现最小视觉映射**

```ts
export const ROBOT_COLORS = ["#1c6dd0", "#117a8b", "#5e8b2f", "#c23b22"] as const;

export function robotColorForIndex(index: number): string {
  return ROBOT_COLORS[index % ROBOT_COLORS.length];
}

export function filterActiveRouteArrows(arrows: ActiveRouteArrow[], taskCells: ReadonlySet<string>): ActiveRouteArrow[] {
  return arrows.filter((arrow) => !taskCells.has(cellKey(arrow.cell)));
}
```

MapBoard 将过滤后的箭头和机器人单元应用相同的 `--robot-color`。删除箭头按 `lane` 切换颜色的 CSS，仅保留轻微偏移。将 `customScenarios` 和 `scenarioId` 收敛为 `importedScenario`；工具栏使用当前 `scenario.name` 标题，导入成功后 `setImportedScenario(importedScenario)`。

- [ ] **Step 4: 运行测试并确认通过**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: PASS，颜色和任务标签过滤测试通过。

### Task 2: 移除指标历史并迁移封锁交互

**Files:**
- Modify: `frontend/src/main.tsx:302-325,587-595,613,740-760,884-932,1064-1185,2042-2047`
- Modify: `frontend/src/styles.css:306-405,780-1010`
- Test: `frontend/src/main.test.ts:244-255,357-452`

**Interfaces:**
- Produces: `export type MapContextAction = "block" | "unblock"`.
- Produces: `export function mapContextAction(cell: Cell, blocked: ReadonlySet<string>, obstacles: ReadonlySet<string>, taskCells: ReadonlySet<string>, occupied: ReadonlySet<string>): MapContextAction | null`.

- [ ] **Step 1: 写入失败测试**

```ts
it("only offers a map context action for an unoccupied non-task cell", () => {
  expect(mapContextAction([2, 2], new Set(), new Set(), new Set(), new Set())).toBe("block");
  expect(mapContextAction([2, 2], new Set(["2,2"]), new Set(), new Set(), new Set())).toBe("unblock");
  expect(mapContextAction([2, 2], new Set(), new Set(["2,2"]), new Set(), new Set())).toBeNull();
  expect(mapContextAction([2, 2], new Set(), new Set(), new Set(["2,2"]), new Set())).toBeNull();
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示 `mapContextAction` 尚未导出。

- [ ] **Step 3: 实现右键菜单和封锁调用**

```ts
export function mapContextAction(
  cell: Cell,
  blocked: ReadonlySet<string>,
  obstacles: ReadonlySet<string>,
  taskCells: ReadonlySet<string>,
  occupied: ReadonlySet<string>
): MapContextAction | null {
  const key = cellKey(cell);
  if (obstacles.has(key) || taskCells.has(key) || occupied.has(key)) return null;
  return blocked.has(key) ? "unblock" : "block";
}
```

移除 `MetricsHistoryChart`、曲线专用类型和 CSS，保留 `getVisibleMetricsHistory` 供运行解释使用。移除突发事件面板中的封锁坐标输入和两个按钮。App 维护 `{ cell, action, x, y } | null` 菜单状态；MapBoard 在 `onContextMenu` 中调用 `preventDefault()` 并只为 `mapContextAction` 有返回值的格子打开菜单。菜单动作调用参数化的 `blockCell(cell)` 或 `unblockCell(cell)`，然后关闭菜单。点击地图其他位置、Escape 和加载状态关闭菜单。

- [ ] **Step 4: 运行测试并确认通过**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: PASS，右键动作判定测试和现有运行时测试通过。

### Task 3: 全量验证和说明同步

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-map-interaction-and-visual-identity-design.md`
- Test: `frontend/src/main.test.ts`

- [ ] **Step 1: 回写验证结果**

在设计说明末尾增加实现和验证记录，不修改已确认的交互规则。

- [ ] **Step 2: 运行完整项目检查**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' run check`

Expected: PASS，前端构建、前端测试和后端测试全部通过。

- [ ] **Step 3: 验证本地页面**

打开 `http://127.0.0.1:5174/`，确认标题替代选择框、指标历史缺失、任务标签格无箭头、机器人与箭头同色，右键普通格出现封锁、右键已封锁格出现解除封锁。

## 执行结果

- 已完成 Task 1 至 Task 3；当前目录不属于 Git 仓库，未创建工作树或提交。
- 已通过页面验证：单场景标题、右键封锁、右键解除封锁和会话重规划结果已生效。
