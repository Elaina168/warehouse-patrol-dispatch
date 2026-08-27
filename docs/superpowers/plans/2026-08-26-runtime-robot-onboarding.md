# 运行中新机器人免训练接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在在线调度会话的真实当前时刻事务式接入新机器人，并让前端操作台同步配置、接入时刻和运行状态。

**Architecture:** 后端新增 `AddRobotRequest` 和机器人接入路由；`DispatchSession` 用 `robot_join_times` 区分初始机器人与运行时机器人，并在候选会话副本中推进、校验、初始化、重规划和构建结果后提交。响应中的 `RobotRuntimeState.start/capabilities/joinedAt` 是前端同步完整配置的最小字段，`DispatchResult.pathStartTimes` 让路径从真实接入时刻开始而不伪造 T=0 历史。前端复用 `sessionRequestCoordinator` 和 `updateSession` 的错误/串行语义，在在线任务面板旁提供机器人接入面板和地图选点。

**Tech Stack:** FastAPI、Pydantic、Python pytest、React、TypeScript、Vite、Vitest。

**Spec:** `docs/superpowers/specs/2026-08-26-runtime-robot-onboarding-design.md`

## Global Constraints

- 新机器人使用现有 `Robot` 模型约束、任务能力约束、故障恢复接口和安全门，不新增训练或另一套调度算法。
- `MAX_SCENARIO_ROBOTS=32` 是运行时机器人总数上限；第 33 台必须被拒绝。
- 接入位置只允许地图内、非固定障碍/货架格/当前封锁格/其他机器人占用格的普通可通行格或空闲充电格。
- 运行时机器人的路径和历史从 `joinedAt` 开始；不得把接入位置复制到 T=0 后当作真实历史。
- 验证失败、重规划失败和结果构建失败都不得发布候选会话状态。
- 前端 UI 文案使用中文；历史回放、tick 中、调度未就绪或无会话时禁用接入。
- 不合并、不推送、不发布软件；完成验证后停在汇报检查点。

---

### Task 1: 后端契约和事务回归测试（RED）

**Files:**
- Modify: `backend/tests/test_api_contract.py`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/tests/test_session_concurrency.py`
- Modify: `backend/tests/test_e2e_demo.py`

**Interfaces:**
- Consumes: current `SessionResult`, `RobotRuntimeState`, `DispatchResult`, `SessionRegistry` and integrated-demo helpers.
- Produces: failing tests that define `AddRobotRequest`, `/api/sessions/{session_id}/robots`, `start/capabilities/joinedAt`, `pathStartTimes`, transactional rollback, and fixed-demo behavior.

- [ ] **Step 1: Add API contract assertions.**

  Register `AddRobotRequest` in the backend/frontend model pairs and assert the robot route uses `AddRobotRequest` and returns `SessionResult`. Assert the new runtime response fields and `DispatchResult.pathStartTimes` exist in both model contracts.

- [ ] **Step 2: Add focused backend behavior tests.**

  Add tests that post a valid robot at T=0 and T>0, omit `currentTime`, reject past time, duplicate ID, the 33rd robot, out-of-bounds/fixed-obstacle/shelf/active-blocked/occupied cells, preserve an authoritative snapshot after failed validation, initialize all runtime robot maps, hide the robot before `joinedAt`, include `joinedAt` in the event, participate in a later task, fail/restore it, and remove it after reset.

- [ ] **Step 3: Add concurrency and integration tests.**

  Submit two same-session requests with the same robot ID through the existing lock test pattern and assert one `200` plus one `409`. Extend the fixed `integrated-demo` online flow to add R5, append an R5-compatible inspection task through `/tasks`, tick until completion, and assert R5 moved with zero active conflicts, failures, deadline misses, and unexpected `safetyIntervention`.

- [ ] **Step 4: Run the new tests before implementation.**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q backend/tests/test_api_contract.py backend/tests/test_sessions.py backend/tests/test_session_concurrency.py backend/tests/test_e2e_demo.py
  ```

  Expected: FAIL because the new request model, route, response fields, state, and UI-facing behavior do not exist yet. Fix only test setup errors; do not implement production behavior in this step.

### Task 2: Backend runtime robot onboarding implementation

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_api_contract.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes: failing tests from Task 1; existing `_clone_session_for_preview`, `_commit_session_candidate`, `_advance_runtime_event`, `_build_result`, `run_dispatch`, and runtime failure/recovery functions.
- Produces: `AddRobotRequest`, `add_robot(session_id, request, registry=None) -> SessionResult`, `POST /api/sessions/{session_id}/robots`, `RobotRuntimeState.start/capabilities/joinedAt`, and `DispatchResult.pathStartTimes`.

