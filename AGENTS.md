# AGENTS.md

## Project Context

This repository is a multi-robot dispatch system for warehouse logistics and campus/facility patrol scenarios. It may later support competition delivery, but current work prioritizes the system itself.

Read this file before each new work session in this repository. It is the current high-level plan and should be used to avoid drifting back into static/batch-only dispatch work.

The current project is a formal frontend/backend split. The earlier root static prototype has been deleted and should not be restored unless the user explicitly asks for it.

Core project goal:

- Multiple robots execute patrol, pickup-delivery, and emergency tasks on grid maps.
- The backend computes task assignment, A* paths, conflict avoidance, conflict detection, dynamic replanning, metrics, and event logs.
- The frontend renders maps, paths, robot states, timeline playback, controls, task queues, metrics, and backend status.

Target direction:

- The project should evolve toward an online task-flow dispatch system, not a static one-shot batch dispatcher.
- The backend should maintain ongoing scheduling session state: robot states, task queues, assigned tasks, paths, ticks, events, and metrics history.
- The frontend should remain an operational control dashboard for online scheduling: start sessions, add manual or timed generated tasks through the unified task endpoint, trigger emergencies, inspect paths, inspect events, and observe metrics.
- Until the user explicitly confirms competition participation and material requirements, prioritize system correctness, safety, scheduling capability, reliability, and maintainability over charts, reports, slides, recordings, 3D, or presentation polish.
- The next feature, hardening item, or algorithm change has not been selected. Do not turn a candidate direction into the project plan without explicit user confirmation.

## Current Progress

Completed and currently expected to remain in the project:

