# 规模与拥堵性能研究收尾设计

## 1. 目标

本阶段不是重新建设性能基准，而是在已经存在的算法边界基准、固定种子压力场景、在线压力流程和规划诊断设施之上完成第三阶段收尾：

1. 把规模、密度、固定种子随机、在线瓶颈和在线动态压力放入同一离线基准目录；
2. 让直接调度和在线真实重规划都输出可复算的确定性规划工作量；
3. 在最新 `main` 上形成优化前基线；
4. 根据当前剖析结果，只实施一项边界清晰的任务分配内存复制优化；
5. 生成优化后结果并验证调度输出和安全结果没有退化。

本阶段完成后，第三项“规模与拥堵性能研究”可以关闭。新的算法替换、自适应阈值修改或更大规模研究仍需单独确认。

## 2. 当前基线与问题证据

当前 `benchmark:algorithm` 已包含九个确定性案例、隔离子进程、单次超时、清理、部分报告、原子最终报告以及 `schemaVersion = 2` 的 JSON/CSV 输出。直接调度已经记录路径候选数、时空 A* 调用数和扩展状态数；在线案例只记录最终业务与执行安全结果，没有汇总真实重规划观察值。

2026-09-04 在合并提交 `253f8f3a7636315197878dde8e0a054c51e2ac22` 上运行一次九案例探针，输出位于：

```text
output/phase3-baseline-probe/20260904T124044Z
```

探针全部 `completed` 且 `correctnessStable = true`。其中：

- `scale-r12-t39` 的 `replanTimeMs = 377.37`，但时空 A* 总扩展状态仅为 `195`；
- `density-r8-t55` 的 `replanTimeMs = 297.31`，两个路径候选共调用时空 A* `147` 次、扩展 `4378` 个状态；
- 三个在线瓶颈案例的规划诊断字段均为 `null`，无法从当前算法边界报告判断真实重规划工作量。

同机 `cProfile` 只用于定位函数热点，不作为可移植耗时结论：

- `scale-r12-t39` 中 `assign_tasks_beam_search` 占主要累计时间，`clone_assignment_candidate` 被调用 `21468` 次；
- `density-r8-t55` 中 `clone_assignment_candidate` 被调用 `17787` 次；
- 当前每次扩展都会复制候选中的所有 `RobotAssignmentState`，但随后只修改被选中的一个机器人状态。

因此本阶段先补齐确定性分配工作量和在线观察值，再把全车队深复制改为“候选列表浅复制 + 被修改机器人状态单独复制”。不在本阶段调整路径规划顺序、束宽、代价函数或 A* 启发式。

## 3. 范围

### 3.1 纳入范围

- 扩展现有 `benchmark:algorithm`，不新增平行的性能主命令；
- 将报告版本从 `schemaVersion = 2` 升为 `schemaVersion = 3`；
- 保留原九个案例的 ID、顺序和构造语义；
- 新增三个固定种子随机直接调度案例和一个带运行时变更操作的在线压力案例；
- 通过现有 `ReplanObservation` 汇总在线真实重规划工作量；
- 为束搜索记录候选扩展数、实际复制的机器人状态数和峰值束宽；
- 采用写时复制优化任务分配候选；
- 运行优化前、优化后各五次的完整十三案例基准；
- 更新算法、实验和测试文档。

### 3.2 不纳入范围

- 不改变任何 FastAPI 路由、业务请求模型或业务响应模型；
- 不增加前端实验面板；
- 不修改当前束宽、任务排序、候选评分、路径规划顺序或滚动窗口策略；
- 不应用同机自适应窗口候选阈值；
- 不并行运行基准；
- 不删除异常值，不把 `timeout` 或 `error` 改写为成功；
- 不宣称跨机器墙钟可比、完整 MAPF/CBS、全局最优或任意输入零冲突。

## 4. 案例矩阵

`BenchmarkFamily` 扩展为以下五个精确值：

```text
scale
density
seeded
bottleneck
online-pressure
```

默认目录固定为十三个案例，并保持以下顺序：

