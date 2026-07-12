# 任务作业时间 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-task service time so a robot remains busy at a task endpoint before the task completes or another task begins.

**Architecture:** `Task.serviceTime` is an optional non-negative tick count with a zero default. Backend planning appends endpoint waits and derives completion from those waits; the session and frontend continue consuming the same completion time. The manual form and random generator supply the field through the existing task insertion path.

**Tech Stack:** FastAPI/Pydantic, Python pytest, React/TypeScript/Vite, Vitest.

## Global Constraints

- Preserve the online-session API and all existing conflict lifecycle behavior.
- Missing `serviceTime` means `0`.
- Use UTF-8 and focused regression tests before production changes.

---

### Task 1: Backend task timing

**Files:**
- Modify: `backend/tests/test_algorithm.py`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/dispatch.py`
- Modify: `backend/app/sessions.py`

- [x] Add failing regressions for endpoint wait, delayed next task, and online task state during service.
- [x] Run focused pytest and verify failures are caused by missing service-time behavior.
- [x] Add `Task.serviceTime`, centralized zero-default lookup, endpoint waits, and service-aware completion tracking.
- [x] Run focused pytest and verify the regressions pass.

### Task 2: Frontend task input and presentation

**Files:**
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/domain/types.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `backend/tests/test_api_contract.py`

- [x] Add failing tests for manual/random task `serviceTime` and task-queue display.
- [x] Run focused Vitest and verify failures are caused by the absent field.
- [x] Add the typed field, manual input, deterministic random value, and queue metric row.
- [x] Extend API contract coverage and run focused tests.

### Task 3: Scenario defaults, docs, and verification

**Files:**
- Modify: `frontend/src/domain/scenarios.json`
- Modify: `docs/testing-guide.md`
- Modify: `docs/baseline.md`

- [x] Give integrated-demo tasks explicit representative service durations.
- [x] Document the observable stop-at-target behavior and manual test steps.
- [x] Run `npm run check` and report its result.
