# 全量复审问题修复设计

## 目标

修复 2026-08-08 对 `codex/full-system-hardening` 分支再次全量复审确认的全部问题，同时保持现有在线调度、固定演示、实验接口、开发脚本和离线报告行为兼容。所有行为修复先写失败回归，再实现最小代码，最后刷新当前文档证据。

## 已批准边界

- HTTP 请求体最大为 `2 MiB`，即 `2_097_152` 字节。
- 标识符最大为 `128` 个字符。
- 名称、标题和实验标签最大为 `256` 个字符。
- 描述最大为 `4096` 个字符。
- 需要返回 JavaScript 客户端的输入整数不得超过 `9_007_199_254_740_991`。
- 固定演示继续保留延迟初始规划，但改为显式请求语义，不再由场景 ID 触发。
- 不修改生成目录 `output/`，不增加展示型前端面板，不扩大认证、持久化或公网部署范围。

## 设计

### 1. 请求和原始字段资源边界

在 `backend/app/limits.py` 增加请求字节、字符串长度和 JavaScript 安全整数常量。在新的 `backend/app/request_limits.py` 中实现纯 ASGI 请求体限制中间件：

- 在调用 FastAPI 路由和 Pydantic 解析前读取请求体。
- `Content-Length` 已超过限制时立即返回 `413`。
- 没有 `Content-Length` 或使用分块传输时，逐块累计，超过限制时返回 `413`。
- 合法请求体只缓存到批准的上限，然后以一个完整 `http.request` 消息重放给下游。
- 中间件只处理 HTTP scope，其他 ASGI scope 原样透传。
- CORS 保持最外层，使合法来源收到的 `413` 仍有现有 CORS 响应头。

Pydantic 使用可复用的受限字符串别名，精确应用到外部输入：

- `Scenario.id`、`Task.id`、`Robot.id`、`Shelf.id`、动态故障机器人 ID 和运行时 `robotId` 使用 128 字符上限。
- `Scenario.name`、`Task.title`、`Robot.name` 和规模实验 `label` 使用 256 字符上限。
- `Scenario.description` 使用 4096 字符上限。
- 既有输出文本不额外裁剪，避免掩盖后端诊断。

### 2. 数值和 OpenAPI 契约

将后端 `NonNegativeInt` 和 `PositiveInt` 增加 JavaScript 安全整数上限。更小的现有业务上限继续优先，包括会话时间、服务时间、充电时间、优先级、地图尺寸和规划窗口。

`SessionTickRequest`、`AddBlockRequest`、`RemoveBlockRequest`、`FailRobotRequest` 和 `RestoreRobotRequest` 的 `currentTime` 改用现有 `SessionTimeInt`，使 OpenAPI 的最大值与运行时 `MAX_SESSION_CURRENT_TIME = 10_000` 一致；会话相对时间检查仍由 `sessions.py` 负责。

前端导入常量显式镜像 `9_007_199_254_740_991`，继续使用 `Number.isSafeInteger`，API 契约测试校验前后端上限一致。

### 3. 任务类型字段互斥

保留当前单一 `Task` 响应结构及无关字段序列化为 `null` 的兼容约定，在 `Task` 模型增加类型级验证：

- `inspection` 必须提供 `targets`，并拒绝非空的 `pickup`、`dropoff`、`demand`、`target`。
- `delivery` 必须提供 `pickup`、`dropoff`、`demand`，并拒绝非空的 `targets`、`target`。
- `emergency` 必须提供 `target`，并拒绝非空的 `targets`、`pickup`、`dropoff`、`demand`。
- 无关字段显式传 `null` 仍合法，保证后端响应和现有前端联合类型兼容。

### 4. 显式延迟初始规划

`CreateSessionRequest` 新增默认 `false` 的 `delayInitialPlanning`。`DispatchSession` 保存该值，`_should_delay_initial_planning` 只读取会话字段，不再检查 `scenario.id`。