- Formal React + TypeScript + Vite frontend and FastAPI backend split.
- Online session API and frontend integration for session creation, ticking, manual task insertion, automatic task stream insertion, runtime blocked cells, and robot failures.
- Backend dispatch logic with beam-style assignment, A* path planning, time-aware conflict avoidance, conflict detection, dynamic replanning metadata, metrics, and event logs.
- Runtime task locking and lock release behavior for affected paths, failed robots, and high-priority task preemption.
- Hard task locks are applied only after a task reaches its execution window in the assigned task sequence, avoiding premature locks for queued-but-not-started tasks.
- Preemption scoring that compares keep-lock and release-lock plans before releasing lower-priority locked tasks.
- Locked-task prefix ordering and rolling-window ordering for online replanning stability.
- Soft assignment preferences reduce unnecessary robot switching across online replans while still allowing better, reachable, or failure-driven reassignment.
- Passive session ticks reuse the active plan instead of replanning every frame, preventing playback horizon growth loops.
- Rolling-window planning defers far-future tasks outside `ASSIGNMENT_REPLAN_WINDOW`; deferred tasks remain visible as pending queue items without inflating current assignments, paths, or failure counts.
- Locked far-future tasks are regression-tested to stay inside the current planning window, preserving online execution continuity during replanning.
- Online sessions trigger replanning when deferred tasks enter the rolling assignment window during passive ticks.
- Online session event logs preserve rolling-window trigger entries after runtime event-note trimming, so far-future task inclusion remains explainable in long sessions.
- Online sessions preserve both event log entries when a scenario dynamic trigger and a rolling-window trigger happen on the same tick, keeping simultaneous online replan causes observable.
- Online sessions activate scenario dynamic tasks, blocked cells, and failed robots at `dynamic.triggerTime` instead of applying them at session creation time.
- Backend scenario validation now matches online dynamic timing: base tasks are validated against fixed map state unless `dynamic.triggerTime` is 0, while dynamic tasks are validated against dynamic blocked cells and failed robots.
- Direct dispatch planning also treats scenario dynamic blocked cells and failed robots as active only when `dynamic.triggerTime` is 0, while still exposing future dynamic tasks and event timing metadata.
- Direct dispatch assignment avoids assigning future dynamic tasks to robots that become failed at the dynamic trigger, while still allowing those robots to handle pre-trigger base tasks.
- Online session task states, session result task payloads, and direct dispatch planning clamp scenario dynamic task release times to at least `dynamic.triggerTime`, so a dynamic task with a missing or earlier `releaseTime` remains pending before activation, reports a consistent release time, and does not start before the dynamic event.
- Online session event logs preserve the scenario dynamic trigger entry after runtime event-note trimming, so `T=<triggerTime> 场景动态事件触发` remains visible alongside preserved dynamic block and robot failure history.
- Session replanning keeps `dynamicTriggerTime` and dynamic event log entries on absolute session time, so runtime task insertion before a dynamic trigger no longer shifts the trigger later.
- Online replanning preserves partial task progress: completed pickup points and completed inspection targets are not planned again.
- Task completion and partial-progress detection respect `releaseTime`; visiting a target before task release no longer marks the task completed.
- Online robot delivery status also respects `releaseTime`; a robot that visited a pickup cell before a delivery task was released remains `toPickup` until it visits the pickup after release.
- Session result construction synchronizes immediately completed tasks back into session state, keeping top-level `completedTaskCount`, task states, and metrics history consistent even for tasks completed at T=0.
- Delivery tasks that have already picked up cargo keep delivery semantics during replanning; if the carrier robot fails, the next robot routes to the cargo's current position before the dropoff.
- Dispatch results and session task states expose per-task `failureReason` values for unassigned or path-failed tasks, and event logs include concrete scheduling failure causes.
- Dispatch results now also expose structured `failureDetails`, and session task states expose `failureCategory` plus `recoveryAction` so temporary runtime failures can be distinguished from permanently impossible tasks.
- Structured `failureDetails` include `blockingCells` and `blockingRobotIds`, making temporary blocked-cell and unavailable-robot recovery conditions inspectable without parsing Chinese event text.
- Unschedulable task recovery classification now checks whether clearing blocked cells or restoring failed robots would actually make a task reachable, including combined block-plus-robot recovery and delivery tasks where only a failed robot has enough load capacity.
- Online session task states now preserve the same recovery semantics through runtime events: combined block-plus-robot failures narrow after a partial fix, and either clear-block or restore-robot alternatives clear task failures as soon as one valid recovery path exists.
- Online session task-state regression now verifies unrelated runtime blocked cells do not pollute load-capacity recovery details: when only a failed high-load robot can serve a delivery task, recovery remains `restoreRobot` with empty `blockingCells`.
- Static map or target reachability failures remain classified as permanent even when unrelated runtime blocked cells exist.
- Assigned tasks whose path planning fails now reuse the same reachability-aware recovery classification as unassigned tasks, preventing static map failures from being misreported as clearable runtime block failures.
- Metrics `failureCount` now counts distinct failed tasks through `failureDetails` instead of raw failure event strings, avoiding double-counting when a temporary condition creates both path and assignment failure events.
- Online sessions support runtime recovery actions for temporary failures: removing runtime blocked cells and restoring runtime failed robots both trigger replanning and clear temporary task failure details when tasks become schedulable again.
- Runtime update APIs that default `currentTime` now use the session's current tick when callers omit `currentTime`, while explicit past times remain rejected.
- Generated runtime tasks skip existing generated-style task IDs before insertion, preventing task-state and event-log ambiguity when imported or manual tasks already use the same ID style.
- Online sessions can clear active scenario dynamic blocked cells through the blocked-cell recovery API, so `clearBlockedCells` recovery actions work for both manual runtime blocks and activated scenario dynamic blocks.
- Online sessions can restore active scenario dynamic failed robots through the robot-restore recovery API, so `restoreRobot` recovery actions work for both manual runtime failures and activated scenario dynamic failures.
- Manual runtime block or robot failure requests are idempotent when the same condition is already active from a scenario dynamic event, preventing duplicate runtime event counts and duplicate manual event log entries. Duplicate manual block requests for an active dynamic blocked cell bypass ordinary occupancy rejection so the request remains a no-op even if a robot is currently on that already-blocked dynamic cell.
- Online session event logs preserve triggered scenario dynamic blocked-cell and failed-robot history after those dynamic conditions are cleared by recovery APIs.
- Online session event logs keep the original triggered scenario dynamic blocked-cell count after partial dynamic block recovery, avoiding misleading reduced-count history entries.
- Runtime session events are stored with their real tick timestamps before being merged into `eventLog`, so task arrivals, lock changes, blocked cells, failures, and dynamic triggers remain time-aligned for debugging.
- Passive ticks that create new runtime lock events now expose those lock events immediately in the returned `eventLog` even when the active plan is reused from cache.
- Runtime event APIs now keep session time and metadata consistent for idempotent or rejected future-time updates: no-op runtime updates that advance `currentTime` update `updatedAt`, while invalid future blocked-cell or robot recovery/failure requests no longer mutate session time.
- Runtime blocked-cell occupancy validation now uses a non-mutating dispatch preview, so rejected future-time block requests do not write `last_result`, metrics history, or assignment preferences.
- Failure recovery details now narrow joint blocked-cell recovery sets, so `blockingCells` reports the dynamic cells that still block recovery without mixing in unrelated runtime blocks.
- Failure recovery classification now avoids suggesting `restoreRobot` when failed robots would still be unable to reach a statically unreachable task after recovery.
- Failure reason text now stays aligned with recovery classification: static map or target failures are not described as dynamic-block or robot-restore failures when those runtime conditions would not actually recover the task.
- Failed locked-robot task reasons now also check restored reachability, so a locked robot failure is not reported as merely restorable when the locked robot would remain statically unreachable.
- Failed locked-robot delivery reasons now also check restored load capacity, so restoring an under-capacity locked robot is reported as a permanent capacity issue instead of a robot-restore issue.
- Metrics history and frontend panels for map, paths, robot states, task queue, event log, online controls, runtime events, and replanning explanation.
- Scenario import/export validation on the frontend.
- Backend scenario validation rejects dynamic failed robot references that are duplicated or do not match any scenario robot ID.
- The direct dispatch API and online session creation now share backend scenario validation, preventing invalid baseline dispatch inputs from bypassing validation.
- Session lifecycle metadata, list/delete/reset APIs, idle-session cleanup, maximum in-memory session pruning, initial-session snapshots for reset, and test isolation for the session store.
- Session store capacity pruning is regression-tested against recent access time, so reopening an older active session protects it from least-recently-accessed eviction.
- Session list summaries are regression-tested against the latest runtime session response for metadata, current time, task counts, runtime event count, and completion count.
- Long online pressure regression covering continuous ticks, urgent and generated task insertion through the unified task endpoint, scenario dynamic activation, runtime blocked cells, robot failures, runtime recovery actions, event ordering, metrics history, task failure reasons, and path/state consistency.
- Direct dispatch regression covers rolling-window deferred tasks remaining visible in `tasks` without entering current assignments, failures, or `failureCount`.
- Direct dispatch regression now covers an 8-robot mixed inspection and delivery pressure scenario with conflict avoidance enabled, preserving full task assignment and zero conflict output.
- Larger online pressure regression covering four robots, denser mixed tasks, repeated automatic arrivals, runtime block/failure recovery, dynamic emergency activation, rolling-window task inclusion, and far-future deferred task visibility.
- Online session pressure regression covers 8 robots with urgent and generated runtime task insertion, scenario dynamic activation, runtime blocked cells, robot failure and restore, metrics history, and deferred far-future task visibility.
- Online session long-horizon stress regression covers 8 robots with continuous ticks, repeated generated tasks, two urgent runtime tasks, two runtime blocked-cell cycles, runtime robot failure/restore, scenario dynamic blocked-cell recovery, scenario dynamic robot restore, metrics history continuity, active-time conflict safety, and far-future deferred task visibility.
- Direct dispatch now has a deterministic scale-pressure scenario family covering 3, 5, and 8 robots with increasing mixed task counts and dynamic emergency tasks, preserving full assignment, zero conflicts, zero failures, and bounded planning time.
- Direct dispatch now has a fixed-seed pressure scenario family covering 4, 6, and 8 robots with randomized obstacles, mixed inspection/delivery tasks, and dynamic emergency tasks, preserving reproducible full assignment, zero conflicts, zero failures, and bounded planning time.
- Fixed-seed seed-43 pressure boundary is now regression-tested as a passing case: task-complete robots can add a reservation-aware parking step when their task endpoint is needed by a later reserved path, eliminating the previous late vertex conflict while preserving full assignment and zero failures.
- Path planning candidate evaluation now preserves priority-first planning, tries long static routes as an early fallback, and stops once a zero-failure/zero-conflict/no-deadline-miss candidate is found, keeping the fixed-seed seed-43 boundary inside the extended pressure experiment without the previous multi-second wasted candidate search.
- Online sessions now reuse the fixed-seed pressure generator in a long-flow regression covering continuous ticks to dynamic activation, manual emergency insertion, runtime blocked-cell insertion and recovery, runtime robot failure and restore, event history, metrics history, and final zero-conflict/zero-failure recovery.
- Online reset regression covers sessions after scenario dynamic activation, runtime blocks, robot failures, and generated runtime tasks, verifying reset clears runtime state and allows the original dynamic event to trigger again on the same absolute session time.
- PowerShell UTF-8 safeguards for Chinese source display: project scripts configure UTF-8 console/Python output, `Get-Content` defaults to UTF-8 inside the scripted environment, and `.editorconfig` declares UTF-8 source files.
- Project-local PowerShell 7.6.2 is installed under `.tools\powershell`, and npm development scripts use it instead of Windows PowerShell 5.1.
- API contract regression tests compare backend Pydantic model fields with frontend TypeScript API types for request models, response models, runtime state models, metrics, events, task failure details, and core nested dispatch types; they also verify dispatch/session OpenAPI routes use the expected request and response models.
- API contract regression tests now include `Scenario` and exported `Zones` frontend/backend field alignment, preventing map zone structure drift.
- API contract regression tests now verify frontend request optional fields match backend defaulted request fields for `DispatchRequest` and `CreateSessionRequest`.
- API contract regression tests verify runtime recovery request defaults such as optional `currentTime`, keeping frontend optionality aligned with backend defaults.
- API contract regression tests now verify response nullable fields, including serialized task variant null fields, so frontend task response types match backend `None` serialization.
- Experiment comparison API now exposes `POST /api/experiments/conflict-avoidance`, returning paired dispatch results for conflict avoidance disabled and enabled on the same scenario.
- Conflict-avoidance experiment scenarios should not use a fully one-dimensional two-robot position swap as a no-conflict success case; without side-bypass space, the current competition-scope planner may still report unavoidable conflicts.
- Conflict-avoidance experiment regression now covers the shared `integrated-demo` scenario: priority avoidance preserves all assignments, reduces direct-plan conflict forecasts to zero, and keeps failure count at zero.
- Experiment comparison API now exposes `POST /api/experiments/dynamic-replanning`, returning paired dispatch results for dynamic events disabled and enabled on the same scenario.
- Dynamic-replanning experiment regression now derives a dynamic-task variant from `integrated-demo`: enabling dynamic replanning introduces `E1` at its configured trigger time while preserving zero failures.
- Experiment comparison API now exposes `POST /api/experiments/replan-window`, returning one dispatch result per requested `assignmentReplanWindow` value.
- Replan-window experiment regression now covers `integrated-demo`: a short window defers the future `E1` task, while a larger window includes it without creating scheduling failures.
- Experiment comparison API now exposes `POST /api/experiments/scale`, returning one dispatch result per supplied labeled scenario for robot/task scale comparison.
- Scale experiment regression keeps generated multi-scale cases and verifies that the shared `integrated-demo` payload remains accepted by the same API.
- Experiment comparison API now exposes `POST /api/experiments/seeded-pressure`, returning compact fixed-seed performance summaries for standard 4/6/8-robot randomized pressure scenarios and an extended seven-case stability set including the fixed seed-43 boundary. Its `assignmentRatePercent` is assigned tasks divided by total tasks; it is not an execution-completion metric.
- Experiment comparison API now exposes `POST /api/experiments/online-pressure`, returning fixed-seed online-flow evidence with runtime task insertion, blocked-cell and robot recovery events, task coverage, released-task completion, metrics history, and event logs.
- Online pressure responses distinguish `coverageRatePercent` from `actualCompletionRatePercent`: coverage measures tasks not left `unassigned`, while actual completion measures completed tasks among tasks released by the experiment end tick.
- Robots expose static `capabilities` using the exact task-type values `inspection`, `delivery`, and `emergency`; omitted fields default to all three values for legacy scenarios, while explicit lists must be non-empty, unique, and valid.
- Task-type capability is a hard eligibility constraint across validation, assignment, locks, preemption, failure handoff, and recovery. Delivery tasks additionally retain the existing `robot.load >= task.demand` constraint.
- Direct dispatch failure details apply future dynamic failed robots and blocked cells only to tasks released at or after the dynamic trigger, so future failures report the compatible restore targets and future corridor blocks report the clearable cells without polluting pre-trigger base tasks or current session state.
- Planning removes a stale task lock only when its robot violates the static task-type or delivery-load constraint and another currently available, reachable, battery-feasible robot can execute the task. Online sessions delete that lock and record the release reason before replanning; compatible locks affected only by failure, blocking, or battery conditions retain their existing recovery lifecycle.
- Manual and generated tasks continue through the unified `POST /api/sessions/{session_id}/tasks` endpoint. The frontend random generator filters candidates by the fleet capability union, and only treats `delivery` as supported when at least one delivery-capable robot has `load >= 1`; the backend remains the validation authority.
- Capability failure recovery is regression-tested so incompatible robots do not enter `blockingRobotIds`, restoring an incompatible robot does not clear the failure, and restoring the unique compatible robot triggers replanning.
- Scale experiment regression now provides labeled `homogeneous-fleet` and `specialized-fleet` evidence through the existing scale endpoint, while an online `specialized-with-failure` regression covers unique-specialist failure and recovery without adding an experiment API.
- All six experiment endpoints are backend-only evidence interfaces. The main frontend has no experiment panel, experiment API clients, experiment result types, generated report helpers, or experiment-specific styles.
- Frontend live deadline metric logic handles nullable serialized task deadlines and excludes still-pending tasks, keeping live deadline misses aligned with backend `deadlineMissCount`.
- Frontend API error handling preserves backend error `detail` text across session creation, updates, ticks, reset, and delete failures, so operational panels surface concrete API failure causes instead of only HTTP status codes.
- Frontend status strip exposes the configured rolling replan window. Scenario dynamic events are always enabled for main-session creation and are not presented as a user toggle.
- Rolling windows now support an optional explainable adaptive mode while fixed mode remains the default. Adaptive decisions use recent planning latency, released-task pressure, future-task availability, and active robot count; dispatch results expose the effective window and reason, and the frontend status strip displays them without adding an experiment panel.
- An offline adaptive-window calibration workflow compares `fixed-4`, `fixed-24`, `fixed-48`, and `adaptive-current-24` across three deterministic online cases. It records only real online replans together with deterministic planning-work diagnostics; production remains on the internal `60/40ms`, latest-5-samples/minimum-3-samples, and `2×` task-pressure policy.
- Adaptive calibration wall-clock values and its evidence-derived candidate envelope are same-machine evidence for a later reviewed decision, not an automatic recommendation or a production-policy update. The workflow does not establish complete MAPF capability, arbitrary-input zero-conflict planning, or portable cross-machine thresholds.
- The reviewed 2026-07-26 pre-fix default calibration produced 60 completed runs, 40 stable runs, 20 completed-but-unstable runs, 1,310 real-replan observations, and 12 summaries with no timeout, error, or residual worker. Every unstable run was one of the five repetitions of each variant on `adaptive-pressure-r8-t45`; the candidate envelope was unavailable because that case contributed no stable completed `fixed-24` observation. This result remains historical diagnosis rather than repaired evidence.
- The calibration-only `adaptive-pressure-r8-t45` copy now normalizes every robot to `battery=150` and `batteryCapacity=150` and every non-null base-task deadline to exactly T=120. The source `density-r8-t43` scenario and other production or benchmark cases remain unchanged.
- Adaptive calibration cumulative `totalDistance` now comes only from the terminal `MetricSnapshot.travelledDistance`; missing metric history is an error rather than a completed result with a zero fallback.
- The repaired 2026-07-26 default calibration result is `output/adaptive-replan-calibration/20260726T150547Z`. Its exact cross-check object is `{"runs": 60, "stable": 60, "observations": 1330, "summaries": 12, "outcomes": {"completed": 60}, "pressureDistanceRange": [671, 671], "candidateEnvelope": {"slowExitThresholdMs": {"min": 9.71, "max": 31.05}, "slowEnterThresholdMs": {"min": 31.05, "max": 69.47}, "taskPressureMultiplier": {"min": 1.75, "max": 3.38}}}`. This candidate envelope is same-machine evidence only; no threshold was applied, and production remains `60/40ms`, latest-5-samples/minimum-3-samples, and `2×`.
- Frontend scenario-data regression now protects `integrated-demo` as the only persisted frontend scenario and verifies its mixed task, charging, and online-dispatch coverage.
- The sole `integrated-demo` baseline now uses a `26 × 16` realistic warehouse grid with twelve `3 × 2` shelf groups, two-cell horizontal and vertical clearances, top inbound cells, bottom outbound cells, right-side robot starts, and right-side charging cells.
- All four `integrated-demo` robots explicitly support `inspection`, `delivery`, and `emergency`; the default demo remains an all-capability flow and robot tooltips expose the three Chinese capability labels.
- The 72 shelf entity cells are impassable, and every shelf has one unique adjacent service cell where robots perform pickup or putaway operations.
- Online sessions are the inventory authority and expose `shelfStates` with `empty`, `inboundReserved`, `occupied`, and `outboundReserved`; the frontend renders these states instead of inferring inventory.
- Inbound tasks run from an inbound-zone cell to an empty shelf service cell, while outbound tasks run from an occupied shelf service cell to an outbound-zone cell. Scenario, manual, and generated delivery tasks use the same backend inventory validation and reservation rules through the unified runtime task endpoint.
- Default delivery coordinates are `T1` inbound `[2,0] -> [2,5]` for shelf `[2,4]`, `T2` outbound `[13,6] -> [2,15]` for shelf `[13,7]`, and `T4` inbound `[8,0] -> [12,9]` for shelf `[12,8]`.
- The fixed demo starts with 12 highlighted stocked shelves. `T2` turns its shelf off when pickup occurs; `T1` and `T4` turn their shelves on only after putaway service completes, leaving 13 stocked shelves at the end.
- The fixed default demo regression now uses only the six scenario tasks and completes by T=700 with zero active conflicts, failures, deadline misses, or charging visits; runtime task, block, failure, and recovery behavior remains covered by focused session regressions.
- Fixed demo e2e reads the shared `integrated-demo` JSON and verifies the default six-task flow, conflict-avoidance comparison, and a derived dynamic-task variant.
- Integrated online-session regression now runs continuous ticks through `E1` release and manual high-priority task insertion, verifying task states, event logs, conflict metrics, and metrics history remain consistent.
- Frontend scenario data now lives in `frontend/src/domain/scenarios.json`, with `frontend/src/domain/scenarios.ts` providing typed exports for the React app and tests.
- Backend tests split by algorithm, session, validation, health, and fixed demo flow.
- Documentation for environment, algorithm behavior, runtime recovery APIs, and demo flow.
- Online sessions serialize same-session operations with a registry/per-session lock protocol while allowing different sessions to plan in parallel; the guarantee is process-local and does not cover multiple Uvicorn workers.
- Repeated identical execution-safety holds expose nullable `SessionResult.safetyStall` after three consecutive interventions without weakening full-fleet safe waiting or automatically failing tasks.
- Scenario inputs are bounded to 64 cells per axis, 1024 total cells, 32 robots, 128 total initial/dynamic/runtime tasks, and 64 targets per task.
- Adaptive rolling-window latency uses the median of the latest five real replans after at least three samples, entering slow state at 60ms and leaving it at 40ms; fixed mode remains unchanged.
- Browser CORS defaults to the two local Vite origins and accepts explicit comma-separated origins through `WAREHOUSE_PATROL_CORS_ORIGINS`; wildcard origins remain rejected.
- Current verification snapshot on 2026-07-27: frontend production build passed, frontend tests `105/105`, backend tests `523/523`.

