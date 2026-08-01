# 最终加固复审问题修复设计

## 目标

在 `codex/full-system-hardening` 分支内修复最终全分支复审确认的四个问题，同时保持现有 API 结构、在线调度语义、进程所有权边界和失败分类不变。

## 范围与约束

- 只修复会话时间上限、场景导入未知字段、开发进程停止身份绑定、时空 A* 剩余预算剪枝四项问题。
- 不修改 10000 tick 的绝对路径和会话上限，不引入新的前端面板，不改变任务调度策略。
- 不恢复已删除的静态原型，不修改或删除现有 `output/`。
- 后端继续以 Pydantic `extra="forbid"` 为输入契约；前端导入边界应在用户选择文件时给出失败，而不是把必然被后端拒绝的数据保存为已导入场景。
- 开发停止脚本只能终止已验证属于本项目的进程对象，不得在身份验证后重新仅按 PID 定位目标。

## 设计

### 1. 统一会话时间上限

将 `MAX_SESSION_CURRENT_TIME = 10_000` 放入 `backend/app/limits.py`，由 `schemas.py` 和 `sessions.py` 共同导入。新增受限整数类型，仅用于：

- `Task.releaseTime`
- `DynamicEvent.triggerTime`

`deadline` 不受该类型限制，因为晚于会话上限的截止时间仍可用于表达“在会话结束前不会超期”，并不要求会话推进到该时间。运行时任务复用 `Task` 模型，因此自动获得同一限制。

前端 `scenarioImport.ts` 导出同值的 `MAX_SESSION_CURRENT_TIME`，导入时将 `releaseTime` 和 `triggerTime` 的最大值限制为 10000。越界 JSON 在导入阶段报错，不进行截断。

### 2. 前端导入严格拒绝未知字段

新增通用的 `hasOnlyKeys` 检查，并在以下对象层级调用：

- Scenario：`id`、`name`、`description`、`width`、`height`、`obstacles`、`zones`、`shelves`、`robots`、`tasks`、`dynamic`、`chargeTime`
- Zones：`warehouse`、`inspection`、`delivery`、`charging`
- Shelf：`id`、`cell`、`serviceCell`、`initialOccupied`
- Robot：`id`、`name`、`start`、`battery`、`batteryCapacity`、`load`、`moveTicks`、`capabilities`
- DynamicEvent：`triggerTime`、`blockedCells`、`failedRobots`、`tasks`
- Task：`id`、`type`、`title`、`priority`、`releaseTime`、`deadline`、`serviceTime`、`targets`、`pickup`、`dropoff`、`demand`、`target`

这些集合与当前后端 `Scenario`、`Zones`、`Shelf`、`Robot`、`DynamicEvent`、`Task` 模型逐项一致。未知字段一律拒绝，不静默删除。

### 3. 将停止操作绑定到已验证进程对象

把现有布尔身份检查拆为返回匹配 `System.Diagnostics.Process` 对象的获取函数。获取后立即访问 `SafeHandle`，再比较 `StartTime`；不匹配时关闭对象并返回空值，匹配时将同一对象传给停止回调。

`Stop-RecordedProcessTree` 的默认回调改为接收 `System.Diagnostics.Process`，通过该对象的 `Kill($true)` 终止进程树并进行有界 `WaitForExit`。无论停止成功或失败，外层都关闭已验证进程对象。manifest 仍只删除成功停止或已经不存在的记录；失败记录继续保留并输出诊断。

测试钩子同步改为接收进程对象。这样身份检查、停止和句柄关闭共享同一对象，消除检查后再执行 `taskkill /PID` 的复用窗口。

### 4. 时空 A* 按实际剩余预算剪枝

`astar_timed` 在设置了 `path_end_time` 时执行以下规则：

1. 静态最早到达时间已经超过 `latest_arrival` 时，立即记录 `exhausted` 并抛出 `_TimedPathBudgetExceeded`。
2. 目标在 `latest_arrival` 前没有未预留到达时刻、但在完整自然时域内存在未预留时刻时，可以证明限制来自剩余预算，立即报告预算超限；若完整自然时域内也没有未预留时刻，继续按 `goalFullyReserved` 返回普通不可达。
3. 邻居扩展使用 `latest_arrival` 剪枝，不再把预算外状态压入堆。
4. 如果搜索在预算边界内耗尽，但静态距离和目标预留预检都不能证明原因仅为预算，则保守返回普通不可达，不再通过搜索预算外状态来区分。这样保留现有“永久预留或静态不可达不得误报为预算不足”的失败语义。

调用方现有的 `_TimedPathBudgetExceeded` 捕获逻辑继续生成“规划路径超过最大时域 10000 tick”的任务失败，不改变外部 API。

## 测试设计

按红—绿顺序增加以下回归：

- 后端模型接受 10000、拒绝 10001 的 `releaseTime` 和 `triggerTime`；会话创建及运行时任务入口均不能保存越界数据。
- 前端导入接受所有合法可选字段，并分别拒绝 Scenario、Zones、Shelf、Robot、DynamicEvent、Task 的未知字段；拒绝 10001 的释放/触发时间。
- manifest 停止回调收到的是启动时间匹配的 `System.Diagnostics.Process` 对象；对象在成功和失败路径后都关闭；源码不再使用默认 `taskkill /PID` 停止已记录进程。
- 时空 A* 在最早到达已超预算时零扩展失败；目标只在预算后才解除预留时零扩展报告预算失败；完整自然时域永久预留仍保持普通不可达；其余搜索扩展数不得超过剩余预算允许的状态范围。

定向测试通过后运行完整 `npm run check`、`pip check`、`git diff --check`，最后确认 5174/8011 无残留监听且 `.runtime/dev-processes.json` 为空。

## 非目标

- 不把前后端模型改为自动代码生成。
- 不静默清洗用户导入数据。
- 不新增跨重启 Windows Job Object 管理系统。
- 不调整 10000 tick 上限、规划候选顺序、调度评分或前端交互布局。
