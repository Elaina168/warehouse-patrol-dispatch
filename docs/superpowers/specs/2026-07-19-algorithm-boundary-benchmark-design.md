# 算法边界基准与性能画像设计

## 1. 目标

在不改动主前端、不新增实验 API、不直接引入 CBS/MAPF 的前提下，为当前调度器建立一套可重复运行的离线算法边界基准。

该基准回答四个问题：

1. 机器人数量增加时，任务分配和路径规划耗时如何变化。
2. 固定机器人数量下提高任务密度时，分配率、预测冲突、失败和耗时从哪里开始恶化。
3. 多机器人共享瓶颈通道时，在线执行安全门是否始终阻止实际冲突，以及需要多少次安全等待。
4. 当前优先级时空 A* 和 beam-style 分配的真实边界在哪里，是否已有证据支持后续局部优化或独立评估 CBS/MAPF。

基准用于诊断和取证，不把所有大规模案例强制加入日常完整检查，也不预设所有边界案例必须成功。

## 2. 已确认范围

### 2.1 包含内容

- 新增后端离线基准模块和 PowerShell/`npm` 入口。
- 建立机器人规模、任务密度和瓶颈通道三个确定性场景族。
- 每个案例在独立子进程中重复运行，并设置可配置超时。
- 输出逐次运行数据、逐案例汇总和完整运行配置。
- 输出 JSON 与 CSV，供后续人工复核、图表制作和算法优化使用。
- 为场景生成、统计计算、超时/异常记录和输出契约增加稳定回归测试。
- 保持 `integrated-demo`、六个实验 API 和现有完整检查不变。

### 2.2 不包含内容

- 不实现 CBS、ECBS 或其他完整 MAPF 求解器。
- 不修改 `POST /api/dispatch`、会话 API 或六个实验 API 的请求/响应结构。
- 不新增 Pydantic API 模型或前端类型。
- 不恢复前端实验面板，不生成前端页面。
- 不在本阶段调整 beam width、候选排序、自适应窗口阈值或充电策略。
- 不把单次墙钟耗时作为日常 pytest 的硬通过门槛。
- 不提交生成的基准结果目录。

## 3. 方案选择

采用“离线基准先行”方案：先收集可复现边界和耗时分布，再根据证据选择后续算法改动。

未选择的方案：

- 直接实现 CBS/MAPF：理论能力更强，但改动面、运行成本和现有在线语义回归风险都较大，当前缺少必须重写的边界证据。
- 立即转入报告和 PPT：可以快速产出材料，但没有经过人工复核的较大规模数据，容易把当前启发式规划器的能力表述过度。

## 4. 组件边界

### 4.1 基准场景模块

新增 `backend/benchmarks/scenarios.py`，只负责构造并返回现有 `Scenario` 模型，不调用调度器，不写文件。

模块提供三组确定性案例目录：

- `scale`：使用彼此独立的横向任务通道，案例机器人数量为 `4`、`8`、`12`，每台机器人对应 `3` 个基础任务，并加入 `3` 个动态突发任务；三个案例的 `caseId` 分别为 `scale-r4-t15`、`scale-r8-t27`、`scale-r12-t39`。该组主要隔离任务分配和候选搜索随机器人数量增长的成本。
- `density`：复用 `backend.app.seeded_scenarios.seeded_pressure_scenario`，固定 `8` 台机器人和种子 `43`，基础任务数量依次为 `28`、`40`、`52`；现有生成器还会加入 `3` 个动态任务，因此结果中的总任务数量依次为 `31`、`43`、`55`，对应 `caseId` 为 `density-r8-t31`、`density-r8-t43`、`density-r8-t55`。该组保持地图与障碍生成规则一致，观察任务密度增长。
- `bottleneck`：使用带侧向避让空间的共享瓶颈地图，案例机器人数量为 `4`、`6`、`8`。每台机器人对应一个 `demand = 1` 的配送任务；取货点位于机器人起始侧，放货点位于共享通道另一侧，从任务结构上强制配送路线穿过瓶颈。三个案例的 `caseId` 分别为 `bottleneck-r4-t4`、`bottleneck-r6-t6`、`bottleneck-r8-t8`。地图必须保留至少一个旁路，避免把无解的一维对向交换误当成规划器缺陷。该组不包含动态任务，通过普通在线会话逐 tick 执行，用于记录执行安全门介入次数。

每个案例拥有固定且唯一的 `caseId`，格式为 `<family>-r<robotCount>-t<totalTaskCount>`。案例目录是代码常量，命令行不接受任意 Python 表达式或动态导入路径。

三组案例统一使用：

```python
DispatchOptions(
    avoidConflicts=True,
    includeDynamic=True,
    assignmentReplanWindow=120,
    adaptiveReplanWindow=False,
)
```

所有 `bottleneck` 案例的 `tickTarget` 固定为 `120`；直接规划案例的 `tickTarget` 为 `None`。

场景测试必须验证：

