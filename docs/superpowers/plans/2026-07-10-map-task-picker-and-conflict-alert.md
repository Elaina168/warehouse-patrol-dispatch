# Map Task Picker And Conflict Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators choose manual-task coordinates directly from the map and keep the latest conflict visible in the replanning explanation.

**Architecture:** Keep selection and conflict-history state in `App`; pass explicit map callbacks and display data into `MapBoard`. Continue consuming the existing frontend `Conflict` type without API changes.

**Tech Stack:** React, TypeScript, Vitest, existing FastAPI session payloads.

## Global Constraints

- Keep UTF-8 source encoding and Chinese code comments where comments are needed.
- Use `apply_patch` for manual edits.
- Do not alter the backend conflict API shape.

---

### Task 1: Map Coordinate Selection

**Files:**
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/main.test.ts`

**Interfaces:**
- Produces a `MapPickTarget` union for `target`, `pickup`, and `dropoff`.
- `MapBoard` receives the active target and an `onPickCell(cell)` callback.

- [x] **Step 1: Write failing tests**

```ts
expect(nextMapPickTarget("pickup")).toBe("dropoff");
expect(applyMapPickToManualTask(form, "target", [3, 2]).target).toBe("3, 2");
```

- [x] **Step 2: Run the focused frontend test**

Run: `npm --prefix frontend run test -- main.test.ts`
Expected: the new exports are unavailable.

- [x] **Step 3: Implement picker state and map callbacks**

```ts
const [mapPickTarget, setMapPickTarget] = useState<MapPickTarget | null>(null);
function pickManualTaskCell(cell: Cell) { /* write coordinate and advance pickup to dropoff */ }
```

- [x] **Step 4: Verify the focused test passes**

Run: `npm --prefix frontend run test -- main.test.ts`
Expected: PASS.

### Task 2: Persistent Conflict Alert

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/main.test.ts`

**Interfaces:**
- Produces a `ConflictAlert` derived from frontend `Conflict` values.
- `ReplanStatusPanel` receives the latest alert and resolved state.

- [x] **Step 1: Write failing tests**

```ts
expect(selectLatestConflictAlert(conflicts, 8)).toMatchObject({ robots: ["R1", "R2"], cell: [3, 2] });
```

- [x] **Step 2: Run the focused frontend test**

Run: `npm --prefix frontend run test -- main.test.ts`
Expected: the new selector is unavailable.

- [x] **Step 3: Implement conflict history, explanation rows, and map badge**

```ts
const observedConflicts = result.conflicts.filter((conflict) => conflict.time <= time);
```

- [x] **Step 4: Verify the focused test and production build**

Run: `npm --prefix frontend run test -- main.test.ts; npm --prefix frontend run build`
Expected: PASS and successful Vite build.

### Task 3: End-to-End Verification

**Files:**
- Test: `frontend/src/main.test.ts`

- [x] **Step 1: Run the repository check**

Run: `npm run check`
Expected: all frontend and backend tests pass.

- [x] **Step 2: Verify in the local browser**

Open: `http://127.0.0.1:5174/`
Expected: map picking fills the visible coordinate field, and a conflict alert identifies both robots in the replanning explanation.
