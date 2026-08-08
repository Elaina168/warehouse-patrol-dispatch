# 全量审查运行问题修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复八个已确认的运行、输入、报告和实验问题，同时不改变地图交互与告警视觉设计。

**Architecture:** 保留现有前后端分层和在线会话模型。算法指标在现有路径工具层修正；输入约束在 Pydantic 与前端导入边界同步；场景导入通过候选会话提交避免破坏活动视图；报告事务和实验入口分别在各自边界完成最小修复。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、pytest、React 18、TypeScript、Vitest、PowerShell 7.6.2。

## Global Constraints

- 项目唯一受支持的 PowerShell 主版本为 PowerShell 7；官方命令统一调用项目内 `.\.tools\powershell\pwsh.exe`。
- Windows PowerShell 5.1 不属于兼容范围，必须在任何进程或清单副作用前失败并给出明确版本错误。
- 每项生产行为修改必须先有失败测试并观察到预期失败，再实施最小修复。
- 不修改第 6 项键盘/读屏交互和第 8 项告警颜色，不触碰 `output/`，不进行无关重构。
- 标识符按原始值精确处理；拒绝空串和纯空白，但不静默修剪、改写大小写或猜测格式。

---

### Task 1: 分离移动距离与时间长度

**Files:**
- Modify: `backend/tests/test_algorithm.py:3996-4024`
- Modify: `backend/app/dispatch.py:1306-1322`
- Modify: `backend/app/dispatch.py:1563-1588`
- Modify: `docs/algorithm.md:225-236`
- Modify: `docs/testing-guide.md:930-932`

**Interfaces:**
- Consumes: `path_movement_count(path: list[Cell]) -> int`。
- Produces: `Metrics.totalDistance` 与 `loadBalance` 使用移动格数；`Metrics.makespan` 使用 tick 数；候选评分保持原 tuple 顺序。

- [ ] **Step 1: 写入距离语义失败测试**

在 `test_release_time_delays_path_execution` 中加入手算断言：

```python
    assert payload["metrics"]["makespan"] == 5
    assert payload["metrics"]["totalDistance"] == 1
```

再增加慢速时间路径回归：

```python
def test_metrics_count_slow_timed_path_as_one_movement() -> None:
    metrics = dispatch_module.calculate_metrics(
        {"R1": [(0, 0), (0, 0), (0, 0), (1, 0)]},
        [],
        [],
        [],
        0,
    )

    assert metrics.makespan == 3
    assert metrics.totalDistance == 1
    assert metrics.loadBalance == 0
```

- [ ] **Step 2: 运行 RED 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py -k "release_time_delays_path_execution or metrics_count_slow_timed_path_as_one_movement" -q
```

Expected: 两个测试均因 `totalDistance` 仍为时间长度而失败。

- [ ] **Step 3: 最小修正指标与候选评分**

在两处分别维护 tick 长度与移动长度：

```python
tick_lengths = [max(0, len(path) - 1) for path in paths.values()]
movement_lengths = [path_movement_count(path) for path in paths.values()]
total_distance = sum(movement_lengths)
makespan = max(tick_lengths, default=0)
mean = total_distance / len(movement_lengths) if movement_lengths else 0
balance = (
    math.sqrt(sum((value - mean) ** 2 for value in movement_lengths) / len(movement_lengths))
    if movement_lengths
    else 0
)
```

候选评分应用同一口径，但不改变失败、冲突、截止时间和 makespan 的排序优先级。

- [ ] **Step 4: 更新指标文档并运行 GREEN 测试**

明确 `totalDistance` 和 `loadBalance` 只统计坐标变化，`makespan` 统计时间展开路径 tick。

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm.py -q
```

Expected: `backend/tests/test_algorithm.py` 全部通过。

- [ ] **Step 5: 提交 Task 1**

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py docs/algorithm.md docs/testing-guide.md
git commit -m "fix: separate movement distance from plan duration"
```

### Task 2: 同步非空白字段与 Scale 标签唯一性

**Files:**
- Modify: `backend/app/schemas.py:1-24`
- Modify: `backend/app/schemas.py:223-233`
- Modify: `backend/tests/test_schema_constraints.py`
- Modify: `frontend/src/domain/scenarioImport.ts:37-171`
- Modify: `frontend/src/domain/scenarioImport.test.ts`

**Interfaces:**
- Produces: `_require_non_blank(value: str) -> str`，保留原值或抛出验证错误。
- Produces: `isNonBlankString(value: unknown): value is string`，用于身份和显示字段。
- Produces: `ScaleExperimentRequest.validate_unique_case_labels()`。

- [ ] **Step 1: 写入后端空白与重复标签失败测试**

在 `test_schema_constraints.py` 使用 `scenario_payload()`，分别将场景 ID、名称、机器人 ID/名称、任务 ID/标题、货架 ID、动态故障机器人 ID 和运行时 `robotId` 设为 `""` 或 `"   "`，断言 `ValidationError`。增加原值保留与描述空串测试：

```python
def test_non_blank_strings_keep_exact_non_empty_value() -> None:
    request = schemas.ScaleExperimentRequest.model_validate({
        "cases": [{"label": " case ", "scenario": scenario_payload()}],
    })
    assert request.cases[0].label == " case "


