# 仓检巡视多机器人调度系统

这是一个面向仓库物流与园区巡检场景的在线多机器人调度系统。

- 唯一固定场景采用 `26 × 16` 真实仓库网格，包含双格通道、12组 `3 × 2` 货架（共72个货架实体格）、顶部进货点、底部出货点和右侧充电区。

项目采用前后端分离：

- `frontend/`：React + TypeScript + Vite，负责界面、地图渲染、时间轴播放、状态和指标展示。
- `backend/`：Python + FastAPI，负责任务分配、路径规划、时空避碰、在线重规划、充电调度、冲突检测和指标计算。
- `scripts/`：环境、启动、停止和检查脚本。
- `docs/environment.md`：环境配置说明。
- `docs/algorithm.md`：调度算法、在线重规划和稳定化边界说明。
- `docs/demo.md`：固定演示流程和验收点。
- `docs/baseline.md`：当前稳定基线、固定场景和验收命令。
- `docs/experiments.md`：后端实验、离线基准和校准工具的内部诊断说明。
- `docs/testing-guide.md`：人工测试说明，包含页面区域、综合场景、推荐测试流程、同场景策略对照和常见现象。

## 当前状态（2026-07-27）

- 主流程已经是持续维护状态的在线任务流调度，不是一次性批量路径演示。
- 后端具备会话创建、tick 推进、统一任务追加、动态事件、运行时封锁、机器人故障与恢复、库存、充电、任务锁、抢占、指标历史和事件日志闭环。
- 开启避碰的在线会话具有执行安全门；同一规范化冲突连续拦截三次后会返回 `safetyStall` 诊断，但不会削弱全车安全等待。
- 在线会话具有单进程内的同会话串行化、输入规模限制、容量清理和显式 CORS 来源校验。
- 运行时任务、封锁、故障和恢复等变更只在会话就绪且不处于历史回放时可用；tick 请求同步期间和历史回放期间统一禁用。后端返回 `4xx` 业务错误时仍保持“在线”状态，其中被拒绝的 tick 会暂停播放，运行时变更错误不会把可达后端误报为离线。
- 历史回放只重放路径、事件和已保留的指标，不伪造任务、库存、封锁、故障或机器人运行时快照；返回最新 `T` 后才显示这些实时状态。
- 路径规划采用优先级时空 A* 与多候选规划顺序，并已加入目标格全时域预留预检；当前仍不是完整 MAPF/CBS 求解器。
- 自适应滚动窗口仍使用生产规则：最近5个真实重规划样本、至少3个样本、中位数达到 `60ms` 进入慢状态、降至 `40ms` 退出，以及 `2×` 任务压力规则。离线校准结果没有自动修改这些值。
- 实验接口、算法边界基准和自适应校准工具当前只服务于系统诊断与回归，不是当前建设重点。
- 在明确参赛和材料要求前，优先继续完善系统本体；下一项功能或算法工作尚未确定。

本机在 2026-07-27 运行完整检查：前端正式构建通过，前端测试 `105/105`，后端测试 `523/523`。

## 仓库库存业务

货架实体格不可通行，每个货架有一个唯一相邻作业格，机器人只在作业格完成取放。入库任务从进货点取货并送到空货架作业格；出库任务从已有货物货架作业格取货并送到出货点。

后端在线会话维护权威库存与预订状态，并通过 `shelfStates` 返回 `empty`、`inboundReserved`、`occupied`、`outboundReserved` 四种状态。`occupied` 和 `outboundReserved` 显示库存高亮；出库机器人实际到达取货作业格后货架熄灭，入库任务完成放货和作业时间后货架亮起。前端只渲染后端状态，不自行推断库存。

默认三项配送任务为：

- `T1` 入库：进货点 `[2,0]` -> 货架 `[2,4]` 的唯一作业格 `[2,5]`。
- `T2` 出库：初始有货货架 `[13,7]` 的唯一作业格 `[13,6]` -> 出货点 `[2,15]`。
- `T4` 入库：进货点 `[8,0]` -> 货架 `[12,8]` 的唯一作业格 `[12,9]`。

默认会话初始有12个货架亮起；`T2` 取货后对应货架熄灭，`T1`、`T4` 放货后对应货架亮起，最终有货货架为13个。默认6个任务在 `T=700` 前全部完成，冲突、失败、截止超期和充电访问均为0；同一固定输入下，无避碰基线的预测冲突大于0，开启避碰后为0。

手工任务和随机任务都提交到统一的 `POST /api/sessions/{sessionId}/tasks` 端点，并执行相同的后端库存校验。随机生成器只把当前未预订的空货架作为入库候选，只把当前有货且未预订的货架作为出库候选；后端仍是最终校验方。

## VS Code 一键启动

在 VS Code 中打开 `D:\codex\summer` 后，运行：