| 案例 ID | 模式 | 机器人 | 最终任务数 | 场景动态任务 | 运行时任务 | 说明 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `scale-r4-t15` | direct | 4 | 15 | 3 | 0 | 现有开放规模案例 |
| `scale-r8-t27` | direct | 8 | 27 | 3 | 0 | 现有开放规模案例 |
| `scale-r12-t39` | direct | 12 | 39 | 3 | 0 | 现有开放规模案例 |
| `density-r8-t31` | direct | 8 | 31 | 3 | 0 | 现有 seed 43 密度案例 |
| `density-r8-t43` | direct | 8 | 43 | 3 | 0 | 现有 seed 43 密度案例 |
| `density-r8-t55` | direct | 8 | 55 | 3 | 0 | 现有 seed 43 密度案例 |
| `seeded-s17-r4-t15` | direct | 4 | 15 | 3 | 0 | seed 17，12 个基础任务 |
| `seeded-s29-r6-t23` | direct | 6 | 23 | 3 | 0 | seed 29，20 个基础任务 |
| `seeded-s31-r8-t27` | direct | 8 | 27 | 3 | 0 | seed 31，24 个基础任务 |
| `bottleneck-r4-t4` | online | 4 | 4 | 0 | 0 | 现有在线瓶颈案例 |
| `bottleneck-r6-t6` | online | 6 | 6 | 0 | 0 | 现有在线瓶颈案例 |
| `bottleneck-r8-t8` | online | 8 | 8 | 0 | 0 | 现有在线瓶颈案例 |
| `online-pressure-s17-r4-t17` | online | 4 | 17 | 3 | 2 | seed 17 动态压力流程 |

默认 `--repetitions 5` 时必须生成 `13 x 5 = 65` 条运行记录和十三条案例汇总。

## 5. 在线压力流程

在线基准统一使用独立 `SessionRegistry(max_sessions=1)`、`enforce_execution_safety=True`、固定窗口 `120` 和 `replan_observer=observations.append`。每次运行结束都必须在 `finally` 中删除会话。

原三个 `bottleneck` 案例只推进到各自 `tick_target = 120`，但现在也汇总真实重规划观察值。

`online-pressure-s17-r4-t17` 使用 `seeded_pressure_scenario` 的 seed 17、4 台机器人和 12 个基础任务，事件顺序固定为：

1. 从 T=0 逐 tick 推进到 T=8；
2. T=8 添加 `RUNTIME-SEED-17` 紧急任务，目标 `[2,9]`、截止 T=28；
3. T=8 封锁 `[3,9]`；
4. T=8 标记 `R4` 故障；
5. T=8 恢复 `R4`；
6. T=8 解除 `[3,9]`；
7. 逐 tick 推进到 T=18；
8. T=18 添加 `G-SEED-17` 巡检任务，目标为场景第一个巡检区域坐标、截止 T=42；
9. 逐 tick 推进到 T=20。

最终必须有两项运行时任务和六次成功的运行时变更操作：两次加任务、一次封锁、一次故障、一次恢复和一次解除封锁。稳定性仍按任务覆盖、最终活动冲突、失败和超期判断，不要求 T=20 时所有已释放任务全部完成；实际完成率继续单独报告。

## 6. 规划诊断语义

### 6.1 新增内部字段

`PlanningDiagnostics` 和 `ReplanObservation` 新增：

```text
assignment_candidate_expansion_count
assignment_robot_state_copy_count
assignment_beam_peak_width
```

定义如下：

- `assignmentCandidateExpansionCount`：束搜索为“某候选 + 某合格机器人 + 某任务”生成新候选的次数；
- `assignmentRobotStateCopyCount`：实际新建 `RobotAssignmentState` 的次数；
- `assignmentBeamPeakWidth`：包含初始束在内，任一任务层保留的最大候选数。

这些字段只在传入可选 `PlanningDiagnostics` 时采集，不改变没有 observer 的生产请求。

### 6.2 报告新增字段

`BenchmarkRun` 新增并序列化：

```text
runtimeTaskCount
runtimeMutationCount
replanObservationCount
assignmentCandidateExpansionCount
assignmentRobotStateCopyCount
assignmentBeamPeakWidth
```

直接调度：

- `replanObservationCount = 0`；
- 规划工作量直接来自一次 `PlanningDiagnostics`；
- `runtimeTaskCount = 0`、`runtimeMutationCount = 0`。

在线调度：

- `replanObservationCount` 是 observer 收到的真实重规划次数；
- `runtimeMutationCount` 是该在线基准流程中成功完成的运行时 API 变更操作数；每个调用成功返回后累加一次，不得从表示当前活动封锁/故障数的 `SessionResult.runtimeEventCount` 读取；
- 候选数、失败候选数、A* 调用数、A* 扩展数、A* 拒绝数和任务分配扩展/复制数在所有观察值上求和；
- 最大单次 A* 扩展数和峰值束宽取所有观察值最大值；
- 因为一次在线运行包含多个候选选择，`selectedPathCandidateIndex` 保持 `null`；
- 至少有一个观察值时 `planningDiagnosticsEvaluated = true`，否则为 `false` 且规划工作量字段保持 `null`。

案例汇总新增：

```text
medianReplanObservationCount
medianAssignmentCandidateExpansionCount
p95AssignmentCandidateExpansionCount
medianAssignmentRobotStateCopyCount
p95AssignmentRobotStateCopyCount
maxAssignmentBeamPeakWidth
```