Recently removed because they are not needed yet:

- Frontend demo acceptance checklist panel.
- Frontend experiment comparison panel and its client-side helpers, types, tests, and styles.

## Current Plan

The project is no longer in a from-scratch build phase. Current work should strengthen the runnable system itself. The next specific implementation item has not been selected and must be confirmed with the user before design or coding begins.

System-complete means the current online dispatch workflow is credible, safe within its documented boundary, maintainable, and protected by regression tests. It does not imply product-grade persistence, authentication, or industrial MAPF guarantees.

Priority order for future work:

1. Freeze and protect a runnable baseline: keep the current online dispatch demo passing checks and avoid broad rewrites.
2. Improve the core algorithm module when a concrete, reproduced system boundary has been selected: task assignment, A* paths, conflict avoidance, dynamic replanning, task locks, preemption, failure recovery, and metrics must remain stable.
3. Complete the online scheduling module: session creation, ticks, unified runtime task insertion, runtime blocked cells, robot failures, recovery APIs, event logs, metrics history, reset/list/delete, and validation should stay closed-loop.
4. Complete the frontend operations module only where controls, state visibility, error explanation, and debugging directly improve operation of the system.
5. Retain experiment APIs and offline benchmarks as internal diagnostic and regression tools; do not prioritize chart or report production before competition requirements are known.
6. Defer competition materials until the user explicitly confirms participation and supplies the applicable requirements.
7. Keep 3D visualization as a later optional presentation phase.