前端创建会话时仅在 `importedScenario === null` 时发送 `delayInitialPlanning: true`。因此：

- 内置 `integrated-demo` 的初始空闲展示保持不变。
- 任意导入场景即使复用 `integrated-demo` ID，也按普通场景在创建时规划。
- 直接 API 调用默认在创建时规划；需要延迟时必须显式请求。
- reset 沿用会话创建时保存的显式设置。

### 5. 实验批量上限

`ReplanWindowExperimentRequest` 的模型验证同时检查 `len(windows) + int(includeAdaptive) <= MAX_EXPERIMENT_CASES`。32 个固定窗口仍合法，但若同时包含自适应案例，则固定窗口最多 31 个。

### 6. 报告事务取消安全

`backend/benchmarks/reporting.py` 和 `backend/benchmarks/adaptive_reporting.py` 的最终报告发布必须在 `KeyboardInterrupt`、`SystemExit` 等 `BaseException` 下也恢复完整旧 bundle：

- 备份、发布和删除 partial 的整个提交阶段共享一个取消安全的事务边界。
- 捕获取消后依据目标、临时文件和备份文件的实际存在状态补全回滚集合，避免 `replace()` 成功但集合尚未更新的窗口。
- 先尝试回滚并保留无法恢复的备份，再重新抛出原始取消异常。
- 普通 `Exception` 继续包装为现有报告写入错误，不改变对外错误类型。

两套报告器分别注入备份、发布和 partial 删除阶段的 `KeyboardInterrupt` 回归。

### 7. 开发进程登记窗口

`Start-ManagedProcess` 在 `Process.Start()` 成功后立即调用 `Add-ManagedProcessOwnership`，然后才启动异步标准输出和错误输出读取。这样读取初始化失败或此时取消时，外层 `finally` 已能通过精确进程对象和 manifest 清理子进程。

测试通过 hook 在启动成功、读取开始前抛出异常，断言精确进程进入清理路径且 manifest 最终为空。

### 8. 包依赖和文档清理

- 删除前端未使用的 `warehouse-patrol-dispatch: file:..` 自引用及 lockfile 对应节点。
- 更新根 `package.json` 描述，使其反映当前在线多机器人调度系统，而不是“竞赛原型和未来全栈应用”。
- 修正 `AGENTS.md` 对动态封锁/故障场景验证的旧语义：定义级验证只检查固定地图和静态资格，动态条件由运行时恢复分类处理。
- 完整检查结束后，用实际日期和精确测试数量刷新 `README.md`、`AGENTS.md`、`docs/baseline.md`、`docs/demo.md`、`docs/environment.md`、`docs/testing-guide.md` 的当前验证快照；带日期的历史设计和实施记录不改写。

## 测试与验收

每项生产代码修改遵循红—绿循环。聚焦回归至少覆盖：

1. `Content-Length` 和无长度分块请求的 `2 MiB` 边界。
2. 每类字符串的最大合法值和超一非法值。
3. `9_007_199_254_740_991` 合法、`9_007_199_254_740_992` 非法。
4. 五种运行时 `currentTime` 请求模型的 OpenAPI 最大值。
5. 三种任务类型的必需字段、无关非空字段拒绝和无关 `null` 兼容。
6. 导入场景复用 `integrated-demo` ID 时仍立即规划，内置场景仍延迟。
7. 31 固定窗口加自适应合法，32 固定窗口加自适应非法。
8. 两套报告器在三个提交阶段取消后保持 bundle 边界。
9. 新进程在输出读取失败前已经纳入精确清理。
10. 前端依赖图不再包含根包自引用，文档快照与最终完整检查一致。

最终运行前端构建、全部前端测试、全部后端测试、`pip check`、官方 registry `npm audit`、`git diff --check`、启动脚本 `-ValidateOnly`，并确认 `5174`、`8011` 无残留监听且 `.runtime/dev-processes.json` 为空。
