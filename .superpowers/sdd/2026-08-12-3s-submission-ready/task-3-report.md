# Task 3 实施报告

## 状态

完成。前端新增独立 3S 竞赛查询入口与清单驱动控制器，默认主面板保持原入口；API 默认基址为精确 `/api` 并支持 `VITE_API_BASE_URL` 覆盖；Vite 开发代理与 FastAPI 生产静态托管已实现。未修改后端业务 API 模型、会话算法或 Task 4+ 内容。

## 改动

- `frontend/src/competition/competition.tsx`
  - 直接导入 `competition/3s/manifests/main-demo.json` 与 `safety-demo.json`。
  - 定义 `CompetitionDemoManifest`、`CompetitionDemoStep`、`CompetitionRunState`，状态精确为 `idle / preparing / running / paused / completed / failed`。
  - 拒绝非版本 1 清单。
  - 提供主演示/安全演示单步、自动、重置控制；单步和自动共用同一执行路径。
  - 写请求出现连接结果不确定时，先 GET 当前会话；仅在后置条件尚未成立时重试一次。
  - HTTP/后置条件失败进入 `failed`；主演示 `safetyIntervention` 立即暂停，且 tick 后不会继续提交计划写请求。
  - `record=1` 隐藏开发控件，并以固定 350ms 讲解停顿执行加速自动流程。
- `frontend/src/main.tsx`
  - API 默认基址改为 `/api`，保留 `VITE_API_BASE_URL`。
  - 公开根入口按 `?competition=3s&demo=main|safety` 渲染竞赛屏幕；普通 URL 仍渲染原 `App`。
  - 所有既有会话调用改为把 API 基址本身视为 `/api`，不再重复拼接 `/api`。
- `frontend/vite.config.ts`
  - `/api` 代理到 `http://127.0.0.1:8011`，保留 5174/8011 双服务。
- `backend/app/main.py`
  - 全部既有 API 路由之后挂载 `frontend/dist`。
  - dist 不存在时静默跳过，API 正常启动。
  - 未知 `/api/*` 对 GET/POST/DELETE/OPTIONS 固定返回 JSON 404，不回 SPA HTML。
  - 静态资产与非 API 前端路径分别提供文件和 `index.html` 回退。
- 新增/更新测试覆盖相对 API、Vite 代理、公开查询入口、可见 UI、清单版本、状态转换、单步/自动等价、录制节奏、GET 核对恢复、安全暂停、重置及生产静态托管。

## TDD RED 证据

1. 相对 API 与开发代理：`npm --prefix frontend test -- src/domain/sessionApi.test.ts src/viteConfig.test.ts`
   - RED：4 个失败；实际请求为 `/api/api/sessions...`，Vite 配置缺少 `server.proxy`。
   - GREEN：10/10 通过。
2. 竞赛清单/控制器/UI：`npm --prefix frontend test -- src/competition/competition.test.ts`
   - 初始 RED：`Cannot find module './competition'`。
   - 固定录制节奏 RED：等待记录实际 `[]`，期望 `[350, 350]`。
   - 公开查询入口 RED：`RootApplication` 未导出，真实入口组件为 `undefined`。
   - tick 安全暂停 RED：实际仍调用 `/tasks` 与 GET，期望 tick 后立即停止。
   - 最终 GREEN：9/9 通过。
3. 生产静态托管：`python -m pytest backend/tests/test_health.py -q`
   - 初始 RED：无法导入 `configure_frontend_static`。
   - 首次实现 RED：静态资产返回 JSON 404，测试隔离后证实是全局应用既有 catch-all 注册状态干扰。
   - GREEN：8/8 通过。
4. 全量回归发现的既有 API 404 契约：首次全量后端 `735` 项中 `733` 通过、2 个失败；未知 POST `/api/...` 因 GET-only SPA catch-all 返回 405。
   - 使用既有 `test_request_models_reject_unknown_fields` 与 `test_session_stream_task_endpoint_is_removed` 作为真实 RED。
   - 添加 API 专用 catch-all 后聚焦 GREEN：10/10 通过。

## GREEN 与回归结果

- 最终命令：`C:\nvm4w\nodejs\npm.cmd run check`
- 前端生产构建：通过，Vite 1589 modules transformed。
- 前端测试：9 files，200/200 通过。
- 后端测试：735/735 通过，79.96 秒。
- `git diff --check`：通过，仅有 Git 对工作区 LF/CRLF 转换的提示，无空白错误。

## 提交

- 实现提交：`bf6b86e feat: add 3s competition demo mode`。
- 本报告的哈希回填作为紧随其后的文档提交，不改变实现范围或验证结果。

## 关注点

- 竞赛控制器已覆盖公开入口与可见 SSR 行为，但本 Task 未增加浏览器 E2E；最终 UI 录制仍建议在 Task 5 的录屏验收中验证浏览器交互与节奏。
- `record=1` 使用固定 350ms 步间停顿；这是本任务要求的确定性加速节奏，不代表演讲稿语速或最终视频剪辑时长。
- FastAPI 静态托管只在启动时发现 `frontend/dist/index.html` 才注册；开发时后构建 dist 需要重启后端才能启用静态托管，API 不受影响。

## Fix round 1/5（2026-08-12）

### 状态与改动

