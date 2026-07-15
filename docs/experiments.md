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
    "assignmentReplanWindow": 24
  },
  "windows": [4, 24, 48]
}
```

`windows` 中的每个值都会生成一个 case，标签格式为 `window-<value>`。接口会保留其他 `options` 设置，只覆盖 `assignmentReplanWindow`。

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

## 当前用途

六个接口用于后端自动化回归和后续竞赛材料取数：

- 避碰对比：同一场景下，对比关闭避碰和开启避碰后的冲突数量、路径长度、任务完成情况和事件日志。
- 动态重规划对比：同一场景下，对比是否启用动态事件后的任务完成情况、路径变化和重规划指标。
- 滚动窗口对比：同一场景下，对比不同窗口参数对当前规划规模、路径代价和失败数的影响。
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

`integrated-demo` 的当前固定输入中，无避碰基线存在预测冲突；开启优先级避碰后6个任务仍全部分配，预测冲突降为0，失败和截止超期保持为0。该结果属于当前场景和回归覆盖，不等于已经实现第三级全规划时域安全门。

当前冲突验收分三级：

1. 已实现硬标准：在线会话已经执行到的 tick 不出现同格顶点冲突或反向边交换冲突。
2. 已实现规划质量：固定种子压力场景的预测冲突为零；当前 `integrated-demo` 固定输入中，开启避碰后预测冲突由大于0降为0。
3. 未实现未来目标：全规划时域零冲突保证，以及路径进入执行前的独立安全门。当前文档和实验结果不得把第二级表述成第三级已经完成。

## 后续实验方向

后续可以继续增加：

1. 根据后端实验 JSON 生成并人工复核报告图表；主界面不恢复实验面板。
2. 面向技术报告的固定实验截图清单。
3. 每组实验的人工审核结论，避免只依赖自动生成文本。
