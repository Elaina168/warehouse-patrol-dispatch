# 实验对比说明

本文档记录当前实验模块、离线算法边界基准和自适应窗口校准的内部诊断出口。现阶段这些工具用于回归与系统问题定位，不代表当前优先制作图表或竞赛材料。

## 避碰对比接口

接口：

```text
POST http://127.0.0.1:8011/api/experiments/conflict-avoidance
```

请求体：

```json
{
  "scenario": {},
  "options": {
    "avoidConflicts": true,
    "includeDynamic": false,
    "assignmentReplanWindow": 24
  }
}
```

`scenario` 使用与 `/api/dispatch` 相同的场景结构。接口会忽略请求中的 `avoidConflicts` 取值，并分别运行两组实验：

- `withoutConflictAvoidance`：关闭时序避碰。
- `withConflictAvoidance`：开启时序避碰。

响应体包含：

- `scenarioId`
- `cases`
  - `label`
  - `options`
  - `result`

每个 `result` 都是完整的 `DispatchResult`，可以直接读取：

- `metrics.conflictCount`
- `metrics.makespan`
- `metrics.totalDistance`
- `metrics.assignedTaskCount`
- `metrics.deadlineMissCount`
- `conflicts`
- `eventLog`

## 动态重规划对比接口

接口：

```text
POST http://127.0.0.1:8011/api/experiments/dynamic-replanning
```

请求体与 `/api/dispatch` 相同。接口会分别运行两组实验：

- `withoutDynamicReplanning`：关闭场景动态事件，等价于只看初始任务和静态地图。
- `withDynamicReplanning`：开启场景动态事件，包含动态任务、动态封锁和动态故障机器人。

响应体同样包含 `scenarioId` 和 `cases`，每个 case 的 `result` 都是完整 `DispatchResult`。

该接口用于内部动态事件对比：

> 同一场景下，对比是否启用动态事件后，任务集合、任务分配、冲突、路径长度和指标变化。

## 滚动窗口参数对比接口

接口：

```text
POST http://127.0.0.1:8011/api/experiments/replan-window
```

请求体：

```json
{
  "scenario": {},
  "options": {
    "avoidConflicts": true,
    "includeDynamic": false,
    "assignmentReplanWindow": 24,
    "adaptiveReplanWindow": false
  },
  "windows": [4, 24, 48],
  "includeAdaptive": true
}
```

`windows` 中的每个值都会生成固定窗口 case，标签格式为 `window-<value>`。`includeAdaptive=true` 时额外生成 `adaptive-window-<base>`，其中 `<base>` 为 `options.assignmentReplanWindow`。每个结果通过 `effectiveAssignmentReplanWindow` 和 `replanWindowReason` 说明实际窗口与原因。

`windows` 必须包含 `1..32` 个值且值不可重复；重复值以 `windows must be unique` 拒绝。规模对比接口的 `cases` 同样必须包含 `1..32` 项。`32` 是单次实验批量上限，不是机器人或任务规模结论。

该接口用于内部滚动窗口对比：

> 同一场景下，对比不同滚动窗口大小对当前分配任务数、远期任务延迟进入、路径长度、冲突数量和截止时间指标的影响。

## 机器人和任务规模对比接口

接口：

```text
POST http://127.0.0.1:8011/api/experiments/scale
```

请求体：

```json
{
  "cases": [
    {
      "label": "small",
      "scenario": {}
    },
    {
      "label": "medium",
      "scenario": {}
    }
  ],
  "options": {
    "avoidConflicts": true,
    "includeDynamic": false,
    "assignmentReplanWindow": 24
  }
}
```

每个 `scenario` 使用与 `/api/dispatch` 相同的场景结构。接口会对每个 case 使用同一组选项运行调度，并返回：

- `label`
- `scenarioId`
- `options`
- `result`

该接口用于内部规模对比：

