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

或：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

检查内容包括：

- `frontend` 构建
- `frontend` 单元测试
- `backend` 测试

## 依赖安装

前端依赖：

```powershell
npm --prefix frontend install
```

后端依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
```
