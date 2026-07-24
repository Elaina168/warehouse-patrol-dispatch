# 在线运行时加固设计

日期：2026-07-24
状态：设计决策已确认，待书面规格审阅

## 1. 背景与问题

当前在线调度、执行安全门和算法边界基准均已形成稳定基线，但代码审查确认仍存在五类运行时风险：

1. `backend.app.sessions` 使用无锁全局 `_sessions` 和可变 `DispatchSession`。同步 FastAPI 路由可以并发进入同一会话，任务 ID 检查、容量检查、状态修改和响应构造不是原子操作。确定性并发复现中，两个相同任务 ID 的请求均成功，最终会话包含两个同 ID 任务。
2. 场景只限制宽高为正数，机器人、任务、障碍、货架、区域单元和任务目标没有统一规模上限。直接调度和实验入口可在进入 beam search 与时空 A* 前接收超出比赛范围的输入。
3. 无解场景会持续执行“全车安全等待、计划失效、下次重规划”。该行为保证安全，但没有结构化停滞状态，操作者无法区分短暂让行和连续无解。
4. 自适应重规划窗口由最近一次 `replanTimeMs >= 50` 直接触发，容易受一次机器负载波动影响。
5. CORS 允许任意来源；本项目不在本阶段增加账号鉴权，但应把浏览器来源收紧到明确配置。

本设计在不改变调度评分、任务业务语义、执行安全契约和固定窗口默认行为的前提下，完成单进程比赛演示范围内的运行时加固。

## 2. 目标

- 同一会话的读取和写入具备进程内原子性。
- 不同会话仍可并行规划，慢会话不得阻塞整个会话注册表。
- 对场景与任务规模设置统一、可测试的比赛范围上限。
- 连续三次相同安全冲突后返回结构化停滞状态，但继续坚持安全等待。
- 自适应窗口使用滚动中位数和迟滞区间，不再由单次墙钟抖动切换。
- CORS 默认只允许本地前端，并可通过环境变量配置具体内网来源。
- 前后端契约、OpenAPI、文档和回归测试保持一致。

## 3. 非目标

- 不引入数据库、Redis、消息队列或分布式锁。
- 不支持多个 Uvicorn worker 共享内存会话。
- 不增加登录、账号、Token、权限模型或请求限流。
- 不自动放弃停滞任务，不自动解除任务锁，不自动关闭会话。
- 不允许安全门降级为执行冲突路径。
- 不实现局部机器人放行、CBS、ECBS 或完整 MAPF。
- 不修改 beam search 评分、候选顺序、A* 搜索时域或充电策略。
- 不新增前端面板。
- 不删除或提交现有 `.superpowers/`、`output/`。

## 4. 总体方案

采用五个相互独立、通过现有会话与 API 契约连接的组件：

1. 会话注册表锁与会话级锁。
2. 结构化连续安全停滞跟踪器。
3. 共享输入规模约束。
4. 自适应耗时状态计算器。
5. 纯函数 CORS 配置解析器。

锁只负责状态一致性；停滞跟踪只解释安全等待；输入限制只拒绝超范围输入；自适应状态只影响已有窗口决策；CORS 只影响浏览器跨域访问。五个组件不得互相改变对方的业务规则。

## 5. 会话并发原子性

### 5.1 锁结构

`backend/app/sessions.py` 增加：

```python
_sessions_lock = RLock()
```

`DispatchSession` 增加不可比较、不可展示的进程内锁：

```python
lock: RLock = field(default_factory=RLock, repr=False, compare=False)
closing: bool = field(default=False, repr=False, compare=False)
```

锁对象和 `closing` 只属于内存生命周期状态，不进入 Pydantic 响应、深拷贝快照或事件日志。

状态所有权固定如下：