Do not add new frontend showcase panels unless they directly support debugging, operating, or explaining the online dispatch workflow.

## Progress-Ordered Roadmap

Read this section before choosing work in each new session. Use it to understand implemented modules and known boundaries, but do not infer the next task from the roadmap: the next specific direction remains pending user confirmation. Progress uses qualitative status only: `稳定基线`, `接近完成`, `部分完成`, `未开始`, and `延后/可选`.

Current module status:

1. Baseline freeze and demo stability - 稳定基线
   - The current full check passes and the online dispatch workflow is implemented.
   - The fixed frontend demo scenario IDs and their intended capability coverage are now protected by frontend scenario-data regression tests.
   - The sole frontend `integrated-demo` scenario is protected by shared JSON, frontend data, and backend end-to-end regressions.
   - The fixed baseline is a `26 × 16` realistic warehouse with twelve `3 × 2` shelf groups, 72 impassable shelf entity cells, 72 unique adjacent service cells, and two-cell clearances.
   - Its default six-task online flow starts with 12 stocked shelves and finishes with 13 by T=700 without runtime task injection, runtime blocks, or robot failures.
   - Baseline maintenance rule: keep this flow stable during backend/frontend changes and avoid destabilizing broad refactors.

2. Core algorithm module completion - 接近完成
   - Assignment, task-type capability eligibility, A* path planning, conflict avoidance, lock stability, preemption scoring, rolling-window behavior, partial-progress replanning, delivery cargo continuity, dynamic timing, and failure recovery classification are implemented with targeted regressions.
   - Dynamic replanning behavior is regression-tested through an `integrated-demo` dynamic-task variant, confirming dynamic task inclusion and zero failures.
   - Rolling-window behavior is regression-tested against `integrated-demo`, confirming far-future task deferral versus inclusion across window settings.
   - Deterministic scale-pressure coverage now verifies increasing 3/5/8-robot scenario families with mixed tasks and dynamic emergency tasks while preserving full assignment, zero conflicts, zero failures, and bounded planning time.
   - Fixed-seed pressure coverage now verifies randomized 4/6/8-robot scenario families with mixed tasks, randomized obstacles, dynamic emergency tasks, full assignment, zero conflicts, zero failures, and bounded planning time.
   - The previous 8-robot seed-43 late-goal conflict boundary is now a passing regression through reservation-aware post-task parking.
   - An explainable adaptive rolling-window policy is implemented with fixed-mode compatibility, task-pressure and planning-latency contraction, low-load future-work expansion, effective-window explanations, and experiment comparison support. Fixed mode remains the cross-environment deterministic regression baseline because wall-clock latency feedback can vary by machine load.
   - Online execution safety is now a code-enforced invariant for `avoidConflicts=true`: the first predicted vertex or reverse-edge conflict tick causes a full-fleet hold, is returned early through `SessionResult.safetyIntervention`, and never writes the conflicting action into actual path history. Baseline comparison, direct dispatch, and experiments intentionally remain prediction/comparison paths and can still return or execute conflicts.
   - The offline algorithm boundary benchmark is implemented with deterministic scale, density, and online bottleneck cases, isolated per-run timeouts, JSON/CSV result reports, and stability summaries. It records planning forecasts separately from online execution safety evidence; it does not establish complete MAPF or full-horizon zero-conflict guarantees.
   - Known boundaries remain beyond the current heuristic planner, including arbitrary-input route solvability, larger-instance performance, portable adaptive thresholds, and full-horizon zero-conflict guarantees. These are documented boundaries, not an approved next task. The safety gate prevents unsafe execution but does not guarantee that every input has a zero-conflict route.
   - The density benchmark performance anomaly has been attributed to a timed A* candidate whose goal remained vertex-reserved for the complete search horizon. The planner now rejects that candidate before state expansion, records deterministic planning-work diagnostics in benchmark schema v2, and preserves the existing candidate order and result semantics.
   - 同机 density x5 优化证据的 `medianReplanTimeMs` / P95 为：`density-r8-t31` 150.85 / 155.90 ms、`density-r8-t43` 213.56 / 214.37 ms、`density-r8-t55` 231.33 / 235.45 ms；31/43 的全预留目标拒绝不再进入 exhausted 搜索，55 保持首个候选成功。
   - The offline adaptive-window calibration compares fixed `4T`/`24T`/`48T` with the current adaptive `24T` baseline on three deterministic online cases. It records real replan decisions, correctness outcomes, execution-safety evidence, and deterministic planning work without changing production `60/40ms`, latest-5/minimum-3 latency sampling, or the `2×` pressure rule.
   - The pre-fix pressure case's incomplete coverage, two failures, and deadline miss were traced to calibration-fixture battery/deadline pressure rather than a window-specific failure. The repaired calibration-only copy normalizes battery and non-null base-task deadlines without changing the source density scenario; a fresh 60-run produced stable completed evidence for all variants and a non-null candidate envelope. The envelope remains same-machine evidence rather than an automatic recommendation, and no production threshold changed. Do not claim portable cross-machine thresholds, complete MAPF capability, or a global zero-conflict guarantee.
   - Battery and charging are now hard runtime constraints: a robot consumes one unit per moved grid cell, routes to a reachable charger when needed, waits for `chargeTime`, and remains unavailable to preemption while charging.