> 对比不同机器人数量、任务数量或地图规模下的完成时间、总路径长度、冲突数量、失败任务数和重规划耗时。

任务类型能力专项证据复用该接口，不新增第七个实验 API：

- `homogeneous-fleet`：三台机器人都显式支持 `inspection`、`delivery`、`emergency`。
- `specialized-fleet`：巡检、取送、突发任务分别只有对应单能力机器人可执行；每类任务至少有一个兼容专业机器人。
- `specialized-with-failure`：通过在线会话专项回归让唯一兼容突发机器人故障，先恢复不兼容机器人验证失败仍存在，再恢复兼容机器人验证自动重规划。

实验断言逐项读取 `assignments`，验证任务 `type` 必须属于被分配机器人 `capabilities`；配送任务还验证 `robot.load >= task.demand`。专项配送使用 `demand: 2` 保留现有载重边界，主演示、手工和随机配送仍使用 `demand: 1`。缺少 `capabilities` 的历史场景按全能力处理。

## 固定种子压力接口

接口：

```text
POST http://127.0.0.1:8011/api/experiments/seeded-pressure
```

请求体：

```json
{
  "caseSet": "standard",
  "options": {
    "avoidConflicts": true,
    "includeDynamic": true,
    "assignmentReplanWindow": 120
  }
}
```

`caseSet` 可取 `standard` 或 `extended`。标准集运行固定的 4/6/8 机器人随机障碍场景；扩展集运行七组固定种子，并包含曾经暴露晚期顶点冲突的 seed-43 边界。

case 和 summary 中的 `assignmentRatePercent` 都是“已分配任务数 / 总任务数”。这个字段只说明任务是否进入调度方案，不表示机器人已经在仿真时间内执行完任务。响应还提供稳定组数、冲突、超期、失败、路径代价、makespan、规划耗时和规划预算通过情况。

## 在线压力接口

接口：

```text
POST http://127.0.0.1:8011/api/experiments/online-pressure
```

该接口运行固定 seed-17 在线流程：持续推进 tick，在预定时间通过统一的 `POST /api/sessions/{session_id}/tasks` 任务接口插入紧急任务和生成任务，并执行运行时封锁、解除封锁、机器人故障和恢复。人工任务与自动生成任务没有第二套接口或来源字段。

核心字段：

- `releasedTaskCount`：实验结束 tick 前已释放的任务数。
- `runtimeTaskCount`：通过统一任务接口成功插入的运行时任务数。
- `coveredTaskCount`：状态不是 `unassigned` 的任务数。
- `coverageRatePercent`：`coveredTaskCount / taskCount`。
- `completedTaskCount`：实验结束时已实际完成的任务数。
- `actualCompletionRatePercent`：`completedTaskCount / releasedTaskCount`。

因此“覆盖率”回答任务是否被调度覆盖，“实际完成率”回答已到达任务在固定实验时域内完成了多少。稳定性不要求固定时域内所有任务完成；当前判定要求任务全覆盖、冲突为零、截止超期为零、失败为零。

## 固定仓库输入口径

`integrated-demo` 的货架实体格不可通行，每个货架只有一个相邻作业格。入库从进货点送到当前空货架作业格，出库从当前有货货架作业格送到出货点。默认配送输入固定为：

- `T1` 入库：`[2,0] -> [2,5]`，对应货架 `[2,4]`。
- `T2` 出库：`[13,6] -> [2,15]`，对应货架 `[13,7]`。
- `T4` 入库：`[8,0] -> [12,9]`，对应货架 `[12,8]`。

后端会话维护 `empty`、`inboundReserved`、`occupied`、`outboundReserved` 四种权威库存状态；手工和随机任务通过统一任务端点接受同一库存校验。固定流程初始12个货架有货，`T2` 实际取货时对应货架熄灭，`T1`、`T4` 完成放货作业后对应货架亮起，最终13个货架有货。