def test_scale_experiment_rejects_duplicate_labels() -> None:
    case = {"label": "same", "scenario": scenario_payload()}
    with pytest.raises(ValidationError, match="case labels must be unique"):
        schemas.ScaleExperimentRequest.model_validate({"cases": [case, case]})
```

- [ ] **Step 2: 写入前端空白字段失败测试**

在 `scenarioImport.test.ts` 对相同导入字段使用表驱动变异，断言 `parseScenario` 抛错；另断言 `description: ""` 仍被接受、`" R1 "` 保持原值。

- [ ] **Step 3: 分别运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_schema_constraints.py -q
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
```

Expected: 空白字段和重复 Scale 标签测试失败；描述空串与非空原值测试通过。

- [ ] **Step 4: 实施后端精确非空验证**

在 `schemas.py` 引入 `AfterValidator`：

```python
from pydantic.functional_validators import AfterValidator


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


IdentifierStr = Annotated[
    str,
    Field(max_length=MAX_IDENTIFIER_LENGTH),
    AfterValidator(_require_non_blank),
]
DisplayNameStr = Annotated[
    str,
    Field(max_length=MAX_DISPLAY_NAME_LENGTH),
    AfterValidator(_require_non_blank),
]
```

为 `ScaleExperimentRequest` 增加 `mode="after"` validator，按原始 label 精确检查 `len(set(labels))`。

- [ ] **Step 5: 实施前端非空验证并运行 GREEN 测试**

新增：

```typescript
function isNonBlankString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
```

身份、名称、标题和动态故障机器人引用改用该函数；描述继续使用 `isString`。然后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_schema_constraints.py -q
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
```

Expected: 两组测试全部通过。

- [ ] **Step 6: 提交 Task 2**

```powershell
git add backend/app/schemas.py backend/tests/test_schema_constraints.py frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts
git commit -m "fix: reject blank identity fields"
```

### Task 3: 让动态实验校验实际执行分支

**Files:**
- Modify: `backend/tests/test_experiments.py:282-309`
- Modify: `backend/app/main.py:101-106`

**Interfaces:**
- Consumes: `DispatchOptions.model_copy(update={"includeDynamic": True})`。
- Produces: 动态实验在比较函数执行前验证动态任务。

- [ ] **Step 1: 写入 includeDynamic=false 失败测试**

```python
def test_dynamic_replanning_experiment_validates_its_enabled_case_when_request_disables_dynamic() -> None:
    scenario = scenario_payload()
    scenario["tasks"] = [{
        "id": "BASE",
        "type": "inspection",
        "title": "基础任务",
        "priority": 1,
        "targets": [[1, 0]],
    }]
    for robot in scenario["robots"]:
        robot["capabilities"] = ["inspection"]
    scenario["dynamic"]["tasks"] = [{
        "id": "BAD",
        "type": "emergency",
        "title": "无兼容机器人",
        "priority": 5,
        "target": [1, 0],
    }]

    response = TestClient(app).post(
        "/api/experiments/dynamic-replanning",
        json={"scenario": scenario, "options": {"includeDynamic": False}},
    )

    assert response.status_code == 422
    assert "BAD" in "；".join(response.json()["detail"])
```

- [ ] **Step 2: 运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_experiments.py -k "validates_its_enabled_case" -q
```

Expected: 当前返回 `200`，测试失败。

- [ ] **Step 3: 按实际动态分支校验并运行 GREEN**

```python
validation_options = request.options.model_copy(update={"includeDynamic": True})
diagnostics = validate_scenario(request.scenario, validation_options)
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_experiments.py -q
```

Expected: 实验测试全部通过。

- [ ] **Step 4: 提交 Task 3**

```powershell
git add backend/app/main.py backend/tests/test_experiments.py
git commit -m "fix: validate dynamic experiment execution branch"
```

### Task 4: 修正两套报告的最终提交判定

