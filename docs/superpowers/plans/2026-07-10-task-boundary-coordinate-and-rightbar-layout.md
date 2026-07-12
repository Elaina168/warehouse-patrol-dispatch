# 同坐标任务边界、坐标输入与右侧布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复同一机器人连续同坐标任务被同时完成的问题，简化坐标输入，并将事件日志和任务队列改为最右侧纵向 1:2 布局。

**Architecture:** 以任务边界作为唯一完成语义：同一机器人下一任务的目标搜索必须晚于前一任务完成 tick；若下一任务首目标就是当前位置，路径规划补入一个停留 tick。前端将坐标输入保存为文本并在请求前解析为现有 `Cell` 结构；地图侧栏放置指标和重规划解释，最右侧栏以事件日志和任务队列的 1:2 高度比例展示。

**Tech Stack:** FastAPI, Pydantic, Python 3.13, React 19, TypeScript, Vite, Vitest, pytest, CSS.

## Global Constraints

- 不改变地图画布尺寸、会话 API 请求字段或后端 `Cell` 坐标格式。
- 只阻止同一机器人复用前一任务的完成 tick；不同机器人仍可并行完成任务。
- 坐标格式固定为 `x,y`，允许逗号两侧空格；非整数和地图外坐标必须在前端阻止提交。
- 地图侧栏顺序为指标、重规划解释；最右侧栏顶部事件日志约占三分之一、底部任务队列约占三分之二。
- 当前目录不是 Git 工作树，不执行提交、回退或分支操作。

---

### Task 1: 建立同坐标连续任务的时间边界

**Files:**
- Modify: `backend/app/dispatch.py:450-482,827-846`
- Modify: `backend/app/sessions.py:929-1049`
- Test: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: `plan_robot_path(...)`、`task_completion_times(...)`、`_session_task_completion_times(...)`、`_update_task_waypoint_progress(...)` 和 `_task_execution_window(...)`。
- Produces: 同一机器人后续任务的完成查找从前一任务 completion tick 的下一 tick 开始；`TaskRuntimeState.completionTime` 对连续同目标任务严格递增。

- [x] **Step 1: 写入连续同坐标任务的失败回归**

在 `backend/tests/test_sessions.py` 新增一个单机器人直线场景，依次通过 `POST /api/sessions/{session_id}/tasks` 在 T=0 加入 `M1`、`M2`、`M3`，三者都是 inspection、目标 `[2, 0]`。推进到 T=2 后断言只有 `M1` 为 completed；推进到 T=3 后断言只有 `M1`、`M2` 为 completed；推进到 T=4 后断言三者完成，且 `completionTime == [2, 3, 4]`。

```python
def test_session_keeps_same_target_tasks_in_separate_ticks() -> None:
    client = TestClient(app)
    scenario = {
        "id": "same-target-tasks",
        "name": "same-target-tasks",
        "description": "same target completion regression",
        "width": 4,
        "height": 1,
        "obstacles": [],
        "zones": {"warehouse": [[0, 0]], "inspection": [[2, 0]], "delivery": []},
        "robots": [{"id": "R1", "name": "R1", "start": [0, 0], "battery": 90, "load": 1}],
        "tasks": [],
        "dynamic": {"triggerTime": 99, "blockedCells": [], "failedRobots": [], "tasks": []},
    }
    session_id = client.post("/api/sessions", json={"scenario": scenario, "options": {"avoidConflicts": True, "includeDynamic": False}}).json()["sessionId"]
    for task_id in ("M1", "M2", "M3"):
        response = client.post(
            f"/api/sessions/{session_id}/tasks",
            json={"task": {"id": task_id, "type": "inspection", "title": task_id, "priority": 3, "releaseTime": 0, "deadline": 20, "targets": [[2, 0]]}},
        )
        assert response.status_code == 200
    states_at_two = client.post(f"/api/sessions/{session_id}/tick", json={"currentTime": 2}).json()["taskStates"]
    assert {state["taskId"] for state in states_at_two if state["status"] == "completed"} == {"M1"}
```

- [x] **Step 2: 运行回归并确认失败**

Run: `& .\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py -k "same_target_tasks" -q`

Expected: FAIL，T=2 时 `M1`、`M2`、`M3` 会同时被标记为 completed，或 completionTime 重复为 `2`。

