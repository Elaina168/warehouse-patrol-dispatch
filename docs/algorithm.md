# 调度算法说明

本文档记录当前已经实现并通过测试的调度规则，用于稳定化和展示验收。

## 调度入口

- 批量调度入口：`POST /api/dispatch`
- 在线会话创建：`POST /api/sessions`
- 在线会话列表：`GET /api/sessions`
- 在线会话详情：`GET /api/sessions/{session_id}`
- 在线会话删除：`DELETE /api/sessions/{session_id}`
- 在线会话重置：`POST /api/sessions/{session_id}/reset`
- 在线任务追加：`POST /api/sessions/{session_id}/tasks`
- 自动任务流：`POST /api/sessions/{session_id}/stream-task`
- 会话时间推进：`POST /api/sessions/{session_id}/tick`
- 运行时封锁：`POST /api/sessions/{session_id}/blocked-cells`
- 解除运行时封锁：`POST /api/sessions/{session_id}/blocked-cells/remove`
- 机器人故障：`POST /api/sessions/{session_id}/failed-robots`
- 恢复机器人：`POST /api/sessions/{session_id}/failed-robots/restore`

核心计算位于 `backend/app/dispatch.py`，在线状态推进位于 `backend/app/sessions.py`。

## 任务分配

任务分配使用 beam search：

- 候选宽度按机器人数量动态设置，当前为 `max(8, min(48, len(active_robots) * 12))`。
- 任务基础排序按优先级、截止时间、释放时间和任务 ID 排列。
- 机器人候选会考虑任务距离、等待时间、截止时间迟到惩罚、低电量惩罚、总距离和负载均衡。
- 不可用机器人不会参与分配。
- 配送任务会检查机器人载重是否满足 `demand`。

## 滚动窗口

当前分配器使用可配置滚动窗口：

- 默认值：`DispatchOptions.assignmentReplanWindow = 24`
- 允许范围：`0` 到 `120`
- 非锁定任务中，释放时间在窗口内的任务优先参与排序。
- 释放时间超过窗口的远期任务会排在当前窗口任务之后。
- 在线会话使用同一个窗口值计算 `T=<time> 滚动窗口纳入远期任务` 的触发 tick。

这个规则用于避免远期高优先级任务过早扰动当前执行计划。

## 锁定任务

在线会话推进时，已经开始执行且尚未完成的任务会锁定到当前机器人。

锁定规则：

- 锁定任务必须继续分配给原机器人。
- 锁定任务在分配排序中作为前缀处理，防止新任务插到已锁定任务之前。
- 任务完成后会从锁定表中移除。

释放规则：

- 机器人故障时，释放该机器人持有的锁定任务。
- 新封锁单元影响未来锁定路径时，释放受影响锁定任务。
- 已到达的高优先级任务会触发抢占试算；只有释放低优先级锁后评分更优，才真正释放。

## 抢占评分

高优先级任务到达后，会比较两个方案：

- 保留当前锁定任务。
- 释放低优先级锁定任务。

评分顺序为：

1. 触发任务是否完成。
2. 触发任务迟到量。
3. 触发任务完成时间。
4. 全局截止时间未达成数量。
5. 失败数量。
6. 冲突数量。
7. 平均迟到。
8. makespan。
9. 总距离。

只有释放方案评分严格更好时，才释放低优先级锁定任务。

## 路径规划

路径规划使用 A* 和时空预约：

- 普通距离计算使用 A*。
- 避碰模式下，路径段使用带时间维度的 A*。
- 规划后会记录顶点预约和边预约，用于降低机器人之间的时空冲突。
- 路径规划会尝试多种机器人规划顺序，并按失败数、冲突数、截止时间、makespan、总距离和负载均衡选择最优候选。

## 动态事件

在线会话支持以下运行时事件：

- 手工新增任务。
- 自动任务流到达。
- 手动封锁单元。
- 手动解除封锁单元。
- 机器人故障。
- 机器人恢复。

封锁单元不能越界，不能重复封锁固定障碍，也不能封锁当前被机器人占用的单元。

