# Final Integration Review Fixes Implementation Plan

**Goal:** 修复会话失效、创建失败残留、错误状态播放和自适应报告事务四项最终复审问题，并保持平台既有正常路径不变。

**Constraints:** 不修改调度算法、API 路由、场景格式、进程 ownership 或 `output/`；所有修改保持 UTF-8；先写失败测试，再写最小实现。

## Task 1: 区分会话失效 404 与可恢复业务 4xx

**Files:**
- Modify: `frontend/src/domain/sessionRequestState.ts`
- Modify: `frontend/src/domain/sessionRequestState.test.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`

- [x] 为 mutation/tick 404 增加失败测试，要求 `invalidateSession=true`、dispatch error、暂停播放。
- [x] 保留 409/422 的既有 online/ready 语义测试。
- [x] 让 `classifySessionRequestFailure` 返回显式 `invalidateSession`。
- [x] active session 请求遇到 404 时清除 session/result/ref；其他 4xx 保留。
- [x] 运行 `sessionRequestState.test.ts` 与 `main.test.ts`。

## Task 2: 创建新会话时原子清除旧展示结果

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`

- [x] 增加 `clearSessionViewState` 回归，证明 session 与 result 同时清除。
- [x] 在 create 生命周期开始和 session 失效路径复用该 helper。
- [x] 验证 create 失败不能继续依赖旧 result 渲染或启用播放。

## Task 3: 播放控件与实际播放门槛统一

**Files:**
- Modify: `frontend/src/domain/sessionRequestState.ts`
- Modify: `frontend/src/domain/sessionRequestState.test.ts`
- Modify: `frontend/src/main.tsx`

- [x] 增加 `canControlOnlinePlayback` 测试：仅 result 存在、ready 且无 tick 时可用。
- [x] 按同一 helper 设置按钮 disabled，并在 handler 内再次保护。
- [x] 验证 loading/error 无法把 `playing` 切为 true。

## Task 4: 统一自适应报告事务边界

**Files:**
- Modify: `backend/benchmarks/adaptive_reporting.py`
- Modify: `backend/tests/test_adaptive_replan_calibration.py`

- [x] 增加有旧 bundle/无旧 bundle 的 partial 删除失败回滚测试。
- [x] 增加提交后 backup cleanup 失败不误报写入失败的测试。
- [x] 增加 rollback restore 失败保留可恢复 backup 的测试。
- [x] 将 rollback tracking、preserved backup 和提交后 best-effort cleanup 与普通报告器语义对齐。
- [x] 运行全部 adaptive final-report 测试。

## Task 5: 完整平台验收

- [x] 运行前端构建和全部前端测试。
- [x] 运行全部后端测试与 `pip check`。
- [x] 运行真实隐藏开发服务 smoke，探测 `8011/health` 与 `5174`。
- [x] 通过 manifest 停止服务，确认记录 PID、端口和 manifest 均无残留。
- [x] 运行 `git diff --check`，只复核本批修复 diff，不扩展新的无边界审查。
