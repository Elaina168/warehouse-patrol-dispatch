# 自适应重规划窗口校准取证设计

日期：2026-07-26
状态：设计方向已确认，待书面规格审阅

## 1. 背景与问题

当前在线会话已经支持固定滚动窗口和可选自适应窗口。自适应模式以 `assignmentReplanWindow` 为基准，根据近期规划耗时、已释放任务压力和远期任务数量，在收缩、保持和扩大三种窗口之间切换。

当前生产规则位于 `backend/app/replan_window.py`：

- 最近 `5` 次真实重规划耗时进入样本窗口。
- 至少积累 `3` 个样本后才允许改变慢状态。
- 非慢状态下，中位数达到 `60ms` 时进入慢状态。
- 慢状态下，中位数降至 `40ms` 时退出慢状态。
- 已释放任务数量达到可用机器人数量的 `2` 倍时进入任务压力收缩。
- 固定窗口仍是默认模式。

在线运行时加固已经把单次耗时触发改成五样本中位数与 `60/40ms` 迟滞，消除了单次墙钟抖动直接切换窗口的问题。但 `60ms`、`40ms` 和两倍任务压力仍是经验值，且墙钟耗时受机器、后台负载和 Python 运行环境影响。现有算法边界基准记录直接规划的工作量和在线瓶颈结果，但在线案例仍使用固定 `assignmentReplanWindow = 120`，不能回答当前自适应阈值是否合理。

本阶段先建立可重复的离线校准取证链路，不直接修改生产阈值。校准报告用于回答：

1. 当前自适应策略与固定 `4T`、`24T`、`48T` 相比，是否保持任务覆盖、截止时间和执行安全。
2. 当前策略在低负载、任务压力和混合转换场景中实际何时收缩、保持或扩大。
3. 每次窗口决策前的规划耗时分布和任务/机器人压力比分布是什么。
4. 真实分布给出的候选区间是否支持后续修改 `60/40ms` 和两倍压力阈值。

## 2. 目标

- 新增独立的离线自适应窗口校准命令，不修改六个实验 API。
- 通过普通在线会话运行三类确定性校准案例。
- 对比 `fixed-4`、`fixed-24`、`fixed-48` 和 `adaptive-current-24` 四个变体。
- 只在真实发生 `run_dispatch` 时记录一条重规划观测，不把被动 tick 或缓存响应误记为重规划。
- 记录窗口决策输入、决策结果、规划耗时、慢状态变化和确定性规划工作量。
- 从稳定的 `fixed-24` 观测中计算耗时与任务压力分位数，生成候选区间，不自动选择或写回生产阈值。
- 每次运行继续使用独立 Windows `spawn` 子进程、有限时等待和可靠清理。
- 输出 UTF-8 JSON、逐运行 CSV、逐重规划 CSV 和逐变体汇总 CSV。
- 保持现有在线 API、前端类型、OpenAPI、默认自适应行为和算法边界基准 schema 不变。

## 3. 非目标

- 本阶段不修改 `SLOW_REPLAN_ENTER_THRESHOLD_MS = 60`。
- 本阶段不修改 `SLOW_REPLAN_EXIT_THRESHOLD_MS = 40`。
- 本阶段不修改两倍任务压力规则。
- 不让程序自动挑选“最佳策略”，也不根据墙钟数据自动修改源码。
- 不把同机校准结果宣称为跨机器通用阈值。
- 不修改 beam search、候选顺序、时空 A*、充电、锁定、抢占或恢复语义。
- 不实现局部机器人放行、CBS、ECBS、完整 MAPF 或全时域零冲突保证。
- 不新增前端页面、实验面板、API 路由、Pydantic API 字段或前端类型。
- 不把重型校准加入日常 `npm run check`。
- 不提交 `output/`、`.superpowers/` 或生成的校准报告。

## 4. 方案选择

采用“两阶段取证、后续单独决策”方案：

1. 本阶段实现真实在线校准运行、重规划观测和分布报告。
2. 人工复核报告后，再单独确认是否需要比较新的候选策略或修改生产默认值。

未采用以下方案：

