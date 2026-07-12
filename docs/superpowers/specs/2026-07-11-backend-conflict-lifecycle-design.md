# 后端冲突生命周期设计

## 目标

把地图冲突提示的生命周期语义下沉到后端，同时保持现有前端展示效果不回退。前端不再只依赖本地推导判断红标是否持续闪烁，但旧的 `conflicts`、指标统计、实验接口和路径兜底逻辑继续保留。

## 数据模型

在 `DispatchResult` 中新增 `conflictStates` 字段。每条状态包含：

- `time`：原始冲突首次发生时间，和现有 `conflicts.time` 保持一致。
- `type`：`vertex` 或 `edge`。
- `robots`：参与冲突的机器人 ID。
- `cell`：现有冲突坐标。
- `status`：`active` 或 `resolved`。
- `startedAt`：冲突首次出现时间。
- `resolvedAt`：当前计划中参与机器人离开重叠格或边交换关系的解除时间；当前计划内仍未解除时为 `null`。

旧 `conflicts` 字段继续表示整条当前规划中检测到的冲突点，不改变含义，避免影响实验和既有统计。

## 后端语义

后端根据当前调度结果的绝对路径和当前 session 时间生成 `conflictStates`：

- 顶点冲突：两个机器人在当前时刻仍同时位于冲突格，状态为 `active`；任一机器人离开该格，状态为 `resolved`，并以离开时刻作为 `resolvedAt`。
- 边冲突：两个机器人在当前时刻仍沿同一条边对向交换，状态为 `active`；交换关系消失后为 `resolved`，并以交换关系消失时刻作为 `resolvedAt`。
- 直接调度接口没有 session 当前时刻，默认以 `currentTime = 0` 生成结构化状态，主要用于类型一致和回归保护。

## 前端语义

前端地图优先读取 `result.conflictStates`，并按当前播放时间和 `resolvedAt` 判断红标是否仍应显示。若后端字段为空或旧响应没有该字段，则继续使用当前的 `conflicts + paths + currentTime` 兜底逻辑，保证页面不因接口过渡而倒退。

重规划解释区仍显示最近冲突，但是否显示“已重规划”应优先参考后端结构化状态；没有后端状态时使用现有前端判断。

## 测试

先补失败测试，再实现：

- 后端：会话响应中，两个机器人重叠时 `conflictStates.status = active`；其中一个机器人按路径离开后，同一冲突变为 `resolved`。
- 前端：地图红标优先使用后端 `active` 状态；后端状态显示 `resolved` 时，即使旧 `conflicts` 仍包含该冲突，也不继续显示红标。
- 合同测试：前后端 `ConflictState` 字段保持一致。
