# 开发进程孤儿清理与安全整数导入修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 manifest 在根进程退出后仍能按精确身份清理已记录后代，并让场景导入拒绝 JavaScript 非安全整数。

**Architecture:** 在现有 version 1 manifest 中把已验证的根和后代分别作为精确身份条目持久化；`start-dev.ps1` 持有原始 `Process` 对象并在启动、就绪等待和主循环中同步进程树。前端继续复用单一整数范围函数，只把整数判定升级为安全整数判定。

**Tech Stack:** PowerShell 7.6、Windows CIM/`System.Diagnostics.Process`、Python pytest、React/TypeScript/Vitest。

## Global Constraints

- 不使用端口扫描、进程名、命令行或路径猜测进程所有权。
- 只有 PID 与 `startedAtUtc` 同时匹配的进程才能被终止。
- 所有读写保持 UTF-8，PowerShell 源码注释使用中文。
- 不修改调度算法、API 路由、端口策略或未跟踪的 `output/`。
- 不提交、不合并、不推送。

---

### Task 1: 用失败回归固定根退出后的进程树行为

**Files:**
- Modify: `backend/tests/test_dev_scripts.py:360-523`
- Test: `backend/tests/test_dev_scripts.py`

**Interfaces:**
- Consumes: `Get-DevProcessDescendants -RootProcess <Process>`、计划新增的 `Sync-DevProcessManifestTree -Role <string> -RootProcess <Process> -ManifestPath <string>`。
- Produces: 两个真实 Windows 进程回归，分别覆盖 CIM 根行消失和持久化后代清理。

- [x] **Step 1: 写入 CIM 根行消失的失败测试**

  新测试在同一个 PowerShell 进程中启动一个根进程，根进程创建长寿命子进程并写出 PID；测试先取得根 `Process.SafeHandle`，等待根退出，再调用 `Get-DevProcessDescendants`。断言返回的进程 ID 包含仍存活的子进程，并在 `finally` 中按精确测试 PID 清理。

- [x] **Step 2: 运行测试并确认 RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py -k "root_exits_before_descendant_capture"
  ```

  Expected: FAIL，返回后代数量为 `0`，而测试子进程仍存活。

- [x] **Step 3: 写入 manifest 已同步后根退出的失败测试**

  新测试启动根与长寿命子进程，在根仍存活时调用：

  ```powershell
  Sync-DevProcessManifestTree `
    -Role 'test-tree' `
    -RootProcess $root `
    -ManifestPath $manifestPath
  ```

  随后仅终止根进程，再调用 `Stop-RecordedProcessTree`。断言子进程不存在且 manifest 没有剩余条目；`finally` 仍执行兜底清理。

- [x] **Step 4: 运行测试并确认 RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py -k "synced_descendant_after_root_exit"
  ```

  Expected: FAIL，因为 `Sync-DevProcessManifestTree` 尚不存在。

---

### Task 2: 持久化并刷新精确进程树身份

**Files:**
- Modify: `scripts/dev-process-manifest.ps1:201-313`
- Modify: `scripts/start-dev.ps1:43-87, 190-307`
- Test: `backend/tests/test_dev_scripts.py`

**Interfaces:**
- Consumes: `Get-DevProcessDescendantRows`、`Get-MatchingDevProcess`、manifest 互斥锁及原始 `System.Diagnostics.Process` 对象。
- Produces: `Sync-DevProcessManifestTree -Role <string> -RootProcess <System.Diagnostics.Process> -ManifestPath <string>`；幂等写入根和后代的 `role`、`pid`、`startedAtUtc`。

- [x] **Step 1: 修复根 CIM 行缺失时的后代捕获**

  在 `Get-DevProcessDescendants` 中保留现有根行存在时的启动时间交叉检查；根行不存在时使用已经从持有句柄读取的 `$rootStartTimeUtc` 作为 `Get-DevProcessDescendantRows -RootCreationDate`，不再返回空数组。候选后代仍逐个取得 `SafeHandle` 并比对 CIM `CreationDate`。

- [x] **Step 2: 实现进程树同步函数**

  `Sync-DevProcessManifestTree` 在 manifest 互斥锁内：

  1. 固定 `$RootProcess.SafeHandle`；
  2. 捕获根及当前后代；
  3. 为每个进程生成精确身份条目；
  4. 以 `role + pid + startedAtUtc` 幂等合并，保留先前已记录但当前快照未出现的条目；
  5. 原子写回 manifest；
  6. 在 `finally` 中只关闭本函数捕获的后代句柄，不关闭调用方持有的根对象。

- [x] **Step 3: 让启动流程周期性同步所有权**

  `start-dev.ps1` 保留现有 `$startedProcesses`，另存每个 `{ role, process }` 所有权记录。真实 `System.Diagnostics.Process` 使用 `Sync-DevProcessManifestTree`；现有测试钩子的非 Process 假对象继续通过 `Add-DevProcessManifestEntry` 登记 PID。

  新增 `Sync-StartedProcessOwnership`，并在以下位置调用：

  - `Add-ManagedProcessOwnership` 初次登记；
  - `Wait-ForService` 每轮检查进程退出之前；
  - 两个服务就绪后；
  - 主监控循环每轮检查退出之前。

  登记或同步失败继续使用现有 `Stop-NewlyStartedProcessTree` 回滚原始进程对象。

- [x] **Step 4: 运行两个新回归并确认 GREEN**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py -k "root_exits_before_descendant_capture or synced_descendant_after_root_exit"
  ```

  Expected: 2 passed，且测试结束后无存活测试子进程。

