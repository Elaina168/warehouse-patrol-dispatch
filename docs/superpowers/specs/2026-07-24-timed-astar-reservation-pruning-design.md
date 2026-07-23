# 时空 A* 目标预留剪枝与性能诊断设计

## 1. 目标

在不改变现有调度结果语义的前提下，消除 `astar_timed` 对“目标格在整个搜索时域内均被占用”的确定性无解搜索，并为离线算法边界基准补充可重复核对的规划工作量数据。

本阶段回答两个问题：

1. `density-r8-t31` 和 `density-r8-t43` 为什么比 `density-r8-t55` 慢。
2. 能否在保持全任务分配、零预测冲突、零失败和零截止超期的前提下，提前拒绝已经可以确定无解的路径候选，并切换到下一个机器人规划顺序。

这是一项局部性能优化，不改变任务分配算法、路径候选评分、在线执行安全门或 API 契约。

## 2. 已确认事实

最终边界基准 `20260722T084744Z` 中，三个固定 seed 43、8 机器人密度案例的规划耗时为：

| 案例 | `medianReplanTimeMs` | `p95ReplanTimeMs` |
| --- | ---: | ---: |
| `density-r8-t31` | 1245.45 | 1281.71 |
| `density-r8-t43` | 1262.46 | 1267.58 |
| `density-r8-t55` | 224.83 | 229.29 |

只读函数级剖析进一步确认：

- `density-r8-t31` 和 `density-r8-t43` 的首个路径候选各有一个失败机器人；随后第二个机器人规划顺序成功。
- 第二个候选本身只需约十几毫秒，主要耗时来自首个候选中的一次 `astar_timed` 失败搜索。
- 两次慢搜索都是从 `[1, 1]` 到 `[16, 10]`。
- 两次搜索从各自最早可能到达时刻到 `max_time` 的每个目标顶点时间槽都已存在于 `Reservations.vertices`，可用目标到达时刻数量为 `0`。
- 当前 `astar_timed` 没有在入队前检查这一确定性条件，而是遍历时空状态直到堆为空或超过搜索时域。
- `density-r8-t55` 的首个路径候选直接成功，没有发生上述穷举失败。

剖析中的绝对耗时只用于定位热点；设计与日常测试不依赖单次墙钟值。

## 3. 范围

### 3.1 包含内容

- 为 `astar_timed` 增加目标时间槽可用性预检。
- 为时空 A* 和路径候选增加可选的内部诊断收集器。
- 将直接规划边界基准的确定性诊断汇总写入 JSON/CSV。
- 将基准报告 `schemaVersion` 从 `1` 升为 `2`。
- 为预检正确性、诊断计数、密度案例结果和报告契约增加回归测试。
- 运行优化前后密度基准和优化后的完整九案例基准，人工核对正确性与性能分布。
- 更新算法、实验和测试文档中的性能边界说明。

### 3.2 不包含内容

- 不改变 `DispatchResult`、`SessionResult`、Pydantic 模型、OpenAPI 或前端 TypeScript 类型。
- 不修改六个实验 API、会话 API 或 `POST /api/dispatch` 的请求和响应。
- 不修改 beam width、任务排序、机器人规划顺序或候选评分。
- 不增加真实时间超时、线程中断或基于墙钟的算法分支。
- 不设置任意的 A* 扩展节点上限。
- 不实现 CBS、ECBS 或其他完整 MAPF 求解器。
- 不修改在线执行安全门、充电、任务锁、抢占或恢复语义。
- 不新增前端面板。
- 不提交 `output/` 中的基准结果。

## 4. 方案选择

### 4.1 采用方案：确定性目标预留剪枝

在 `astar_timed` 建立搜索堆之前，计算：

```text
earliest_arrival =
    start_time + manhattan(start, goal) * move_ticks

max_time =
    start_time + scenario.width * scenario.height * 4 * move_ticks
```

然后检查闭区间 `[earliest_arrival, max_time]`：

- 只要存在一个目标顶点时间槽不在 `Reservations.vertices` 中，继续执行现有时空 A*。
- 如果该闭区间内每个目标顶点时间槽都已被预留，直接返回空路径，并记录 `goalFullyReserved` 拒绝。
- 当 `start` 与 `goal` 相同时，保持现有立即成功语义，不执行预检拒绝。

该检查只根据目标顶点预留作出否定结论，不根据边预留、曼哈顿距离或启发式评分推断路径一定可达。曼哈顿距离只提供不晚于真实最短路径的到达下界；在这个更宽的时间区间内都没有可用目标时间槽时，才判定当前搜索时域内不可能到达，因此不会把现有可行路径误剪掉。

检查按时间升序，在发现第一个可用目标时间槽时立即结束。多数成功搜索只进行一次或少量集合查询；只有目标长期占用时才扫描完整搜索时域。

### 4.2 未采用：A* 扩展节点上限

