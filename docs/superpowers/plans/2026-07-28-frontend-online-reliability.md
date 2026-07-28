# Frontend Online Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make online mutations race-safe, distinguish business errors from connectivity failures, provide honest path-only history playback, clean stale created sessions, and align scenario import validation with the backend.

**Architecture:** Extract deterministic state decisions and scenario-import validation from the large React entry file into focused domain helpers. Keep the existing request coordinator, API routes, and dashboard layout; add one compact operational error/notice area rather than a new panel. Historical mode intentionally hides state that the backend cannot reconstruct for the selected tick.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, FastAPI contract tests.

## Global Constraints

- Do not add a showcase or experiment panel.
- Do not add backend history snapshot fields.
- Runtime mutations are disabled while a tick is in flight or while viewing past time.
- 4xx means the backend is reachable; network/5xx means operational failure.
- Mutation 4xx does not stop playback; tick 4xx stops playback.
- Historical mode shows paths, events, and metrics only.
- Stale successful create responses must delete their server session.
- The backend remains the final scenario-validation authority.
- Follow RED → GREEN for every production behavior.

---

### Task 1: Preserve HTTP status and classify request failures

**Files:**
- Modify: `frontend/src/domain/apiError.ts`
- Modify: `frontend/src/domain/apiError.test.ts`
- Create: `frontend/src/domain/sessionRequestState.ts`
- Create: `frontend/src/domain/sessionRequestState.test.ts`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Produces: `ApiRequestError extends Error`
- Produces: `classifySessionRequestFailure(error, operation) -> SessionFailureDecision`
- Consumes: `apiErrorFromResponse(response, prefix)`

- [ ] **Step 1: Add typed API-error tests**

Assert:

```typescript
const error = await apiErrorFromResponse(response422, "session update failed");
expect(error).toBeInstanceOf(ApiRequestError);
expect(error.status).toBe(422);
expect(error.message).toContain("任务数已达上限");
```

Keep existing detail formatting assertions.

- [ ] **Step 2: Add failure-decision tests**

Create exact table tests:

```typescript
expect(classifySessionRequestFailure(new ApiRequestError(409, "conflict"), "mutation"))
  .toEqual({
    apiStatus: "online",
    dispatchStatus: "ready",
    pausePlayback: false
  });

expect(classifySessionRequestFailure(new ApiRequestError(422, "past"), "tick"))
  .toEqual({
    apiStatus: "online",
    dispatchStatus: "ready",
    pausePlayback: true
  });

expect(classifySessionRequestFailure(new ApiRequestError(503, "down"), "mutation"))
  .toEqual({
    apiStatus: "error",
    dispatchStatus: "error",
    pausePlayback: true
  });

expect(classifySessionRequestFailure(new TypeError("fetch failed"), "mutation"))
  .toEqual({
    apiStatus: "offline",
    dispatchStatus: "error",
    pausePlayback: true
  });
```

- [ ] **Step 3: Run RED**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- apiError.test.ts sessionRequestState.test.ts
```

Expected: typed error and classifier do not exist.

- [ ] **Step 4: Implement the typed error**

```typescript
export class ApiRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}
```

Return this class from `apiErrorFromResponse`.

- [ ] **Step 5: Implement the pure classifier**

Define exact union types:

```typescript
export type SessionOperation = "tick" | "mutation";
export type ApiStatus = "checking" | "online" | "offline" | "error";
export type DispatchStatus = "loading" | "ready" | "error";
```

Classify 400–499 as reachable, 500–599 as backend error, and non-HTTP errors as offline.

- [ ] **Step 6: Wire visible operational errors**

In `App` add `operationError: string | null`. In tick/update catch blocks:

1. classify the error;
2. set API and dispatch status from the decision;
3. pause only when requested;
4. set `operationError`.

Render the exact message under the online control status whenever non-null. Clear it on successful payload application, reset, scenario change, or a new request start.

- [ ] **Step 7: Run GREEN and frontend suite**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

Expected: all tests and build pass.

- [ ] **Step 8: Commit**

```powershell
git add frontend/src/domain/apiError.ts frontend/src/domain/apiError.test.ts frontend/src/domain/sessionRequestState.ts frontend/src/domain/sessionRequestState.test.ts frontend/src/main.tsx
git commit -m "fix: distinguish API business errors"
```

---

### Task 2: Gate every runtime mutation during tick and history playback

**Files:**
- Modify: `frontend/src/domain/sessionRequestState.ts`
- Modify: `frontend/src/domain/sessionRequestState.test.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Produces: `canMutateOnlineSession(input: MutationAvailabilityInput) -> boolean`
- Produces: `isHistoricalPlayback(displayTime, sessionCurrentTime) -> boolean`