- `_sessions` 成员关系和 `closing` 只由 `_sessions_lock` 保护。
- `last_accessed_at` 只在创建时或请求成功取得 `_sessions_lock -> session.lock` 时更新；清理和裁剪可在 `_sessions_lock` 内安全读取。
- `updated_at` 和其余可变会话业务状态只由 `session.lock` 保护。
- 现有 `_touch_session` 拆分访问时间与业务更新时间职责，避免持有单一会话锁时写入注册表裁剪依据。

### 5.2 固定锁顺序

所有需要同时接触注册表和会话的代码固定使用：

```text
_sessions_lock -> session.lock
```

持有 `session.lock` 的代码不得再获取 `_sessions_lock`。所有锁都通过上下文管理器或 `finally` 释放。

任何线程都不得在持有 `_sessions_lock` 时阻塞等待 `session.lock`。实际获取使用两阶段重试：

1. 在 `_sessions_lock` 内精确查找会话并以非阻塞方式尝试 `session.lock`。
2. 成功时先释放 `_sessions_lock`，再进入会话原子区间。
3. 失败时保存当前对象引用，释放 `_sessions_lock`，阻塞等待该对象的 `session.lock` 可用后立即释放，然后从注册表查找重新开始。

重试必须重新检查字典中的对象身份和 `closing`，不能继续使用可能已删除的旧对象。这样真正同时持有两把锁时仍只有固定顺序，等待同一会话时又不会占用注册表锁。

### 5.3 会话获取

增加内部上下文管理器：

```python
@contextmanager
def _locked_session(session_id: str) -> Iterator[DispatchSession]
```

流程：

1. 获取 `_sessions_lock`。
2. 执行注册表清理。
3. 精确查找 `session_id`；不存在或 `closing = True` 时返回现有 `404`。
4. 在仍持有注册表锁时非阻塞尝试 `session.lock`。
5. 成功时更新访问时间并释放 `_sessions_lock`。
6. 向调用者提供已锁定的 `DispatchSession`，调用结束时释放 `session.lock`。
7. 非阻塞尝试失败时按 5.2 的两阶段规则在注册表外等待，然后从第 1 步重试。

这样 delete 无法在请求取得对象和取得会话锁之间移除该对象，也不会因等待繁忙会话而阻塞其他会话。

### 5.4 创建、清理与容量裁剪

创建会话时：

1. 在注册表外验证并构造完整 `DispatchSession`。
2. 在该会话尚未对外可见时完成货架初始化和初始结果构造。
3. 获取 `_sessions_lock`。
4. 清理过期会话并按容量裁剪。
5. 每次释放注册表锁等待繁忙裁剪候选后，重新计算容量和候选，不沿用旧判断。
6. 容量满足时原子写入 `_sessions`。
7. 释放注册表锁并返回已经构造的响应。

过期清理和容量裁剪的候选选择、`closing` 标记、条件复核与移除必须在 `_sessions_lock` 内执行。普通过期清理跳过正在使用的会话，留待下次清理。容量裁剪优先移除未被使用的最久未访问会话；只有没有足够空闲候选时，才把最久未访问的繁忙候选标记为 `closing`，在注册表外等待其锁可用，再重新检查身份、访问时间、容量和裁剪条件。等待期间访问时间发生变化且容量可由其他候选满足时，取消该候选的 `closing`。整个过程不得在等待时占用 `_sessions_lock`，也不得让会话数超过 `MAX_SESSIONS`。

### 5.5 删除语义

delete 先在 `_sessions_lock` 内把目标精确标记为 `closing`，使新请求立即返回 `404`，再按 5.2 的两阶段规则等待已有请求释放 `session.lock`。最终只在同时持有 `_sessions_lock -> session.lock` 且字典仍指向同一对象时移除：

- 已经取得 `session.lock` 的请求先完成，随后 delete 移除会话并返回 `200`。
- delete 先移除会话时，后续请求返回 `404`。
- 不允许请求在会话已从注册表移除后继续取得该对象并写入孤立状态。
- 并发 delete 中只有第一个设置 `closing` 的请求继续；后续请求返回现有 `404`。

