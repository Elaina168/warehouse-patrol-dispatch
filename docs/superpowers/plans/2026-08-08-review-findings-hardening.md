# Review Findings Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-08-08 全量复审确认的输入边界、调度语义、实验上限、报告事务、开发进程、依赖和文档问题。

**Architecture:** 在现有 FastAPI/Pydantic 边界增加统一请求和原始字段限制；把内置演示的延迟规划改为显式会话选项；对两个报告发布器和 PowerShell 启动器补取消安全事务。保留现有前后端结构和响应模型，只做可由失败回归证明的最小兼容修改。

**Tech Stack:** Python 3.13、FastAPI、Pydantic 2、pytest、React 19、TypeScript、Vitest、PowerShell 7、npm。

## Global Constraints

- HTTP 请求体最大 `2_097_152` 字节。
- 标识符最大 `128` 字符；名称、标题和实验标签最大 `256` 字符；描述最大 `4096` 字符。
- JavaScript 安全整数上限精确为 `9_007_199_254_740_991`。
- `MAX_SESSION_CURRENT_TIME` 保持 `10_000`。
- 内置场景延迟初始规划；导入和普通 API 场景默认立即规划。
- 任务无关字段为 `null` 时兼容，非空时拒绝。
- 不修改 `output/`，不增加认证、持久化、展示面板或公网部署行为。
- 所有源码、测试和文档保持 UTF-8；代码注释使用中文。

---

### Task 1: 请求体、字符串、整数和 currentTime 契约

**Files:**
- Create: `backend/app/request_limits.py`
- Create: `backend/tests/test_request_limits.py`
- Modify: `backend/app/limits.py`
- Modify: `backend/app/schemas.py:5-25,46-120,191-243`
- Modify: `backend/app/main.py:47-63`
- Modify: `backend/tests/test_schema_constraints.py`
- Modify: `backend/tests/test_api_contract.py`
- Modify: `frontend/src/domain/scenarioImport.ts:4-11,156-216`
- Modify: `frontend/src/domain/scenarioImport.test.ts`

**Interfaces:**
- Produces: `MAX_REQUEST_BODY_BYTES`, `MAX_IDENTIFIER_LENGTH`, `MAX_DISPLAY_NAME_LENGTH`, `MAX_DESCRIPTION_LENGTH`, `MAX_SAFE_INTEGER` in `backend.app.limits`.
- Produces: `RequestBodyLimitMiddleware(app, max_body_size: int)`.
- Produces: Pydantic aliases `IdentifierStr`, `DisplayNameStr`, `DescriptionStr` and bounded integer schemas.

- [ ] **Step 1: Read the test-quality rules**

Read `superpowers/test-driven-development/writing-good-tests.md` completely before editing any test.

- [ ] **Step 2: Write failing ASGI request-limit tests**

Create direct ASGI tests that do not depend on Uvicorn chunking behavior:

```python
def test_request_body_limit_accepts_exact_boundary() -> None:
    received: list[bytes] = []
    sent = asyncio.run(_invoke_middleware([_request(b"a" * MAX_REQUEST_BODY_BYTES)], received))
    assert received == [b"a" * MAX_REQUEST_BODY_BYTES]
    assert _response_status(sent) == 204


def test_request_body_limit_rejects_content_length_over_boundary_without_reading() -> None:
    sent = asyncio.run(_invoke_middleware([], [], content_length=MAX_REQUEST_BODY_BYTES + 1))
    assert _response_status(sent) == 413


def test_request_body_limit_rejects_chunked_body_over_boundary() -> None:
    sent = asyncio.run(_invoke_middleware([
        _request(b"a" * MAX_REQUEST_BODY_BYTES, more_body=True),
        _request(b"b"),
    ], []))
    assert _response_status(sent) == 413
```

- [ ] **Step 3: Write failing schema and contract tests**

Add parameterized tests asserting maximum and over-limit values for every reusable string class, plus:

```python
def test_javascript_safe_integer_boundary() -> None:
    payload = scenario_payload()
    payload["robots"][0]["battery"] = MAX_SAFE_INTEGER
    payload["robots"][0]["batteryCapacity"] = MAX_SAFE_INTEGER
    assert schemas.Scenario.model_validate(payload).robots[0].battery == MAX_SAFE_INTEGER
    payload["robots"][0]["battery"] = MAX_SAFE_INTEGER + 1
    with pytest.raises(ValidationError):
        schemas.Scenario.model_validate(payload)


def test_runtime_current_time_openapi_maximum_matches_session_limit() -> None:
    for model in (
        schemas.SessionTickRequest,
        schemas.AddBlockRequest,
        schemas.RemoveBlockRequest,
        schemas.FailRobotRequest,
        schemas.RestoreRobotRequest,
    ):
        assert model.model_json_schema()["properties"]["currentTime"]["maximum"] == MAX_SESSION_CURRENT_TIME
```