- [ ] **Step 1: Add the Pydantic request and response fields.**

  Define `AddRobotRequest` with `robot: Robot` and `currentTime: SessionTimeInt = 0`. Add `start`, `capabilities`, and `joinedAt` to `RobotRuntimeState`, preserving response serialization for every robot. Add defaulted `pathStartTimes: dict[str, SessionTimeInt]` to `DispatchResult` so direct dispatch remains compatible while session results can report real offsets.

- [ ] **Step 2: Add the route and session entry point.**

  Import `AddRobotRequest` and `add_robot` in `backend/app/main.py`, then register `POST /api/sessions/{session_id}/robots` with `response_model=SessionResult` and the same route ordering/error behavior as the existing runtime endpoints.

- [ ] **Step 3: Persist join times and initialize/reset state.**

  Add `robot_join_times: dict[str, int]` to `DispatchSession`; initialize all initial robots to `0` in `create_session` and `_reset_session_runtime`. Build runtime state fields from the authoritative robot definition and join map. Use relative robot history lists for runtime robots and retain the join offset separately.

- [ ] **Step 4: Implement exact candidate validation.**

  Add `_robot_request_time` and `_validate_runtime_robot`. Validate total count against `MAX_SCENARIO_ROBOTS`, ID uniqueness, bounds, fixed obstacles, shelf cells, currently active dynamic/runtime blocked cells, and current robot positions. Do not reject charging cells; allow service cells when they are otherwise free. Preserve Pydantic validation errors for battery, capability, name, ID, and move tick constraints.

- [ ] **Step 5: Implement candidate-only mutation and publish.**

  Under `_locked_session`, clone the session, attach the observer to a local observation list, advance only the candidate to the requested time, validate and append the robot, initialize position/history/distance/battery/join time, clear safety stall, invalidate the old plan, touch updated time, and record `T=<time> 新机器人接入：...`. Build the candidate result, then call `_commit_session_candidate`; return only after commit. Leave the authoritative session untouched for all validation/replan/build failures.

- [ ] **Step 6: Make path and metrics time-aware.**

  Update `_restore_absolute_result` to keep runtime paths sliced from `joinedAt` and populate `pathStartTimes`. Add internal session-time path helpers for energy checks, execution application, completion/progress, lock release, robot state lookup, and conflict-state construction. Update reset and result construction so pre-join paths/events/metrics are not exposed as runtime history and post-join distance starts at zero.

- [ ] **Step 7: Run backend tests and refactor only after green.**

  Run the focused command from Task 1. Expected: all onboarding, contract, concurrency, safety and integrated-demo tests pass. Then run the full backend suite and fix regressions without weakening existing task, inventory, failure, recovery, conflict, or safety semantics.

### Task 3: Frontend API and pure behavior tests (RED)

**Files:**
- Modify: `frontend/src/domain/sessionApi.ts`
- Modify: `frontend/src/domain/sessionApi.test.ts`
- Modify: `frontend/src/domain/sessionRequestState.ts`
- Modify: `frontend/src/domain/sessionRequestState.test.ts`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Consumes: `AddRobotRequest`, `SessionResult`, current mutation coordinator and map-pick patterns.
- Produces: `addRobot(apiBase, sessionId, request, fetcher) -> Promise<SessionResult>`, robot form builders, map-pick helpers, availability predicates, runtime-robot merge helpers, and historical path filtering tests.

- [ ] **Step 1: Add failing API-helper tests.**

  Assert `addRobot` posts to the encoded session URL with method `POST`, JSON content type, and the exact `{ robot, currentTime }` body when supplied; assert backend `detail` text is preserved on a failed response.

- [ ] **Step 2: Add failing pure frontend behavior tests.**

  Define tests for required ID/name/coordinate/capability validation, numeric normalization, map selection writing the coordinate, disabled onboarding when no session/not ready/tick in flight/history, success payload replacement including the new robot, retained session on 4xx, pre-join map hiding based on `pathStartTimes`, and reset restoring the original robot set.

- [ ] **Step 3: Run Vitest before implementation.**

  Run:

  ```powershell
  & 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- sessionApi sessionRequestState main
  ```

  Expected: FAIL because the API helper, form helpers, runtime robot merge, path offset handling, and panel behavior do not exist yet.