- 安全入口不再执行两个空 `accept`。它使用 Task 1 安全清单内嵌场景创建会话，依次精确推进 T=2、T=3、T=4：每次要求本次 `safetyIntervention.time` 与目标一致、机器人返回位置唯一、末条 `metricsHistory.activeConflictCount === 0`，第三次还要求 `safetyStall.consecutiveCount === 3`。
- 安全界面明确专项证据边界：只以每次结构化返回证明危险移动未提交、机器人处于可观察安全等待，不宣称前端拥有后端内部全程轨迹。
- 所有 tick 写入与 TypeError 后 GET 核对均要求 `currentTime ===` 目标时点，不再接受越过时点。
- 主演示最终 `accept` 同时要求 `completedTaskCount === 7`、末条 `metricsHistory.activeConflictCount === 0`，以及结果指标 `conflictCount / failureCount / deadlineMissCount` 均为 0。
- 控制器新增 `subscribe`，所有状态更新统一经 `update` 同步通知。React 订阅真实控制器；自动运行会发布 `preparing`、每步 `running`、步骤后置条件与 `record=1` 的每次 350ms 讲解停顿快照。
- reset 复用不确定写核对：POST TypeError 后 GET 检查 T=0、`runtimeTaskCount === 0`、`runtimeEventCount === 0`；已重置不重复，未重置最多再 POST 一次。reset 的 HTTP、核对或重试错误均转换为 `failed` 并通知订阅者，不再 reject 越过 React 状态更新。
- 未处理已台账 Minor PUT/PATCH/HEAD，未改默认面板、后端业务模型/算法或 Task 4+。

### 逐项 RED / GREEN

测试文件：`frontend/src/competition/competition.test.ts`。

1. 安全门契约 RED：聚焦运行 11 tests 时 2 failed；界面缺少“每次返回状态证明危险移动未提交”，控制器在 `stepIndex=1` 进入 `failed`，无法到第三次 `safetyStall=3`。GREEN：11/11。
2. 精确 tick 与主终验 RED：越时 GET 返回 `currentTime=13` 后控制器仍为 `paused`，未进入预期 `failed`；现有 accept 也没有完整结构化校验。GREEN：12/12。
3. 订阅 RED：`controller.subscribe is not a function`。GREEN：13/13，测试从真实控制器收集 `preparing`、`running`、步骤推进与 `录制讲解停顿 350ms` 序列。
4. reset 不确定写 RED：两例均 `promise rejected "TypeError: connection lost" instead of resolving`；补充自动录制停顿错误 RED 为 `promise rejected "Error: pause failed" instead of resolving`。GREEN：16/16，覆盖 GET 已生效不重试、GET 未生效后最多一次重试失败转 `failed` 通知，以及自动流程异常不越过通知边界。

### 最终验证

- 聚焦：`npm --prefix frontend test -- src/competition/competition.test.ts`，16/16 passed。
- 全量：`npm run check`，前端生产构建通过（1589 modules transformed），前端 9 files / 207 tests passed，后端 735 tests passed（80.00s）。
- `git diff --check`：通过，仅有 Git LF/CRLF 工作区转换提示，无空白错误。

### 修复提交

- 提交后由本轮最终消息报告精确哈希。

## Fix round 2/5（2026-08-12）

### 状态与最小修复

- 仅修复 `record=1` 自动运行在终态后仍无条件写入 350ms 停顿的问题。
- `runAutomatically` 现在只在 `step()` 返回后仍未 `completed`、未 `failed` 且没有 `pauseReason` 时发布并等待录制停顿。
- completed 保留“全部步骤已完成 / 全部后置条件已满足”；failed 保留失败消息与“演示已停止”；主演示安全暂停保留 `pauseReason` 与“安全门已暂停主演示”。三类终态均不再额外调用停顿。
- 未修改 Task 4 或旧 Minor。

### RED / GREEN

- 测试文件：`frontend/src/competition/competition.test.ts`。
- 命令：`npm --prefix frontend test -- src/competition/competition.test.ts`。
- RED：18 tests 中 4 failed。completed 的 wait 实际为 `[350, 350, 350]`、期望 `[350, 350]`；failed 与 safety-paused 的 wait 均实际为 `[350]`、期望 `[]`；最终 completed 快照的 `message/postcondition` 实际为“录制讲解停顿中 / 录制讲解停顿 350ms”，而非完成文案。
- GREEN：18/18 passed，1 file passed；耗时 875ms。

### 提交范围

- `frontend/src/competition/competition.tsx`
- `frontend/src/competition/competition.test.ts`
- `.superpowers/sdd/2026-08-12-3s-submission-ready/task-3-report.md`

## Fix round 3/5（2026-08-12）

### 编译 RED 与根因

- RED 命令：`C:\nvm4w\nodejs\npm.cmd run check`。
- RED 结果：在 frontend build 的 `tsc -b` 阶段退出 1；`competition.tsx:182` 与 `:183` 各报 TS2367，循环条件已把 `runState` 窄化为非终态联合，TypeScript 不会因 `await step()` 重新分析方法内部对 `this.state` 的副作用，因此 completed/failed 比较被静态判为无重叠。

### 最小修复与指定验证

- 仅将 record 停顿判断封装为 `shouldPauseForRecording()`，在方法调用中重新读取最新控制器状态；运行语义保持不变：completed、failed、safety-paused 不停顿，正常非终态 record 步骤仍停顿 350ms。
- Build 命令：`npm --prefix frontend run build`；结果通过，`tsc -b` 无错误，Vite `1589 modules transformed`，构建耗时 1.83s。
- 聚焦命令：`npm --prefix frontend test -- src/competition/competition.test.ts`；结果 1 file / 18 tests passed，测试耗时 33ms，总耗时 880ms。
- 未开始 Task 4，未处理旧 Minor。
