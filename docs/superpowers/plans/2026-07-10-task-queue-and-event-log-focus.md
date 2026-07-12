# 任务队列与事件日志聚焦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将任务详情改为单行指标，并让历史事件只由事件日志展示。

**Architecture:** 在 `main.tsx` 中以 `buildTaskQueueMetricRows` 生成任务的标签和值，再由任务卡按行渲染。将现有混合历史事件和状态的 `OperationalInsights` 替换为仅含实时调度状态的 `ReplanStatus`，指标模块不再渲染重规划摘要，右侧模块不再渲染历史事件。后端继续作为事件日志的唯一写入来源。

**Tech Stack:** React 19, TypeScript, Vite, Vitest, CSS。

## Global Constraints

- 只修改前端任务队列、指标模块和重规划解释模块的渲染与样式。
- 后端任务录入、自动任务流、机器人故障、动态事件、封锁和解锁的事件写入逻辑保持不变。
- 未完成任务默认展开，已完成任务默认收起。
- 事件日志是唯一的历史事件展示位置，继续保留跳转功能。

---

### Task 1: 建立任务指标行模型

**Files:**
- Modify: `frontend/src/main.tsx:66-86,955-992,1287-1302`
- Test: `frontend/src/main.test.ts:568-633`

**Interfaces:**
- Consumes: `TaskSnapshot`。
- Produces: `buildTaskQueueMetricRows(snapshot): Array<{ label: string; value: string }>`，顺序为状态、执行机器人、任务类型、优先级、锁定状态、到达时间、截止时间、完成时间。

- [x] **Step 1: 写入失败测试**

在 `frontend/src/main.test.ts` 导入 `buildTaskQueueMetricRows`，并在现有 `task queue display` 用例中加入：

```ts
expect(buildTaskQueueMetricRows(snapshot)).toEqual([
  { label: "状态", value: "已完成" },
  { label: "执行机器人", value: "R1" },
  { label: "任务类型", value: "巡检" },
  { label: "优先级", value: "2" },
  { label: "锁定状态", value: "可重分配" },
  { label: "到达时间", value: "T=4" },
  { label: "截止时间", value: "无" },
  { label: "完成时间", value: "T=9" }
]);
```

- [x] **Step 2: 运行定向测试并确认失败**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示 `buildTaskQueueMetricRows` 尚未导出。

- [x] **Step 3: 实现指标行并替换任务卡详情渲染**

在 `main.tsx` 中新增：

```ts
export function buildTaskQueueMetricRows(snapshot: TaskSnapshot) {
  const completion = snapshot.completionTime == null
    ? "未完成"
    : snapshot.status === "done"
      ? `T=${snapshot.completionTime}`
      : `预计 T=${snapshot.completionTime}`;
  return [
    { label: "状态", value: taskStatusLabel(snapshot.status) },
    { label: "执行机器人", value: snapshot.assignedRobotId ?? "未分配" },
    { label: "任务类型", value: taskTypeLabel(snapshot.task.type) },
    { label: "优先级", value: String(snapshot.task.priority) },
    { label: "锁定状态", value: snapshot.locked ? "已锁定" : "可重分配" },
    { label: "到达时间", value: `T=${snapshot.releaseTime}` },
    { label: "截止时间", value: snapshot.task.deadline == null ? "无" : `T=${snapshot.task.deadline}` },
    { label: "完成时间", value: completion }
  ];
}
```

将 `TaskQueueItem` 的 `<summary>` 收敛为任务 ID、标题和既有伪元素控制；使用以下结构渲染行：

```tsx
<div className="task-metric-list">
  {buildTaskQueueMetricRows(snapshot).map((item) => (
    <div className="task-metric-row" key={item.label}>
      <span>{item.label}</span>
      <strong>{item.value}</strong>
    </div>
  ))}
</div>
```

保留失败原因、恢复说明、恢复按钮和进度条在指标列表之后。

- [x] **Step 4: 运行定向测试并确认通过**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: PASS，现有时间字段和排序测试与新增指标行测试均通过。

### Task 2: 删除重复历史事件展示