当前固定输入结果为：默认6任务在 `T=700` 前全部完成，冲突、失败、截止超期和充电访问均为0；无避碰基线预测冲突大于0，开启避碰后预测冲突为0。这些是固定输入的自动化回归口径，不是任意场景的全时域安全证明。

默认四台机器人显式使用 `["inspection", "delivery", "emergency"]`，固定输入回归还验证所有分配均不违反能力或载重资格，最终有货货架仍为13个。能力专项证据只说明机器人资格约束与恢复分类正确，不实现也不证明尚未完成的全规划时域零冲突保证。

## 当前用途

六个接口用于后端自动化回归和必要的系统诊断：

- 避碰对比：同一场景下，对比关闭避碰和开启避碰后的冲突数量、路径长度、任务完成情况和事件日志。
- 动态重规划对比：同一场景下，对比是否启用动态事件后的任务完成情况、路径变化和重规划指标。
- 滚动窗口对比：同一场景下，对比不同固定窗口以及可选自适应窗口对当前规划规模、路径代价、失败数和规划耗时的影响。
- 规模对比：不同机器人数量、任务数量或地图规模下，对比完成时间、总路径长度、冲突数量、失败任务数和重规划耗时。
- 固定种子压力：验证随机障碍和多规模下的分配稳定性、冲突安全与规划耗时预算。
- 在线压力：验证持续任务到达、运行时封锁、机器人故障和恢复后的覆盖率、实际完成率、事件历史与指标连续性。

## 前端状态

主前端不提供实验面板，避免人工演示时被多组实验操作分散注意力。

当前前端只在综合场景中保留普通策略切换：

- `避碰规划`：正式演示策略。
- `基线对比`：同一综合场景下关闭避碰，用于证明不开避碰会产生冲突。

后端实验接口继续保留，供自动化回归、系统问题定位和3S证据生成使用。正式图表、报告及其原始数据由 `competition/3s/` 下的竞赛流程管理；当前不恢复前端实验面板。

## 场景设计注意

避碰对比场景不能使用完全一维的单通道对向交换作为“开启避碰后应无冲突”的验收样例。

已验证现象：

- 在 `height = 1` 的单通道中，两台机器人从两端互换位置，即使开启时序避碰，也可能无法完全消除冲突。
- 这不是接口错误，而是场景本身缺少可避让空间，超出了当前优先级时空 A* 避碰策略的竞赛级验收边界。
- 用于证明避碰有效的实验场景应至少提供一个侧向避让空间，例如 `height = 2` 的走廊或带旁路的窄巷道。

当前后端回归测试使用带侧向避让行的交叉配送场景：关闭避碰会产生中点冲突，开启避碰后机器人可通过旁路绕行并消除冲突。

`integrated-demo` 的当前固定输入中，无避碰基线存在预测冲突；开启优先级避碰后6个任务仍全部分配，预测冲突降为0，失败和截止超期保持为0。该结果属于当前场景和回归覆盖，不等于已经实现第三级全规划时域零冲突保证。

当前冲突验收分三级：

1. 已实现执行安全（代码强制）：开启避碰的在线会话会在首个预测顶点或反向边冲突 tick 让全车安全等待，不把冲突动作写入实际路径历史；大跨度 tick 请求在首个危险 tick 提前返回，`SessionResult.safetyIntervention` 提供绝对时间、冲突类型、机器人和单元格。
2. 已实现规划质量：固定种子压力场景的预测冲突为零；当前 `integrated-demo` 固定输入中，开启避碰后预测冲突由大于0降为0。
3. 未实现未来目标：全规划时域零冲突保证。当前文档和实验结果不得把第二级表述成第三级已经完成；当前仍不是完整 MAPF/CBS 求解器，安全门保证不执行不安全动作，不保证任意输入都能找到零冲突路线。

基线对比、直接调度和实验接口仍可返回或执行预测冲突，用于对照；安全门只属于开启避碰的在线执行链路。实验接口不模拟前端暂停或在线执行拦截。