固定节点上限可以限制最坏耗时，但可能把原本可解、只是需要较长等待的路径误判为失败。当前没有证据支持一个对所有地图、`moveTicks` 和预留密度都安全的节点阈值，因此本阶段不采用。

### 4.3 未采用：调整机器人规划顺序

当前两个慢案例的第二个顺序更好，但直接修改顺序启发式可能只是在拟合这两个案例，并影响锁定任务、优先级、截止时间和其他固定种子场景。先消除可证明无效的搜索成本，保持候选顺序与结果选择规则不变。

### 4.4 未采用：直接引入 CBS/MAPF

现有完整边界基准为 45/45 稳定，尚未发现需要重写求解器的正确性证据。本阶段只处理已定位的启发式规划性能热点。

## 5. 内部诊断模型

新增 `backend/app/planning_diagnostics.py`，只包含普通 dataclass 和字面量类型，不依赖 FastAPI 或 Pydantic。

### 5.1 单次时空 A* 诊断

`TimedAStarCallDiagnostics` 记录：

- `outcome`：`success`、`invalidEndpoint`、`goalFullyReserved` 或 `exhausted`。
- `expandedStateCount`：进入 closed 集合的状态数量。

不记录墙钟时间、完整预留表、完整路径或事件文本。

### 5.2 单个路径候选诊断

`PathCandidateDiagnostics` 记录：

- `robotOrder`：本候选使用的精确机器人 ID 顺序。
- `failureCount`。
- `conflictCount`。
- `deadlineMissCount`。
- `timedAStarCallCount`。
- `timedAStarExpandedStateCount`。
- `maxTimedAStarExpandedStateCount`。
- `timedAStarExhaustedSearchCount`。
- `timedAStarGoalFullyReservedRejectCount`。

候选开始时创建局部计数器；候选评分完成后写入最终失败、冲突和截止超期数量。

### 5.3 一次调度诊断

`PlanningDiagnostics` 记录：

- `pathCandidates`：按实际尝试顺序保存 `PathCandidateDiagnostics`。
- `selectedPathCandidateIndex`：最终选中候选在列表中的零基索引；没有候选时为 `None`。

并提供下列汇总值：

- `pathCandidateCount`。
- `failedPathCandidateCount`。
- `timedAStarCallCount`。
- `timedAStarExpandedStateCount`。
- `maxTimedAStarExpandedStateCount`。
- `timedAStarExhaustedSearchCount`。
- `timedAStarGoalFullyReservedRejectCount`。

诊断对象通过可选参数显式传递，不使用模块级可变全局、`ContextVar` 或日志文本解析。未提供诊断对象时，现有调用路径只执行必要的目标预检，不构造候选诊断列表。

## 6. 调度器数据流

`run_dispatch` 增加仅限 Python 内部调用的可选参数：

```python
planning_diagnostics: PlanningDiagnostics | None = None
```

该参数依次显式传给：

```text
run_dispatch
  -> build_paths
    -> build_paths_for_order
      -> plan_robot_path / parking helpers
        -> astar_timed
```

处理流程：

1. `build_paths` 为实际尝试的每个机器人顺序创建一个候选诊断。
2. 该候选内的所有 `astar_timed` 调用更新同一个候选诊断。
3. `path_planning_candidate_score` 计算完成后，记录候选的失败、冲突和截止超期数量。
4. 当 `best_candidate` 更新时，同步更新 `selectedPathCandidateIndex`。
5. 现有“零失败、零冲突、零截止超期后提前结束”条件保持不变。
6. 诊断只观察现有控制流，不参与候选评分或结果选择。

API 入口仍按原方式调用 `run_dispatch`，不传诊断对象；因此响应结构和 API 契约不发生变化。

## 7. 基准报告

### 7.1 直接规划运行

`backend/benchmarks/runner.py` 在 `_execute_direct` 中创建 `PlanningDiagnostics`，传给 `run_dispatch`，并将下列扁平字段写入 `BenchmarkRun`：

- `planningDiagnosticsEvaluated = true`
- `pathCandidateCount`
- `selectedPathCandidateIndex`
- `failedPathCandidateCount`
- `timedAStarCallCount`
- `timedAStarExpandedStateCount`
- `maxTimedAStarExpandedStateCount`
- `timedAStarExhaustedSearchCount`
- `timedAStarGoalFullyReservedRejectCount`

### 7.2 在线运行、超时和异常

在线基准目前经过会话内部的多次重规划，不在本阶段跨会话聚合诊断：

- `planningDiagnosticsEvaluated = false`
- 其余规划诊断字段为 `null`

超时或异常发生在诊断结果可用之前时同样写入 `false` 和 `null`，不得伪造为 `0`。

### 7.3 JSON/CSV 与汇总

- `BenchmarkReport.create` 将 `schemaVersion` 改为 `2`。
- `results.json` 和 `runs.csv` 都包含上述逐次运行字段。
- `case-summaries.csv` 及 `caseSummaries` 增加：
  - `medianTimedAStarExpandedStateCount`
  - `p95TimedAStarExpandedStateCount`
  - `maxTimedAStarGoalFullyReservedRejectCount`