Extend the frontend import test to accept `9_007_199_254_740_991` and reject `9_007_199_254_740_992`. Extend the API contract test so `_frontend_numeric_constant("MAX_SAFE_INTEGER", FRONTEND_SCENARIO_IMPORT)` equals the backend constant.

- [ ] **Step 4: Run RED tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_request_limits.py backend/tests/test_schema_constraints.py backend/tests/test_api_contract.py
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
```

Expected: request middleware imports are missing; current string/integer schemas accept over-limit values; runtime OpenAPI schemas have no maximum; frontend constant is missing.

- [ ] **Step 5: Implement approved limits and middleware**

Add exact constants:

```python
MAX_REQUEST_BODY_BYTES = 2_097_152
MAX_IDENTIFIER_LENGTH = 128
MAX_DISPLAY_NAME_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 4096
MAX_SAFE_INTEGER = 9_007_199_254_740_991
```

Implement `RequestBodyLimitMiddleware` as pure ASGI. It checks `Content-Length`, buffers `http.request` messages only up to the maximum, returns `PlainTextResponse(status_code=413)` on overflow, and replays one complete request message to the downstream app. Register it before the existing CORS middleware so CORS stays outermost.

Update schemas exactly:

```python
IdentifierStr = Annotated[str, Field(max_length=MAX_IDENTIFIER_LENGTH)]
DisplayNameStr = Annotated[str, Field(max_length=MAX_DISPLAY_NAME_LENGTH)]
DescriptionStr = Annotated[str, Field(max_length=MAX_DESCRIPTION_LENGTH)]
NonNegativeInt = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
PositiveInt = Annotated[int, Field(gt=0, le=MAX_SAFE_INTEGER)]
```

Use `SessionTimeInt` for all five runtime request `currentTime` fields. Export `MAX_SAFE_INTEGER = 9_007_199_254_740_991` in `scenarioImport.ts` and replace unbounded numeric maxima with it where no smaller approved maximum exists.

- [ ] **Step 6: Run GREEN tests**

Run the commands from Step 4. Expected: all selected backend and frontend tests pass.

- [ ] **Step 7: Commit Task 1**

```powershell
git add backend/app/request_limits.py backend/app/limits.py backend/app/schemas.py backend/app/main.py backend/tests/test_request_limits.py backend/tests/test_schema_constraints.py backend/tests/test_api_contract.py frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts
git commit -m "fix: bound request primitives"
```

---

### Task 2: 任务判别字段和实验总案例上限

**Files:**
- Modify: `backend/app/schemas.py:46-59,175-188`
- Modify: `backend/tests/test_schema_constraints.py`
- Modify: `backend/tests/test_experiments.py`
- Modify: `frontend/src/domain/scenarioImport.ts:156-187`
- Modify: `frontend/src/domain/scenarioImport.test.ts`

**Interfaces:**
- Produces: `Task.validate_task_shape() -> Task`.
- Produces: `ReplanWindowExperimentRequest.validate_total_case_count() -> ReplanWindowExperimentRequest` integrated with the existing unique-window validator.

- [ ] **Step 1: Write failing backend task-shape tests**

Add one accepted payload and one rejected non-null irrelevant field for each task type. Explicit `None` stays accepted:

```python
@pytest.mark.parametrize(
    ("payload", "irrelevant_field"),
    [
        ({"id": "I", "type": "inspection", "title": "I", "priority": 1, "targets": [[0, 0]], "target": [1, 0]}, "target"),
        ({"id": "D", "type": "delivery", "title": "D", "priority": 1, "pickup": [0, 0], "dropoff": [1, 0], "demand": 1, "targets": [[1, 0]]}, "targets"),
        ({"id": "E", "type": "emergency", "title": "E", "priority": 1, "target": [1, 0], "pickup": [0, 0]}, "pickup"),
    ],
)
def test_task_rejects_non_null_irrelevant_variant_fields(payload, irrelevant_field) -> None:
    with pytest.raises(ValidationError, match=irrelevant_field):
        schemas.Task.model_validate(payload)