## 离线算法边界基准

现有六个 `/api/experiments/*` 接口保持不变：`/api/experiments/conflict-avoidance`、`/api/experiments/dynamic-replanning`、`/api/experiments/replan-window`、`/api/experiments/scale`、`/api/experiments/seeded-pressure`、`/api/experiments/online-pressure`。离线基准是独立的命令行取证流程，不新增实验接口，也不替代这些接口的回归用途。

在项目根目录运行默认完整基准：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --repetitions 5 --timeout-seconds 30 --output-dir output/algorithm-boundary-benchmark
```

默认场景族共九个案例：`scale-r4-t15`、`scale-r8-t27`、`scale-r12-t39`、`density-r8-t31`、`density-r8-t43`、`density-r8-t55`、`bottleneck-r4-t4`、`bottleneck-r6-t6`、`bottleneck-r8-t8`。每个案例默认运行五次，结果写入命令输出目录下按 UTC 时间创建的子目录。

报告中的 `predictedConflictCount` 对应直接规划或在线最终规划的 `Metrics.conflictCount`，是规划预测，不表示冲突动作已经执行。在线案例还以 `activeConflictCount` 和 `safetyInterventionCount` 提供实际执行安全证据：前者是最终指标快照中的活动冲突数，后者是执行过程中安全门的介入次数。直接规划案例的 `executionSafetyEvaluated` 为 `false`，在线案例为 `true`。

`timeout` 和 `error` 是算法边界基准的结果类别，不自动判为代码缺陷；应结合案例、错误信息、稳定运行率和人工复核再判断。报告结论必须人工复核，不能把预测零冲突或在线安全门表述为完整 MAPF 保证或任意输入下的全规划时域零冲突保证。

`medianWallClockMs` 和 `p95WallClockMs` 仅用于观察当前机器上的离线运行分布。真实 wall-clock 只属于同机离线证据，日常 pytest 不以它设置通过或失败阈值；应与正确性、稳定运行率及案例上下文一起人工复核。

算法边界报告 `schemaVersion = 2`。直接规划运行设置 `planningDiagnosticsEvaluated = true`，并记录：

- `pathCandidateCount`
- `selectedPathCandidateIndex`
- `failedPathCandidateCount`
- `timedAStarCallCount`
- `timedAStarExpandedStateCount`
- `maxTimedAStarExpandedStateCount`
- `timedAStarExhaustedSearchCount`
- `timedAStarGoalFullyReservedRejectCount`

在线、超时和异常记录不伪造规划工作量；未评价时 `planningDiagnosticsEvaluated = false`，其余字段为 `null`。扩展状态数是确定性工作量指标，墙钟中位数和 P95 仍只用于同机人工比较。

最终 `results.json`、`runs.csv` 和 `case-summaries.csv` 按一个报告 bundle 发布：先完整写入临时文件，再备份同名旧文件，最后逐一替换。任一备份或发布步骤失败时，会删除本轮已发布文件并恢复旧 bundle；原来没有 bundle 时则不留下半套最终文件，`results.partial.json` 继续保留。若恢复本身失败，可恢复的 `.backup` 会保留，异常同时报告最初发布错误、恢复错误和备份路径，避免把新旧三文件混成一次成功报告。

算法边界基准和自适应校准共用隔离子进程清理。父进程先通过 Pipe 的 `poll`/`recv` 接收完整载荷，再等待或清理 worker；超时、`KeyboardInterrupt` 等 `BaseException` 和父进程异常都会进入 `finally`，按“终止、有限等待、必要时强制终止、再次有限等待”的顺序确认退出并关闭进程与 Pipe 端点。普通取消在清理后重新抛出原对象；若无法确认 worker 已停止或资源关闭失败，则升级为基础设施错误并保留原异常链。

## 离线自适应窗口校准

自适应窗口校准是独立命令行取证流程，不新增实验 API，也不允许 HTTP 调用方传入内部 `AdaptiveReplanPolicy`。在项目根目录运行默认完整校准：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan
```

