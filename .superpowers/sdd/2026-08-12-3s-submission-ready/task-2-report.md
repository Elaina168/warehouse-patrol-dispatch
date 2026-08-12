# Task 2 报告：35 次正式证据套件、统计、图表和原子输出

## 状态

完成。仅实现 Task 2，未实现 Task 3 及后续任务，未修改 Task 1 已审通过的 manifest/runner 行为。

实现提交：

- `8f1d6b8984d2068d52371be486c4785790b70b24` `feat: add reproducible 3s evidence suite`
- `0e0180a7c8a2d9ec7d9d7d866c9d415a6aff562e` `fix: record exact 3s evidence parameters`

## 改动

- 新增 `backend/competition/evidence.py`：
  - CLI 默认 `--repetitions 5`、`--timeout-seconds 30.0`，默认执行固定 7 个案例，共 35 条记录。
  - 每条记录调用既有 `backend.benchmarks.process_isolation.run_isolated_process`；复用其 spawn、Pipe、terminate、bounded join、kill fallback 和资源关闭语义。
  - 固定案例为：
    - `integrated-demo-without-conflict-avoidance`
    - `integrated-demo-with-conflict-avoidance`
    - `main-demo-online`
    - `seed-17`
    - `seed-29`
    - `seed-31`
    - `safety-gate-boundary`
  - 三个种子案例直接从 `SEEDED_PRESSURE_CASES` 构造目录，并直接使用 `SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS`，没有复制标签、种子、机器人/任务数量或 2000ms 预算到第二份生产常量。
  - 主演示和安全门分别复用 Task 1 的 `run_main_demo()`、`run_safety_demo()`。
  - timeout、子进程 error、父进程普通异常、completed-but-rejected 均保留为运行记录；任一记录未完成或未通过案例验收时，完整发布后 CLI 返回 1。
  - 生成 `results.json`、`runs.csv`、`case-summaries.csv`、`evidence-manifest.json`，以及四组 SVG/PNG：避碰对照、动态事件时间线、4/6/8 规模表现、安全门执行轨迹。
  - SVG 和 PNG 都只使用 raw `EvidenceRun` 数据。PNG 由 Python 标准库 `struct`、`zlib` 生成有效 RGB PNG，不引入新依赖，也不使用占位图片。
  - manifest 记录 UTC 时间、Git commit/dirty、操作系统、CPU、Python 版本、案例顺序、重复数、单次超时、实际输出目录和复用的规划预算；记录除 manifest 自身外每个发布文件的 SHA-256。
  - 整包先暂存、再备份旧文件、再发布新文件；备份阶段或发布阶段失败都恢复旧包并清理事务文件。
- 新增 `backend/tests/test_competition_evidence.py`，共 10 项行为测试。

## RED 记录

以下每项均先加入测试并观察到因行为缺失而失败，再实现最小生产代码：

1. 固定七案例、默认 35 条计划记录、种子常量复用
   - RED：`ModuleNotFoundError: No module named 'backend.competition.evidence'`
   - GREEN：目录和 `planned_runs()` 实现后 `1 passed`。
2. 隔离批处理保留 timeout/error/验收异常
   - RED：`ImportError: cannot import name 'EvidenceRun'`
   - 中间 RED：error 文本仍为 `boom\nnext`，期望共享规则的 `boomnext`。
   - GREEN：批处理调用共享隔离器、清洗错误文本并保留三类异常后 `2 passed`。
3. 七个真实案例执行和验收
   - RED：`NotImplementedError: 尚未实现证据案例`
   - GREEN：真实 direct/main/seeded/safety 执行后该测试 `1 passed`。
4. CSV 重算、manifest 哈希、四组 SVG/PNG、图值来源、发布回滚
   - RED：`ImportError: cannot import name 'EvidenceReport'`
   - GREEN：报告、图、manifest 和事务发布实现后聚焦文件 `6 passed`。
5. 备份阶段失败回滚
   - RED：受控第二个 backup replace 失败后旧 `results.json` 丢失。
   - GREEN：把备份和发布置于同一回滚边界后两个回滚测试 `2 passed`。