- [x] **Step 5: 运行全部开发脚本测试**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py
  ```

  Expected: 全部通过，无超时、警告或进程残留。

---

### Task 3: 拒绝 JavaScript 非安全整数

**Files:**
- Modify: `frontend/src/domain/scenarioImport.test.ts:120-150`
- Modify: `frontend/src/domain/scenarioImport.ts:212-216`
- Test: `frontend/src/domain/scenarioImport.test.ts`

**Interfaces:**
- Consumes: `parseScenario(value: unknown): Scenario` 和 `isIntegerInRange`。
- Produces: 所有通过 `isIntegerInRange` 校验的数值必须满足 `Number.isSafeInteger`。

- [x] **Step 1: 写入安全整数失败回归**

  在现有 `describe("scenario import contract")` 中加入：

  ```ts
  it.each<[string, (scenario: Scenario) => void]>([
    ["robot battery", (scenario) => {
      const unsafeInteger = Number.MAX_SAFE_INTEGER + 1;
      scenario.robots[0].battery = unsafeInteger;
      scenario.robots[0].batteryCapacity = unsafeInteger;
    }],
    ["task deadline", (scenario) => {
      scenario.tasks[0].deadline = Number.MAX_SAFE_INTEGER + 1;
    }]
  ])("rejects unsafe integer in %s", (_, mutate) => {
    const scenario = buildScenario();
    mutate(scenario);

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });
  ```

  两个字段都走同一个 `isIntegerInRange`，避免为单个字段编写特例。

- [x] **Step 2: 运行测试并确认 RED**

  Run:

  ```powershell
  & 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
  ```

  Expected: 新增用例 FAIL，因为 `Number.isInteger(Number.MAX_SAFE_INTEGER + 1)` 为 true。

- [x] **Step 3: 实现最小安全整数修复**

  将：

  ```ts
  Number.isInteger(value)
  ```

  改为：

  ```ts
  Number.isSafeInteger(value)
  ```

  不增加新的字段特例或业务上限。

- [x] **Step 4: 运行场景导入测试并确认 GREEN**

  Run:

  ```powershell
  & 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
  ```

  Expected: 场景导入测试全部通过。

---

### Task 4: 完整验证与工作树审计

**Files:**
- Verify only: all modified files

**Interfaces:**
- Consumes: Tasks 1-3 的实现和测试。
- Produces: 可复核的构建、测试、依赖与工作树证据。

- [x] **Step 1: 运行完整检查**

  Run:

  ```powershell
  & 'C:\nvm4w\nodejs\npm.cmd' run check
  ```

  Expected: 前端生产构建、全部前端测试和全部后端测试通过。

- [x] **Step 2: 检查 Python 依赖**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```

  Expected: `No broken requirements found.`

- [x] **Step 3: 检查补丁格式和范围**

  Run:

  ```powershell
  git diff --check
  git status --short
  git diff -- scripts/dev-process-manifest.ps1 scripts/start-dev.ps1 backend/tests/test_dev_scripts.py frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts docs/superpowers/specs/2026-08-01-process-orphan-safe-integer-fixes-design.md docs/superpowers/plans/2026-08-01-process-orphan-safe-integer-fixes.md
  ```

  Expected: 无空白错误；改动仅包含两个修复、回归测试及两份确认文档；`output/` 仍只是原有未跟踪目录。

- [x] **Step 4: 确认临时进程与文件均已清理**

  检查测试记录的 PID 均不存在，系统临时目录中没有本次测试前缀目录；不得按端口或进程名批量清理。