### 5.6 路由原子区间

以下操作在同一个 `session.lock` 内完成验证、状态修改和 `SessionResult` 构造：

- get
- reset
- add task
- tick
- add/remove blocked cell
- fail/restore robot

同一会话请求串行，不同会话使用不同锁并可并行。规划过程只持有目标会话锁，不持有 `_sessions_lock`。

list 在 `_sessions_lock` 内取得当时非 `closing` 会话的有序 ID 快照，随后使用不更新时间的会话锁获取流程逐个构造 `SessionSummary`。快照之后创建的会话不进入本次结果；构造前已被删除的会话跳过。list 可以在注册表外等待正在修改的单个会话，但不得观察半写入状态，也不得阻塞其他会话的访问。

### 5.7 并发响应规则

- 相同任务 ID 并发提交时，先取得锁者成功，后取得锁者返回现有 `409`。
- 两个未来 tick 串行处理；较小时间如果在较大时间之后执行，返回现有“过去时间”错误，`currentTime` 不得倒退。
- 容量检查和任务追加属于同一原子区间。
- reset、运行时事件和响应快照不得与 tick 交错。

## 6. 连续安全停滞

### 6.1 对外契约

`backend/app/schemas.py` 增加：

```python
class SafetyStall(ApiModel):
    conflict: Conflict
    consecutiveCount: PositiveInt
    firstInterventionTime: NonNegativeInt
    latestInterventionTime: NonNegativeInt
```

`SessionResult` 增加必有可空字段：

```python
safetyStall: SafetyStall | None = None
```

前端增加完全同名类型和字段：

```ts
export interface SafetyStall {
  conflict: Conflict;
  consecutiveCount: number;
  firstInterventionTime: number;
  latestInterventionTime: number;
}

safetyStall: SafetyStall | null;
```

`DispatchResult` 和六个实验响应不增加该字段。

### 6.2 内部状态

`DispatchSession` 增加：

```python
safety_stall_signature: tuple[str, tuple[str, ...], Cell] | None = None
consecutive_safety_intervention_count: int = 0
safety_stall_first_time: int | None = None
last_safety_stall: SafetyStall | None = None
```

签名由以下精确值组成：

1. `Conflict.type`
2. 排序后的 `Conflict.robots`
3. `Conflict.cell`

冲突时间不属于签名。

### 6.3 计数规则

每次 `_apply_safety_hold`：

- 与上一签名相同：计数加一，保留原 `firstInterventionTime`。
- 与上一签名不同：签名替换，计数设为一，首次时间设为当前冲突时间。
- 计数小于三：`last_safety_stall = None`。
- 计数大于等于三：构造或更新 `SafetyStall`。
- `latestInterventionTime` 始终等于本次冲突的绝对时间。
- `SafetyStall.conflict` 使用本次 `Conflict`，保留其实际时间。

达到三次的状态转换只额外记录一次事件：

```text
T=<time> 连续安全停滞：同一冲突已拦截 3 次
```

第四次及以后仍保留现有逐 tick 安全拦截事件，但不重复写入“达到停滞阈值”事件。

### 6.4 清除规则

以下情况清除签名、计数、首次时间和 `last_safety_stall`：

- 下一次 tick 实际安全推进至少一个 tick。
- 成功新增任务。
- 成功新增或移除封锁单元。
- 成功标记故障机器人或恢复机器人。
- reset。

幂等请求、被拒绝请求、普通读取、list 和 delete 前的读取不清除停滞。

运行时修改清除状态后，下一次仍遇到同一冲突时从一次重新计数，因为调度条件已经改变并完成了重新评估。

### 6.5 安全与任务语义

- 达到停滞阈值后仍使用现有全车等待。
- 任务不自动失败，不解除锁，不修改优先级。
- 截止时间继续推进。
- 已开始的服务和充电继续使用现有等待语义。
- `safetyIntervention` 仍只表示最近一次 tick 尝试；`safetyStall` 表示连续性状态。
- 用户可以再次播放或发起 tick，系统重新规划，但绝不执行冲突动作。