- **直接把阈值改成新的固定数字**：当前没有可复核的在线分布证据，任何新数字都只是替换一组经验值。
- **自动从一次运行选出生产阈值**：同机墙钟数据不能证明跨环境通用性，自动写回还会把取证和生产行为变更混在一起。
- **扩展 `/api/experiments/replan-window`**：校准需要逐重规划内部观测和大量重复运行，不应扩大公开 API 或主前端契约。
- **复用算法边界报告 schema**：现有 `schemaVersion = 2` 面向规模、密度和瓶颈边界；自适应校准包含变体和嵌套重规划观测，强行混合会破坏既有报告语义。
- **直接评估或引入 CBS/MAPF**：当前缺口是现有自适应策略的证据与阈值校准，不是求解器替换。

## 5. 自适应策略的内部可注入边界

### 5.1 `AdaptiveReplanPolicy`

在 `backend/app/replan_window.py` 增加内部不可变配置：

```python
@dataclass(frozen=True, slots=True)
class AdaptiveReplanPolicy:
    slow_enter_threshold_ms: float
    slow_exit_threshold_ms: float
    task_pressure_multiplier: float
```

增加：

```python
DEFAULT_ADAPTIVE_REPLAN_POLICY = AdaptiveReplanPolicy(
    slow_enter_threshold_ms=SLOW_REPLAN_ENTER_THRESHOLD_MS,
    slow_exit_threshold_ms=SLOW_REPLAN_EXIT_THRESHOLD_MS,
    task_pressure_multiplier=2,
)
```

现有常量名称和值保留，避免现有测试、文档和调用方发生无关漂移。

策略创建时必须验证：

- 三个值均为有限数值。
- `slow_exit_threshold_ms >= 0`。
- `slow_enter_threshold_ms > slow_exit_threshold_ms`。
- `task_pressure_multiplier > 0`。

`update_latency_slow_state` 和 `decide_replan_window` 增加仅限 Python 内部的关键字参数：

```python
policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY
```

未传入策略时必须逐输入保持当前行为。生产 API、直接调度和现有实验均不传入自定义策略。

任务压力判断从固定的：

```python
released_task_count >= active_robot_count * 2
```

改为语义等价的：

```python
released_task_count >= active_robot_count * policy.task_pressure_multiplier
```

默认策略下结果必须完全一致。

### 5.2 会话内部注入

`DispatchSession` 增加两个不进入响应、不进入初始快照比较的内部字段：

```python
adaptive_replan_policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY
replan_observer: Callable[[ReplanObservation], None] | None = field(
    default=None,
    repr=False,
    compare=False,
)
```

`create_session` 增加内部关键字参数：

```python
adaptive_replan_policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY
replan_observer: Callable[[ReplanObservation], None] | None = None
```

`backend/app/main.py` 继续只调用：

```python
create_session(request)
```

因此 OpenAPI 和 HTTP 请求结构不变。只有离线校准运行器传入 observer；本阶段的四个变体全部使用 `DEFAULT_ADAPTIVE_REPLAN_POLICY`，不运行尚未获批的新阈值。

`reset_session` 清除近期耗时样本、慢状态和窗口状态，但保留同一会话的内部 policy 与 observer。校准流程本身不调用 reset，此行为只用于保证内部生命周期完整。

## 6. 真实重规划观测

### 6.1 `ReplanObservation`

在 `backend/app/replan_window.py` 增加不可变内部观测结构：

```python
@dataclass(frozen=True, slots=True)
class ReplanObservation:
    time: int
    configured_window: int
    effective_window: int
    reason: str
    released_task_count: int
    future_task_count: int
    active_robot_count: int
    task_pressure_ratio: float
    latency_samples_before_ms: tuple[float, ...]
    latency_median_before_ms: float | None
    latency_slow_before: bool
    replan_time_ms: float
    latency_slow_after: bool
    path_candidate_count: int
    selected_path_candidate_index: int | None
    failed_path_candidate_count: int
    timed_astar_call_count: int
    timed_astar_expanded_state_count: int
    max_timed_astar_expanded_state_count: int
    timed_astar_exhausted_search_count: int
    timed_astar_goal_fully_reserved_reject_count: int
```