- [ ] **Step 1: Add availability matrix tests**

Cover:

```typescript
expect(canMutateOnlineSession({
  hasSession: true,
  dispatchStatus: "ready",
  tickInFlight: false,
  displayTime: 5,
  sessionCurrentTime: 5
})).toBe(true);
```

Return false independently for:

- no session;
- `dispatchStatus !== "ready"`;
- `tickInFlight=true`;
- `displayTime < sessionCurrentTime`.

- [ ] **Step 2: Add handler-level no-request tests**

Extract or reuse a pure guard so tests prove a blocked operation never calls its request closure:

```typescript
let calls = 0;
await runOnlineMutation(false, async () => { calls += 1; });
expect(calls).toBe(0);
```

- [ ] **Step 3: Run RED**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- sessionRequestState.test.ts main.test.ts
```

Expected: availability helper and handler guard are absent.

- [ ] **Step 4: Implement one authoritative availability value**

In `App` calculate:

```typescript
const onlineMutationEnabled = canMutateOnlineSession({
  hasSession: session !== null,
  dispatchStatus,
  tickInFlight,
  displayTime: time,
  sessionCurrentTime: session?.currentTime ?? null
});
```

Use it for:

- `MapBoard.canManageBlocks`;
- task recovery button `disabled`;
- manual task submit button;
- robot failure/restore controls;
- automatic task generation effect.

- [ ] **Step 5: Guard event handlers**

At the start of block, unblock, fail, restore, recover and task-submit handlers:

```typescript
if (!onlineMutationEnabled) return;
```

Do not remove `currentTime` from block/fail/restore payloads.

- [ ] **Step 6: Keep playback generation stable**

Add `tickInFlight` and `onlineMutationEnabled` to the automatic generator conditions and dependencies. A generator tick already queued before `tickInFlight` becomes true remains serialized by the coordinator; no new generator operation starts during the tick.

- [ ] **Step 7: Run GREEN and build**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

- [ ] **Step 8: Commit**

```powershell
git add frontend/src/domain/sessionRequestState.ts frontend/src/domain/sessionRequestState.test.ts frontend/src/main.tsx frontend/src/main.test.ts
git commit -m "fix: gate online mutations during ticks"
```

---

### Task 3: Implement honest path-only historical playback

**Files:**
- Modify: `frontend/src/domain/sessionRequestState.ts`
- Modify: `frontend/src/domain/sessionRequestState.test.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `isHistoricalPlayback(displayTime, sessionCurrentTime)`
- Produces: `historicalRuntimeOverlay(...)`
- Changes: `MapBoard` props include `historicalPlayback: boolean`

- [ ] **Step 1: Add historical-overlay tests**

Create a current payload with:

- one unavailable robot;
- one runtime blocked cell;
- one stocked shelf;
- a latest task state marked `unassigned`.

At `displayTime < sessionCurrentTime`, assert:

```typescript
expect(historicalRuntimeOverlay(payload, true)).toEqual({
  robotStates: [],
  shelfStates: [],
  extraBlocked: [],
  unavailableRobotIds: []
});
```

At the current tick, assert the same helper returns all live values.