- `Scenario.model_validate` 成功。
- 机器人 ID、任务 ID 和起点唯一。
- 相同案例重复构造得到完全相同的 `model_dump(mode="json")`。
- 所有基础和动态任务目标在静态地图上可达。
- `bottleneck` 场景确实存在共享通道和侧向避让空间。

### 4.2 单次运行器

新增 `backend/benchmarks/algorithm_boundary.py`，负责目录选择、子进程运行、统计和输出。

直接规划案例使用现有：

```python
run_dispatch(scenario, options)
```

在线瓶颈案例使用现有普通会话链路：

```python
create_session(CreateSessionRequest(...))
tick_session(session_id, SessionTickRequest(currentTime=...))
delete_session(session_id)
```

在线案例必须保持 `enforce_execution_safety=True` 的默认行为，不得复用实验模块中为对照证据设置的 `enforce_execution_safety=False`。每次 tick 的目标时间固定为当前会话时间加 `1`，一直推进到案例的 `tickTarget`。每个非空 `SessionResult.safetyIntervention` 计为一次安全介入。

### 4.3 进程隔离和超时

父进程使用 Python `multiprocessing` 的 `spawn` 上下文执行每个“案例 × 重复序号”。每次运行使用新进程，避免全局会话状态、缓存和异常污染后续案例。

父进程对每次运行执行以下控制：

1. 启动子进程并记录父进程墙钟开始时间。
2. 在 `timeoutSeconds` 内等待结构化结果。
3. 正常返回时记录 `outcome = "completed"`。
4. 超时时终止该基准子进程，记录 `outcome = "timeout"`，继续后续运行。
5. 子进程抛出异常时记录 `outcome = "error"`、异常类型和异常文本，继续后续运行。

超时和异常属于基准结果，不让整个批次丢失；只有配置无效、输出目录不可创建或父进程自身失败时，命令整体返回非零退出码。

## 5. 命令行入口

在根 `package.json` 增加：

```json
"benchmark:algorithm": ".\\.venv\\Scripts\\python.exe -m backend.benchmarks.algorithm_boundary"
```

标准运行命令：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --repetitions 5 --timeout-seconds 30 --output-dir output/algorithm-boundary-benchmark
```

公开参数固定为：

- `--families`：逗号分隔的 `scale`、`density`、`bottleneck`；默认运行三组。
- `--repetitions`：每个案例的重复次数，默认 `5`，必须大于 `0`。
- `--timeout-seconds`：每次运行超时，默认 `30`，必须大于 `0`。
- `--output-dir`：结果根目录，默认 `output/algorithm-boundary-benchmark`。

命令在结果根目录下创建 UTC 时间戳子目录，避免覆盖既有证据。程序启动后在控制台打印最终绝对路径。`output/` 及其中结果不进入 Git 提交。

## 6. 原始数据契约

JSON 顶层包含：

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-07-19T00:00:00Z",
  "config": {},
  "runs": [],
  "caseSummaries": []
}
```

`runs` 中每项使用以下字段；字段始终存在，不适用的数值字段为 `null`，CSV 中对应空单元格：

- 标识：`caseId`、`family`、`mode`、`seed`、`runIndex`。
- 输入规模：`robotCount`、`taskCount`、`dynamicTaskCount`、`obstacleCount`、`tickTarget`。
- 运行结果：`outcome`、`errorType`、`errorMessage`、`correctnessStable`。
- 调度结果：`releasedTaskCount`、`coveredTaskCount`、`assignedTaskCount`、`completedTaskCount`、`assignmentRatePercent`、`coverageRatePercent`、`actualCompletionRatePercent`。
- 安全与质量：`predictedConflictCount`、`activeConflictCount`、`executionSafetyEvaluated`、`safetyInterventionCount`、`deadlineMissCount`、`failureCount`。
- 路径与时间：`totalDistance`、`makespan`、`replanTimeMs`、`maxSnapshotReplanTimeMs`、`wallClockMs`。

字段映射必须明确：

- `predictedConflictCount` 来自现有 `Metrics.conflictCount`，表示规划预测，不表示冲突动作已经执行。
- `mode` 只允许 `direct` 和 `online`；`scale`、`density` 使用 `direct`，`bottleneck` 使用 `online`。
- `density` 的 `seed` 为 `43`；不依赖随机数的 `scale` 和 `bottleneck` 的 `seed` 为 `null`。
- 直接规划案例的 `executionSafetyEvaluated` 为 `false`，`safetyInterventionCount` 为 `0`，`activeConflictCount` 为 `null`。
- 在线案例的 `executionSafetyEvaluated` 为 `true`；`activeConflictCount` 取最终 `MetricSnapshot.activeConflictCount`，`maxSnapshotReplanTimeMs` 取全部 `metricsHistory` 中 `replanTimeMs` 的最大值。
- `wallClockMs` 由父进程使用单调时钟测量，包含单次案例的会话创建、规划、tick 和清理时间，但不包含其他案例。

`correctnessStable` 的计算规则：