`task_pressure_ratio` 精确计算为：

```python
released_task_count / max(1, active_robot_count)
```

`latency_median_before_ms` 在没有样本时为 `None`，否则为本轮决策前最多五个样本的中位数。

### 6.2 记录时机

仅在 `backend/app/sessions.py::_build_result` 确实调用 `run_dispatch` 的分支记录观测：

1. 保存本轮决策前的近期耗时样本和 `adaptive_latency_slow`。
2. 使用相同输入计算 `ReplanWindowDecision`。
3. observer 非空时创建新的 `PlanningDiagnostics`，传给 `run_dispatch`。
4. 完成现有 `_record_replan_window_decision`。
5. 完成现有 `_record_replan_latency`。
6. 构造 `ReplanObservation` 并调用 observer。

以下操作不得产生新观测：

- 被动 tick 复用 `last_result`。
- 同一 tick 重复读取会话。
- 列表、摘要、删除或响应重新序列化。
- 仅刷新 `conflictStates`、`eventLog` 或 metrics snapshot。

初始会话创建在 `T=0` 确实执行一次规划，因此记录一条观测。

observer 只供进程内校准使用。生产会话的 observer 为 `None`，不得创建或保留观测列表。校准 observer 固定使用不会抛错的 `list.append`；observer 抛出的异常按校准运行错误处理，不吞掉或伪装为调度成功。

### 6.3 结果不变保证

启用 observer 和 `PlanningDiagnostics` 后，除真实墙钟字段 `replanTimeMs` 外，`DispatchResult.model_dump(mode="json")` 必须与 observer 关闭时一致。测试比较时先移除 `metrics.replanTimeMs`，不得通过弱化任务、冲突、失败或路径断言来通过。

## 7. 校准案例

新增 `backend/benchmarks/adaptive_scenarios.py`。该模块复用 `backend.benchmarks.scenarios.build_benchmark_scenario`，只做确定性深拷贝和释放时间调整，不修改原有九案目录。

所有校准案例：

- 使用 `avoidConflicts=True`。
- 使用 `includeDynamic=True`。
- 使用普通在线会话，保持 `enforce_execution_safety=True`。
- 从 `T=0` 逐 tick 推进到 `T=120`。
- 在完成 `T=20` 和 `T=40` 的 tick 后，各通过现有 `add_task` 插入一个运行时 emergency 任务，确保每个会话产生足够的真实重规划样本。
- 运行时任务 ID 分别按 `{caseId}-runtime-20` 和 `{caseId}-runtime-40` 生成。
- 运行时任务 `releaseTime` 等于插入 tick，`deadline` 等于插入 tick 加 `40`，`priority = 5`。
- 运行时任务目标优先取 `scenario.zones.inspection[0]`；若 inspection 为空，则精确取 `scenario.zones.delivery[0]`。案例构造测试必须证明两者至少一个存在。

### 7.1 `adaptive-low-load-r4-t17`

- 基础场景：`scale-r4-t15`。
- 保留 `4` 台机器人、`12` 个基础 inspection 任务和 `3` 个动态 emergency 任务。
- 按任务在现有列表中的索引，把基础任务 `releaseTime` 设置为 `48 + index % 3`。
- 保留基础任务现有 deadline；最晚基础 release 为 `50`，仍早于该场景最早 deadline。
- 把 `dynamic.triggerTime` 设置为 `72`。
- 加入两个运行时任务后，最终任务数为 `17`。

该案例在 `T=0` 没有已释放任务但存在远期任务，用于稳定触发当前自适应策略的低负载扩大分支。

### 7.2 `adaptive-pressure-r8-t45`

- 基础场景：`density-r8-t43`。
- 保留种子 `43`、`8` 台机器人、`40` 个基础任务、`3` 个动态任务和现有 release/deadline。
- 加入两个运行时任务后，最终任务数为 `45`。

该案例在前几个 tick 内快速累积大量已释放任务，用于观察任务/机器人压力比和规划耗时。

### 7.3 `adaptive-transition-r4-t6`