默认参数为 `--repetitions 5`、`--timeout-seconds 30` 和 `--output-dir output/adaptive-replan-calibration`。案例、变体和默认运行规模精确为：

- 三个案例：`adaptive-low-load-r4-t17`、`adaptive-pressure-r8-t45`、`adaptive-transition-r4-t6`。
- 四个变体：`fixed-4`、`fixed-24`、`fixed-48`、`adaptive-current-24`。
- 每个“案例 × 变体”重复 5 次，因此默认共 `3 × 4 × 5 = 60` 条运行记录和 12 条变体汇总。

命令在输出根目录下创建 UTC 时间戳子目录。成功完成后保留四个最终文件：

1. `results.json`
2. `runs.csv`
3. `replan-observations.csv`
4. `variant-summaries.csv`

每条子进程结果完成时会原子更新 `results.partial.json`；四个最终文件全部成功写入后删除 partial 文件。最终 `results.json.schemaVersion = 1`，JSON 使用 UTF-8 无 BOM，三份 CSV 使用 UTF-8 BOM。`replan-observations.csv` 只展开真实 `run_dispatch` 产生的观测，其行数应等于 JSON 中所有运行的 `replanCount` 之和。

候选范围只使用 `variantId = "fixed-24"`、`outcome = "completed"` 且 `correctnessStable = true` 的观测。三个默认案例各自都至少贡献 3 条合格观测时，`candidateEnvelopeAvailable` 才为 `true` 且 `candidateEnvelope` 才非空；否则必须报告不可用和 `null`，不能用其他变体或不稳定运行补足。范围按合格观测的 nearest-rank 分位数构造：

- 慢状态退出候选：重规划耗时 P50 到 P75。
- 慢状态进入候选：重规划耗时 P75 到 P95。
- 任务压力倍数候选：任务压力比 P50 到 P75。

候选范围只是同机证据，供后续单独批准的生产策略决策人工复核，不是自动推荐，也不会修改当前 `60/40ms`、最近 5 个样本且至少 3 个样本、`2×` 压力规则。`timeout`、`error` 和 completed-but-unstable 运行必须按原始记录报告；不得提高 30 秒超时、删除不稳定记录或弱化正确性条件来美化结论。该流程不证明跨机器阈值可移植性、完整 MAPF 能力或任意输入下的全规划时域零冲突。

### 2026-07-26 修复前默认 60-run 人工复核

本次同机、无并发重型命令的结果目录为 `output/adaptive-replan-calibration/20260726T103029Z`。该目录是未跟踪的本地证据，不进入 Git。交叉核对结果：

- `60` 条 run 全部为 `completed`，其中 `40` 条 stable、`20` 条 completed-but-unstable，`timeout = 0`、`error = 0`。
- `runs.csv` 为 60 行，`replan-observations.csv` 为 1,310 行并等于全部 `replanCount` 之和，`variant-summaries.csv` 为 12 行。
- 四个 final 文件存在，`results.partial.json` 与 `*.tmp` 不存在；JSON 为 UTF-8 无 BOM，三份 CSV 为 UTF-8 BOM；校准前后均无新增 `multiprocessing.spawn` worker。
- stable completed `fixed-24` 合格观测数为：`adaptive-low-load-r4-t17 = 100`、`adaptive-pressure-r8-t45 = 0`、`adaptive-transition-r4-t6 = 30`。因此 `candidateEnvelopeAvailable = false`、`candidateEnvelope = null`，没有可报告的候选 envelope。
- 仅对现有合格观测计算的分布为：重规划耗时 P50/P75/P95 = `1.23/2.25/4.07ms`，任务压力比 P50/P75/P95 = `0.25/1.0/2.75`，样本数均为 130。由于压力案例没有合格 `fixed-24` 观测，这些分位数不能替代不可用的 candidate envelope。