- 只有 `planningDiagnosticsEvaluated = true` 且运行完成的记录参与扩展状态统计。
- 没有可用诊断样本时，中位数和 P95 为 `null`，拒绝次数最大值为 `null`。
- CSV 继续使用 UTF-8 with BOM，JSON 继续使用 UTF-8。

## 8. 测试设计

### 8.1 `astar_timed` 单元测试

增加确定性小地图测试：

1. 目标在 `[earliest_arrival, max_time]` 每个 tick 都被顶点预留时：
   - 返回空路径；
   - `outcome = goalFullyReserved`；
   - `expandedStateCount = 0`。
2. 目标在搜索时域内晚些时候释放时：
   - 不被预检拒绝；
   - 返回包含必要等待的有效路径。
3. `start == goal` 时保持现有立即成功行为。
4. `moveTicks > 1` 时使用乘以 `moveTicks` 的最早到达下界，并保留现有移动展开语义。
5. 非法起点或目标继续返回空路径，并记录 `invalidEndpoint`。

### 8.2 路径候选诊断测试

使用小型确定性场景验证：

- 候选数量与实际尝试顺序一致。
- 选中索引对应 `best_candidate`。
- 各候选的时空 A* 调用、扩展状态、穷举失败和目标完全预留拒绝互不串扰。
- 未传诊断对象时结果与传入诊断对象时完全相同。

### 8.3 密度回归

对 `density-r8-t31`、`density-r8-t43` 和 `density-r8-t55` 分别执行一次直接规划，验证：

- 全任务分配。
- `conflictCount = 0`。
- `failureCount = 0`。
- `deadlineMissCount = 0`。
- 31/43 任务案例存在一次 `goalFullyReserved` 拒绝并继续尝试后续候选。
- 55 任务案例的首个候选仍直接成功。
- 不对真实毫秒值设置 pytest 断言。

### 8.4 报告契约测试

验证：

- `schemaVersion = 2`。
- JSON、`runs.csv` 和 `case-summaries.csv` 使用精确的新字段名。
- 直接规划、在线、超时和异常记录遵守 `true/false/null` 语义。
- partial/final 原子写入和 UTF-8/BOM 行为不退化。

## 9. 性能验收

实现分两阶段取证：

1. 诊断接线完成、剪枝尚未启用时，运行 `density × 5`，记录基线扩展状态数、中位数和 P95。
2. 启用目标预留剪枝后，在同一机器、无并发重型任务的条件下再次运行 `density × 5`。

验收要求：

- 两次基准都为 15/15 completed、15/15 stable、0 timeout、0 error。
- 优化后 31/43 任务案例的 `timedAStarGoalFullyReservedRejectCount` 非零。
- 被拒绝的目标完全预留调用扩展状态数为 `0`。
- 31/43 任务案例不再由一次穷举失败占据主要扩展状态。
- 优化后 31/43 任务案例的 `medianReplanTimeMs` 各自不高于同机优化前基线的 50%；该比例只做人工验收，不加入 pytest。
- 55 任务案例的正确性保持不变；墙钟变化只记录，不设置自动阈值。

随后运行优化后的完整九案例 × 5：

- 45 条运行和 9 条汇总结构完整。
- 45/45 stable，且没有新增 timeout 或 error。
- 所有直接规划案例仍保持全任务分配、零预测冲突、零失败和零截止超期。
- 所有在线瓶颈案例仍保持零活动冲突，执行安全字段完整。

如果正确性回归或同机 50% 目标未达到，不通过调整墙钟阈值掩盖结果；保留诊断证据并重新判断根因。

## 10. 文档更新

实现完成后更新：

- `docs/algorithm.md`：说明目标完全预留预检、适用边界和不等于完整 MAPF。
- `docs/experiments.md`：说明 `schemaVersion = 2` 及新增规划工作量字段。
- `docs/testing-guide.md`：增加优化前后密度基准和完整基准人工复核方法。
- `AGENTS.md`：记录病态失败候选已完成性能归因和局部优化，并以实际最终数据更新下一方向。

文档不得把目标预留剪枝描述为任意动态障碍、边预留或多机器人场景的完整无解证明。

## 11. 完成标准

本阶段完成必须同时满足：

1. 目标时间槽完全预留时，`astar_timed` 在展开状态前确定性返回无路径。
2. 可行路径、延迟可达路径、慢速机器人路径和 `start == goal` 语义不退化。
3. 诊断数据不参与调度决策，API 契约保持不变。
4. 密度案例正确性不变，31/43 任务案例的病态搜索成本按同机对照明显下降。
5. 前端完整测试、后端完整测试和生产构建通过。
6. 最终完整边界基准人工复核通过。
7. 生成的 `output/` 结果不暂存、不提交。
