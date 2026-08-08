# 在线压力实验会话隔离设计

## 目标

让 `run_online_pressure_experiment` 完整复用真实在线会话逻辑，但不读取、写入、淘汰或删除生产全局 session registry 中的任何会话。

## 已确认根因

`backend/app/experiments.py` 当前直接调用 `create_session`、`tick_session`、运行时事件函数和 `delete_session`。这些函数最终访问 `backend/app/sessions.py` 的模块级 `_sessions` 与 `_sessions_lock`。当 `_sessions` 达到 `MAX_SESSIONS` 时，`_publish_session` 会淘汰最久未访问的会话；实验结束后的 `delete_session` 只能删除实验会话，不能恢复已淘汰会话。

## 方案比较

1. **显式注入 `SessionRegistry`（采用）**：每个 session 操作接收可选的 registry，默认仍使用现有全局 registry；实验创建私有 registry 并在整条调用链中显式传递。状态边界清晰，可并发测试，不依赖隐式线程或上下文状态。
2. **临时替换模块级全局变量**：改动较少，但并发请求可能观察到临时 registry，不能满足隔离要求。
3. **为实验复制一套调度流程**：不会触碰全局 registry，但会复制在线 session 行为，后续容易与生产逻辑漂移。

## 设计

在 `backend/app/sessions.py` 中增加 `SessionRegistry`，拥有 `sessions`、`lock` 和 `max_sessions`。现有 `_sessions` 与 `_sessions_lock` 继续作为默认 registry 的别名，保持已有测试和内部诊断兼容。

以下函数增加仅限关键字的 `registry: SessionRegistry | None = None`：

- `create_session`
- `get_session`
- `list_sessions`
- `delete_session`
- `reset_session`
- `add_task`
- `add_blocked_cell`
- `remove_blocked_cell`
- `fail_robot`
- `restore_robot`
- `tick_session`

`None` 解析为默认 registry。`_locked_session`、`_cleanup_sessions_locked`、`_cleanup_sessions` 和 `_publish_session` 在同一调用中只访问解析后的 registry。默认 registry 的容量继续读取可由测试覆盖的 `MAX_SESSIONS`；私有 registry 使用构造时的明确容量。

`run_online_pressure_experiment` 每次请求创建一个私有 `SessionRegistry(max_sessions=1)`，并向全部 session 操作传递同一实例。实验结束仍调用私有 registry 上的 `delete_session`，但无论正常或异常路径，生产全局 registry 都不会发生变化。

## 兼容性与错误语义

- FastAPI 路由继续按原有位置参数调用 session 函数，不改变请求、响应或 OpenAPI。
- 默认 registry 的 TTL、容量淘汰、单 session 锁和关闭协议保持原语义。
- 实验响应字段、执行安全关闭设置和运行时事件顺序保持不变。
- 不通过快照回滚生产 registry，因为并发写入下快照恢复会覆盖合法请求。

## 验证

新增真实 API 回归：把 `MAX_SESSIONS` 临时设为 1，创建一个生产会话，运行 `/api/experiments/online-pressure`，然后断言全局 `_sessions` 的键和值对象均保持不变。该测试在旧实现上会因为容量淘汰而失败，在私有 registry 实现上通过。

同时运行在线压力实验测试、session 容量与并发测试、全部后端测试以及完整项目检查。
