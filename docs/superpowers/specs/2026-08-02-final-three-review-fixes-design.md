# 最终三项复审问题修复设计

## 目标

一次性修复本分支最终复审确认的三项问题，同时保持在线调度平台、benchmark 报告发布和开发进程清理的既有行为稳定：

1. reset 请求返回 HTTP 4xx 后保留有效会话的可操作性。
2. 消除 benchmark 最终报告“返回失败但新 final 已公开”的不一致状态。
3. 开发进程 manifest 身份不匹配时明确报告，同时继续避免误杀无关进程。

本次不修改调度算法、API 路由、场景结构、端口配置或进程归属判定规则。

## 前端会话错误状态

`applySessionRequestFailure` 必须显式知道失败时是否存在可继续使用的有效会话结果。

- 首次创建会话失败时没有可保留结果。即使 HTTP 状态是 4xx，页面仍进入 dispatch error 状态，但后端连接状态保持 online。
- reset 失败时保留原会话和最后一次有效结果。HTTP 4xx 使用 `classifySessionRequestFailure` 的 mutation 决策，恢复 dispatch ready，使播放和运行时操作可以继续。
- HTTP 5xx 和网络错误继续进入 error/offline 状态，不因存在旧结果而恢复操作。
- 错误文本继续同时写入 dispatch error 和可见的 operation error。

测试分别覆盖首次创建 4xx 与保留会话的 reset 4xx，防止两个调用场景相互污染。

## Benchmark 最终报告事务边界

`write_final_report` 的事务提交点定义为：三个 final 文件均已发布，并且 `results.partial.json` 已成功删除。

- 在提交点之前发生 staging、backup、final publish 或 partial 删除失败时，使用现有 rollback 机制恢复旧完整 bundle；此前没有旧 bundle 时删除已发布的新 final。
- partial 删除失败时，备份仍完整存在，必须先尝试 rollback，再抛出包含原始失败和可能的 rollback 失败信息。
- 提交点之后只剩 `.tmp` 和 `.backup` 清理。此阶段清理失败不得再将已提交报告描述为写入失败，也不得尝试使用可能已被部分删除的备份回滚。
- `finally` 继续执行尽力清理；无法删除的事务文件可以留待诊断或后续清理，但不会改变 final bundle 的有效性。

测试覆盖有旧 bundle 和无旧 bundle时的 partial 删除失败，并覆盖提交后的事务文件清理失败不会产生错误的写入失败结果。

## 开发进程身份不匹配报告

`Stop-RecordedProcessTree` 继续逐条使用 PID 与 `startedAtUtc` 双重验证进程身份。

- PID 不存在时移除过期记录。
- PID 存在但启动身份不匹配时保留 manifest 条目，不调用停止回调，并输出包含 role 与 PID 的 warning。
- 身份匹配时保持现有停止、关闭句柄和失败保留逻辑。

本次只增加诊断，不新增端口扫描、进程名匹配、命令行匹配或仅凭 PID 的终止行为。

## 验证策略

严格按测试驱动顺序实施：

1. 先增加三组回归测试并确认它们因当前缺陷失败。
2. 逐项完成最小生产代码修改，每项先运行聚焦测试。
3. 运行前端生产构建、全部前端测试、全部后端测试和 `pip check`。
4. 运行开发服务启停 smoke，核对项目端口、项目进程和 `.runtime/dev-processes.json` 均无残留。
5. 执行 `git diff --check` 并复核最终差异只包含本设计范围内的修改。

## 非目标

- 不重构会话请求协调器。
- 不改变 benchmark 报告字段或文件格式。
- 不改变开发进程 ownership 模型。
- 不修改或清理未跟踪的 `output/`。
- 不合并或推送分支。
