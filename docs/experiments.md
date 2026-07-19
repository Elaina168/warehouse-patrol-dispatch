# 实验对比说明

本文档记录当前实验模块的数据出口和后续对比方向。

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

该接口用于竞赛材料中的第二组实验：

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

该接口用于竞赛材料中的第三组实验：

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

该接口用于竞赛材料中的第四组实验：

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

六个接口用于后端自动化回归和后续竞赛材料取数：

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

后端实验接口继续保留，供自动化回归、报告取数或后续竞赛材料整理使用。正式材料中仍应结合综合场景截图、路径图和事件日志解释实验现象。

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

## 后续实验方向

后续可以继续增加：

1. 根据后端实验 JSON 生成并人工复核报告图表；主界面不恢复实验面板。
2. 面向技术报告的固定实验截图清单。
3. 每组实验的人工审核结论，避免只依赖自动生成文本。