- [x] **Step 3: 路径中为零距离任务转换补入停留 tick**

在 `backend/app/dispatch.py` 的 `plan_robot_path(...)` 中改为带索引遍历任务；完成当前任务路径后，若仍有后续任务且后续任务的第一个 waypoint 与当前 `cursor` 相同，则追加 `cursor`，使下一个任务拥有独立的路径时间索引。

```python
for task_index, task in enumerate(tasks):
    # 保留现有 release-time 等待与每个 waypoint 的寻路。
    ...
    next_tasks = tasks[task_index + 1 : task_index + 2]
    next_waypoints = task_waypoints(next_tasks[0]) if next_tasks else []
    if next_waypoints and same_cell(cursor, next_waypoints[0]):
        path.append(cursor)
```

在 `task_completion_times(...)` 中，每个完整任务写入 completion 后将下一个任务的 `cursor_index` 设置为 `completion_index + 1`；同一任务内部多个 waypoint 仍保持原有顺序搜索。

```python
if completion_index is not None:
    completions[task.id] = completion_index
    cursor_index = completion_index + 1
```

- [x] **Step 4: 将相同边界规则应用到会话状态**

在 `backend/app/sessions.py` 中，对 `_session_task_completion_times(...)`、`_update_task_waypoint_progress(...)` 和 `_task_execution_window(...)` 使用相同的后继 cursor 规则：完整任务后返回或保留 `completion_index + 1`；未完成任务仍使用当前 waypoint 的原有 cursor。

```python
if _is_task_fully_completed(task, completed_count) and completion_index is not None:
    session.task_completion_times[task.id] = completion_index
    cursor_index = completion_index + 1
```

```python
return max(start_index, release_time), completion_index + 1 if completion_index is not None else cursor_index
```

- [x] **Step 5: 运行后端回归并确认通过**

Run: `& .\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py -k "same_target_tasks" -q`

Expected: PASS；`M1`、`M2`、`M3` 在 T=2、T=3、T=4 依次完成，完成时间不重复。

### Task 2: 改为单字段坐标输入并在前端校验

**Files:**
- Modify: `frontend/src/main.tsx:28-39,282-294,703-725,882-912,2049-2107`
- Test: `frontend/src/main.test.ts`

**Interfaces:**
- Consumes: `ManualTaskForm`、当前 `Scenario.width` / `Scenario.height`、现有 `buildManualTask(...)`。
- Produces: `parseCoordinateInput(value, scenario): Cell | null`；手动任务提交前产生现有 `Task` 联合类型，非法坐标不发送请求。

- [x] **Step 1: 写入坐标解析失败测试**

在 `frontend/src/main.test.ts` 导入新的 `parseCoordinateInput`，覆盖合法、空格、格式错误、非整数和地图外坐标。

```ts
it("parses one coordinate field only when the pair is inside the map", () => {
  const map = { width: 8, height: 6 };
  expect(parseCoordinateInput("3, 0", map)).toEqual([3, 0]);
  expect(parseCoordinateInput(" 7 , 5 ", map)).toEqual([7, 5]);
  expect(parseCoordinateInput("3 0", map)).toBeNull();
  expect(parseCoordinateInput("3.5,0", map)).toBeNull();
  expect(parseCoordinateInput("8,0", map)).toBeNull();
});
```

- [x] **Step 2: 运行前端测试并确认失败**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL，`parseCoordinateInput` 尚未导出。

- [x] **Step 3: 替换表单状态和坐标控件**

将 `ManualTaskForm` 的 `targetX` / `targetY`、`pickupX` / `pickupY`、`dropoffX` / `dropoffY` 替换为 `target`、`pickup`、`dropoff` 字符串。将 `CoordinateInput` 改为一个文本输入框，使用 `placeholder="例如 3, 0"`。

新增解析函数并在 `buildManualTask(...)` 中使用：

```ts
export function parseCoordinateInput(value: string, scenario: Pick<Scenario, "width" | "height">): Cell | null {
  const match = /^\s*(\d+)\s*,\s*(\d+)\s*$/.exec(value);
  if (!match) return null;
  const cell: Cell = [Number(match[1]), Number(match[2])];
  return cell[0] < scenario.width && cell[1] < scenario.height ? cell : null;
}
```