3. Online scheduling module completion - 接近完成
   - Implemented session metadata such as creation time and last access/update time.
   - Implemented idle-session cleanup, least-recently-accessed capacity pruning, explicit delete/reset endpoints, task caps, tick caps, metrics-history caps, and event-note caps.
   - Implemented runtime task insertion through one unified task endpoint, timed frontend task generation through that same endpoint, runtime blocked cells, robot failure and recovery, scenario dynamic timing, rolling-window triggers, task locks, event logs, and metrics history.
   - Integrated-scenario default continuity is regression-tested through T=700; manual insertion, runtime events, and recovery edge coverage remain protected by focused session regressions.
   - Fixed-seed randomized online pressure is now regression-tested through continuous ticks, dynamic activation, manual emergency insertion, runtime block/failure events, recovery APIs, event history, and metrics history.
   - Unselected boundary candidates include harder online pressure cases or dedicated performance profiling if algorithm scale later becomes a confirmed priority. Persistence, auth, or external observability are not current capabilities.

4. Online pressure and regression coverage - 接近完成
   - Larger and continuous online regressions cover repeated ticks, timed generated arrivals and urgent task insertion through the unified task endpoint, dynamic activation, runtime blocked cells, robot failures, recovery actions, deferred tasks, metrics history, event ordering, and path/state consistency.
   - Direct dispatch now has an 8-robot mixed inspection and delivery pressure regression with conflict avoidance enabled.
   - Direct dispatch now also has a deterministic 3/5/8-robot scale-pressure family with dynamic emergency tasks and bounded planning time.
   - Direct dispatch now also has a fixed-seed 4/6/8-robot randomized pressure family with randomized obstacles, mixed tasks, dynamic emergency tasks, zero conflicts, zero failures, and bounded planning time.
   - Online sessions now also have a fixed-seed randomized long-flow regression with continuous ticks, dynamic activation, manual emergency insertion, runtime blocked-cell recovery, runtime robot failure/restore, preserved event history, and recovered zero-conflict/zero-failure metrics.
   - Online sessions have an 8-robot pressure regression with urgent insertion, repeated generated tasks through the unified endpoint, scenario dynamic activation, runtime blocked cells, robot failure and restore, and deferred far-future task visibility.
   - Online sessions also have an 8-robot long-horizon runtime stress regression covering continuous ticks, repeated generated tasks, multiple runtime block/failure recovery cycles, scenario dynamic recovery, metrics history continuity, and active-time conflict safety.
   - Online sessions now also cover `integrated-demo` through continuous ticks, `E1` release, and manual high-priority task insertion.
   - Focused generic scenarios retain dynamic robot failure, block recovery, and emergency handoff regression coverage without adding more persisted demo maps.
   - The integrated conflict-avoidance comparison retains direct-plan baseline and avoidance coverage; active-session conflict safety remains covered through continuous ticks.
   - Unselected coverage candidates include additional randomized scenario families or dedicated performance profiling if algorithm scale later becomes a confirmed priority.

