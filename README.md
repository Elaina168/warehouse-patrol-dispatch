# 仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统

这是一个面向动态仓储场景的多机器人在线调度与安全决策系统。系统以持续运行的调度会话为核心，统一处理仓储任务流、机器人协同路径、冲突规避、库存变化、通道封锁、设备故障恢复以及运行时机器人接入和安全退役。

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

## 当前状态（2026-08-30）

- 主流程已经是持续维护状态的在线任务流调度，不是一次性批量路径演示。
- 后端具备会话创建、tick 推进、统一任务追加、动态事件、运行时封锁、机器人故障与恢复、库存、充电、任务锁、抢占、指标历史和事件日志闭环。
- 在线会话运行到任意当前 tick 后，可以通过“机器人接入”面板或 `POST /api/sessions/{sessionId}/robots` 接入新机器人；接入会复用现有分配、A*、时空预约避碰、冲突检测和执行安全门，新机器人只需提供合法配置即可参与后续调度。满足空闲、无携货、无硬锁和仍有其他活动机器人等检查的机器人还可以在“当前机器人管理”中永久退役，退役前路径与事件保留，`reset` 恢复初始场景。
- 开启避碰的在线会话具有执行安全门；同一规范化冲突连续拦截三次后会返回 `safetyStall` 诊断，但不会削弱全车安全等待。
- 在线会话具有单进程内的同会话串行化、输入规模限制、容量清理和显式 CORS 来源校验。
- 运行时任务、封锁、故障和恢复等变更只在会话就绪且不处于历史回放时可用；tick 请求同步期间和历史回放期间统一禁用。后端返回 `4xx` 业务错误时仍保持“在线”状态，其中被拒绝的 tick 会暂停播放，运行时变更错误不会把可达后端误报为离线。
- 历史回放只重放路径、事件和已保留的指标，不伪造任务、库存、封锁、故障或机器人运行时快照；返回最新 `T` 后才显示这些实时状态。
- 路径规划采用优先级时空 A* 与多候选规划顺序，并已加入目标格全时域预留预检；当前仍不是完整 MAPF/CBS 求解器。
- 自适应滚动窗口仍使用生产规则：最近5个真实重规划样本、至少3个样本、中位数达到 `60ms` 进入慢状态、降至 `40ms` 退出，以及 `2×` 任务压力规则。离线校准结果没有自动修改这些值。
- 实验接口、算法边界基准和自适应校准工具服务于系统诊断、自动化回归和3S竞赛证据生成，不在默认主界面提供实验面板。
- 3S申报材料、证据与演示资源独立维护在 `competition/3s/`；默认平台继续保持在线调度操作台定位，下一项功能或算法工作仍需单独确认。

本机在 2026-08-30 运行完整检查：前端正式构建通过，前端测试 `219/219`，后端测试 `798 passed, 19 skipped`（共收集 `817` 项）。

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

在 VS Code 中打开项目根目录后，运行：

```powershell
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

脚本会自动：

- 固定使用 `C:\nvm4w\nodejs` 下的 Node/npm。
- 启动 FastAPI 后端，或复用 `8011` 上的兼容后端：`http://127.0.0.1:8011/health`
- 启动 Vite 前端：`http://127.0.0.1:5174`
- 打开本地浏览器窗口到正式前端页面。
- 按 `Ctrl+C` 时，只停止脚本拥有、已记录在 `.runtime/dev-processes.json` 且 PID 与 `startedAtUtc` 仍匹配的进程；复用的外部兼容后端不会被停止。

项目脚本仅支持仓库内的 PowerShell 7，不支持 Windows PowerShell 5.1。直接使用上述命令可以同时固定版本并绕过本机脚本执行策略限制。

如果不想自动打开浏览器：