6. CLI 默认 35 条、失败后仍发布、非法配置前置拒绝、真实超时无子进程残留
   - RED：`ImportError: cannot import name 'main'`
   - GREEN：CLI 和复用隔离行为实现后新测试文件 `9 passed`；最终扩展后为 `10 passed`。
7. manifest 精确参数
   - RED：CLI config 缺少 `caseIds`、实际 `outputDir`、`seededPressurePlanningTimeBudgetMs`。
   - GREEN：补录精确参数后单项 `1 passed`，聚焦集合 `106 passed`。

## GREEN 与回归结果

### Task 1 基线

```text
backend/tests/test_competition_manifests.py
11 passed in 1.36s
```

### Task 2 聚焦及相关回归

最终命令使用工作区内 basetemp，避免系统临时目录一次性 ACL 扫描错误：

```text
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .pytest-task2 \
  backend\tests\test_competition_evidence.py \
  backend\tests\test_competition_manifests.py \
  backend\tests\test_benchmark_process_isolation.py \
  backend\tests\test_algorithm_benchmark.py \
  backend\tests\test_experiments.py

106 passed in 12.58s
```

一次不用自定义 basetemp 的复跑曾产生 35 个 setup error；所有 error 都来自 pytest 扫描 `C:\Users\zytx\AppData\Local\Temp\pytest-of-zytx` 时的 `PermissionError`，并同时影响现有 benchmark 的全部 `tmp_path` 测试。第一次自定义 basetemp 选在不存在的 `.runtime` 父目录也产生相同数量的 `FileNotFoundError`。改用现存 worktree 根下 `.pytest-task2` 后，同一测试集合 106/106 通过；临时目录随后按精确绝对路径删除。

### 真实默认 35 次 CLI

命令：

```text
.\.venv\Scripts\python.exe -m backend.competition.evidence \
  --output-dir output\3s-submission-evidence
```

结果目录：`output/3s-submission-evidence/20260812T054933Z`（验证后已删除，未提交生成物）。

独立解析交叉检查：

```text
运行记录：35 JSON / 35 CSV
案例摘要：7
outcome：35 completed / 0 timeout / 0 error
验收：35 accepted
manifest 哈希：11/11 匹配
PNG：4/4 有效，均为 320 x 180
发布文件总数：12
活动子进程：0
CLI exit：0
总耗时：19.4s
```

### 最终全量验证（必要参数修正后新鲜运行）

```text
& 'C:\nvm4w\nodejs\npm.cmd' run check

frontend production build: passed
frontend tests: 190/190 passed
backend tests: 728/728 passed in 77.43s
overall exit: 0
```

另有：

```text
git diff --check: passed
```

## 自审

- 未修改 `backend/competition/manifests.py` 或 `backend/competition/runners.py`。
- 未复制 `SEEDED_PRESSURE_CASES` 的精确数值，也未复制 2000ms 预算。
- 每条正式记录通过共享隔离器执行；基础隔离清理测试及 Task 2 超时无残留测试均覆盖。
- completed-but-rejected 不会被误算为 timeout/error，但会使 CLI 返回非零。
- case summary 只由 runs 重算；CSV 包含生成同一摘要所需的每条记录字段。
- 图数据函数直接选择 raw run 的 metrics/timeline/trajectory；SVG/PNG 从该选择结果生成。
- manifest 不自包含自身哈希，避免不可解的自引用；它覆盖包内其余 11 个文件。
- 原子边界覆盖全部 12 个文件，而不只是 JSON/CSV。
- 未新增第三方依赖、API 路由、前端面板或 Task 3+ 内容。

## 关注点