5. Unschedulable task recovery semantics - 接近完成
   - Temporary and permanent failure categories, structured recovery actions, blocking cells, blocking robots, runtime recovery APIs, and frontend recovery buttons are implemented and regression-tested.
   - Online recovery state now has targeted coverage for mixed load-capacity fleets where unrelated runtime blocked cells must not be reported as recovery blockers.
   - Unselected boundary candidates include larger mixed-capacity fleet edges and clearer operator workflows around permanent definition fixes.

6. API contract alignment infrastructure - 稳定基线
   - Contract tests now compare backend Pydantic models with frontend TypeScript API types, request optionality, response nullability, runtime literal unions, rolling-window constants, recovery actions, OpenAPI request models, OpenAPI response models, and expected session routes.
   - Optional future automation includes schema snapshot generation or client generation if API growth later justifies it.

7. Frontend operational clarity - 接近完成
   - Keep the frontend as a dense operational dashboard.
   - Current UI covers online session creation, ticking, manual and timed generated task insertion through one endpoint, runtime blocked cells, robot failure/recovery, task states, failure recovery actions, metrics history, event replay, scenario import/export, backend status, and rolling-window visibility.
   - Scenario dynamic events are enabled automatically for main sessions; the main UI has no dynamic-event toggle and no experiment panel.
   - Improve only controls, state visibility, event inspection, metrics visibility, and debugging clarity that support online dispatch.
   - Do not add marketing or showcase panels.