```powershell
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1 -NoBrowser
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

2026-08-30 的当前验证结果为：前端构建通过、前端测试 `219/219`、后端测试 `798 passed, 19 skipped`（共收集 `817` 项）。

## Windows x64 参赛包

使用 `npm run competition:package` 生成便携目录和源码 ZIP。便携目录只包含运行所需的 Windows x64 可执行文件、Python 运行时、后端、前端正式构建产物和主演示/安全专项清单；源码 ZIP 只包含前端、后端应用、构建脚本、依赖锁、启动脚本、运行说明和两个运行清单，不包含内部过程记录、申报材料、实验输出、测试目录或本机环境目录。

具体构建要求和从源码 ZIP 重建方法见 `competition/BUILDING.md`。最终 ZIP 在生成时会检查批准路径、用户目录绝对路径和受限文字；发现问题会拒绝发布。

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

六个实验接口作为后端回归、内部诊断和3S证据生成入口；主界面没有实验面板。正式实验数据与图表由 `competition/3s/` 下的竞赛流程组织，不把实验控制重新加入默认平台。主界面创建在线会话时固定启用场景动态机制，导入场景中的非空动态内容会在 `dynamic.triggerTime` 自动触发。

在线会话接口用于持续追加任务并触发重规划：

```text
POST http://127.0.0.1:8011/api/sessions
GET  http://127.0.0.1:8011/api/sessions
GET  http://127.0.0.1:8011/api/sessions/{sessionId}
DELETE http://127.0.0.1:8011/api/sessions/{sessionId}
POST http://127.0.0.1:8011/api/sessions/{sessionId}/reset
POST http://127.0.0.1:8011/api/sessions/{sessionId}/tasks
POST http://127.0.0.1:8011/api/sessions/{sessionId}/robots
POST http://127.0.0.1:8011/api/sessions/{session_id}/robots/remove
POST http://127.0.0.1:8011/api/sessions/{sessionId}/tick
POST http://127.0.0.1:8011/api/sessions/{sessionId}/blocked-cells
POST http://127.0.0.1:8011/api/sessions/{sessionId}/blocked-cells/remove
POST http://127.0.0.1:8011/api/sessions/{sessionId}/failed-robots
POST http://127.0.0.1:8011/api/sessions/{sessionId}/failed-robots/restore
```

人工添加和定时自动生成的任务都使用同一个 `POST /api/sessions/{sessionId}/tasks` 接口；会话响应通过 `runtimeTaskCount` 统一统计成功插入的运行时任务。

机器人接入使用 `POST /api/sessions/{sessionId}/robots`：

```json
{
  "robot": {
    "id": "R5",
    "name": "运行时巡检车",
    "start": [25, 15],
    "battery": 512,
    "batteryCapacity": 512,
    "load": 1,
    "moveTicks": 1,
    "capabilities": ["inspection"]
  },
  "currentTime": 12
}
```

`currentTime` 可以省略，后端会使用会话当前 tick。接入位置必须是地图内的可通行空闲格或空闲充电格，不能是固定障碍、货架实体格、当前封锁格或其他机器人当前占用格；机器人 ID、能力、电量、容量、载重和移动 tick 仍由后端模型校验。响应中的 `robotStates` 返回当前完整机器人配置以及 `joinedAt`，`result.pathStartTimes` 标记每台机器人的真实路径起点；重置会移除运行时加入的机器人并恢复初始场景。接入失败不会发布候选会话状态。

运行时机器人永久退役使用 `POST /api/sessions/{session_id}/robots/remove`：

```json
{
  "robotId": "R5",
  "currentTime": 13
}
```

`currentTime` 可以省略，后端会使用会话当前 tick。第一版只允许移除真正空闲、只有未锁定远期任务的等待机器人，或已经完成任务移交且没有携货进度的故障机器人；正在执行任务、已取货未送达、存在硬锁定任务、正在前往充电或充电中，以及移除后没有其他活动机器人的情况会返回 `409`。成功后该机器人仍保留在 `robotStates`，状态为 `removed` 并带有绝对 `removedAt`；`joinedAt <= T < removedAt` 时可见，`T >= removedAt` 时不再参与规划、分配、冲突预测或安全门。退役前的路径、里程快照和事件仍可历史回放；重复请求幂等且不重复记事件，当前会话内 ID 不可复用。已退役机器人不能通过故障或恢复接口重新启用，`reset` 会恢复原始机器人集合。该功能是仿真调度会话中的模型化机器人免训练接入与安全退役，不代表实体机器人即插即用或边缘端部署。

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