**Files:**
- Modify: `backend/tests/test_algorithm_benchmark.py:845-934`
- Modify: `backend/tests/test_adaptive_replan_calibration.py:1512-1601`
- Modify: `backend/benchmarks/reporting.py:176-197`
- Modify: `backend/benchmarks/reporting.py:280-308`
- Modify: `backend/benchmarks/adaptive_reporting.py:250-271`
- Modify: `backend/benchmarks/adaptive_reporting.py:369-397`

**Interfaces:**
- Produces in each reporter: `_final_bundle_is_committed(target_paths, temporary_paths, partial_path) -> bool`。
- Preserves: 原 `KeyboardInterrupt`/`SystemExit` 继续向调用方传播。

- [ ] **Step 1: 写入全新目录 partial-delete 失败测试**

两套测试分别创建 partial、安装现有 `partial-delete` 中断注入、不预置旧 final，调用 `write_final_report` 并断言：

```python
with pytest.raises(KeyboardInterrupt, match="partial-delete"):
    write_final_report(tmp_path, report)

assert not (tmp_path / "results.partial.json").exists()
assert all((tmp_path / name).exists() for name in FINAL_REPORT_FILE_NAMES)
_assert_no_final_report_transaction_files(tmp_path)
```

自适应测试使用 `ADAPTIVE_FINAL_REPORT_FILE_NAMES` 和对应清理断言。

- [ ] **Step 2: 修正已有旧 bundle 的 partial-delete 期望并运行 RED**

`backup`、`publish` 仍断言恢复旧 bundle；`partial-delete` 改为断言新 final 全部存在且内容不再等于 `b"old ..."`。运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm_benchmark.py -k "final_report_cancellation" -q
.\.venv\Scripts\python.exe -m pytest backend/tests/test_adaptive_replan_calibration.py -k "final_report_cancellation" -q
```

Expected: 两套全新目录测试均发现 final 被删除；旧 bundle 测试发现错误恢复旧文件。

- [ ] **Step 3: 实施提交状态判断**

两模块使用相同逻辑：

```python
def _final_bundle_is_committed(
    target_paths: tuple[Path, ...],
    temporary_paths: dict[Path, Path],
    partial_path: Path,
) -> bool:
    return (
        not partial_path.exists()
        and all(target_path.exists() for target_path in target_paths)
        and all(not temporary_paths[target_path].exists() for target_path in target_paths)
    )
```

在 `except BaseException` 的第一行、回滚前判断：

```python
if active_path == partial_path and _final_bundle_is_committed(
    target_paths,
    temporary_paths,
    partial_path,
):
    raise
```

外层 `finally` 继续清理备份与临时文件。

- [ ] **Step 4: 运行 GREEN 与相邻报告测试**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_algorithm_benchmark.py -q
.\.venv\Scripts\python.exe -m pytest backend/tests/test_adaptive_replan_calibration.py -q
```

Expected: 两个测试文件全部通过，没有残留 `.tmp` 或 `.backup`。

- [ ] **Step 5: 提交 Task 4**

```powershell
git add backend/benchmarks/reporting.py backend/benchmarks/adaptive_reporting.py backend/tests/test_algorithm_benchmark.py backend/tests/test_adaptive_replan_calibration.py
git commit -m "fix: preserve committed benchmark reports"
```

### Task 5: 将场景导入改为有大小边界的候选事务

**Files:**
- Modify: `frontend/src/domain/scenarioImport.ts`
- Modify: `frontend/src/domain/scenarioImport.test.ts`
- Modify: `frontend/src/domain/sessionApi.ts`
- Modify: `frontend/src/domain/sessionApi.test.ts`
- Modify: `frontend/src/main.tsx:193-229`
- Modify: `frontend/src/main.tsx:369-418`
- Modify: `frontend/src/main.tsx:706-721`
- Modify: `frontend/src/main.test.ts:78-290`

**Interfaces:**
- Produces: `MAX_SCENARIO_IMPORT_BYTES = 2_097_152`。
- Produces: `importScenarioCandidate(file, create, commit) -> Promise<void>`。
- Produces: `createSession(apiBase, request, fetcher?) -> Promise<SessionResult>`。
- Produces: `precreatedScenarioRef`，只跳过成功导入触发的下一次自动创建 effect。

- [ ] **Step 1: 写入文件大小和提交顺序失败测试**

在 `scenarioImport.test.ts` 引入 `vi`，增加：

