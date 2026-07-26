# 自适应窗口校准证据修复设计

## 1. 背景

现有离线校准在三个案例和四个窗口变体上运行 60 次。2026-07-26 的已审查结果为：

- 60 次运行全部 completed；
- 低负载和过渡案例共 40 次运行全部稳定；
- `adaptive-pressure-r8-t45` 的四个变体各运行 5 次，20 次结果均为 43/45 覆盖、43/45 完成、2 个失败和 1 次超期；
- 所有运行的预测冲突、活动冲突和安全介入均为 0；
- 压力案例没有合格的 stable completed `fixed-24` 观测，因此 candidate envelope 不可用。

只读复现将压力案例的两个失败任务精确定位为 `D9` 和 `D33`。二者均从 `[1,1]` 运送到 `[16,10]`。该案例继承自 `density-r8-t43`：

- 机器人初始电量为 83–90，容量为 100；
- 没有充电单元；
- T=8 激活动态封锁 `[16,7]`、`[16,8]`；
- 同一在线会话连续执行 45 个任务直至 T=120。

因此，这两项失败由电量、动态封锁和早期 deadline 共同混入窗口比较。加充电单元的对照会增加充电等待和超期；仅把电量提高到 120 虽能覆盖全部任务，但 T=120 时仍有任务未完成且 `I1` 超期。电量 150 能在 T=120 前完成 45 个任务，剩余超期来自 `I1` 的原 deadline 80。

第二个证据问题位于距离口径。校准运行当前读取最终 `DispatchResult.metrics.totalDistance`。会话在 T=120 已无活动分配时，该字段为 0；同一响应的最后一个 `MetricSnapshot.travelledDistance` 为实际累计值，压力复现中为 556。现有 60 次报告的 `totalDistance` 因此全部错误地记录为 0。

### 1.1 T=120 smoke 后的设计修订

初版归一化实现后，四个压力变体各运行一次，均得到：

- 45 个任务全部 released、covered，但只完成 44 个；
- `deadlineMissCount`、`failureCount`、预测/活动冲突和安全介入均为 0；
- `totalDistance = 664`；
- 唯一未完成任务为 `D33`，其原 deadline 为 T=122；
- `D33` 的合法计划完成时刻为 T=121，T=120 时 R8 位于 `[15,10]`，距离 dropoff `[16,10]` 仍差一格。

因此，`deadline = max(originalDeadline, 120)` 仍保留了晚于观察终点的 deadline，使规划器可以合法地把任务排到 T=121；同时 `correctnessStable` 只检查 coverage、活动冲突、超期和失败，不检查完成数，所以该结果仍被标记为 stable。显式 45/45 artifact 门禁正确拦截了这组证据。

只读单变量对照进一步确认：

- 把电量和容量从 150 提高到 160 不改变结果，排除剩余电量不足；
- 只特判 `D33` 的 deadline 虽能完成 45/45，但不能成为通用规则；
- 将压力案例所有非空基础 deadline 统一为 T=120 时，一次确定性 `fixed-24` 对照在 T=120 完成 45/45，且超期、失败、冲突和安全介入仍全部为 0。

经用户确认，本设计修订为“统一校准截止时间”，不再保留晚于 T=120 的基础 deadline。该对照只证明修订方向，四变体 smoke 和默认 60-run 仍必须重新通过。

## 2. 目标

本次修复只解决以下两项：

1. 让 `adaptive-pressure-r8-t45` 成为不受电量耗尽和早期 deadline 干扰的窗口校准案例，同时保留其任务压力、动态封锁和在线重规划行为。
2. 让校准报告中的 `totalDistance` 使用在线会话的权威累计执行距离。

修复后重新运行目标 smoke、完整项目检查和默认 60-run。只有新证据满足正确性门禁时才报告 candidate envelope；不得自动修改生产策略。

## 3. 非目标

本次不做：

- 不修改生产 `60/40ms` 迟滞、最近 5 次/至少 3 次样本或 `2×` 压力倍数。
- 不修改调度评分、候选顺序、A*、任务锁、抢占、充电、恢复或执行安全语义。
- 不修改 HTTP、Pydantic API、OpenAPI 或前端契约。
- 不修改原始 `density-r8-t43` 或现有九个算法边界案例。
- 不增加新的案例 ID、变体 ID、报告字段或 schema 版本。
- 不放宽 `correctnessStable`，不隐藏 completed-but-unstable、timeout 或 error。
- 不在本阶段选择或应用 candidate envelope 中的阈值。

## 4. 压力案例归一化

### 4.1 作用范围

只修改 `build_adaptive_calibration_scenario("adaptive-pressure-r8-t45")` 返回的深拷贝。原始 `density-r8-t43`、其他两个校准案例和所有生产场景保持不变。

在校准场景模块定义两个内部常量：

```python
PRESSURE_CALIBRATION_BATTERY_BUDGET = 150
PRESSURE_CALIBRATION_DEADLINE = 120
```

### 4.2 电量

对压力案例深拷贝中的每台机器人设置：

```text
battery = 150
batteryCapacity = 150
```

这是校准输入归一化，不是生产机器人默认值，也不改变任何充电算法。目的在于让窗口比较测量任务压力和重规划工作，而不是测量没有充电设施时的电量耗尽。

### 4.3 Deadline

只处理压力案例 `scenario.tasks` 中已有非空 deadline 的 40 个基础任务：

```text
deadline = 120
```

以下内容保持原值：

- releaseTime、优先级、任务类型和目标；
- 3 个动态 emergency 任务；
- T=20、T=40 插入的两个运行时任务及其 `currentTime + 40` deadline；
- `tickTarget = 120`。