```powershell
.\scripts\start-dev.ps1
```

脚本会自动：

- 固定使用 `C:\nvm4w\nodejs` 下的 Node/npm。
- 启动 FastAPI 后端：`http://127.0.0.1:8011/health`
- 启动 Vite 前端：`http://127.0.0.1:5174`
- 打开本地浏览器窗口到正式前端页面。
- 按 `Ctrl+C` 时停止前后端进程。

如果 PowerShell 阻止脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

如果不想自动打开浏览器：

```powershell
.\scripts\start-dev.ps1 -NoBrowser
```

## VS Code 任务

也可以用 VS Code 任务启动：

1. 按 `Ctrl+Shift+P`
2. 输入 `Tasks: Run Task`
3. 选择 `Start project`

停止服务：

1. 按 `Ctrl+Shift+P`
2. 输入 `Tasks: Run Task`
3. 选择 `Stop project`

## 项目检查

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

检查内容：

- 正式前端构建
- 前端单元测试
- FastAPI 后端测试

2026-07-27 的当前验证结果为：前端构建通过、前端测试 `105/105`、后端测试 `523/523`。

## API

正式前端通过后端接口获取调度结果：

```text
POST http://127.0.0.1:8011/api/dispatch
```

请求内容包含场景和策略选项；响应包含任务分配、路径、冲突、指标和事件日志。

避碰实验对比接口用于同一场景下自动生成关闭避碰和开启避碰两组结果：

```text
POST http://127.0.0.1:8011/api/experiments/conflict-avoidance
```

动态重规划实验对比接口用于同一场景下自动生成关闭动态事件和开启动态事件两组结果：

```text
POST http://127.0.0.1:8011/api/experiments/dynamic-replanning
```

滚动窗口实验对比接口用于同一场景下生成多个固定 `assignmentReplanWindow` 结果，并可通过 `includeAdaptive=true` 增加自适应窗口结果：

```text
POST http://127.0.0.1:8011/api/experiments/replan-window
```

规模实验对比接口用于提交多组不同规模场景并用同一组选项运行调度：

```text
POST http://127.0.0.1:8011/api/experiments/scale
```

固定种子压力接口用于运行标准或扩展的可复现随机压力场景；其中 `assignmentRatePercent` 表示已分配任务数占总任务数，不代表在线执行完成率：

```text
POST http://127.0.0.1:8011/api/experiments/seeded-pressure
```

在线压力接口用于运行固定时长的在线任务流、封锁、故障和恢复流程。响应分别提供 `coverageRatePercent`（未处于 `unassigned` 的任务占比）和 `actualCompletionRatePercent`（实验结束时，已完成任务占已释放任务的比例）：

```text
POST http://127.0.0.1:8011/api/experiments/online-pressure
```

六个实验接口仅作为后端回归和内部诊断入口；主界面没有实验面板。只有在后续明确参赛及材料要求后，才决定是否使用这些结果制作图表或报告。主界面创建在线会话时固定启用场景动态机制，导入场景中的非空动态内容会在 `dynamic.triggerTime` 自动触发。

在线会话接口用于持续追加任务并触发重规划：

```text
POST http://127.0.0.1:8011/api/sessions
GET  http://127.0.0.1:8011/api/sessions
GET  http://127.0.0.1:8011/api/sessions/{sessionId}
DELETE http://127.0.0.1:8011/api/sessions/{sessionId}
POST http://127.0.0.1:8011/api/sessions/{sessionId}/reset
POST http://127.0.0.1:8011/api/sessions/{sessionId}/tasks
POST http://127.0.0.1:8011/api/sessions/{sessionId}/tick
POST http://127.0.0.1:8011/api/sessions/{sessionId}/blocked-cells
POST http://127.0.0.1:8011/api/sessions/{sessionId}/blocked-cells/remove
POST http://127.0.0.1:8011/api/sessions/{sessionId}/failed-robots
POST http://127.0.0.1:8011/api/sessions/{sessionId}/failed-robots/restore
```

人工添加和定时自动生成的任务都使用同一个 `POST /api/sessions/{sessionId}/tasks` 接口；会话响应通过 `runtimeTaskCount` 统一统计成功插入的运行时任务。

任务优先级范围为 `0..5`：数值越大优先级越高，`0` 为普通/后台，`5` 为最高/应急；突发任务至少为 `4`，同优先级任务不触发抢占。

## 当前机器环境

已确认可用：

- Node `v20.20.2`
- npm `10.8.2`
- nvm-windows `1.2.2`
- Python `3.13.2`

普通 `node` 命令可能解析到 Codex 应用目录，因此项目脚本会显式使用：

```text
C:\nvm4w\nodejs\node.exe
C:\nvm4w\nodejs\npm.cmd
```