### 6.6 前端行为

- 任意非空 `safetyIntervention` 继续触发现有自动暂停。
- `safetyStall != null` 时，现有状态区显示：

```text
连续拦截 <consecutiveCount> 次，当前调度停滞
```

- 继续复用现有冲突单元和机器人高亮。
- 不增加独立面板、弹窗或实验页面。

## 7. 输入规模限制

### 7.1 共享常量

定义并复用以下精确限制：

```python
MAX_SCENARIO_AXIS_LENGTH = 64
MAX_SCENARIO_CELL_COUNT = 1024
MAX_SCENARIO_ROBOTS = 32
MAX_SCENARIO_TASKS = 128
MAX_TASK_TARGETS = 64
```

`MAX_SESSION_TASKS` 使用 `MAX_SCENARIO_TASKS`，不再保留独立的 `500`。

### 7.2 Pydantic 结构限制

- `Scenario.width`、`Scenario.height`：`1..64`。
- `Scenario.robots`：最多 32。
- `Scenario.tasks`：最多 128。
- `DynamicEvent.tasks`：最多 128。
- `Task.targets`：最多 64。
- `Scenario.obstacles`：最多 1024。
- `Scenario.shelves`：最多 1024。
- `Zones.warehouse`、`inspection`、`delivery`、`charging`：各最多 1024。
- `DynamicEvent.blockedCells`：最多 1024。
- `DynamicEvent.failedRobots`：最多 32。

结构限制在路由进入算法前由 Pydantic 返回 `422`。

### 7.3 跨字段限制

`Scenario` 模型验证或共享场景验证增加：

```text
width * height <= 1024
len(tasks) + len(dynamic.tasks) <= 128
```

障碍、货架和每个区域列表还必须满足：

```text
len(list) <= width * height
```

`DynamicEvent.blockedCells` 使用相同的地图总格数限制。

现有坐标范围、重复 ID、机器人能力、任务结构、货架库存和动态引用验证保持不变。

### 7.4 在线任务容量

会话的初始任务、场景动态任务和运行时任务共享 128 上限。新增任务时容量检查与追加操作在同一会话锁内完成。

直接调度、在线会话和所有实验入口必须依赖同一模型与共享验证，不能为实验创建更宽松的旁路。

### 7.5 算法边界

本阶段不增加 A* 节点上限、beam search 墙钟超时或调度降级。所有处于限制内的既有合法输入必须保持原算法结果。

## 8. 自适应窗口稳定化

### 8.1 常量

`backend/app/replan_window.py` 使用：

```python
REPLAN_TIME_SAMPLE_WINDOW = 5
MIN_REPLAN_TIME_SAMPLES = 3
SLOW_REPLAN_ENTER_THRESHOLD_MS = 60
SLOW_REPLAN_EXIT_THRESHOLD_MS = 40
```

原单值 `SLOW_REPLAN_THRESHOLD_MS = 50` 删除。

### 8.2 会话状态

`DispatchSession` 增加：

```python
recent_replan_times_ms: list[float] = field(default_factory=list)
adaptive_latency_slow: bool = False
```

现有只保存单值的 `last_replan_time_ms` 删除，避免单值状态与样本窗口并存。

只有真正调用 `run_dispatch` 并产生新计划时才追加 `result.metrics.replanTimeMs`。普通 get、list、缓存结果复用、空闲结果和未重规划 tick 不追加样本。

样本只保留最近五个。reset 清空样本并将 `adaptive_latency_slow` 恢复为 `False`。

### 8.3 状态转换

样本少于三个时，不根据耗时改变 `adaptive_latency_slow`。

样本达到三个后取最近五个以内样本的中位数：