```typescript
it("rejects an oversized scenario before reading it", async () => {
  const text = vi.fn(async () => "{}");
  const commit = vi.fn();
  await expect(importScenarioCandidate(
    { size: MAX_SCENARIO_IMPORT_BYTES + 1, text },
    vi.fn(),
    commit
  )).rejects.toThrow("2 MiB");
  expect(text).not.toHaveBeenCalled();
  expect(commit).not.toHaveBeenCalled();
});

it("does not commit a candidate when session creation fails", async () => {
  const scenario = buildScenario();
  const commit = vi.fn();
  await expect(importScenarioCandidate(
    { size: 100, text: async () => JSON.stringify(scenario) },
    async () => { throw new Error("backend rejected"); },
    commit
  )).rejects.toThrow("backend rejected");
  expect(commit).not.toHaveBeenCalled();
});
```

增加成功测试，断言 create 得到解析后的候选，commit 仅在 create resolve 后得到同一候选和 payload。

- [ ] **Step 2: 为 createSession 写入失败测试**

在 `sessionApi.test.ts` 添加成功 POST 和后端 `422 detail` 测试，断言请求体为传入的 `CreateSessionRequest`，错误保留后端 detail。

- [ ] **Step 3: 运行 RED 测试**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport sessionApi
```

Expected: 新接口尚不存在，测试失败。

- [ ] **Step 4: 实施导入边界与会话创建 helper**

`scenarioImport.ts` 新增：

```typescript
export const MAX_SCENARIO_IMPORT_BYTES = 2_097_152;