- 基础场景：`bottleneck-r4-t4`。
- 保留 `4` 台机器人和共享瓶颈地图。
- `D1`、`D2` 的 `releaseTime` 设置为 `0`。
- `D3`、`D4` 的 `releaseTime` 设置为 `48`。
- 保留现有 deadline。
- 加入两个运行时任务后，最终任务数为 `6`。

该案例用于观察有当前任务、阶段性低负载和后续任务释放之间的窗口转换。

### 7.4 确定性要求

案例测试必须验证：

- 三个 `caseId`、机器人数量、最终任务数量和 `tickTarget` 精确匹配目录。
- 重复构造得到完全一致的 `Scenario.model_dump(mode="json")`。
- 所有任务 ID 唯一。
- 所有目标在地图内且不是静态障碍。
- release/deadline 关系有效。
- `Scenario.model_validate` 和现有后端 `validate_scenario` 均通过。
- 原有 `benchmark_cases()` 和九个边界案例内容不变。

## 8. 对比变体

新增 `backend/benchmarks/adaptive_variants.py`，提供固定目录：

| `variantId` | `assignmentReplanWindow` | `adaptiveReplanWindow` |
| --- | ---: | --- |
| `fixed-4` | 4 | false |
| `fixed-24` | 24 | false |
| `fixed-48` | 48 | false |
| `adaptive-current-24` | 24 | true |

四个变体均使用 `DEFAULT_ADAPTIVE_REPLAN_POLICY`。固定模式仍记录真实重规划观测，但 `reason` 必须为现有的 `固定窗口`，且 `effectiveWindow` 必须等于配置值。

案例执行顺序固定为目录顺序；每个案例内按上述变体顺序运行；每个变体内按 `runIndex = 1..repetitions` 运行。报告不得通过随机打乱隐藏机器负载趋势；人工复核时同时查看运行顺序和墙钟分布。

## 9. 子进程隔离复用

现有 `backend/benchmarks/runner.py` 已实现并验证：

- Windows `spawn`。
- 父进程先 `poll/recv`，避免大载荷管道回压。
- timeout 后执行 `terminate -> 有界 join -> kill -> 有界 join`。
- 无法确认子进程停止时抛出 `BenchmarkInfrastructureError`。
- 单次普通 timeout/error 不丢失整批结果。

校准不得复制一份不同的进程清理逻辑。新增 `backend/benchmarks/process_isolation.py`，把现有已审查的管道、错误净化和清理行为提取为领域无关的隔离执行器。现有算法边界运行器和新的校准运行器都调用它。

共享隔离执行器返回：

```python
@dataclass(frozen=True, slots=True)
class IsolatedExecution:
    outcome: Literal["completed", "timeout", "error"]
    value: object | None
    error_type: str | None
    error_message: str | None
    wall_clock_ms: float
```

领域运行器分别把 `value` 转换为 `BenchmarkRun` 或 `AdaptiveCalibrationRun`。共享层不导入任何场景、调度或报告模型。

行为要求：

- worker 必须可 pickle；启动前失败记录为结构化 error。
- worker 内异常保留有限的 `errorType` 与最多 `500` 字符单行 `errorMessage`。
- completed payload 类型不匹配时记录结构化 error。
- timeout 和普通 error 后继续后续运行。
- `BenchmarkInfrastructureError` 立即终止整批，不能降级成普通运行结果。
- 迁移后现有算法边界进程测试和输出语义必须保持。

## 10. 运行结果与观测结果

### 10.1 `AdaptiveCalibrationRun`

新增 `backend/benchmarks/adaptive_results.py`，定义逐运行结果。JSON 使用现有项目的 camelCase：

- `caseId`
- `variantId`
- `runIndex`
- `robotCount`
- `taskCount`
- `tickTarget`
- `outcome`
- `errorType`
- `errorMessage`
- `correctnessStable`
- `releasedTaskCount`
- `coveredTaskCount`
- `completedTaskCount`
- `coverageRatePercent`
- `actualCompletionRatePercent`
- `predictedConflictCount`
- `activeConflictCount`
- `safetyInterventionCount`
- `safetyStallReached`
- `maxConsecutiveSafetyInterventionCount`
- `deadlineMissCount`
- `failureCount`
- `totalDistance`
- `makespan`
- `wallClockMs`
- `replanCount`
- `windowChangeCount`
- `replanObservations`