```

Add missing required-field tests and an API regression proving a formerly accepted inspection `target` returns 422.

- [ ] **Step 2: Write failing frontend and experiment tests**

Add frontend import variants matching the backend rejection rules. Add:

```python
def test_replan_window_total_case_limit_counts_adaptive_case() -> None:
    payload = {"scenario": scenario_payload(), "windows": list(range(32)), "includeAdaptive": True}
    with pytest.raises(ValidationError):
        ReplanWindowExperimentRequest.model_validate(payload)
    payload["windows"] = list(range(31))
    assert len(ReplanWindowExperimentRequest.model_validate(payload).windows) == 31
```

- [ ] **Step 3: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_schema_constraints.py backend/tests/test_experiments.py -k "task_shape or irrelevant or total_case_limit"
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
```

Expected: current models accept irrelevant non-null fields and 32 windows plus adaptive.

- [ ] **Step 4: Implement minimal validators**

Add a `Task` after-validator that requires the type's own fields and rejects only non-null irrelevant fields. Keep explicit null values valid. Extend the replan request after-validator with:

```python
if len(self.windows) + int(self.includeAdaptive) > MAX_EXPERIMENT_CASES:
    raise ValueError(f"total experiment case count must be <= {MAX_EXPERIMENT_CASES}")
```

Mirror the task field rules in `isTask` using exact key values; do not remove nullable response fields from TypeScript types.

- [ ] **Step 5: Run GREEN tests and commit**

Run Step 3, then:

```powershell
git add backend/app/schemas.py backend/tests/test_schema_constraints.py backend/tests/test_experiments.py frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts
git commit -m "fix: enforce request shape limits"
```

---

### Task 3: 显式延迟初始规划

**Files:**
- Modify: `backend/app/schemas.py:213-216`
- Modify: `backend/app/sessions.py:95-140,205-240,1029-1038`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/tests/test_api_contract.py`
- Modify: `frontend/src/domain/types.ts:253-256`
- Modify: `frontend/src/main.tsx:193-230,385-391`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Produces: `CreateSessionRequest.delayInitialPlanning: bool = False`.
- Produces: `DispatchSession.delay_initial_planning: bool = False`.

- [ ] **Step 1: Write failing backend regressions**

Create two sessions from the same non-demo payload whose ID is `integrated-demo`:

```python
def test_integrated_demo_id_does_not_implicitly_delay_planning() -> None:
    scenario = scenario_payload()
    scenario["id"] = "integrated-demo"
    response = TestClient(app).post("/api/sessions", json={"scenario": scenario})
    assert response.status_code == 200
    assert len(response.json()["result"]["assignments"]) == 2


def test_explicit_delay_initial_planning_preserves_idle_creation_and_reset() -> None:
    response = TestClient(app).post(
        "/api/sessions",
        json={"scenario": scenario_payload(), "delayInitialPlanning": True},
    )
    assert response.status_code == 200
    assert response.json()["result"]["assignments"] == []
```

Continue the second test through one tick and reset to prove the saved flag is reused.

- [ ] **Step 2: Write failing frontend contract tests**

Assert `CreateSessionRequest` contains optional `delayInitialPlanning`. In `main.test.ts`, inspect the session POST body and assert the built-in scenario sends `true`; after importing a scenario with ID `integrated-demo`, assert it sends `false`.

- [ ] **Step 3: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py backend/tests/test_api_contract.py -k "delay_initial or integrated_demo_id"
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main
```

Expected: backend still branches on ID and request/frontend fields do not exist.

- [ ] **Step 4: Implement the explicit flag**

Add the optional request field, persist it in `DispatchSession`, initialize it in `create_session`, and change:

```python
def _should_delay_initial_planning(session: DispatchSession) -> bool:
    return session.delay_initial_planning
```

Add `delayInitialPlanning?: boolean` to the frontend type and send `delayInitialPlanning: importedScenario === null` in the session creation body.

- [ ] **Step 5: Run GREEN tests and commit**

Run Step 3, then:

```powershell
git add backend/app/schemas.py backend/app/sessions.py backend/tests/test_sessions.py backend/tests/test_api_contract.py frontend/src/domain/types.ts frontend/src/main.tsx frontend/src/main.test.ts
git commit -m "fix: make initial planning delay explicit"
```

---

### Task 4: 算法报告发布取消安全

**Files:**
- Modify: `backend/benchmarks/reporting.py:192-313`
- Modify: `backend/tests/test_algorithm_benchmark.py:740-1120`

**Interfaces:**
- Produces: `_reconcile_final_bundle_state(...) -> tuple[set[Path], set[Path]]` local to the algorithm reporter.

- [ ] **Step 1: Write failing cancellation tests**

Using the existing report fixtures, create an old complete bundle and a partial report. Monkeypatch `Path.replace` or `Path.unlink` so the real filesystem operation succeeds and then raises `KeyboardInterrupt` at each boundary:

