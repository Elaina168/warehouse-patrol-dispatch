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

## 当前用途

这些接口用于竞赛材料中的四组实验：

- 避碰对比：同一场景下，对比关闭避碰和开启避碰后的冲突数量、路径长度、任务完成情况和事件日志。
- 动态重规划对比：同一场景下，对比是否启用动态事件后的任务完成情况、路径变化和重规划指标。
- 滚动窗口对比：同一场景下，对比不同窗口参数对当前规划规模、路径代价和失败数的影响。
- 规模对比：不同机器人数量、任务数量或地图规模下，对比完成时间、总路径长度、冲突数量、失败任务数和重规划耗时。

## 前端状态

主前端已经撤掉独立实验对比面板，避免人工演示时被多组实验按钮分散注意力。

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

## 后续实验方向

后续可以继续增加：

1. 固定主演示场景的图表导出。
2. 面向技术报告的固定实验截图清单。
3. 每组实验的人工审核结论，避免只依赖自动生成文本。