`replanCount` 等于 `replanObservations` 数量。`windowChangeCount` 比较相邻观测的 `effectiveWindow`，首条观测不计为变化。

`safetyStallReached` 在任一响应出现非空 `safetyStall` 时为 `true`；`maxConsecutiveSafetyInterventionCount` 取所有非空 `safetyStall.consecutiveCount` 的最大值，没有停滞时为 `0`。

`correctnessStable` 只检查：

- 最终 `coveredTaskCount == taskCount`。
- 最终 `activeConflictCount == 0`。
- `deadlineMissCount == 0`。
- `failureCount == 0`。

墙钟、窗口变化次数、完成率和安全介入次数不进入 `correctnessStable`，避免把取证指标伪装成正确性定理。

timeout/error 记录不可用业务字段为 `null`，`correctnessStable = false`，已知的 case/variant/run 配置仍必须保留。

### 10.2 逐重规划记录

每条 `ReplanObservation` 序列化为 camelCase，并附加：

- `caseId`
- `variantId`
- `runIndex`
- `observationIndex`

`observationIndex` 从 `1` 开始，严格按 observer 收到的顺序递增。

## 11. 汇总与候选区间

### 11.1 逐变体汇总

每个 `caseId + variantId` 生成一条 `AdaptiveVariantSummary`：

- `caseId`
- `variantId`
- `runCount`
- `completedRunCount`
- `timeoutCount`
- `errorCount`
- `stableRunCount`
- `stableRunRatePercent`
- `medianWallClockMs`
- `p95WallClockMs`
- `medianRunReplanTimeMs`
- `p95RunReplanTimeMs`
- `medianReplanCount`
- `medianWindowChangeCount`
- `medianCoverageRatePercent`
- `medianActualCompletionRatePercent`
- `maxSafetyInterventionCount`
- `maxConsecutiveSafetyInterventionCount`
- `windowReasonCounts`

为避免重规划次数多的单次运行获得更高权重：

1. 先计算每个 completed run 内 `replanTimeMs` 的中位数。
2. `medianRunReplanTimeMs` 和 `p95RunReplanTimeMs` 再对这些“逐运行中位数”汇总。

P95 使用现有 `nearest_rank_p95` 语义。

### 11.2 观测分布

候选区间只使用：

- `variantId == "fixed-24"`。
- `outcome == "completed"`。
- `correctnessStable == true`。

跨三个案例汇总所有真实重规划观测，分别计算：

```text
replanTimeMs: p50 / p75 / p95 / sampleCount
taskPressureRatio: p50 / p75 / p95 / sampleCount
```

P50、P75、P95 均使用 nearest-rank。报告保留两位小数。

### 11.3 候选区间

报告按以下确定性映射生成候选区间：

```text
candidateEnvelope.slowExitThresholdMs.min = observedLatencyMs.p50
candidateEnvelope.slowExitThresholdMs.max = observedLatencyMs.p75
candidateEnvelope.slowEnterThresholdMs.min = observedLatencyMs.p75
candidateEnvelope.slowEnterThresholdMs.max = observedLatencyMs.p95
candidateEnvelope.taskPressureMultiplier.min = observedTaskPressureRatio.p50
candidateEnvelope.taskPressureMultiplier.max = observedTaskPressureRatio.p75
```

以上是观察区间，不是自动推荐：

- 不保证区间内任意组合都满足严格的 exit < enter。
- 任务压力分布可能包含 `0`；该端点只是观测结果，不能绕过 policy 的正数校验。
- 不自动生成新 `AdaptiveReplanPolicy`。
- 不自动排名或写回 `backend/app/replan_window.py`。
- 下一阶段必须结合四个现有变体的正确性、完成率、规划耗时、窗口抖动和安全证据人工选择候选策略。

只有三个校准案例都至少贡献 `3` 条来自稳定 completed `fixed-24` 运行的观测时，`candidateEnvelopeAvailable = true`。这个门槛与当前自适应策略改变慢状态所需的最少样本数一致，并保证低负载、任务压力和混合转换三类输入都进入区间计算。

如果任一案例不满足上述条件：