**Files:**
- Modify: `frontend/src/main.tsx:72-84,176-180,621-624,820-826,871-909,1315-1346`
- Test: `frontend/src/main.test.ts:205-275`

**Interfaces:**
- Consumes: `SessionResult["taskStates"]`、`DispatchResult["unavailableRobotIds"]`。
- Produces: `buildReplanStatus(result, session): { lockedTaskCount: number; unassignedTaskCount: number; failedRobotCount: number }`。

- [x] **Step 1: 写入失败测试**

在 `frontend/src/main.test.ts` 导入 `buildReplanStatus`，将当前 `buildOperationalInsights` 断言替换为：

```ts
expect(buildReplanStatus(result, session)).toEqual({
  lockedTaskCount: 1,
  unassignedTaskCount: 0,
  failedRobotCount: 1
});
```

- [x] **Step 2: 运行定向测试并确认失败**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示 `buildReplanStatus` 尚未导出。

- [x] **Step 3: 最小化实现状态摘要并移除事件副本**

将 `OperationalInsights` 类型、`buildOperationalInsights` 和 `OperationalInsightPanel` 替换为：

```ts
type ReplanStatus = {
  lockedTaskCount: number;
  unassignedTaskCount: number;
  failedRobotCount: number;
};

export function buildReplanStatus(result: DispatchResult, session: SessionResult | null): ReplanStatus {
  const taskStates = session?.taskStates ?? [];
  return {
    lockedTaskCount: taskStates.filter((state) => state.locked).length,
    unassignedTaskCount: taskStates.filter((state) => state.status === "unassigned").length,
    failedRobotCount: result.unavailableRobotIds.length
  };
}
```

新增只渲染三个 `Metric` 的 `ReplanStatusPanel`；指标模块删除该面板，右侧“重规划解释”只传入 `ReplanStatusPanel`。保留事件日志的 `result.eventLog.map(...)` 和跳转按钮，不修改它们。

- [x] **Step 4: 运行定向测试并确认通过**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: PASS，重规划状态只由运行时状态和不可用机器人推导，不再依赖事件历史或指标历史。

### Task 3: 为单行指标添加布局样式并执行浏览器验证

**Files:**
- Modify: `frontend/src/styles.css:922-1029`

**Interfaces:**
- Consumes: `.entity`、`.task-metric-list`、`.task-metric-row`、`.task-recovery`。
- Produces: 每个指标在独立行内显示标签和值；任务卡的恢复区和进度条保持在指标行之后。

- [x] **Step 1: 添加单行指标样式**

新增：

```css
.task-metric-list {
  display: grid;
  gap: 4px;
  margin-top: 8px;
}

.task-metric-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.35;
}

.task-metric-row span,
.task-metric-row strong {
  margin: 0;
}

.task-metric-row strong {
  color: var(--text);
  font-size: inherit;
  font-weight: 700;
  text-align: right;
}
```

移除只服务于旧任务详情的连续中点文本样式依赖，不调整通用 `.entity span` 的其他消费者。

- [x] **Step 2: 浏览器验证任务卡和事件唯一性**

打开 `http://127.0.0.1:5174/`，确认未完成任务详情依次显示八项独立行；已完成任务默认收起且可展开。确认指标模块只保留八项实时指标；重规划解释只有三项当前状态；事件日志仍显示手动任务、封锁、机器人故障和重规划事件。

### Task 4: 执行完整回归

**Files:**
- Verify only: `frontend/src/main.tsx`, `frontend/src/main.test.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: 前三项完成后的前端渲染、纯函数和样式。
- Produces: 已构建并通过项目测试的工作区。

- [x] **Step 1: 运行前端构建**

Run: `npm run frontend:build`

Expected: `tsc -b && vite build` 退出码为 0。

- [x] **Step 2: 运行完整检查**

Run: `npm run check`

Expected: 前端构建、68 项前端测试和 162 项后端测试通过；计数如后续新增测试而增加，以实际输出为准。

- [x] **Step 3: 记录验证结果**

最终说明列出定向测试、完整检查和浏览器验证结果。当前目录不是可用 Git 工作树时，不执行提交或回退操作。