- wall-clock 和 `replanTimeMs` 仍是同机证据，不应宣传为跨机器可复现性能阈值。
- Git `dirty` 是生成时工作树状态；正式材料应从期望提交上的干净工作树重新运行 CLI，以获得 `dirty=false` 的证据包。
- 默认正式运行会在 `output/3s-submission-evidence/<UTC timestamp>` 创建新目录；验证产生的目录本次没有提交。
- pytest 系统临时目录出现过一次 ACL 扫描错误；代码相关集合已用工作树内 basetemp 重跑并通过，完整 `npm run check` 随后也在默认配置下通过。

## Fix round 1/5（2026-08-12）

状态：完成。本轮仅修复 Task 2 图表、manifest 参数和 CSV 摘要验证规格；未修改 Task 1 文件，未开始 Task 3+。

覆盖测试文件：`backend/tests/test_competition_evidence.py`。

### 业务语义图表

- RED 命令：

```text
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider \
  --basetemp .pytest-task2-fix1 \
  backend\tests\test_competition_evidence.py -k "charts or business_chart"
```

- RED 结果：测试收集失败，`ImportError: cannot import name 'render_chart'`。当前 HEAD 只有无标签通用折线，无法按业务字段验证渲染。
- GREEN：新增四个专用 renderer；SVG 包含标题、标签和图例，PNG 用标准库像素绘制业务几何及内置 3×5 字体。
  - 避碰对照：`without`/`with`、`Predicted conflicts`、`Failures`。
  - 动态时间线：每条 `T=<time>`、`action`、completed/failure 状态。
  - 规模表现：按实际 `robotCount` 分组，显示 planning ms、task count 和 accepted 数。
  - 安全门轨迹：每 tick 的机器人 ID/坐标、intervention、stall count。
- 测试分别改变 failure、action、robotCount 和 position，要求 SVG 文本/结构和 PNG 实际字节同时改变，并检查多标签、多颜色像素结构。
- GREEN 结果：`5 passed, 9 deselected in 4.35s`。

### manifest 精确调度参数

- RED 命令：

```text
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider \
  --basetemp .pytest-task2-fix1-manifest \
  backend\tests\test_competition_evidence.py::test_evidence_cli_defaults_to_35_runs_and_publishes_diagnostics_on_failure
```

- RED 结果：config 缺少 `dispatchParameters` 和 `seededPressureCases`。
- GREEN：direct without/direct with/seeded 三类运行与 manifest 共用 `_direct_options()`/`_seeded_options()`；精确记录 `avoidConflicts`、`includeDynamic`、`assignmentReplanWindow`、`adaptiveReplanWindow`。`seededPressureCases` 逐项从 `SEEDED_PRESSURE_CASES` 派生 label/seed/robotCount/taskCount，没有复制常量。
- GREEN 结果：`1 passed in 0.06s`。

### runs.csv 独立重算全部 11 个摘要字段

- 新测试使用同一案例的 5 条 completed（含一条验收拒绝）、1 timeout、1 error，并从 CSV 的标量列和 JSON `metrics` 单元独立手算：caseId、runCount、completedRunCount、timeoutCount、errorCount、acceptedRunCount、acceptedRunRatePercent、medianWallClockMs、p95WallClockMs、medianReplanTimeMs、p95ReplanTimeMs。
- 初次执行因测试夹具只提供 `replanTimeMs`，不符合冲突图 raw-run 的完整 metrics 结构而出现 `KeyError: conflictCount`；这不是目标规格的有效 RED，补全真实结构后重跑。
- 补全夹具后的结果：`1 passed in 0.28s`。生产摘要实现已有 11 字段能力；本轮修复的是原测试只核对 4 字段的覆盖缺口，因此没有为制造 RED 修改正确的生产统计。
- CSV 和 JSON 两份摘要都逐字段等于独立期望。

### 回归与全量

```text
相关集合：111 passed in 15.43s

& 'C:\nvm4w\nodejs\npm.cmd' run check
frontend production build: passed
frontend tests: 190/190 passed
backend tests: 733/733 passed in 74.96s
overall exit: 0
git diff --check: passed
```

本轮创建的 `.pytest-task2-fix1*` 临时目录均逐个解析绝对路径、确认位于当前 worktree 根后删除；没有保留测试输出目录。