```python
@pytest.mark.parametrize("stage", ["backup", "publish", "partial-delete"])
def test_algorithm_final_report_cancellation_restores_existing_bundle(tmp_path, monkeypatch, stage) -> None:
    old_contents = _write_existing_algorithm_bundle(tmp_path)
    _install_interrupt_after_filesystem_change(monkeypatch, tmp_path, stage)
    with pytest.raises(KeyboardInterrupt):
        write_final_report(tmp_path, _algorithm_report())
    assert _read_algorithm_bundle(tmp_path) == old_contents
    assert not list(tmp_path.glob(".*.tmp"))
```

Assert recoverable `.backup` files remain if an injected rollback restore failure occurs.

- [ ] **Step 2: Run RED test**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py -k "final_report_cancellation"
```

Expected: cancellation bypasses rollback and the old bundle assertion fails.

- [ ] **Step 3: Implement cancellation-safe commit**

Keep staging unchanged. Wrap only the backup/publish/partial-delete commit phase in `except BaseException`. Track `active_path` and `publication_started`. Before rollback, reconcile:

```python
backed_up_targets.update(
    target for target in target_paths if backup_paths[target].exists()
)
if publication_started:
    published_targets.update(
        target
        for target in target_paths
        if target.exists() and not temporary_paths[target].exists()
    )
```

Call `_rollback_final_bundle` before transaction cleanup. Existing ordinary exceptions continue through `_raise_write_error(active_path, exc, rollback_error=...)`; non-`Exception` cancellations are re-raised unchanged after rollback.

- [ ] **Step 4: Run GREEN tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py
git add backend/benchmarks/reporting.py backend/tests/test_algorithm_benchmark.py
git commit -m "fix: roll back canceled algorithm reports"
```

---

### Task 5: 自适应报告发布取消安全

**Files:**
- Modify: `backend/benchmarks/adaptive_reporting.py:271-402`
- Modify: `backend/tests/test_adaptive_replan_calibration.py:1280-1660`

**Interfaces:**
- Produces: the same cancellation-safe publication semantics as Task 4 for the four-file adaptive bundle.

- [ ] **Step 1: Write and run failing adaptive cancellation tests**

Add `backup`、`publish`、`partial-delete` parameterized tests using the adaptive report fixtures. Each test performs the actual filesystem mutation before raising `KeyboardInterrupt`, then asserts all four old files are restored, partial evidence is not falsely finalized, and transaction files follow the backup-preservation rule.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_adaptive_replan_calibration.py -k "final_report_cancellation"
```

Expected: at least one bundle-boundary assertion fails because current code catches only `Exception`.

- [ ] **Step 2: Implement the verified Task 4 transaction pattern explicitly**

Apply the same `active_path`、`publication_started`、filesystem reconciliation、rollback-before-cleanup and cancellation re-raise logic to the adaptive reporter, using its exact four target paths. Do not import test helpers or change report formats.

- [ ] **Step 3: Run GREEN tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_adaptive_replan_calibration.py
git add backend/benchmarks/adaptive_reporting.py backend/tests/test_adaptive_replan_calibration.py
git commit -m "fix: roll back canceled adaptive reports"
```

---

### Task 6: 新开发进程立即登记所有权

**Files:**
- Modify: `scripts/start-dev.ps1:65-85,171-219`
- Modify: `backend/tests/test_dev_scripts.py`

**Interfaces:**
- Consumes: existing `Add-ManagedProcessOwnership` and `Stop-StartedProcesses` exact-process cleanup.
- Produces: optional test hook `BeginManagedProcessOutputRead`, invoked only after ownership registration.

- [ ] **Step 1: Write failing behavioral regression**

Extend the existing PowerShell harness with a fake process returned by `StartManagedProcess`, a manifest hook, and:

```powershell
BeginManagedProcessOutputRead = {
  param($process)
  throw 'stream initialization failed'
}
```

Invoke startup, assert it exits nonzero, assert `RollbackStartedProcess` or `StopOwnedProcess` received the same fake process ID, and assert the final manifest contains `"processes": []`.

- [ ] **Step 2: Run RED test**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py -k "output_read_failure or ownership_before_output"
```

Expected: no such hook is invoked and the regression cannot observe cleanup after the target failure point.

- [ ] **Step 3: Refactor acquisition order minimally**

Both real and test-hook branches must acquire a process object, call `Add-ManagedProcessOwnership`, then initialize output readers. Real readers remain:

```powershell
$process.BeginOutputReadLine()
$process.BeginErrorReadLine()
```

When `BeginManagedProcessOutputRead` exists, call it instead. Any thrown error reaches the existing outer `finally`, where the process is already in `$startedProcesses` and manifest ownership.

- [ ] **Step 4: Run GREEN tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py
git add scripts/start-dev.ps1 backend/tests/test_dev_scripts.py
git commit -m "fix: register dev processes before stream setup"
```

