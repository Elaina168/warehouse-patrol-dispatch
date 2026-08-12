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