export async function importScenarioCandidate(
  file: Pick<File, "size" | "text">,
  create: (scenario: Scenario) => Promise<SessionResult>,
  commit: (scenario: Scenario, payload: SessionResult) => void | Promise<void>
): Promise<void> {
  if (file.size > MAX_SCENARIO_IMPORT_BYTES) {
    throw new Error("场景文件不能超过 2 MiB");
  }
  const scenario = parseScenario(JSON.parse(await file.text()) as unknown);
  const payload = await create(scenario);
  await commit(scenario, payload);
}
```

`sessionApi.ts` 新增 `createSession`，使用 `apiErrorFromResponse(response, "session failed")`。

- [ ] **Step 5: 将 App 导入流程接入候选事务**

增加 `precreatedScenarioRef`。导入时先 `invalidate()` 获取 generation，通过 `enqueue()` 创建候选会话；commit 中使用 `settleCreatedSession`：当前响应设置 skip ref、提交 `importedScenario` 并调用 `applySessionPayload`，过期响应删除新建会话。失败只更新 `importStatus/importError`，不清空当前 session/result。

会话创建 effect 最前面消费精确对象引用：

```typescript
if (precreatedScenarioRef.current === scenario) {
  precreatedScenarioRef.current = null;
  return;
}
```

普通选项变化和 reset 仍走原自动创建流程；原 inline fetch 改用 `createSession`，不改变请求体。

- [ ] **Step 6: 增加 skip-ref 与失败保留回归并运行 GREEN**

在 `main.test.ts` 为可导出的纯判断 helper `shouldReusePrecreatedScenario(precreated, scenario)` 增加精确对象引用测试，防止按 ID 猜测。导入事务失败测试负责证明 commit 未发生，现有 `settleCreatedSession` 测试继续证明过期会话被删除。

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport sessionApi main
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

Expected: 相关测试和生产构建通过。

- [ ] **Step 7: 提交 Task 5**

```powershell
git add frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts frontend/src/domain/sessionApi.ts frontend/src/domain/sessionApi.test.ts frontend/src/main.tsx frontend/src/main.test.ts
git commit -m "fix: make scenario import transactional"
```

### Task 6: 固定并验证 PowerShell 7

**Files:**
- Modify: `scripts/env.ps1:1`
- Modify: `scripts/dev-process-manifest.ps1:1`
- Modify: `backend/tests/test_dev_scripts.py:12-18`
- Modify: `.vscode/tasks.json:5-28`
- Modify: `README.md:50-75`
- Modify: `docs/environment.md:36-65`
- Modify: `AGENTS.md:350-370`

**Interfaces:**
- Produces: `env.ps1` 与可独立加载的 `dev-process-manifest.ps1` 在入口要求 `$PSVersionTable.PSVersion.Major -ge 7`。
- Produces: 所有官方直接脚本命令使用 `.\.tools\powershell\pwsh.exe`。

- [ ] **Step 1: 写入 Windows PowerShell 5.1 行为测试**

在 `test_dev_scripts.py` 增加 `WINDOWS_POWERSHELL` 常量和测试：

```python
def test_start_script_rejects_windows_powershell_before_manifest_changes() -> None:
    manifest_path = REPOSITORY_ROOT / ".runtime" / "dev-processes.json"
    before = manifest_path.read_bytes() if manifest_path.exists() else None
    completed = subprocess.run(
        [
            str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(START_SCRIPT),
            "-ValidateOnly",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    after = manifest_path.read_bytes() if manifest_path.exists() else None
    assert completed.returncode != 0
    assert "PowerShell 7" in completed.stdout + completed.stderr
    assert after == before
```

- [ ] **Step 2: 运行 RED 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_dev_scripts.py -k "rejects_windows_powershell" -q
```

Expected: 当前 `-ValidateOnly` 在 Windows PowerShell 5.1 下仍返回成功，因此测试失败。

- [ ] **Step 3: 增加脚本版本保护**

在 `env.ps1` 和可独立加载的 `dev-process-manifest.ps1` 的任何函数和副作用之前加入相同保护：

```powershell
if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw "PowerShell 7 or later is required. Run .\.tools\powershell\pwsh.exe instead of powershell.exe."
}
```

- [ ] **Step 4: 统一任务与文档命令**

`.vscode/tasks.json` 的 start/stop `command` 改为 `${workspaceFolder}\\.tools\\powershell\\pwsh.exe`，参数加入 `-NoLogo`、`-NoProfile`。README、环境文档和 AGENTS.md 的直接脚本示例全部以 `.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File` 开头；删除 `powershell -ExecutionPolicy ...` 的 5.1 fallback。

- [ ] **Step 5: 运行 PowerShell GREEN 验证**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_dev_scripts.py -q
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1 -ValidateOnly
```

Expected: Python 脚本测试全部通过，项目内 PowerShell 7 输出 `Dev startup validation passed.`，5.1 测试证明清单未变化。

- [ ] **Step 6: 提交 Task 6**

```powershell
git add scripts/env.ps1 scripts/dev-process-manifest.ps1 backend/tests/test_dev_scripts.py .vscode/tasks.json README.md docs/environment.md AGENTS.md
git commit -m "fix: require project PowerShell 7"
```

### Task 7: 完整回归、原始复现与边界审计

**Files:**
- Verify only: all changed files
- Preserve: `output/`

**Interfaces:**
- Consumes: Tasks 1-6 的全部产物。
- Produces: 当前 HEAD 的新鲜完整验证证据。

- [ ] **Step 1: 运行前端完整检查**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
```

Expected: 所有 Vitest 测试和生产构建通过，无未处理错误。

- [ ] **Step 2: 运行后端完整检查**

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m pip check
```

Expected: 全部 pytest 通过，`pip check` 输出 `No broken requirements found.`。

- [ ] **Step 3: 运行统一检查和依赖审计**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend audit --audit-level=high --registry=https://registry.npmjs.org
git diff --check
```

Expected: `check` 退出 0，npm 官方源报告 0 个漏洞，Git 无空白错误。

- [ ] **Step 4: 重新执行八项原始复现**

逐项确认：

- 释放等待路径 `makespan=5`、`totalDistance=1`。
- 两套 reporter 在全新目录 partial-delete 中断后保留全部 final。
- 后端拒绝空白身份字段，前端导入拒绝同类输入。
- 动态实验在请求 `includeDynamic=false` 时对无兼容动态任务返回 `422`。
- 超过 `2_097_152` 字节的文件不会调用 `text()`。
- 候选会话创建失败不会调用导入 commit。
- 重复 Scale label 被 Pydantic 拒绝。
- PowerShell 5.1 明确要求 PowerShell 7 且不改变 manifest；项目内 PowerShell 7 `-ValidateOnly` 通过。

- [ ] **Step 5: 检查文档链接、改动范围和工作树**

使用以下只读脚本检查 Markdown 相对链接，然后检查 Git 范围：

```powershell
@'
import re
from pathlib import Path

root = Path.cwd()
missing = []
for document in root.rglob("*.md"):
    if any(part in {"node_modules", ".git", "output"} for part in document.parts):
        continue
    text = document.read_text(encoding="utf-8")
    for raw_target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = raw_target.strip().split("#", 1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "/")):
            continue
        if not (document.parent / target).resolve().exists():
            missing.append(f"{document.relative_to(root)} -> {raw_target}")
if missing:
    raise SystemExit("\n".join(missing))
print("MARKDOWN_LINKS_OK")
'@ | .\.venv\Scripts\python.exe -
```

然后执行：

```powershell
git status --short
git diff --stat ea94810..HEAD
```

Expected: 仅出现计划内 tracked 改动和既有 `?? output/`；第 6、8 项对应的地图键盘交互和 `.safety-status` 颜色没有修改。

- [ ] **Step 6: 处理最终验证结果**

若验证失败，返回失败所属 Task，先增加或收紧对应失败测试，再使用该 Task 已列出的精确 `git add` 文件集合提交修复。若所有验证通过，不创建额外空提交。
