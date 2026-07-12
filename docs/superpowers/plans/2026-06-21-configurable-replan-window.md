# Configurable Replan Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the online assignment rolling window configurable through `DispatchOptions` while preserving the current default behavior.

**Architecture:** Add a camelCase API option named `assignmentReplanWindow` with default value `24`, pass it from `run_dispatch` into assignment task filtering and ordering, and keep existing direct dispatch and session flows using the same options model. Frontend type alignment is maintained through `frontend/src/domain/types.ts` and existing API contract tests.

**Tech Stack:** FastAPI, Pydantic, pytest, React TypeScript type definitions.

---

### Task 1: Backend Behavior Test

**Files:**
- Modify: `backend/tests/test_algorithm.py`

- [ ] **Step 1: Write the failing test**

Add a dispatch regression test that creates one immediate task and one future task with `releaseTime` slightly above a custom short window. Call `run_dispatch` with `DispatchOptions(avoidConflicts=True, includeDynamic=False, assignmentReplanWindow=4)` and assert the future task remains visible in `result.tasks` but is not assigned and does not increment `failureCount`.

- [ ] **Step 2: Run the targeted test**

Run: `.\.venv\Scripts\python.exe -m pytest backend\tests\test_algorithm.py::test_dispatch_uses_configured_assignment_replan_window -q`

Expected before implementation: fail because `DispatchOptions` does not accept `assignmentReplanWindow`.

### Task 2: Backend Implementation

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/dispatch.py`

- [ ] **Step 1: Add schema field**

Add `assignmentReplanWindow: NonNegativeInt = 24` to `DispatchOptions`.

- [ ] **Step 2: Thread the option into rolling-window logic**

Change `split_tasks_for_planning` and `assignment_task_sort_key` to receive an `assignment_replan_window: int` argument and compare `task_release_time(task)` against that value.

- [ ] **Step 3: Pass the option from `run_dispatch`**

Set `assignment_replan_window = options.assignmentReplanWindow` inside `run_dispatch`, pass it to `split_tasks_for_planning`, and pass it to `assign_tasks_beam_search`; update `assign_tasks_beam_search` so it passes the value into sorting.

- [ ] **Step 4: Preserve direct helper defaults**

Give helper functions a default value of `ASSIGNMENT_REPLAN_WINDOW` so existing direct unit tests that call them without options keep current behavior.

### Task 3: Contract And Documentation

**Files:**
- Modify: `frontend/src/domain/types.ts`
- Modify: `docs/algorithm.md`

- [ ] **Step 1: Update frontend type**

Add `assignmentReplanWindow: number;` to `DispatchOptions`.

- [ ] **Step 2: Update algorithm documentation**

Change the rolling-window section to state that the default is `24` and can be overridden with `DispatchOptions.assignmentReplanWindow`.

### Task 4: Verification

**Files:**
- Test: `backend/tests/test_algorithm.py`
- Test: `backend/tests/test_api_contract.py`
- Test: `backend/tests/test_sessions.py`

- [ ] **Step 1: Run targeted backend tests**

Run: `.\.venv\Scripts\python.exe -m pytest backend\tests\test_algorithm.py::test_dispatch_uses_configured_assignment_replan_window backend\tests\test_algorithm.py::test_dispatch_keeps_far_future_tasks_visible_without_current_assignment_or_failure backend\tests\test_api_contract.py::test_frontend_types_match_backend_api_model_fields -q`

Expected: all pass.

- [ ] **Step 2: Verify online session trigger behavior**

Run: `.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py::test_session_uses_configured_assignment_replan_window_for_rolling_trigger -q`

Expected: pass after `backend/app/sessions.py` uses `session.options.assignmentReplanWindow` for rolling-window trigger ticks.

- [ ] **Step 3: Run full checks**

Run: `& 'C:\nvm4w\nodejs\npm.cmd' run check`

Expected: frontend build, frontend tests, and backend tests all pass.

### Self-Review

- Scope is limited to rolling-window configurability.
- No placeholder tasks remain.
- Field names are read from source and new API field is consistently named `assignmentReplanWindow`.
- Git commit steps are intentionally omitted because this workspace reports that it is not a valid Git repository.