`submitManualTask(...)` 在解析失败时设置局部表单错误并直接返回；成功时清除错误并调用现有会话 API。`createManualTaskForm(...)` 使用 `${x}, ${y}` 初始化三个字段。

- [x] **Step 4: 运行前端测试并确认通过**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: PASS；合法输入解析为现有 `Cell`，非法输入返回 `null`。

### Task 3: 调整指标、重规划解释、事件日志和任务队列位置

**Files:**
- Modify: `frontend/src/main.tsx:601-659,811-844,850-858`
- Modify: `frontend/src/styles.css:127-138,612-659`
- Test: `frontend/src/main.test.ts`

**Interfaces:**
- Consumes: 现有 `ReplanStatusPanel`、任务分组 `taskGroups`、运行时 `eventLog`。
- Produces: 地图侧栏仅展示指标和重规划解释；最右侧 `.rightbar` 的事件日志和任务队列拥有明确的 `rightbar-event-log` 与 `rightbar-task-queue` class，并按纵向 1:2 布局。

- [ ] **Step 1: 写入结构性布局失败测试**

在 `frontend/src/main.test.ts` 为可导出的布局 class 常量或结构 helper 添加断言，确保事件日志 class 为 `rightbar-event-log`，任务队列 class 为 `rightbar-task-queue`，并在 CSS 中使用这两个 class 的纵向网格规则。

```ts
it("keeps the rightbar split into event log and task queue regions", () => {
  expect(RIGHTBAR_EVENT_LOG_CLASS).toBe("rightbar-event-log");
  expect(RIGHTBAR_TASK_QUEUE_CLASS).toBe("rightbar-task-queue");
});
```

- [ ] **Step 2: 运行前端测试并确认失败**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: FAIL，右侧栏 class 常量尚未导出。

- [ ] **Step 3: 移动模块并定义纵向比例**

将 `ReplanStatusPanel` 从页面最右侧移至 `.map-side` 的指标区之后。将任务队列 JSX 从 `.map-side` 移到 `.rightbar`，事件日志保留在 `.rightbar` 顶部。`Panel` 新增可选 `className`，以便右侧两个区域拥有稳定 class。

```tsx
<aside className="rightbar">
  <Panel title="事件日志" className={RIGHTBAR_EVENT_LOG_CLASS}>...</Panel>
  <Panel title="任务队列" className={RIGHTBAR_TASK_QUEUE_CLASS}>...</Panel>
</aside>
```

在 CSS 中让 `.rightbar` 使用两行纵向网格，并让任务队列面板内部列表填充可用高度：

```css
.rightbar {
  grid-template-rows: minmax(0, 1fr) minmax(0, 2fr);
  min-height: 720px;
}

.rightbar-task-queue .compact-list {
  max-height: none;
}
```

保留移动端现有的单列断点规则，并让 `.rightbar` 在该断点取消最小高度和比例行。

- [ ] **Step 4: 运行前端测试并确认通过**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts`

Expected: PASS；右侧栏结构标识存在，坐标与现有视图 helper 测试均未回归。

### Task 4: 全量验证与浏览器检查

**Files:**
- Verify only: `backend/app/dispatch.py`, `backend/app/sessions.py`, `backend/tests/test_sessions.py`, `frontend/src/main.tsx`, `frontend/src/main.test.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: 完成后的任务边界、坐标解析和右侧布局。
- Produces: 可重复验证的任务完成状态、坐标表单和布局行为。

- [ ] **Step 1: 运行后端测试套件**

Run: `& .\.venv\Scripts\python.exe -m pytest backend/tests -q`

Expected: PASS；新增同坐标回归与全部既有后端测试通过。

- [ ] **Step 2: 运行项目完整检查**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' run check`

Expected: 前端构建、前端测试和后端测试全部通过；计数以命令实际输出为准。

- [ ] **Step 3: 浏览器验证**

打开 `http://127.0.0.1:5174/`，连续加入三个相同目标任务，推进仿真确认它们按不同 tick 转入已完成；输入 `3, 0` 能加入任务，`3 0` 和地图外坐标被前端拒绝；地图侧栏中重规划解释紧跟指标，最右侧栏上方为事件日志、下方为任务队列。

- [ ] **Step 4: 记录结果**

最终报告列出新增任务边界回归、坐标格式、右侧 1:2 布局，以及完整检查的实际结果。当前目录不是可用 Git 工作树时，不执行提交或回退操作。
