# 运行事件真实性与故障机器人占位 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让在线日志只显示已发生事件，并使故障机器人位置在故障后参与后续避碰重规划。

**Architecture:** 直接规划日志不再生成未来成功预测；在线会话仅汇总会话事件并在实际完成时补充完成记录。路径规划开始前预留本次重规划中已不可用机器人的当前起点，避免可用机器人穿越故障位置。前端以播放已启动和当前回放时间双重过滤事件，并压缩实时指标区以扩大任务队列。

**Tech Stack:** FastAPI, Pydantic, Python 3.13, React 19, TypeScript, Vite, Vitest, pytest, CSS。

## Global Constraints

- 不改变场景动态事件的触发时间、任务分配接口、路径坐标系或固定地图画布尺寸。
- 故障位置只在故障发生后被写入当前及后续重规划的保留；恢复机器人后自动移除。
- 在线事件日志是实际运行事件的唯一展示源；播放开始前前端不展示日志。
- 未检测到冲突和预测按时完成不生成日志。

---

### Task 1: 清理规划预测日志并建立会话实际事件流

**Files:**
- Modify: `backend/app/dispatch.py:1217-1262`
- Modify: `backend/app/sessions.py:479-490,541-593`
- Test: `backend/tests/test_algorithm.py:15-45`
- Test: `backend/tests/test_sessions.py:613-650`

**Interfaces:**
- Consumes: `build_event_log(...)` 的直接规划输入、`DispatchSession.event_notes` 和任务完成时间。
- Produces: 直接规划日志只包含 `time == 0` 的配置/失败/冲突检测；在线会话 `eventLog` 只包含实际 `event_notes`、保留的动态/滚动窗口事件和当前检测到的冲突。

- [x] **Step 1: 写入预测日志与实际完成的失败测试**

在 `backend/tests/test_algorithm.py` 的直接调度 API 测试中加入：

```python
assert all(event["time"] == 0 for event in payload["eventLog"])
assert not any("未检测到时空路径冲突" in event["text"] for event in payload["eventLog"])
assert not any("均按时完成" in event["text"] for event in payload["eventLog"])
```

在 `backend/tests/test_sessions.py` 新增会话测试：创建会话后断言 `eventLog == []`；推进到任务开始和完成后，断言日志含有对应 `锁定给机器人` 和 `任务 <id> 已完成`，且所有日志时间不晚于返回的 `currentTime`。

- [x] **Step 2: 运行定向后端测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py backend/tests/test_sessions.py -k "event_log or demo_online_dispatch_flow" -q`

Expected: FAIL，当前直接规划日志仍包含 `time=1/2` 预测消息，会话创建仍混入规划日志，且没有实际任务完成事件。

- [x] **Step 3: 最小化实现实际事件语义**

在 `backend/app/dispatch.py` 中将 `build_event_log` 收敛为时间 `0` 的当前规划配置、失败原因和已检测冲突；移除任务分配完成、未来动态触发、动态封锁/故障、无冲突、截止成功/预测超期等未来时间事件。

在 `backend/app/sessions.py` 中：

```python
def _build_session_event_log(session: DispatchSession, result: DispatchResult) -> list[EventItem]:
    events = list(session.event_notes)
    if result.conflicts:
        _append_event_if_missing(
            events,
            EventItem(time=session.current_time, text=f"T={session.current_time} 检测到 {len(result.conflicts)} 次路径冲突"),
        )
    events = _preserve_rolling_window_event_history(session, events)
    events = _preserve_triggered_dynamic_event_history(session, events)
    return sorted(events, key=lambda item: item.time)
```

在 `_advance_session` 中，仅对尚未完成且 `completion_time <= target_time` 的任务写入：

```python
_record_session_event(session, completion_time, f"T={completion_time} 任务 {task_id} 已完成")
```

并在 `_build_result` 中传入完整 `result` 而非仅传入 `result.eventLog`。

- [x] **Step 4: 运行定向后端测试并确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py backend/tests/test_sessions.py -k "event_log or demo_online_dispatch_flow" -q`

Expected: PASS，预测成功消息不再出现；会话日志按实际事件时间增长。

### Task 2: 在故障发生后保留实际故障位置

**Files:**
- Modify: `backend/app/dispatch.py:620-700`
- Test: `backend/tests/test_sessions.py:after test_session_real_campus_flow_stays_consistent_through_dynamic_and_manual_task`

**Interfaces:**
- Consumes: `build_paths_for_order(..., unavailable_robot_ids, ...)` 中已由会话更新为实际位置的 `robots`。
- Produces: 启用避碰时，不可用机器人 `robot.start` 在整个规划期保留；可用机器人的路径不能进入该位置。

- [x] **Step 1: 写入综合场景故障占位失败测试**

在 `backend/tests/test_sessions.py` 新增：创建 `integrated-demo` 会话并逐 tick 推进到 `T=32`；确认 `R3` 不可用且位置为 `[4, 4]`；添加以下任务并推入自动任务流：