所有中位数和 P95 只从 `completed` 且已经评价规划诊断的运行计算。旧 `schemaVersion = 2` 输出不修改；新运行只写版本 3。

## 7. 定向优化

现有 `clone_assignment_candidate(candidate)` 会复制候选中的全部机器人状态。它改为接收 `robot_index`，只执行：

1. 浅复制 `candidate.robots` 列表；
2. 深复制 `candidate.robots[robot_index]`；
3. 用新状态替换浅复制列表中的对应元素；
4. 返回新 `AssignmentCandidate`。

束搜索只修改新候选中的 `robot_index`，其他状态作为只读对象共享。测试必须证明：

- 修改子候选的选中机器人不会修改父候选；
- 两个从同一父候选分出的兄弟候选互不污染；
- 未选中的状态没有被写入；
- 最终任务分配、路径、冲突、失败、超期、距离和 makespan 保持现有回归结果。

优化前，诊断应记录每次扩展复制整个活动车队；优化后，每次扩展只复制一个机器人状态，因此在所有实际产生候选的案例中：

```text
assignmentRobotStateCopyCount == assignmentCandidateExpansionCount
```

不把墙钟下降设为自动测试门；优化有效性的确定性门是复制计数下降且业务结果完全稳定。墙钟中位数与 P95只作为同机辅助证据。

## 8. 文件职责

- `backend/app/planning_diagnostics.py`：任务分配与路径规划确定性工作量容器；
- `backend/app/dispatch.py`：采集束搜索工作量并实现单机器人写时复制；
- `backend/app/replan_window.py`：让在线 observer 携带新增工作量；
- `backend/app/sessions.py`：把本次真实 `PlanningDiagnostics` 映射到 `ReplanObservation`；
- `backend/benchmarks/scenarios.py`：十三案例目录与场景构造；
- `backend/benchmarks/online_flow.py`：仅负责两类在线基准流程、事件执行和会话清理；
- `backend/benchmarks/runner.py`：将直接或在线执行结果归一化为 `BenchmarkRun`；
- `backend/benchmarks/results.py`：版本 3 运行/汇总模型和统计；
- `backend/benchmarks/reporting.py`：版本 3 JSON/CSV列；
- `backend/benchmarks/algorithm_boundary.py`：五个默认场景族与参数校验；
- `backend/tests/test_algorithm.py`：写时复制隔离和调度语义回归；
- `backend/tests/test_sessions.py`：observer 字段映射回归；
- `backend/tests/test_algorithm_benchmark.py`：目录、在线流程、统计、序列化和65条记录完整性；
- `docs/algorithm.md`、`docs/experiments.md`、`docs/testing-guide.md`、`AGENTS.md`：边界、命令、字段和最新验证快照。

## 9. 证据与验收门

实施按两个真实基准阶段执行：

1. 加入诊断和十三案例但尚未优化时，运行五次完整基准并保留输出目录；
2. 写时复制优化后，在相同机器、无并发重型任务、相同参数下再运行五次完整基准。

两个报告都必须满足：

- 65 条运行、十三条汇总且顺序一致；
- 每个案例恰好五次；
- `timeout = 0`、`error = 0`；
- 65 次 `correctnessStable = true`；
- 直接案例全分配、预测冲突为0、失败为0、超期为0；
- 在线案例最终活动冲突为0、失败为0、超期为0；
- 在线压力案例运行时任务数为2、成功的运行时变更操作数为6；
- JSON、两份 CSV 数值可交叉重算；
- `results.partial.json` 和事务临时文件在成功发布后不存在；
- 没有残留子进程或会话。

优化前后必须进一步满足：

- 案例 ID、运行次数、场景参数与调度选项完全一致；
- 每个对应运行的任务数、已分配数、冲突、失败、超期、总距离和 makespan 相同；
- `assignmentCandidateExpansionCount` 相同；
- 优化后 `assignmentRobotStateCopyCount` 不大于优化前，且所有产生候选的运行严格下降；
- 优化后每个运行均满足复制数等于扩展数；
- 墙钟数据原样保留，但不以某个固定百分比作为成功条件。

最后重新运行 `npm run check`。任何超时、错误、正确性退化、报告不一致或测试失败都使第三阶段收尾失败；禁止删除失败运行或扩大超时掩盖问题。

## 10. 完成边界

本阶段完成只表示：现有启发式调度器在十三个受控规模/拥堵/在线案例上有统一、可复算的工作量证据，并减少了束搜索候选状态复制。它不证明更大任意输入的性能、不建立跨机器耗时阈值，也不完成中央—边缘协同或完整 CBS/MAPF。