- 直接规划：全部任务已分配，并且 `predictedConflictCount`、`deadlineMissCount`、`failureCount` 均为 `0`。
- 在线执行：所有任务均被覆盖，并且最终 `activeConflictCount`、`deadlineMissCount`、`failureCount` 均为 `0`。安全门介入不直接判定失败，因为介入代表不安全动作被阻止。
- `timeout` 或 `error` 的 `correctnessStable` 固定为 `false`。

## 7. 汇总统计

每个 `caseId` 生成一条 `caseSummaries`，包含：

- `runCount`
- `completedRunCount`
- `timeoutCount`
- `errorCount`
- `stableRunCount`
- `stableRunRatePercent`
- `medianWallClockMs`
- `p95WallClockMs`
- `medianReplanTimeMs`
- `p95ReplanTimeMs`
- `maxSafetyInterventionCount`

统计规则固定为：

- 耗时统计只使用 `outcome = "completed"` 的运行。
- 中位数使用 Python `statistics.median`。
- P95 使用 nearest-rank：将数值升序排列，取下标 `ceil(0.95 * n) - 1`。
- 没有正常完成运行时，四个耗时统计字段为 `null`。
- 百分比统一保留一位小数。

第一版只报告分布，不根据墙钟耗时自动给出“算法通过/失败”结论，也不修改现有 `SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS = 2000`。人工复核结果后，才能决定是否需要新的竞赛口径或自适应阈值。

## 8. 输出文件

每个时间戳目录包含：

- `results.json`：完整配置、逐次运行和逐案例汇总。
- `runs.csv`：一行对应一次运行，列名与 `runs` 字段一致。
- `case-summaries.csv`：一行对应一个案例，列名与 `caseSummaries` 字段一致。

文件使用 UTF-8 编码；CSV 使用 `utf-8-sig`，保证 Windows 表格软件直接打开时中文不会乱码。JSON 使用 UTF-8、`ensure_ascii=False` 和两空格缩进。

为减少中途失败造成的数据丢失，父进程每完成一次运行就原子更新同目录的 `results.partial.json`。全部案例结束后写入三个最终文件；成功完成时删除 `results.partial.json`。命令因父进程错误退出时保留该文件供诊断。

## 9. 错误处理

- 未知 `--families` 值：启动前报错并返回非零，不创建结果目录。
- 非正数 `--repetitions` 或 `--timeout-seconds`：启动前报错并返回非零。
- 场景构造或 Pydantic 校验失败：该次运行记录为 `error`；同一批次继续执行其他案例。
- 子进程超时：只终止该基准子进程，不结束其他案例。
- 在线会话无论成功或异常都在 `finally` 中调用 `delete_session`。
- 输出写入失败：控制台打印精确目标路径并返回非零，不声称结果完整。
- 错误文本写入 JSON/CSV 前移除换行并限制长度，避免一条异常破坏 CSV 结构；完整 traceback 只输出到控制台。

## 10. 测试设计

新增 `backend/tests/test_algorithm_benchmark.py`，覆盖：

1. 三个场景族的案例 ID、机器人数量、总任务数量和执行模式符合目录定义。
2. 同一案例重复生成的场景 JSON 完全一致。
3. 所有目标静态可达，瓶颈场景包含旁路，不构造完全一维无解交换。
4. 百分比、中位数和 nearest-rank P95 计算正确。
5. 正常子进程结果映射为 `completed`。
6. 测试专用短等待 worker 被记录为 `timeout`，批次继续执行。
7. 测试专用异常 worker 被记录为 `error`，异常字段完整且不会破坏后续案例。
8. 临时目录下生成的 JSON/CSV 字段、编码和行数一致。
9. 一个最小在线瓶颈案例逐 tick 执行后，实际历史不存在顶点或反向边交换冲突；如果发生安全介入，计数与非空 `safetyIntervention` 响应次数一致。

日常测试只运行小型确定性案例和测试 worker，不运行完整多次基准，不断言真实墙钟耗时上限。完整手工基准通过 `npm run benchmark:algorithm` 单独执行。

## 11. 文档更新

实现时同步更新：

- `docs/experiments.md`：说明离线基准与六个实验 API 的边界、字段含义和取数命令。
- `docs/testing-guide.md`：增加运行基准、读取结果和人工复核步骤。
- `AGENTS.md`：记录场景族、结果契约和当前已知边界，不提前宣称算法升级完成。

## 12. 完成标准

满足以下条件才可以声称算法边界基准完成：

- 三个确定性场景族均可通过统一命令运行。
- 每次运行受到独立超时保护，单个错误不会丢失整个批次。
- JSON 与 CSV 使用已定义字段，逐次结果和汇总能够互相核对。
- 在线瓶颈案例通过普通安全会话链路执行，安全介入次数来自结构化字段，不解析中文事件日志。
- 日常完整检查不执行重型基准，且现有前端构建、前端测试和后端测试继续通过。
- 生成结果保持未提交；人工复核基准输出后，再决定下一项算法优化。