```python
{
    "id": "M1",
    "type": "inspection",
    "title": "人工追加任务",
    "priority": 3,
    "releaseTime": 32,
    "deadline": 56,
    "targets": [[3, 0]],
}
```

断言 `metrics.conflictCount == 0`、`conflicts == []`，并断言 `R4` 在索引 `33:` 的路径不包含 `[4, 4]`。

- [x] **Step 2: 运行该回归并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py -k "integrated_demo_failure_position" -q`

Expected: FAIL，当前 R4 路径在 `T=42` 进入 `[4, 4]`，并产生顶点冲突。

- [x] **Step 3: 在路径规划前预留不可用机器人的当前起点**

在 `build_paths_for_order` 创建 `Reservations()` 后、遍历 `planning_order` 前加入：

```python
if avoid_conflicts:
    horizon_padding = max(12, scenario.width * scenario.height * 4)
    for robot in robots:
        if robot.id in unavailable_robot_ids:
            reserve_path([robot.start], reservations, horizon_padding=horizon_padding)
```

保留不可用机器人在循环中生成 `[robot.start]` 路径的现有行为，但不重复保留。会话在故障事件发生后才将机器人列入 `unavailable_robot_ids`，因此故障前不受影响；恢复后该 ID 不再传入，保留自动消失。

- [x] **Step 4: 运行该回归并确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py -k "integrated_demo_failure_position" -q`

Expected: PASS，R4 绕开 `[4, 4]`，冲突数为 0。

### Task 3: 前端仅展示已发生事件并扩大任务队列

**Files:**
- Modify: `frontend/src/main.tsx:807-845,1910-1940`
- Modify: `frontend/src/styles.css:281-305,617-626`
- Test: `frontend/src/main.test.ts:274-281`

**Interfaces:**
- Consumes: `DispatchResult["eventLog"]`、`routeHintsEnabled`、当前 `time`。
- Produces: `visibleRuntimeEvents(events, routeHintsEnabled, time)`，播放前返回空数组，播放后只返回 `event.time <= time` 的事件。

- [x] **Step 1: 写入前端事件可见性失败测试**

在 `frontend/src/main.test.ts` 导入 `visibleRuntimeEvents` 并加入：

```ts
const events = [
  { time: 0, text: "启动" },
  { time: 2, text: "实际锁定" }
];
expect(visibleRuntimeEvents(events, false, 0)).toEqual([]);
expect(visibleRuntimeEvents(events, true, 1)).toEqual([{ time: 0, text: "启动" }]);
expect(visibleRuntimeEvents(events, true, 2)).toEqual(events);
```

- [x] **Step 2: 运行定向前端测试并确认失败**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: FAIL，提示 `visibleRuntimeEvents` 尚未导出。

- [x] **Step 3: 实现事件过滤与侧栏空间调整**

在 `main.tsx` 中导出：

```ts
export function visibleRuntimeEvents<T extends { time: number }>(events: T[], started: boolean, time: number): T[] {
  if (!started) return [];
  return events.filter((event) => event.time <= time);
}
```

事件日志改为遍历 `visibleRuntimeEvents(result.eventLog, routeHintsEnabled, time)`。实时指标网格改为 `className="metrics-grid map-metrics-grid"`。

在 `styles.css` 中新增：

```css
.map-metrics-grid {
  gap: 6px;
}

.map-metrics-grid .metric {
  min-height: 46px;
  padding: 7px 8px;
}

.map-metrics-grid .metric strong {
  margin-top: 3px;
  font-size: 14px;
}

.map-side .compact-list {
  max-height: 420px;
}
```

保留移动端 `.task-queue-columns` 的单列规则和固定地图尺寸。

- [x] **Step 4: 运行定向前端测试并确认通过**

Run: `npm --prefix frontend run test -- main.test.ts`

Expected: PASS，播放前无日志，播放后只显示已发生事件。

### Task 4: 执行完整回归与浏览器验证

**Files:**
- Verify only: `backend/app/dispatch.py`, `backend/app/sessions.py`, `backend/tests/test_algorithm.py`, `backend/tests/test_sessions.py`, `frontend/src/main.tsx`, `frontend/src/main.test.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: 前三项完成后的事件、路径和前端过滤逻辑。
- Produces: 无未来预测、故障位置安全和可验证侧栏布局的在线演示。

- [x] **Step 1: 运行前端构建**

Run: `npm run frontend:build`

Expected: `tsc -b && vite build` 退出码为 0。

- [x] **Step 2: 运行完整检查**

Run: `npm run check`

Expected: 前端构建、前端测试和后端测试全部通过；计数以实际输出为准。

- [x] **Step 3: 浏览器验证**

打开 `http://127.0.0.1:5174/`：播放前事件日志为空；播放后日志按当前时间逐步出现；指标卡更紧凑且任务队列可显示更多条目。推进至动态故障后，新增巡检和自动任务流时，地图与指标不出现路径冲突。

- [x] **Step 4: 记录验证结果**

最终报告列出预测日志移除、故障位置回归、构建、完整测试和浏览器验证的实际结果。当前目录不是可用 Git 工作树时，不执行提交或回退操作。