- [ ] **Step 2: Add task-queue mode test**

Define:

```typescript
historicalPlaybackNotice(true)
```

and assert the exact Chinese copy:

```text
历史回放仅提供路径、事件和指标；返回最新 T 查看实时状态。
```

- [ ] **Step 3: Run RED**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- sessionRequestState.test.ts main.test.ts
```

Expected: latest runtime state is still used during past playback.

- [ ] **Step 4: Remove current-only map overlays in history mode**

When historical:

- robot positions come from `result.paths`;
- `extraBlocked=[]`;
- `unavailableRobotIds=[]`;
- `shelfStates=[]`, which renders shelf state as `状态未知`;
- `safetyIntervention` is displayed only if its time equals the selected display tick.

Fixed obstacles, zones, shelves, path geometry, events and metric history remain.

- [ ] **Step 5: Replace current task queue with the notice**

Do not call `buildTaskSnapshots(result, time, session.taskStates)` for historical rendering. Replace both task columns with the notice and a “返回最新 T” button that sets `time=session.currentTime`.

The current tick continues to show the existing task queue and recovery controls.

- [ ] **Step 6: Style the existing control area**

Add a small `.history-playback-notice` style consistent with existing operational notices. Do not add a new panel or chart.

- [ ] **Step 7: Run GREEN and build**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

- [ ] **Step 8: Commit**

```powershell
git add frontend/src/domain/sessionRequestState.ts frontend/src/domain/sessionRequestState.test.ts frontend/src/main.tsx frontend/src/main.test.ts frontend/src/styles.css
git commit -m "fix: make historical playback time honest"
```

---

### Task 4: Delete stale successful session creations

**Files:**
- Modify: `frontend/src/domain/sessionApi.ts`
- Modify: `frontend/src/domain/sessionApi.test.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Produces: `settleCreatedSession(payload, current, apply, remove) -> Promise<"applied" | "deleted">`
- Consumes: `deleteSession(apiBase, sessionId)`

- [ ] **Step 1: Add applied/stale tests**

Applied:

```typescript
expect(await settleCreatedSession(payload, true, apply, remove)).toBe("applied");
expect(apply).toHaveBeenCalledWith(payload);
expect(remove).not.toHaveBeenCalled();
```

Stale:

```typescript
expect(await settleCreatedSession(payload, false, apply, remove)).toBe("deleted");
expect(remove).toHaveBeenCalledWith(payload.sessionId);
expect(apply).not.toHaveBeenCalled();
```

Delete failure is swallowed after being observed by the returned promise; it must not apply stale state.

- [ ] **Step 2: Run RED**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- sessionApi.test.ts main.test.ts
```

Expected: stale create payload is discarded without DELETE.

- [ ] **Step 3: Implement settlement helper**

Use dependency-injected callbacks rather than importing React state into the domain helper.

- [ ] **Step 4: Stop aborting an already-started create request**

Remove the create fetch `AbortController.signal`. In effect cleanup call `sessionRequestCoordinator.invalidate()` so the eventual response is classified stale.

When a create response arrives:

```typescript
await settleCreatedSession(
  payload,
  sessionRequestCoordinator.isCurrent(requestGeneration),
  applySessionPayload,
  (sessionId) => deleteSession(API_BASE, sessionId).then(() => undefined)
);
```

Do not mark API offline for a stale request.

- [ ] **Step 5: Cover StrictMode-style cleanup**

Add a test with two create generations resolving out of order. Assert:

- newer payload is applied;
- older payload is deleted;
- active newer session is not deleted.

- [ ] **Step 6: Run GREEN and frontend suite**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/domain/sessionApi.ts frontend/src/domain/sessionApi.test.ts frontend/src/main.tsx frontend/src/main.test.ts
git commit -m "fix: delete stale created sessions"
```

---

### Task 5: Extract and align scenario import validation