- 当前不是慢状态，且中位数 `>= 60`：进入慢状态。
- 当前是慢状态，且中位数 `<= 40`：退出慢状态。
- 中位数在 `(40, 60)`：保持原状态。

阈值比较包含 40 和 60。

### 8.4 窗口决策接口

`decide_replan_window` 不再直接解释单次墙钟值，改为接收：

```python
latency_slow: bool
```

窗口优先级保持：

1. 固定模式直接返回配置窗口。
2. `latency_slow = True` 时收缩窗口。
3. 已释放任务数达到活动机器人两倍时收缩窗口。
4. 没有已释放任务且存在未来任务时扩大窗口。
5. 其他情况保持基准窗口。

直接调度和实验没有历史会话样本，传入 `latency_slow = False`；其任务压力和未来任务规则保持现状。

慢状态原因文本改为：

```text
近期规划耗时中位数较高，收缩窗口
```

已有 `effectiveAssignmentReplanWindow`、`replanWindowReason` 和窗口调整事件继续解释结果，不增加 API 字段。

一次重规划先使用已有样本和已有慢状态决定本次窗口；规划完成后再记录本次耗时并更新慢状态，供下一次重规划使用。当前响应始终报告本次真正使用的窗口和原因，不用新样本追溯修改已经执行的规划，也不为尚未发生的窗口变化提前记录事件。

### 8.5 测试时间语义

策略测试直接传入确定的样本数组或慢状态，不读取真实计时器，不断言实际毫秒耗时。

## 9. CORS 配置

### 9.1 配置模块

增加精确模块 `backend/app/config.py`，定义：

```python
WAREHOUSE_PATROL_CORS_ORIGINS
```

环境变量缺失时默认来源为：

```text
http://127.0.0.1:5174
http://localhost:5174
```

### 9.2 解析规则

- 多个来源使用英文逗号分隔。
- 去除每项首尾空白。
- 去重并保留首次出现顺序。
- 每项必须是带主机的 `http` 或 `https` origin。
- 不允许路径、查询参数或 fragment。
- 不允许 `*`。
- 环境变量存在但解析后为空，启动时明确失败，不回退到任意来源。

具体内网前端可以配置为：

```text
WAREHOUSE_PATROL_CORS_ORIGINS=http://192.168.1.10:5174
```

### 9.3 中间件

FastAPI CORS 设置改为：

```python
allow_origins=cors_allowed_origins()
allow_credentials=False
allow_methods=["GET", "POST", "DELETE"]
allow_headers=["Content-Type"]
```

CORS 不承担身份验证。非浏览器客户端仍可直接访问 API；本阶段明确不声称公网安全。

## 10. 错误处理与兼容性

- 并发串行化复用现有 `404`、`409`、`422` 错误，不新增并发专用状态码。
- 规模超限统一在规划前返回 `422`。
- CORS 配置错误在应用启动时失败并输出具体环境变量原因。
- `safetyStall` 是 `SessionResult` 的新增必有可空字段；旧客户端若忽略未知响应字段可继续运行，仓库前端与契约测试必须同步升级。
- `safetyIntervention`、`DispatchResult`、任务状态、指标和事件原字段不删除、不改名。
- 固定窗口模式和所有未达到新规模上限的现有场景保持兼容。

## 11. 测试设计

### 11.1 并发

使用 `ThreadPoolExecutor` 和 `Barrier` 构造确定性同步点，不依赖真实 sleep：

- 两个相同任务 ID：一个成功、一个 `409`，会话只保留一个任务。
- 两个不同任务在剩余一个容量名额时：只能一个成功。
- 并发 `T=1` 与 `T=2`：最终时间不倒退，较晚执行的过去请求返回现有错误。
- tick 与 reset：不得产生半重置状态。
- tick 与 delete：先取得会话锁者完成，后续访问 `404`。
- list/get 不观察任务追加或 tick 的中间状态。
- 两个不同会话可以同时进入受控规划同步点，证明没有全局串行化。
- 一个线程等待繁忙会话 A 时，会话 B 的 get、tick 或任务追加仍可完成，证明等待路径没有占用 `_sessions_lock`。
- delete、过期清理和容量裁剪等待繁忙会话时，其他会话仍可访问；最终移除对象必须与最初标记 `closing` 的对象身份相同。

