# 活跃路径与任务队列 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在在线调度地图中以当前任务的小箭头替代完整路径，并完成速度输入与双栏任务队列改造。

**Architecture:** 路径箭头由 `frontend/src/main.tsx` 中可单测的纯函数从当前结果、显示时间和运行时任务状态推导，再覆盖到与 `.grid-map` 同构的 CSS 网格中。App 保留一个路径提示启用状态，首次播放或运行时重规划成功后开启，场景切换与会话重置关闭。任务快照在渲染前按未完成和已完成拆分，保留原有详情与恢复操作。

**Tech Stack:** React 18、TypeScript、Vite、Vitest、Lucide React、CSS Grid。

## Global Constraints

- 只修改前端，不改动后端调度、路径规划、会话 API 或场景 JSON。
- 所有源文件使用 UTF-8；代码注释使用中文。
- 仅显示当前活跃任务的剩余路径方向；未启动、等待、故障和已完成任务不得显示箭头。
- 地图画布尺寸保持固定，箭头必须与实际 CSS Grid 单元对齐。
- 工作目录未被 Git 识别；不执行工作树或提交操作。

---

### Task 1: 路径提示和速度的纯逻辑

**Files:**
- Modify: `frontend/src/main.tsx:39-80,1601-1685,1760-1880`
- Test: `frontend/src/main.test.ts:357-608`

**Interfaces:**
- Produces: `export type ActiveRouteArrow = { robotId: string; cell: Cell; angle: number; lane: number; }`.
- Produces: `export function buildActiveRouteArrows(result: DispatchResult, time: number, runtimeStates: SessionResult["robotStates"], enabled: boolean): ActiveRouteArrow[]`.
- Produces: `export function parsePlaybackSpeed(value: string): number | null`.

- [ ] **Step 1: 写入失败测试**

```ts
it("does not return route arrows before playback has started", () => {
  expect(buildActiveRouteArrows(result, 0, [], false)).toEqual([]);
});

it("returns only the current task's remaining directions and removes them after completion", () => {
  expect(buildActiveRouteArrows(result, 1, [], true)).toEqual([
    { robotId: "R1", cell: [2, 0], angle: 90, lane: 0 }
  ]);
  expect(buildActiveRouteArrows(result, 3, [], true)).toEqual([]);
});

it("parses only supported playback rates", () => {
  expect(parsePlaybackSpeed("2.4")).toBe(2.4);
  expect(parsePlaybackSpeed("0.1")).toBeNull();
  expect(parsePlaybackSpeed("10.1")).toBeNull();
  expect(parsePlaybackSpeed("abc")).toBeNull();
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示 `buildActiveRouteArrows` 和 `parsePlaybackSpeed` 尚未导出。

- [ ] **Step 3: 实现最小路径与速度逻辑**

```ts
export type ActiveRouteArrow = {
  robotId: string;
  cell: Cell;
  angle: number;
  lane: number;
};