**Files:**
- Create: `frontend/src/domain/scenarioImport.ts`
- Create: `frontend/src/domain/scenarioImport.test.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`
- Modify: `backend/tests/test_api_contract.py`

**Interfaces:**
- Produces: `parseScenario(value: unknown) -> Scenario`
- Produces exact constants: axis 64, cells 1024, robots 32, tasks 128, targets 64, service/charge 10,000

- [ ] **Step 1: Add accepted-contract tests**

Assert `parseScenario` accepts:

- `releaseTime=null`, `deadline=null`, `serviceTime=null`;
- a base-task target that matches a `dynamic.blockedCells` entry with `triggerTime=5`;
- omitted optional fields allowed by backend models.

- [ ] **Step 2: Add rejected-boundary tests**

Assert exact rejection for:

- priority `1.5`, `-1`, or `6`;
- `serviceTime=10_001`;
- `chargeTime=10_001`;
- width/height above 64 or total cells above 1024;
- 33 robots;
- 129 initial+dynamic tasks;
- 65 inspection targets;
- repeated robot start coordinates.

- [ ] **Step 3: Run RED**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport.test.ts
```

Expected: nullable payload and future block overlap are rejected, while several invalid numeric boundaries are accepted.

- [ ] **Step 4: Move parser code into a focused module**

Move `parseScenario` and all private structural helpers out of `main.tsx`. Keep exact identifier casing from `Scenario`; do not normalize keys.

Use:

```typescript
function isNullableOptionalInteger(
  value: unknown,
  minimum: number,
  maximum: number
): boolean {
  return value === undefined
    || value === null
    || (Number.isInteger(value) && value >= minimum && value <= maximum);
}
```

- [ ] **Step 5: Match dynamic timing semantics**

Only fixed obstacles invalidate base robot starts and base task targets. Future dynamic blocked cells are checked for bounds and duplicate coordinates but are not merged into the immediate blocked set.

Dynamic task targets remain invalid when placed on fixed obstacles.

- [ ] **Step 6: Add exact frontend/backend alignment tests**

Extend `test_api_contract.py` to extract named frontend constants from `scenarioImport.ts` and compare them with:

- `MAX_SCENARIO_AXIS_LENGTH`;
- `MAX_SCENARIO_CELL_COUNT`;
- `MAX_SCENARIO_ROBOTS`;
- `MAX_SCENARIO_TASKS`;
- `MAX_TASK_TARGETS`;
- `MAX_TASK_SERVICE_TIME`;
- `MAX_SCENARIO_CHARGE_TIME`.

Also retain nullable type comparisons.

- [ ] **Step 7: Re-export only if existing tests require it**

If callers import `parseScenario` from `main.tsx`, add:

```typescript
export { parseScenario } from "./domain/scenarioImport";
```

Production code should import directly from the domain module.

- [ ] **Step 8: Run GREEN, contract tests, and build**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_api_contract.py
```

- [ ] **Step 9: Commit**

```powershell
git add frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts frontend/src/main.tsx frontend/src/main.test.ts backend/tests/test_api_contract.py
git commit -m "fix: align scenario import validation"
```

---

### Task 6: Frontend integration verification

**Files:**
- Review: all changed `frontend/src` files
- Review: `backend/tests/test_api_contract.py`

**Interfaces:**
- Verifies Tasks 1–5 as one online-control flow.

- [ ] **Step 1: Run complete frontend and contract suites**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_api_contract.py
```

- [ ] **Step 2: Inspect request entry points**

Search all calls to `updateSession`, `enqueueTask`, block/fail/restore and session create. Confirm every path uses the new error decision, mutation gate, or stale-create settlement.

- [ ] **Step 3: Run diff checks**

```powershell
git diff --check
git status --short
```

- [ ] **Step 4: Commit review corrections if needed**

Add a failing regression before each correction, then:

```powershell
git add frontend/src backend/tests/test_api_contract.py
git commit -m "fix: close frontend hardening review gaps"
```