全部 20 条 completed-but-unstable 记录如下；字段值来自 `results.json`，没有删除或合并运行：

| `caseId` | `variantId` | `runIndex` | `predictedConflictCount` | `activeConflictCount` | `deadlineMissCount` | `failureCount` | `safetyInterventionCount` | `actualCompletionRatePercent` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `adaptive-pressure-r8-t45` | `fixed-4` | 1 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-4` | 2 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-4` | 3 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-4` | 4 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-4` | 5 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-24` | 1 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-24` | 2 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-24` | 3 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-24` | 4 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-24` | 5 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-48` | 1 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-48` | 2 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-48` | 3 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-48` | 4 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `fixed-48` | 5 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `adaptive-current-24` | 1 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `adaptive-current-24` | 2 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `adaptive-current-24` | 3 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `adaptive-current-24` | 4 | 0 | 0 | 1 | 2 | 0 | 95.6 |
| `adaptive-pressure-r8-t45` | `adaptive-current-24` | 5 | 0 | 0 | 1 | 2 | 0 | 95.6 |

修复前人工结论：低负载和过渡案例共 40 条运行均稳定，且自适应变体确实记录到扩大、保持和收缩窗口；压力案例中四个变体的五次重复均以相同正确性指标不稳定，说明这组历史证据不能把问题归因于某一个窗口，也不能据此推荐新的慢状态或压力阈值。

### 2026-07-26 修复后默认 60-run 人工复核

压力案例的修复只发生在校准用的 deep copy：所有机器人使用 `battery=150`、`batteryCapacity=150`，所有非空 base-task deadline 统一为 T=120；源 `density-r8-t43` 和其他案例未被改写。校准 run 级 `totalDistance` 改为终止时 `MetricSnapshot.travelledDistance`，缺少指标历史时直接记录错误，不再静默回退到零。

fresh 同机、无并发重型命令的结果目录为 `output/adaptive-replan-calibration/20260726T150547Z`。该目录是未跟踪的本地证据，不进入 Git。Step 7 打印对象精确为：

```json
{"runs": 60, "stable": 60, "observations": 1330, "summaries": 12, "outcomes": {"completed": 60}, "pressureDistanceRange": [671, 671], "candidateEnvelope": {"slowExitThresholdMs": {"min": 9.71, "max": 31.05}, "slowEnterThresholdMs": {"min": 31.05, "max": 69.47}, "taskPressureMultiplier": {"min": 1.75, "max": 3.38}}}
```

交叉核对确认 60 条 run 全部 `completed` 且 `correctnessStable = true`，1,330 条 `replan-observations.csv` 记录等于全部 `replanCount` 之和，`variant-summaries.csv` 为 12 行。20 条压力 run 均完成 45/45 已释放任务，预测/活动冲突、安全介入、超期和失败均为 0，累计距离范围为 `[671, 671]`。三份 CSV 的 UTF-8 BOM、`results.partial.json`/`*.tmp` 清理及校准后 worker 残留检查均通过。

本次 candidate envelope 是同机人工复核证据，不是自动推荐；没有应用任何阈值。生产继续使用 `60/40ms`、最近 5 个样本且至少 3 个样本和 `2×` 压力规则。

## 当前使用边界

1. 实验 API、算法边界基准和自适应校准继续作为内部回归与诊断工具，不是当前独立开发目标。
2. 不因为已有 JSON/CSV 结果而自动启动图表、截图、技术报告或答辩材料整理。
3. 不新增前端实验面板，除非后续系统调试出现主界面必须解释的操作需求并得到明确批准。
4. 不根据同机候选范围自动修改生产 `60/40ms`、最近5个样本且至少3个样本或 `2×` 压力规则。
5. 当前没有选定下一项实验、性能调优或算法替换工作；需要时应先从具体系统问题和验收目标重新设计。
