# 仓检巡视多机器人调度竞赛项目

这是一个面向计算机竞赛展示的仓库物流与园区巡检一体化多机器人调度系统。

项目采用前后端分离：

- `frontend/`：React + TypeScript + Vite，负责界面、地图渲染、时间轴播放、状态和指标展示。
- `backend/`：Python + FastAPI，负责任务分配、路径规划、时空避碰、在线重规划、充电调度、冲突检测和指标计算。
- `scripts/`：环境、启动、停止和检查脚本。
- `docs/environment.md`：环境配置说明。
- `docs/algorithm.md`：调度算法、在线重规划和稳定化边界说明。
- `docs/demo.md`：固定演示流程和验收点。
- `docs/baseline.md`：当前 P0 基线冻结范围、固定场景和验收命令。
- `docs/experiments.md`：后端实验对比接口和报告取数说明。
- `docs/testing-guide.md`：人工测试说明，包含页面区域、综合场景、推荐测试流程、同场景策略对照和常见现象。

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

滚动窗口实验对比接口用于同一场景下自动生成多个 `assignmentReplanWindow` 参数结果：

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

六个实验接口仅作为后端回归和报告取数入口；主界面没有实验面板。主界面创建在线会话时固定启用场景动态机制，导入场景中的非空动态内容会在 `dynamic.triggerTime` 自动触发。

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
