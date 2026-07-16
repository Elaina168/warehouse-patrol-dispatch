# Frontend Regression Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复工具栏遮挡、故障机器人样式缺失和播放前事件隐藏三个前端回归问题。

**Architecture:** 业务状态判断保留原始英文状态，中文只用于显示；事件过滤只依赖事件时间与当前时间；工具栏根据自身容器宽度切换布局。所有修改限制在现有 React 页面、前端测试和样式表内。

**Tech Stack:** React 19、TypeScript、Vitest、CSS Container Queries、Playwright CLI。

## Global Constraints

- 不修改后端协议、调度算法、地图数据或默认任务流程。
- 不改变事件排序、事件内容或事件跳转规则。
- 所有文本和源文件保持 UTF-8。
- 不提交 `.superpowers/`、`output/` 或 Playwright 临时产物。

---

### Task 1: 已发生事件立即显示

**Files:**
- Modify: `frontend/src/main.test.ts:598-615`
- Modify: `frontend/src/main.tsx:888`
- Modify: `frontend/src/main.tsx:1824-1827`

**Interfaces:**
- Produces: `visibleRuntimeEvents<T extends { time: number }>(events: T[], currentTime: number): T[]`

- [ ] **Step 1: Write the failing test**

```ts
it("shows events that already happened even before playback starts", () => {
  const events = [
    { time: 0, text: "启动" },
    { time: 2, text: "实际锁定" }
  ];

  expect(visibleRuntimeEvents(events, 0)).toEqual([{ time: 0, text: "启动" }]);
  expect(visibleRuntimeEvents(events, 1)).toEqual([{ time: 0, text: "启动" }]);
  expect(visibleRuntimeEvents(events, 2)).toEqual(events);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: FAIL because the existing function still requires a `started` argument and hides events while it is false.

- [ ] **Step 3: Write minimal implementation**

```ts
export function visibleRuntimeEvents<T extends { time: number }>(events: T[], currentTime: number): T[] {
  return events.filter((event) => event.time <= currentTime);
}
```

Update the render call to `visibleRuntimeEvents(result.eventLog, time)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: all `main.test.ts` tests PASS.

### Task 2: 故障机器人使用原始状态判断

**Files:**
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/main.tsx:1201-1217`
- Modify: `frontend/src/main.tsx:1800-1820`

**Interfaces:**
- Produces: `isFailedRobotState(status: SessionResult["robotStates"][number]["status"] | null | undefined): boolean`

- [ ] **Step 1: Write the failing test**

```ts
describe("robot failure presentation", () => {
  it("marks only the raw failed runtime status as failed", () => {
    expect(isFailedRobotState("failed")).toBe(true);
    expect(isFailedRobotState("idle")).toBe(false);
    expect(isFailedRobotState(null)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: FAIL because `isFailedRobotState` is not exported.

- [ ] **Step 3: Write minimal implementation**

```ts
export function isFailedRobotState(
  status: SessionResult["robotStates"][number]["status"] | null | undefined
): boolean {
  return status === "failed";
}
```

Use `isFailedRobotState(runtimeState?.status)` when appending `failed-robot-cell`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: all `main.test.ts` tests PASS.

### Task 3: 工具栏根据主栏宽度换行

**Files:**
- Modify: `frontend/src/styles.css:376-387`
- Verify: `frontend/src/main.tsx:581-630`

**Interfaces:**
- Consumes: existing `.workspace` and `.map-toolbar` classes.
- Produces: a container-query layout that does not overlap `.rightbar`.

- [ ] **Step 1: Reproduce the failing layout**

At `1280 × 720`, use Playwright to assert that the center point of the “导出模板” button is not owned by that button and that clicking it is intercepted by `.rightbar-event-log`.

Expected: FAIL before the CSS change.

- [ ] **Step 2: Write minimal CSS implementation**

```css
.workspace {
  container-type: inline-size;
  overflow: visible;
}

@container (max-width: 840px) {
  .map-toolbar {
    grid-template-columns: minmax(150px, 1fr) minmax(200px, 1fr) minmax(160px, 0.8fr) auto;
  }

  .map-toolbar > * {
    min-width: 0;
  }
}
```

- [ ] **Step 3: Verify the browser regression**

At `1280 × 720`, verify:

- the “导出模板” button center resolves to the button or one of its children;
- clicking it completes without interception;
- “导入场景” remains visible;
- `.map-toolbar` right edge does not exceed `.main-column` right edge.

Also verify the existing wide layout at `1366 × 768`.

Expected: all assertions PASS.

### Task 4: Full verification

**Files:**
- Verify: `frontend/src/main.tsx`
- Verify: `frontend/src/main.test.ts`
- Verify: `frontend/src/styles.css`

- [ ] **Step 1: Run frontend tests**

Run: `npm --prefix frontend run test`

Expected: all frontend tests PASS.

- [ ] **Step 2: Run production build**

Run: `npm --prefix frontend run build`

Expected: TypeScript compilation and Vite build PASS.

- [ ] **Step 3: Run complete project verification**

Run: `npm run check`

Expected: frontend build, frontend tests, and all backend tests PASS.

- [ ] **Step 4: Check source and worktree**

Run: `git diff --check`

Expected: no whitespace errors. Confirm `.superpowers/` and `output/` remain untracked and unstaged.