8. Experiment and comparison tooling - 稳定基线
   - The backend has six experiment APIs for conflict avoidance, dynamic events, rolling windows, scale, fixed-seed pressure, and online pressure. They are covered by backend tests and OpenAPI contract assertions.
   - Fixed-seed pressure reports assignment rate. Online pressure separately reports task coverage and actual released-task completion, avoiding the previous metric-name ambiguity.
   - The main frontend intentionally has no experiment panel. Charts, reviewed report conclusions, and competition evidence tables are deferred until participation and material requirements are explicitly confirmed.
   - Conflict-avoidance comparison now has backend regression coverage on a real fixed demo scenario instead of only synthetic scenarios.
   - Dynamic-replanning comparison now has backend regression coverage on a real fixed demo scenario instead of only synthetic scenarios.
   - Rolling-window comparison now has backend regression coverage on a real fixed demo scenario and can include an adaptive case alongside fixed-window cases.
   - Scale comparison now has backend regression coverage across the real fixed demo scenario set, not only synthetic scale inputs.
   - Scale comparison also covers labeled homogeneous and specialized capability fleets, and a focused online regression covers the unique-compatible-robot failure and recovery sequence without adding another experiment endpoint.
   - Fixed-seed pressure comparison has backend coverage for standard and extended stability sets, including the repaired seed-43 pressure boundary, with assignment-rate, deadline-miss, planning-budget, distance, and makespan evidence.
   - The offline algorithm boundary benchmark is implemented as command-line evidence for nine deterministic scale, density, and online bottleneck cases. Its reports distinguish predicted conflicts from active online conflicts and safety interventions; the six existing experiment APIs remain unchanged.
   - 已完成 density x5 取证：`density-r8-t31`、`density-r8-t43`、`density-r8-t55` 的 `medianReplanTimeMs` / P95 依次为 150.85 / 155.90 ms、213.56 / 214.37 ms、231.33 / 235.45 ms。该结果保留为内部诊断证据，不自动指定后续性能调优、阈值修改或 MAPF 评估方向，也不得表述为完整 MAPF 结果。
   - The separate offline adaptive-window calibration uses three deterministic online cases and four variants (`fixed-4`, `fixed-24`, `fixed-48`, `adaptive-current-24`), with 60 runs by default. Its schema-v1 JSON/CSV evidence preserves completed-but-unstable, timeout, and error outcomes; any candidate envelope is built only from stable completed `fixed-24` observations and must be manually reviewed.
   - The reviewed pre-fix default 60-run evidence had 40 stable and 20 completed-but-unstable runs. All four variants were stable in the low-load and transition cases, while all five repetitions of every variant in the pressure case had zero predicted/active conflicts but one deadline miss and two failures; the candidate envelope was therefore correctly unavailable rather than backfilled from ineligible evidence.
   - The repaired default 60-run evidence at `output/adaptive-replan-calibration/20260726T150547Z` has 60 completed stable runs, 1,330 real-replan observations, 12 summaries, pressure cumulative distance `[671, 671]`, and a non-null same-machine candidate envelope. It did not modify the production policy.
   - Current outputs serve regression and engineering diagnosis. Do not start chart, table, report, or defense-material production until the user confirms the applicable competition requirements.

9. Competition materials and demo package - 延后/可选
   - Environment notes, algorithm notes, README, and a fixed demo flow exist.
   - Project description, technical report, PPT, demo script, screen recording, diagrams, experiment charts, and backup scenarios are not current work. Reassess only after the user explicitly confirms participation and requirements.

10. Demo presentation and 3D visualization - 延后/可选
   - Treat 3D visualization as a later phase.
   - The fixed 2D demo flow and documentation exist, but Three.js or another 3D rendering layer has not been added.
   - Do not start this unless the user explicitly prioritizes presentation after participation requirements are known.

## Future Presentation Goal

If later participation or presentation requirements justify it, the final demo may become a 3D real-time visualization. This is not part of the current roadmap.

Desired direction for that later phase:

- 3D warehouse/campus-style scene.
- Real-time robot movement based on backend paths and online session ticks.
- Interactive camera controls: drag/rotate view and zoom in/out.
- Clear visualization of tasks, blocked cells, paths, conflicts, failures, and replanning events.
- Prefer Three.js or another proven 3D rendering approach when this phase starts.
- Keep this as a later presentation phase; do not implement it without explicit user approval.

## Repository Structure

- `frontend/`
  - React + TypeScript + Vite frontend.
  - Main UI entry: `frontend/src/main.tsx`
  - Scenario data: `frontend/src/domain/scenarios.json`, exported through `frontend/src/domain/scenarios.ts`
  - Shared frontend types: `frontend/src/domain/types.ts`
  - View-only helpers: `frontend/src/domain/view.ts`