### 11.2 停滞

- 同签名第 1、2 次没有 `safetyStall`，第 3 次出现。
- 第 4 次计数和最新时间更新，不重复阈值事件。
- 机器人顺序不同但集合相同视为同签名。
- type 或 cell 改变时重置为一次。
- 安全推进清除。
- 成功任务、封锁、故障和恢复操作清除。
- 幂等及被拒绝操作不清除。
- reset 清除。
- 已执行历史仍无顶点和反向边冲突。
- 充电、服务和库存语义不退化。

### 11.3 契约与前端

- 后端 Pydantic、OpenAPI 与前端 TypeScript 的 `SafetyStall` 字段完全一致。
- `SessionResult.safetyStall` 必有可空。
- 非空停滞状态显示正确文案并保持自动暂停。
- reset 或安全推进后提示清除。
- 不新增面板。

### 11.4 输入限制

- 每个限制测试最大合法值和超一非法值。
- 测试 64×16 合法、64×64 因总格数超限非法。
- 初始与动态任务合计 128 合法、129 非法。
- 运行时任务竞争不得超过 128。
- 直接调度、会话与实验入口对同一超限输入均返回 `422`。
- 固定 `integrated-demo`、九个算法边界案例和固定种子实验均保持合法。

### 11.5 自适应窗口

- 少于三个样本不改变慢状态。
- 最近五个样本取中位数。
- 60 进入慢状态，40 退出慢状态。
- 40 与 60 之间保持前态。
- 只保留最近五个样本。
- 缓存复用不追加样本。
- reset 清空样本。
- 固定模式完全不受样本影响。
- 任务压力与未来任务规则保持原优先级。

### 11.6 CORS

- 缺少环境变量返回两个默认来源。
- 逗号分隔、空白和去重行为正确。
- 具体内网来源可用。
- `*`、空配置、路径、查询和 fragment 被拒绝。
- 中间件 methods、headers 和 credentials 精确匹配设计。

### 11.7 完整回归

- 固定六任务演示。
- 在线压力、长时域压力和运行时恢复。
- 执行安全门与重复无解等待。
- 充电、库存和机器人能力。
- API 契约。
- 九案例算法边界基准的 smoke 运行。
- 完整 `npm run check`。

## 12. 文档更新

实施完成后更新：

- `AGENTS.md`：记录单进程会话并发契约、停滞状态、输入范围和自适应迟滞。
- `docs/algorithm.md`：说明停滞只提供活性诊断，不等于 MAPF 解。
- `docs/environment.md`：说明 `WAREHOUSE_PATROL_CORS_ORIGINS`。
- `docs/testing-guide.md`：增加并发、规模限制、停滞和 CORS 验证方法。

## 13. 验收标准

本阶段完成必须同时满足：

1. 确定性并发复现不再产生重复任务 ID。
2. 同一会话所有读写操作原子，不同会话仍可并行。
3. 所有入口在规划前拒绝超限输入，现有演示与基准不受影响。
4. 同一安全冲突连续三次后返回结构化 `safetyStall`，且机器人始终安全等待。
5. 不自动失败任务、不解除锁、不执行冲突动作。
6. 自适应窗口使用五样本、三样本门槛和 60/40ms 迟滞；固定模式不变。
7. CORS 默认仅允许本地前端，可配置具体内网来源且禁止通配符。
8. 前后端类型、OpenAPI、状态提示和 reset 生命周期一致。
9. 固定演示、在线压力、安全门、充电、库存、能力和算法边界回归通过。
10. 完整 `npm run check` 通过，工作树无计划外 tracked 修改。
