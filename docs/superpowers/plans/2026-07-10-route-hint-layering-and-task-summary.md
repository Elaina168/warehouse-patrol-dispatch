# 路径提示层级与任务摘要布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让仿真启动前的地图编辑不显示路径箭头，并使机器人标识始终高于箭头、任务摘要控件不再重叠。

**Architecture:** 保留前端已有的 `routeHintsEnabled` 状态，将其唯一开启时机收敛到播放首次启动；运行时会话更新仅写入后端返回的数据。地图继续使用同一套网格与箭头层，通过提升机器人单元的层级而非新增渲染层实现置顶。任务卡仅调整 CSS 定位，不改变 `details` 的展开语义。

**Tech Stack:** React 19, TypeScript, Vite, Vitest, CSS。

## Global Constraints

- 只修改前端 `main.tsx` 的路径提示状态更新和 `styles.css` 的地图、任务卡布局。
- 复用现有会话封锁、解除封锁和后端重规划接口，不修改后端调度算法、接口契约或任务数据。
- 页面初始化、重置会话或替换导入场景后，活跃路径箭头保持隐藏；首次播放后暂停时保留。
- 机器人标识、选中轮廓和悬停提示必须高于活跃路径箭头。
- 未完成任务默认展开，已完成任务默认收起。

---

### Task 1: 收敛路径提示状态边界

**Files:**
- Modify: `frontend/src/main.tsx:611-782`
- Test: `frontend/src/main.test.ts:707-764`

**Interfaces:**
- Consumes: `buildActiveRouteArrows(result, time, robotStates, routeHintsEnabled)`，当最后一个参数为 `false` 时返回空数组。
- Produces: `updateSession` 只应用 `SessionResult`，不改变 `routeHintsEnabled`；`togglePlayback` 是唯一将其设为 `true` 的运行时入口。

- [x] **Step 1: 增加运行时更新不启用路径提示的失败测试**

在 `frontend/src/main.test.ts` 中导入并测试一个纯函数 `routeHintsAfterSessionUpdate`：

```ts
it("keeps route hints disabled after a pre-start session update", () => {
  expect(routeHintsAfterSessionUpdate(false)).toBe(false);
  expect(routeHintsAfterSessionUpdate(true)).toBe(true);
});
```

- [x] **Step 2: 运行定向测试并确认失败**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示 `routeHintsAfterSessionUpdate` 尚未导出。

- [x] **Step 3: 实现状态保持函数并在会话更新中使用**

在 `frontend/src/main.tsx` 的其他导出辅助函数附近加入：

```ts
export function routeHintsAfterSessionUpdate(routeHintsEnabled: boolean): boolean {
  return routeHintsEnabled;
}
```

将 `updateSession` 成功分支替换为：

```ts
applySessionPayload(payload);
setRouteHintsEnabled(routeHintsAfterSessionUpdate);
```

保留 `togglePlayback` 中的 `setRouteHintsEnabled(true)`，以及场景切换、重置中设为 `false` 的现有行为。

- [x] **Step 4: 运行定向测试并确认通过**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: PASS，现有活跃路径箭头测试和新增状态保持测试均通过。

- [x] **Step 5: 在浏览器验证会话状态边界**

打开 `http://127.0.0.1:5174/`：初始时右键普通地图格并选择封锁，确认地图没有 `.active-route-arrow`；点击播放后确认出现当前任务箭头；暂停后右键封锁，确认箭头仍可见并跟随返回结果更新。

### Task 2: 调整机器人与路径箭头的层级

**Files:**
- Modify: `frontend/src/styles.css:783-855`

**Interfaces:**
- Consumes: `.map-wrap`、`.active-route-layer`、`.grid-map`、`.cell.robot-cell`、`.robot-marker` 的现有 DOM 结构。
- Produces: 正常网格地块低于 `.active-route-layer`，`.cell.robot-cell` 及其子内容高于该路径层。

- [x] **Step 1: 确认当前视觉回归场景**

Run: 在浏览器开始播放，等待至少一条活跃箭头与机器人位置相邻或重叠。

Expected: 当前实现中箭头层的 `z-index: 2` 高于 `.grid-map` 的 `z-index: 1`，可作为修复前层级证据。

- [x] **Step 2: 最小化 CSS 层级改动**

将 `.grid-map` 的显式 `z-index: 1` 移除，保持其定位和网格尺寸不变；为 `.cell.robot-cell` 增加高于 `.active-route-layer` 的 `z-index: 3`；为 `.robot-marker` 保留相对定位并设置 `z-index: 1`，确保提示内容同属机器人单元层。

```css
.grid-map {
  position: relative;
  display: grid;
}

.cell.robot-cell {
  z-index: 3;
}

.robot-marker {
  position: relative;
  z-index: 1;
}
```

- [x] **Step 3: 浏览器验证层级**

开始播放并观察带箭头的机器人：圆形机器人标识、其颜色边框和悬停提示均压在箭头上方；普通道路格仍能显示箭头，任务标签格仍不显示箭头。

### Task 3: 固定任务卡展开状态提示

**Files:**
- Modify: `frontend/src/styles.css:888-925`

**Interfaces:**
- Consumes: `TaskQueueItem` 生成的 `<details className="entity">`、`<summary>`、`<strong>` 和 `<span>`。
- Produces: 任务卡右上角固定的 `summary::after` 提示，摘要文本避开该区域。

- [x] **Step 1: 确认当前摘要重叠场景**

Run: 在浏览器中检查未完成和已完成任务卡，特别是时间字段较长时的摘要。

Expected: 现有 `summary::after` 使用 `float: right`，会与同一摘要区域竞争布局空间。

- [x] **Step 2: 最小化 CSS 布局改动**

将 `.entity` 改为定位容器；为 `.entity summary` 增加右侧内边距；将伪元素改为绝对定位并移除浮动：

```css
.entity {
  position: relative;
}

.entity summary {
  padding-right: 52px;
}

.entity summary::after {
  position: absolute;
  top: 10px;
  right: 10px;
  float: none;
}
```

- [x] **Step 3: 浏览器验证任务队列**

确认未完成任务默认展开、已完成任务默认收起；两类任务卡中“展开/收起”均在右上角，任务名称、状态及时间指标不与其重叠。

### Task 4: 执行完整回归

**Files:**
- Verify only: `frontend/src/main.tsx`, `frontend/src/main.test.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: 前三项完成后的前端状态与样式。
- Produces: 已构建且通过测试的前后端项目。

- [x] **Step 1: 运行前端构建**

Run: `npm run frontend:build`

Expected: `vite build` 完成且退出码为 0。

- [x] **Step 2: 运行完整检查**

Run: `npm run check`

Expected: 前端构建、前端 Vitest 和后端 pytest 全部通过。

- [x] **Step 3: 记录验证结果**

在最终说明中列出实际执行的构建、测试和浏览器交互验证；当前目录不是可用 Git 工作树时，不执行提交或回退操作。