---

### Task 7: 删除自引用依赖并修正文档语义

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `package.json:6`
- Modify: `backend/tests/test_dependency_lock.py`
- Modify: `AGENTS.md:44`

**Interfaces:**
- Produces: frontend lock graph without package `warehouse-patrol-dispatch` or `file:..` dependency.

- [ ] **Step 1: Write failing dependency regression**

```python
def test_frontend_has_no_root_package_self_dependency() -> None:
    package = json.loads((PROJECT_ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((PROJECT_ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert "warehouse-patrol-dispatch" not in package["dependencies"]
    assert "warehouse-patrol-dispatch" not in lock["packages"][""]["dependencies"]
    assert "node_modules/warehouse-patrol-dispatch" not in lock["packages"]
    assert ".." not in lock["packages"]
```

- [ ] **Step 2: Run RED test**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dependency_lock.py -k "self_dependency"
```

Expected: all four assertions expose current self-reference entries.

- [ ] **Step 3: Remove self-reference and update stable wording**

Remove only the unused self-dependency and exact lockfile nodes; do not upgrade unrelated packages. Change the root description to:

```json
"description": "Online multi-robot task-flow dispatch system for warehouse logistics and patrol scenarios."
```

Replace the old `AGENTS.md` dynamic-validation statement with the exact current behavior: definition-level validation checks the fixed map and static robot eligibility; dynamic blocked cells and failed robots, including at `triggerTime = 0`, are runtime-recoverable conditions used by dispatch failure classification.

- [ ] **Step 4: Run GREEN tests, lock dry-run, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dependency_lock.py
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend ci --dry-run --ignore-scripts --offline
git add frontend/package.json frontend/package-lock.json package.json backend/tests/test_dependency_lock.py AGENTS.md
git commit -m "build: remove frontend self dependency"
```

---

### Task 8: 全量验证和当前文档快照

**Files:**
- Modify after verification: `README.md`
- Modify after verification: `AGENTS.md`
- Modify after verification: `docs/baseline.md`
- Modify after verification: `docs/demo.md`
- Modify after verification: `docs/environment.md`
- Modify after verification: `docs/testing-guide.md`

**Interfaces:**
- Consumes: completed Tasks 1-7 and their focused regressions.
- Produces: one verified current snapshot using actual final test counts.

- [ ] **Step 1: Run complete verification**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
.\.venv\Scripts\python.exe -m pip check
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend audit --registry=https://registry.npmjs.org
git diff --check
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1 -ValidateOnly
```

Require exit code 0 from every command. Record the exact frontend and backend totals from `npm run check`; do not predict them.

- [ ] **Step 2: Verify no process residue**

```powershell
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in @(5174, 8011) } |
  Select-Object LocalAddress, LocalPort, OwningProcess
Get-Content -LiteralPath '.runtime\dev-processes.json' -Encoding UTF8
```

Expected: no listener rows and manifest exactly contains version 1 with an empty `processes` array.

- [ ] **Step 3: Refresh only current documentation snapshots**

Use the actual current date `2026-08-08` and exact counts from Step 1 in every current-state section. Update `105/105`、`523/523`、`154/154`、`616/616` occurrences only in current top-level docs; do not edit dated history under `docs/superpowers/`.

- [ ] **Step 4: Re-run documentation and focused integrity checks**

```powershell
rg -n --encoding UTF-8 "105/105|523/523|154/154|616/616|Competition prototype|warehouse-patrol-dispatch.*file:\.\." README.md AGENTS.md docs package.json frontend/package.json frontend/package-lock.json
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_api_contract.py backend/tests/test_dependency_lock.py
git diff --check
```

Expected: no stale current snapshot or self-dependency match outside dated historical records; selected tests pass.

- [ ] **Step 5: Commit documentation evidence**

```powershell
git add README.md AGENTS.md docs/baseline.md docs/demo.md docs/environment.md docs/testing-guide.md
git commit -m "docs: refresh hardening verification"
```

- [ ] **Step 6: Final branch audit**

```powershell
git status --short --branch
git log --oneline --decorate -10
```

Expected: only the pre-existing untracked `output/` remains; the branch contains the design, plan, focused fixes, and refreshed evidence commits.