该规则把基础任务截止时间统一到校准观察终点，既消除过早 deadline，也避免规划器依据晚于观察终点的 deadline 合法地把任务排到 T=121。它只修改校准深拷贝，不修改原始 `density-r8-t43`。

### 4.4 必须保持的压力特征

归一化后仍必须满足：

- 8 台机器人；
- 最终 45 个任务；
- 原种子 43 的地图、障碍和任务目标；
- T=8 的动态任务与动态封锁；
- T=20、T=40 的运行时 emergency 插入；
- 四个变体仍为 `fixed-4`、`fixed-24`、`fixed-48`、`adaptive-current-24`；
- 普通在线会话逐 tick 推进，`enforce_execution_safety=True`。

## 5. 累计距离口径

完成在线运行后，校准运行器只读取一次最后的指标快照：

```python
final_snapshot = session.metricsHistory[-1]
```

字段映射为：

```text
AdaptiveCalibrationRun.total_distance =
    final_snapshot.travelledDistance

AdaptiveCalibrationRun.active_conflict_count =
    final_snapshot.activeConflictCount
```

不得再把 `session.result.metrics.totalDistance` 写入校准报告。该字段描述当前规划结果，在最终空闲 tick 可以合法为 0，不是累计执行距离。

completed 在线运行必须存在 `metricsHistory`。若为空，运行器抛出：

```text
自适应窗口校准完成但缺少指标历史
```

该异常由现有进程隔离层记录为 error；不得以 0 作为静默后备值。会话仍必须通过现有 `finally` 清理。

JSON 中的逐运行记录和 `runs.csv` 继续使用现有 `totalDistance` 字段；现有 variant summary 不包含距离字段，保持不变。schema 保持 v1。

## 6. 测试设计

### 6.1 场景测试

新增或加强测试，证明：

- 压力案例所有机器人精确为 `battery=150`、`batteryCapacity=150`；
- 压力案例所有非空基础 deadline 均精确为 120；
- release、任务 ID、任务数量、动态封锁、动态任务和运行时任务规则不变；
- `build_benchmark_scenario("density-r8-t43")` 在构造校准场景前后完全一致；
- 低负载和过渡案例不受归一化影响；
- 重复构造仍得到完全一致的 JSON。
- 真实 `fixed-24` 压力在线流在 T=120 达到 45/45 完成，且超期、失败、活动冲突和安全介入均为 0。

### 6.2 距离测试

测试必须能区分两个数据源：

- 最终 `DispatchResult.metrics.totalDistance = 0`；
- 最后 `MetricSnapshot.travelledDistance` 为非零哨兵值；
- 生成的 `AdaptiveCalibrationRun.total_distance` 精确等于哨兵值。

另加空 `metricsHistory` 回归，验证：

- 抛出精确错误；
- 会话仍被删除；
- 不生成 completed 且 `totalDistance=0` 的伪结果。

现有真实在线流测试增加 `run.total_distance > 0` 断言。

### 6.3 不使用真实墙钟断言

pytest 不增加毫秒上限。规划耗时只在无并发重型命令的手工校准中观察。

## 7. 验证顺序

1. 运行校准场景、运行器、结果和报告的聚焦测试。
2. 运行完整 `npm run check`。
3. 单独运行压力案例的一次四变体 smoke：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan -- `
  --cases adaptive-pressure-r8-t45 `
  --repetitions 1 `
  --timeout-seconds 30 `
  --output-dir output/adaptive-calibration-evidence-fix-smoke
```

4. smoke 通过后，在无其他重型命令并发时运行默认 60-run。
5. 交叉核对 JSON、三份 CSV、UTF-8/BOM、partial/tmp、worker 和 Git 范围。

## 8. 验收标准

- 完整项目检查通过。
- 压力 smoke 的四个变体均：
  - `outcome = completed`；
  - 45/45 覆盖；
  - 45/45 完成；
  - 零 deadline miss；
  - 零 failure；
  - 零预测/活动冲突；
  - 零安全介入；
  - `totalDistance > 0`。
- 默认 60-run 为 60 completed、60 stable、0 timeout、0 error。
- 每个 completed run 的 `totalDistance` 与对应会话最后指标快照一致且大于 0。
- JSON 和 CSV 数量、字段和值一致。
- 三个案例都提供合格 stable completed `fixed-24` 观测时，`candidateEnvelopeAvailable=true` 且 candidate envelope 非空。
- 新 envelope 只作为同机证据记录；生产策略保持不变。
- `output/`、`.superpowers/`、依赖 junction 和缓存不进入提交。

若压力 smoke 或完整 60-run 未满足门禁，必须报告精确 case、variant、run、任务、deadline、failure、冲突和安全介入，不得通过提高 timeout、放宽正确性或修改生产阈值获得绿色结果。

## 9. 文档更新

实现验证完成后更新：

- `AGENTS.md`：记录压力混杂因素已修复、累计距离口径和新 60-run 结论。
- `docs/algorithm.md`：说明在线累计距离来自 `MetricSnapshot`，与当前规划结果距离不同。
- `docs/experiments.md`：保留原 40 stable/20 unstable 证据作为修复前诊断，并追加新结果目录与结论，不覆盖历史事实。
- `docs/testing-guide.md`：更新 smoke 和 artifact 交叉检查，显式校验压力案例 45/45 和 `totalDistance > 0`。

## 10. 预计修改范围

生产代码不变。预计只修改：

- `backend/benchmarks/adaptive_scenarios.py`
- `backend/benchmarks/adaptive_runner.py`
- `backend/tests/test_adaptive_replan_calibration.py`
- `AGENTS.md`
- `docs/algorithm.md`
- `docs/experiments.md`
- `docs/testing-guide.md`

设计和后续实施计划分别提交；生成结果不提交。