### Task 4: Frontend robot onboarding panel implementation

**Files:**
- Modify: `frontend/src/domain/types.ts`
- Modify: `frontend/src/domain/sessionApi.ts`
- Modify: `frontend/src/domain/sessionRequestState.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/domain/sessionApi.test.ts`

**Interfaces:**
- Consumes: backend response/request fields from Task 2 and failing frontend tests from Task 3.
- Produces: a visible Chinese “机器人接入” panel in the main operational dashboard, map coordinate picking, exact API submission, current robot synchronization, and disabled/error behavior consistent with existing online mutations.

- [ ] **Step 1: Align TypeScript types and API helper.**

  Add `start?`, `capabilities?`, and `joinedAt?` to `RobotRuntimeState`, add optional `pathStartTimes` to `DispatchResult`, add `AddRobotRequest`, and implement `addRobot` using the same error helper and URL encoding as other session helpers.

- [ ] **Step 2: Add pure robot form functions.**

  Introduce a typed `RuntimeRobotForm`, a default form, coordinate parsing through the existing map dimensions, at-least-one capability validation, numeric clamping to the existing model limits, exact `Robot` construction, and a helper that applies a picked cell to the form.

- [ ] **Step 3: Wire the panel to the existing mutation coordinator.**

  Add panel state and a submit handler that first pauses playback, checks `onlineMutationEnabled`, calls `addRobot` through `sessionRequestCoordinator.enqueue`, applies the returned `SessionResult`, and uses the existing failure classifier so a 4xx keeps the session and displays backend detail. Do not restart playback after success or failure.

- [ ] **Step 4: Synchronize the current robot set.**

  Merge runtime robot states into the map scenario for display and task candidate capability filtering. Use `joinedAt` and `pathStartTimes` when selecting historical path cells and route arrows, so a runtime robot is absent before its join time and visible from its real path start. Reset panel state and merged robots from the reset response.

- [ ] **Step 5: Add panel and map styles.**

  Use the existing dense dashboard panel/form primitives, add a map-pick button and capability checkbox row, and provide disabled/error/selected styles without adding a separate demo entry or acceptance checklist.

- [ ] **Step 6: Run frontend tests/build.**

  Run the focused Vitest command from Task 3, then `& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build`. Expected: all tests pass and the production build emits no TypeScript errors.

### Task 5: Documentation and manual acceptance instructions

**Files:**
- Modify: `README.md`
- Modify: `docs/algorithm.md`
- Modify: `docs/demo.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Consumes: verified endpoint behavior, response fields, path offset semantics, and fixed-demo regression from Tasks 1–4.
- Produces: concise operation, algorithm-boundary, and manual-acceptance instructions for runtime robot onboarding.

- [ ] **Step 1: Document the backend endpoint and state semantics.**

  Explain the request fields, validation failures, candidate transaction, `joinedAt`, `pathStartTimes`, current complete robot state, failure/recovery reuse, and reset removal. State that the feature does not imply arbitrary-input zero-conflict or full MAPF/CBS.

- [ ] **Step 2: Document the dashboard flow.**

  Add manual steps to pause, choose a free map cell, fill the robot form, submit, verify the event and map, append a task, observe movement, and reset. Include the disabled conditions, error-retention behavior, and pre-join history check.

- [ ] **Step 3: Run documentation encoding and diff checks.**

  Run `git diff --check` and inspect all changed Chinese files as UTF-8 before the full verification command.

### Task 6: Full verification and pause checkpoint

**Files:**
- Read: all changed source/tests/docs files

**Interfaces:**
- Consumes: completed implementation and documentation from Tasks 1–5.
- Produces: exact current verification counts and a bounded handoff; no merge, push, publication, or follow-up feature work.

- [ ] **Step 1: Run the complete repository check.**

  Run:

  ```powershell
  & 'C:\nvm4w\nodejs\npm.cmd' run check
  ```

  Record the exact frontend build result, frontend test count, and backend test count, including any skips or warnings.

- [ ] **Step 2: Recheck feature-specific evidence.**

  Confirm the focused backend integration flow, API contract, concurrency behavior, frontend form/request tests, pre-join rendering test, and reset test are included in the full run. If a test fails, use the systematic-debugging skill and return to a failing regression before changing production code.

- [ ] **Step 3: Report and stop.**

  Report changed files, exact test results, supported semantics, known boundary limitations, and that the worktree remains unmerged/unpushed. Do not begin unrelated polish, submission material, deployment, or external coordination.