export function parsePlaybackSpeed(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0.2 && parsed <= 10 ? parsed : null;
}
```

为每个已分配机器人确定运行时 `currentTaskId` 或当前活跃任务；从 `time` 对应的路径索引开始，依次查找该任务尚未经过的目标点。针对每对相邻单元返回一个箭头，箭头放在下一目标格，方向为 `Math.atan2(deltaX, -deltaY) * 180 / Math.PI`，并按机器人在结果路径中的顺序设置 `lane`。没有活跃任务、没有下一格或 `enabled` 为 `false` 时返回空数组。

- [ ] **Step 4: 运行测试并确认通过**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: PASS，路径和速度测试均通过。

### Task 2: 地图箭头覆盖层和播放速度输入

**Files:**
- Modify: `frontend/src/main.tsx:117-249,544-553,757-783,988-1100`
- Modify: `frontend/src/styles.css:193-207,819-875`
- Test: `frontend/src/main.test.ts:357-608`

**Interfaces:**
- Consumes: `buildActiveRouteArrows(...)` 和 `parsePlaybackSpeed(...)`。
- Produces: `routeHintsEnabled: boolean`，由地图渲染使用。

- [ ] **Step 1: 扩展失败测试**

```ts
it("uses an active runtime task instead of a future assigned task for route arrows", () => {
  expect(buildActiveRouteArrows(result, 1, [
    { robotId: "R1", name: "R1", position: [1, 0], status: "inspecting", battery: 90, load: 0, currentTaskId: "T1" }
  ], true).map((arrow) => arrow.robotId)).toEqual(["R1"]);
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL，运行时任务分支尚未覆盖。

- [ ] **Step 3: 替换地图覆盖层和控制器**

```tsx
<div className="active-route-layer" aria-hidden="true">
  {routeArrows.map((arrow) => (
    <span
      className={`active-route-arrow active-route-arrow-${arrow.lane % 6}`}
      key={`${arrow.robotId}-${arrow.cell[0]}-${arrow.cell[1]}-${arrow.angle}`}
      style={{ gridColumn: arrow.cell[0] + 1, gridRow: arrow.cell[1] + 1, "--arrow-angle": `${arrow.angle}deg`, "--arrow-lane": arrow.lane } as React.CSSProperties}
    >
      <ArrowUp size={15} strokeWidth={2.6} />
    </span>
  ))}
</div>
```

删除 `.path-layer` 和 SVG 折线。`.active-route-layer` 与 `.grid-map` 使用相同的 `grid-template-columns`、`grid-template-rows`、`gap: 3px`、`inset: 16px`；箭头在下一目标单元中心显示，因此不会覆盖机器人所在格，并通过 CSS 将 `--arrow-angle` 旋转和把 `--arrow-lane` 映射为最多 2px 的横向偏移。

增加 `routeHintsEnabled` 状态：会话创建、场景/选项变化和重置时置为 `false`；从停止切换到播放时置为 `true`；运行时任务、封锁或机器人更新成功后置为 `true`。MapBoard 接收该状态并传给 `buildActiveRouteArrows`。

将 `speedMs` 改为已提交的 `playbackRate` 和可编辑的 `playbackRateInput`。播放定时器使用 `1000 / playbackRate`；输入框使用 `type="number"`、`min="0.2"`、`max="10"`、`step="0.1"`，失焦或按 Enter 时仅在 `parsePlaybackSpeed` 返回数值后提交，否则恢复最近一次有效值。

- [ ] **Step 4: 运行测试并确认通过**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: PASS，活跃运行时任务路径和速度校验均通过。

### Task 3: 双栏任务队列和在线任务精简

**Files:**
- Modify: `frontend/src/main.tsx:156-159,584-634`
- Modify: `frontend/src/main.tsx:1192-1204`
- Modify: `frontend/src/styles.css:268-272,721-748,971-1010,1150-1165`
- Test: `frontend/src/main.test.ts:515-608`

**Interfaces:**
- Produces: `export function splitTaskSnapshotsForDisplay(snapshots: TaskSnapshot[]): { unfinished: TaskSnapshot[]; completed: TaskSnapshot[]; }`.
- Consumes: `sortTaskSnapshotsForDisplay(...)`。

- [ ] **Step 1: 写入失败测试**

```ts
it("splits unfinished and completed tasks into independent ordered lists", () => {
  const groups = splitTaskSnapshotsForDisplay(snapshots);
  expect(groups.unfinished.map((snapshot) => snapshot.task.id)).toEqual(["ACTIVE", "PENDING"]);
  expect(groups.completed.map((snapshot) => snapshot.task.id)).toEqual(["DONE"]);
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示 `splitTaskSnapshotsForDisplay` 尚未导出。

- [ ] **Step 3: 实现分栏渲染和样式**

```ts
export function splitTaskSnapshotsForDisplay(snapshots: TaskSnapshot[]) {
  const sorted = sortTaskSnapshotsForDisplay(snapshots);
  return {
    unfinished: sorted.filter((snapshot) => snapshot.status !== "done"),
    completed: sorted.filter((snapshot) => snapshot.status === "done")
  };
}
```

在 App 中通过 `useMemo` 生成 `taskGroups`，渲染“未完成”和“已完成”两个 `.task-queue-column`。继续使用现有任务详情内容；已完成项保留 `open={false}`，未完成项保留展开。把 `.map-body` 第二列改为 `460px`，新增 `.task-queue-columns` 两列网格和窄屏单列规则。删除在线任务面板中 `.session-note` 的会话统计段落，但不删除仿真启动面板的同步状态说明。

- [ ] **Step 4: 运行测试并确认通过**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: PASS，任务分组与既有任务队列测试均通过。

### Task 4: 全量验证和说明同步

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-active-route-and-task-queue-design.md`
- Test: `frontend/src/main.test.ts`

- [ ] **Step 1: 更新设计说明的实现状态**

在“验收与测试”后补充已执行的自动验证命令与手工验证步骤；不改变已确认的交互规则。

- [ ] **Step 2: 运行前端测试和构建**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test`

Expected: PASS，所有前端测试通过。

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build`

Expected: PASS，TypeScript 编译与 Vite 构建成功。

- [ ] **Step 3: 手工验证本地页面**

打开 `http://127.0.0.1:5174/`，确认初始无箭头；播放后箭头在格内对齐；暂停后保留；动态重规划后更新；任务完成后消失并移入已完成列；在线任务面板不再有会话统计小字。

## 执行结果

- 已完成 Task 1 至 Task 4；因工作目录不属于 Git 仓库，未创建工作树或提交。
- 自动验证通过：前端测试 65 项，前端生产构建通过。
- 页面验证通过：初始无箭头，播放后显示活跃任务箭头，任务完成后进入默认折叠的已完成栏，播放速度输入和在线任务面板精简均已生效。