- `candidateEnvelopeAvailable = false`。
- `candidateEnvelope = null`。
- 观测分布仍报告实际 `sampleCount`；样本为空时各分位数字段为 `null`。
- 报告仍正常写出，不伪造数值。

## 12. 报告契约

新增 `backend/benchmarks/adaptive_reporting.py`。默认输出目录：

```text
output/adaptive-replan-calibration/{YYYYMMDDTHHMMSSZ}/
```

生成：

```text
results.json
runs.csv
replan-observations.csv
variant-summaries.csv
```

执行中维护：

```text
results.partial.json
```

报告根结构：

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-07-26T00:00:00Z",
  "config": {},
  "runs": [],
  "variantSummaries": [],
  "observationDistribution": {},
  "candidateEnvelopeAvailable": true,
  "candidateEnvelope": {}
}
```

`config` 必须记录：

- `caseIds`
- `variantIds`
- `repetitions`
- `timeoutSeconds`
- `tickTarget`
- `runtimeTaskTicks`
- `outputDir`
- `defaultPolicy`

JSON 使用 UTF-8、无 BOM、`ensure_ascii=False` 和结尾换行。三份 CSV 使用 UTF-8 BOM。字段表必须由常量固定，`DictWriter(extrasaction="raise")` 防止模型与 CSV 静默漂移。

每次运行完成后，以“同目录临时文件写入后原子 replace”的方式更新 `results.partial.json`。最终四份报告全部成功后才删除 partial。任何最终文件写入失败都返回非零退出码并保留可用的 partial。

生成结果保持未跟踪，不进入 Git。

## 13. CLI

新增：

```text
backend/benchmarks/adaptive_replan_calibration.py
```

新增 npm 命令：

```json
"benchmark:adaptive-replan": ".\\.venv\\Scripts\\python.exe -m backend.benchmarks.adaptive_replan_calibration"
```

命令参数：

```text
--cases CASE_IDS
--variants VARIANT_IDS
--repetitions REPETITIONS
--timeout-seconds TIMEOUT_SECONDS
--output-dir OUTPUT_DIR
```

默认：

```text
cases = 全部三个校准案例
variants = 全部四个变体
repetitions = 5
timeout-seconds = 30
output-dir = output/adaptive-replan-calibration
```

默认完整运行共：

```text
3 cases × 4 variants × 5 repetitions = 60 runs
```

所有参数必须在创建输出目录前完成验证：

- cases 非空且全部来自固定目录。
- variants 非空且全部来自固定目录。
- repetitions 为正整数。
- timeout 为有限正数。

命令成功时只向 stdout 输出最终绝对结果目录。配置错误和运行失败写 stderr 并返回非零退出码。

## 14. 错误与中断语义

- 单次 timeout：记录 timeout，继续后续运行。
- 单次 worker 异常：记录 error，继续后续运行。
- 报告写入失败：立即停止并返回非零；保留已成功写出的 partial。
- 无法确认子进程停止或通信端点清理失败：抛出 `BenchmarkInfrastructureError`，立即终止整批。
- 用户中断：不把 partial 改名为最终报告。
- completed 但 `correctnessStable = false`：属于算法证据，不伪装为进程错误，也不自动改变生产阈值。
- 同机墙钟分位数只供人工比较，不加入 pytest 的真实毫秒上限。

## 15. 测试设计

### 15.1 策略兼容

- 默认 policy 的边界仍是进入 `60ms`、退出 `40ms`、压力倍数 `2`。
- 隐式默认 policy 与显式 `DEFAULT_ADAPTIVE_REPLAN_POLICY` 对相同输入返回相同结果。
- 非有限、负数、exit 大于等于 enter、非正压力倍数被拒绝。
- 固定模式忽略慢状态和压力，保持配置窗口。

### 15.2 observer 生命周期

- 初始创建产生一条 `T=0` 观测。
- 被动 tick 和同 tick GET 不增加观测。
- 运行时任务插入产生一次真实观测。
- 每条观测的决策前样本、慢状态前后值和规划诊断与实际运行一致。
- reset 清空自适应运行状态但保留内部 observer 和 policy。
- observer 关闭时不创建规划诊断对象。
- observer 开启前后，去掉 `replanTimeMs` 后的调度结果完全一致。

### 15.3 案例与变体

- 三个校准 case 精确匹配第 7 节。
- 四个 variant 精确匹配第 8 节。
- 筛选保持目录顺序。
- 未知或空 case/variant 被拒绝。
- 原九个算法边界案例不变。

### 15.4 运行器

- 三个案例均使用普通在线安全会话。
- tick 逐次推进到 `120`。
- `T=20`、`T=40` 通过 `add_task` 插入精确 ID 的任务。
- 会话在成功、异常和 timeout 路径都被删除。
- 安全介入和连续停滞按实际响应累计。
- 单次失败不丢失后续运行。

### 15.5 进程隔离迁移

- 现有大错误载荷不会被误判为 timeout。
- 父进程先接收 Pipe 载荷再清理。
- terminate 失败或进程仍存活时使用 kill 后备。
- 无法确认停止时向上抛基础设施错误。
- 现有算法边界 focused tests 全部保持。

### 15.6 统计

- nearest-rank P50、P75、P95 边界正确。
- 逐运行中位数先算、逐变体再汇总。
- timeout/error 不进入 completed 规划耗时分布。
- 非稳定 `fixed-24` 不进入候选区间。
- 没有稳定样本时输出显式不可用状态。
- `windowChangeCount` 不把首条观测计为变化。
- `windowReasonCounts` 与逐重规划记录一致。

### 15.7 报告与 CLI

- JSON camelCase 字段精确匹配 schema v1。
- 三份 CSV 表头与模型字段完全一致。
- 中文 reason 以 UTF-8 正确往返。
- partial 每次运行后原子更新，成功后清除。
- 无效参数在创建目录前失败。
- 同秒输出目录不会覆盖。
- npm smoke 使用 `--repetitions 1`，产生 `12` 条运行记录。

## 16. 验证顺序

实施阶段按以下顺序验证：

1. 纯策略和 observer 聚焦测试。
2. 校准案例、变体、统计和报告聚焦测试。
3. 现有算法边界基准测试，验证进程隔离迁移没有回归。
4. 在线会话与自适应窗口聚焦回归。
5. `npm run benchmark:adaptive-replan -- --repetitions 1` smoke。
6. 完整 `npm run check`。
7. 无并发重型任务时运行默认 `60` 次校准。
8. 人工交叉核对 JSON 与三份 CSV 的运行数、观测数、分位数、UTF-8 和 partial 清理状态。
9. 检查运行前后没有残留校准 Python worker。

## 17. 验收标准

- 未传入内部 policy/observer 时，生产在线行为和 API 契约保持不变。
- 默认 policy 与当前 `60/40ms`、两倍压力语义完全一致。
- 三个校准案例和四个变体可通过独立子进程完成 smoke。
- 每条重规划观测只对应一次真实 `run_dispatch`。
- 默认完整校准生成 `60` 条运行记录以及数量一致的 JSON/CSV。
- timeout/error/不稳定运行都能被诚实记录，且不会伪造候选区间。
- 报告给出基于稳定 `fixed-24` 数据的观察分布和候选区间，但不自动修改生产阈值。
- 现有算法边界 schema v2、九案结果契约和进程可靠性保持。
- 完整 `npm run check` 通过。
- 不提交任何生成报告。

## 18. 后续决策门禁

本阶段结束后必须人工审阅：

- 四个变体的稳定率和完成率。
- 自适应窗口变化次数与 reason 分布。
- 当前 `60/40ms` 是否在观察分布之外或导致长期不触发。
- 当前两倍压力是否过早或过晚收缩。
- 规划耗时改善是否伴随任务覆盖、截止时间或安全退化。

只有审阅后才能选择下一项：

1. 保留当前生产阈值，仅把校准报告用于竞赛证据。
2. 设计并运行一组获批候选 policy 对比。
3. 用确定性规划工作量替代或补充墙钟慢状态。
4. 如果出现现有优先级规划器无法解决的稳定边界，再独立评估 MAPF/CBS。

任何生产阈值变更都必须使用新的设计和实施计划，不属于本规格。