运行时更新请求的 `currentTime` 可以省略；省略时后端使用会话当前 tick。显式传入早于会话当前 tick 的时间仍会被拒绝，避免回退在线状态。

如果同一个封锁单元或故障机器人已经由场景动态事件激活，手动重复封锁或重复标记故障会按幂等请求处理，不会增加 `runtimeEventCount`，也不会写入重复的手动事件日志。已激活动态封锁单元的重复手动封锁会跳过普通机器人占用拒绝，确保请求保持无操作语义。

自动任务流使用 `A<n>` 编号；如果场景、动态任务或手动任务已占用某个编号，后端会跳到下一个未占用编号，避免重复任务 ID 干扰任务状态和事件日志。

如果场景动态事件和滚动窗口纳入远期任务发生在同一个 tick，事件日志会同时保留两类触发原因，便于回放时判断本次重规划是否由多个运行时条件共同触发。

## 会话生命周期

在线会话在内存中保存持续调度状态：

- `createdAt`、`updatedAt`、`lastAccessedAt` 用于区分创建、状态更新和最近访问时间。
- 空闲会话会按 `SESSION_TTL_SECONDS` 清理。
- 会话总数超过 `MAX_SESSIONS` 时，会按最近访问时间裁剪最久未访问的会话。
- 单个会话的指标历史按 `MAX_METRICS_HISTORY_SNAPSHOTS` 保留最近快照。
- 运行时事件备注按 `MAX_SESSION_EVENT_NOTES` 保留最近事件。
- 会话时间受 `MAX_SESSION_CURRENT_TIME` 限制，防止无界 tick 增长。
- 会话任务总量受 `MAX_SESSION_TASKS` 限制，包含初始任务、动态任务、手动任务和自动任务流。

场景动态事件触发后，即使后续运行时事件超过 `MAX_SESSION_EVENT_NOTES` 并裁剪旧备注，事件日志仍会恢复 `T=<triggerTime> 场景动态事件触发`、原始动态封锁数量和动态故障机器人历史。

动态任务的有效释放时间不会早于 `dynamic.triggerTime`；即使动态任务自身带有更早的 `releaseTime`，在线会话在动态事件触发前仍保持 `pending`，会话返回的任务对象与任务状态保持同一个释放时间，直接调度路径也不会在动态事件前出发。仍处于 `pending` 的任务不会计入后端 `deadlineMissCount` 或前端实时超期数。

滚动窗口纳入远期任务的触发 tick 会作为会话状态保留；即使对应运行时备注被裁剪，事件日志仍会恢复 `T=<time> 滚动窗口纳入远期任务`，避免长会话回放时丢失远期任务进入当前计划的原因。

`reset` 会恢复初始场景和初始选项，清空运行时封锁、机器人故障、手动任务、自动任务、锁定关系、指标历史和事件备注，并允许原始动态事件在同一绝对会话时间再次触发。

## 指标与失败

当前指标包括：

- makespan
- totalDistance
- conflictCount
- loadBalance
- assignedTaskCount
- deadlineMissCount
- averageLateness
- failureCount
- replanTimeMs

未分配任务会计入 `failureCount`，并写入事件日志，避免调度失败被静默忽略。

任务失败详情通过 `failureDetails` 暴露：

- `category` 区分 `temporary` 与 `permanent`。
- `recoveryAction` 给出恢复方向，例如清除封锁、恢复机器人、解除锁后重规划或修正地图/任务定义。
- `blockingCells` 和 `blockingRobotIds` 提供结构化恢复条件，前端不需要解析中文事件文本。

## 当前边界

- 当前不是完整 MAPF/CBS 求解器，冲突处理采用时空 A* 加多候选规划顺序。
- 滚动窗口已有 API 参数，当前仍使用固定数值策略，没有按场景压力自适应调整。
- 高优先级抢占只释放低优先级锁定任务，不处理同优先级抢占。
- 前端目前主要消费结果和状态，算法计算集中在后端。