- `backend/`
  - FastAPI backend.
  - API entry: `backend/app/main.py`
  - Dispatch algorithm: `backend/app/dispatch.py`
  - Pydantic schemas: `backend/app/schemas.py`
  - Tests: `backend/tests/`
- `scripts/`
  - Windows PowerShell helper scripts for environment, start, stop, and checks.
- `docs/`
  - Environment notes.

## Runtime Environment

Use the real local Node managed by nvm-windows:

```text
C:\nvm4w\nodejs\node.exe
C:\nvm4w\nodejs\npm.cmd
```

Do not rely on plain `node` before loading the environment. Plain `node` may resolve to the Codex app bundled Node path and has previously failed with access-denied behavior.

Use project-local PowerShell 7 for PowerShell commands:

```text
D:\codex\summer\.tools\powershell\pwsh.exe
```

Do not rely on Windows PowerShell 5.1 for project scripts unless PowerShell 7 is unavailable. The npm `dev`, `dev:no-browser`, `dev:stop`, and `backend:dev` scripts call this project-local `pwsh.exe`.

Known working versions:

```text
PowerShell 7.6.2
Node v20.20.2
npm 10.8.2
nvm-windows 1.2.2
Python 3.13.2
```

Backend virtual environment:

```text
.venv\
```

## Development Commands

Start the full project:

```powershell
.\scripts\start-dev.ps1
```

Start without opening a browser:

```powershell
.\scripts\start-dev.ps1 -NoBrowser
```

Stop development services:

```powershell
.\scripts\stop-dev.ps1
```

Run all checks:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

This script runs the same three checks as `npm run check` in the same order:

```powershell
.\scripts\test-all.ps1
```

Read source snippets that contain Chinese strings with explicit UTF-8 decoding:

```powershell
Get-Content -Path backend\app\sessions.py -Encoding UTF8 | Select-Object -Skip 70 -First 40
.\scripts\show-source.ps1 -Path backend\app\sessions.py -Skip 70 -First 40
```

Windows PowerShell 5.1 may misread UTF-8 files without BOM when plain `Get-Content` is used. Use `Get-Content -Encoding UTF8` or `scripts\show-source.ps1` before editing Chinese string literals or tests that assert Chinese text. Do not copy Chinese text from garbled PowerShell output into `apply_patch`; if terminal output shows mojibake such as `鎵` or `浠`, re-read the file with explicit UTF-8 decoding and patch against the actual UTF-8 text. Project scripts dot-source `scripts\env.ps1`, which sets UTF-8 console/Python output and makes `Get-Content` default to UTF-8 inside that scripted environment.

Frontend build only:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

Backend tests only:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
```

## Service Ports

- Frontend dev server: `http://127.0.0.1:5174`
- Backend API: `http://127.0.0.1:8011`
- Health check: `http://127.0.0.1:8011/health`
- Main dispatch API: `POST http://127.0.0.1:8011/api/dispatch`

Ports `8000` and `8001` had abnormal stale listeners during development. Keep the project on `8011` unless the user explicitly asks to change it.

## API Contract

The frontend can create online dispatch sessions through:

```text
POST /api/sessions
GET /api/sessions
GET /api/sessions/{session_id}
DELETE /api/sessions/{session_id}
POST /api/sessions/{session_id}/reset
POST /api/sessions/{session_id}/tasks
POST /api/sessions/{session_id}/tick
POST /api/sessions/{session_id}/blocked-cells
POST /api/sessions/{session_id}/blocked-cells/remove
POST /api/sessions/{session_id}/failed-robots
POST /api/sessions/{session_id}/failed-robots/restore
```

The older batch dispatch API is kept for baseline checks:

```text
POST /api/dispatch
```

The backend returns:

- `assignments`
- `paths`
- `conflicts`
- `metrics`
- `failureReasons`
- `failureDetails`
- `eventLog`
- `tasks`
- `shelfStates`
- dynamic replanning metadata

Schema definitions live in:

```text
backend/app/schemas.py
frontend/src/domain/types.ts
```

Keep these two files aligned when changing request or response shapes. Do not guess field names; read both files before modifying API code.

## Code Style

General:

- Keep changes scoped. Do not reintroduce the deleted static prototype.
- Prefer existing project patterns over new abstractions.
- Use clear, explicit names for algorithm concepts: `assignments`, `paths`, `conflicts`, `metrics`, `eventLog`.
- Preserve camelCase API fields because the frontend consumes them directly.

Frontend:

- Use React function components and hooks.
- Keep algorithm computation out of the frontend. The frontend may keep view helpers only.
- Scenario data lives in `frontend/src/domain/scenarios.json`, with typed exports in `frontend/src/domain/scenarios.ts`.
- UI text is Chinese.
- Keep the operational dashboard style: dense, clear, and utilitarian.

Backend:

- Use FastAPI and Pydantic models.
- Keep dispatch logic in `backend/app/dispatch.py`.
- Keep API schemas in `backend/app/schemas.py`.
- Add or update pytest coverage when changing algorithms or API behavior.

## Testing Expectations

Before reporting implementation complete, run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Latest full verification snapshot on 2026-07-27: frontend production build passed, frontend tests `105/105`, backend tests `523/523`.

For frontend behavior changes, also verify in a browser when practical:

- map cells render
- scenario switching works
- strategy switching works
- play/pause works
- frontend creates online sessions and calls the session APIs
- backend status shows online when the backend is running

## Important Notes

- The repository uses Git. Work on feature or fix branches, run the relevant checks, then merge reviewed changes into `main`.
- `frontend/node_modules`, `frontend/dist`, `.venv`, and Python caches are generated artifacts and should not be treated as source.
- `scripts/start-dev.ps1` starts both frontend and backend and opens the frontend URL.
- `Ctrl+C` in the startup terminal should stop both services.
- If a dev service is left running, use `.\scripts\stop-dev.ps1`.
