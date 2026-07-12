# Task Flow Static Start and Random Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old independent automatic stream-task feature with one unified task-queue insertion flow, keep sessions idle before simulation starts, and add a frontend random task generator that uses the same insertion flow as manual tasks.

**Architecture:** Backend sessions gain an explicit planning-started state so creation and pre-play task insertion can return an idle result without assignments. The old `/api/sessions/{session_id}/stream-task` product endpoint is removed; all inserted tasks go through `/api/sessions/{session_id}/tasks`. The frontend removes the old stream button and auto stream switch, renames manual insertion to “推入任务队列”, and adds a random generator that builds normal `Task` objects and submits them through the same form insertion path.

**Tech Stack:** FastAPI, Pydantic, pytest, React, TypeScript, Vite/Vitest.

## Global Constraints

- Only first-phase scope is implemented: task-flow merge, static pre-start session state, random task generator.
- Do not implement robot dragging, robot context menu failure, conflict separation, task service time, robot speed, battery charging, new warehouse map, priority migration, or playback slider restoration.
- Random ordinary tasks use priority `-1` in the design, but current backend schema only accepts non-negative `priority`; first implementation must keep generated ordinary tasks at `0` unless schema migration is explicitly approved later.
- Random urgent tasks use positive priority from `1` to `5`; larger number is higher priority.
- Random generated tasks must call the same `/api/sessions/{sessionId}/tasks` endpoint as manually entered tasks.
- Old `/api/sessions/{sessionId}/stream-task` must not remain as a product endpoint.

---

### Task 1: Backend Static Start and Stream Endpoint Removal

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/sessions.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/experiments.py`
- Test: `backend/tests/test_sessions.py`
- Test: `backend/tests/test_api_contract.py`

**Interfaces:**
- Consumes: existing `create_session`, `tick_session`, `add_task`, `DispatchResult`, `Metrics`, and `TaskRuntimeState`.
- Produces: `DispatchSession.planning_started: bool`, `_ensure_planning_started(session)`, `_build_idle_result(session)`, no public `/stream-task` route.

- [ ] **Step 1: Write failing backend tests**

Add tests proving session creation is idle, manual task insertion before play stays pending, first tick starts planning, and `/stream-task` is gone.

- [ ] **Step 2: Run focused backend tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py::test_session_create_keeps_tasks_waiting_until_first_tick backend/tests/test_sessions.py::test_session_add_task_before_play_stays_pending_until_tick backend/tests/test_sessions.py::test_session_stream_task_endpoint_is_removed -q`

Expected: FAIL because existing create-session returns running assignments and `/stream-task` still exists.

- [ ] **Step 3: Implement backend idle session state**

Add `planning_started` to `DispatchSession`. `create_session`, `reset_session`, and pre-play `add_task` should return `_build_idle_result` through `_build_result` while `planning_started` is false. `tick_session` sets `planning_started` before advancing to a future time. Manual task insertion sets planning started only when the session has already started.

- [ ] **Step 4: Remove old stream-task product route**

Remove `StreamTaskRequest`, `add_stream_task`, `_make_stream_task`, `_next_stream_task_sequence`, route imports, and OpenAPI contract expectations for `/api/sessions/{session_id}/stream-task`. Update `experiments.py` to insert its generated runtime task through `add_task` with a normal `Task`.

- [ ] **Step 5: Run focused backend tests and contract tests**

Run: `.\.venv\Scripts\python.exe -m pytest backend/tests/test_sessions.py backend/tests/test_api_contract.py -q`

Expected: PASS.

### Task 2: Frontend Unified Task Queue and Random Generator

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/domain/types.ts`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/domain/sessionApi.test.ts`

**Interfaces:**
- Consumes: `buildManualTask(form, tasks, currentTime, scenario)`, `submitManualTask`, `updateSession`, `Task`, `Scenario`, `Cell`.
- Produces: `RandomTaskGeneratorForm`, `buildRandomGeneratedTask(...)`, `shouldGenerateRandomTaskAtTime(...)`, frontend UI for random task generation that posts to `/tasks`.

- [ ] **Step 1: Write failing frontend tests**

Add tests proving the manual button copy is “推入任务队列”, random generation uses the same task insertion endpoint, and old `stream-task` request types/tests are gone.

- [ ] **Step 2: Run focused frontend tests and verify failure**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main`

Expected: FAIL because UI still renders “推入自动任务流” and uses `/stream-task`.

- [ ] **Step 3: Replace old frontend stream state**

Remove `autoStream`, `lastAutoStreamTime`, `pushStreamTask`, `shouldPushAutoStreamAtTime`, and the old button. Add random generator state: enabled flag, interval input, seed input, last generated time, and sequence.

- [ ] **Step 4: Implement random task construction**

Create helper functions in `main.tsx` using existing `Task` and `Scenario` types. Generated tasks get IDs like `G1`, `G2`, `G3`, avoid existing task IDs, set `releaseTime` to current session time, use valid scenario cells, and submit through `/api/sessions/{sessionId}/tasks`.

- [ ] **Step 5: Update UI copy and controls**

Rename form submit button to “推入任务队列”. Add a compact random generator block in the online task panel or simulation panel with enabled checkbox, interval input, seed input, and current status.

- [ ] **Step 6: Run focused frontend tests**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main sessionApi`

Expected: PASS.

### Task 3: Full Verification

**Files:**
- No new production files.

**Interfaces:**
- Consumes: completed Task 1 and Task 2.
- Produces: verified application behavior.

- [ ] **Step 1: Run full check**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' run check`

Expected: frontend build/tests and backend tests pass.

- [ ] **Step 2: Browser smoke test**

Open `http://127.0.0.1:5174/`, confirm no “推入自动任务流” text, initial task queue has no running task, manual push updates queue/log, random generator adds tasks while playing.

