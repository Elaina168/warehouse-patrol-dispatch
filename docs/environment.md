# 环境与框架配置

## 技术栈

正式项目采用前后端分离：

- 前端：React + TypeScript + Vite
- 后端：Python + FastAPI
- 算法：后端负责任务分配、A* 路径规划、优先级避碰、冲突检测、动态重规划和指标计算

## 当前机器发现

普通 `node` 命令可能优先解析到 Codex 应用目录：

```text
C:\Program Files\WindowsApps\OpenAI.Codex_26.602.4764.0_x64__2p2nqsd0c76g0\app\resources\node.exe
```

该路径此前出现过拒绝访问。机器上可用的真实 Node 位于：

```text
C:\nvm4w\nodejs\node.exe
C:\nvm4w\nodejs\npm.cmd
```

已确认版本：

```text
Node v20.20.2
npm 10.8.2
nvm-windows 1.2.2
Python 3.13.2
```

2026-07-27 已使用上述环境重新创建本地 `.venv`、恢复前端依赖并完成前后端启动检查。`.venv`、`frontend/node_modules` 和 `frontend/dist` 都是可重新生成的忽略目录，不属于源码。

## 启动项目

一键启动正式版前后端并打开浏览器：

```powershell
.\scripts\start-dev.ps1
```

前端地址：

```text
http://127.0.0.1:5174
```

后端健康检查：

```text
http://127.0.0.1:8011/health
```

停止开发服务：

```powershell
.\scripts\stop-dev.ps1
```

## 单独启动

启动前端：

```powershell
.\scripts\dev-frontend.ps1
```

启动后端：

```powershell
.\scripts\dev-backend.ps1
```

## 浏览器跨域来源

未配置时，浏览器 CORS 仅允许两个本地 Vite 来源：`http://127.0.0.1:5174` 和 `http://localhost:5174`。需要显式允许其他浏览器来源时，使用逗号分隔的 `WAREHOUSE_PATROL_CORS_ORIGINS`：

```powershell
$env:WAREHOUSE_PATROL_CORS_ORIGINS='http://192.168.1.10:5174,https://demo.example.com'
npm run backend:dev
```

空值、通配符、包含路径、查询或片段的值，以及非 HTTP(S) 协议都会使后端启动失败。CORS 不是认证机制，非浏览器客户端不受浏览器 CORS 策略影响。

## 检查项目

```powershell
.\scripts\test-all.ps1
```

或（两条命令均按相同顺序运行 `frontend:build`、`frontend:test` 和 `backend:test`）：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

检查内容包括：

- `frontend` 构建
- `frontend` 单元测试
- `backend` 测试

2026-07-27 的当前结果：前端构建通过、前端测试 `105/105`、后端测试 `523/523`。

## 依赖安装

前端依赖：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend install
```

后端依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
```

`backend/requirements.txt` 是后端直接依赖及其版本的权威来源；`backend/requirements.lock.txt` 是从已完成全量验证的虚拟环境生成的运行、测试和传递依赖精确锁。日常或新环境安装使用 lock。只有在有意更新直接依赖、重建并验证完整环境后，才可用该环境的 `pip freeze --all` 刷新 lock；刷新时排除 `pip` 自身，并重新运行完整前后端检查。

如果 `.venv` 目录存在但缺少 `Scripts\python.exe`，应将其视为未安装完成的生成目录，重新创建虚拟环境并安装锁定的后端依赖；不要修改项目脚本绕过该检查。
