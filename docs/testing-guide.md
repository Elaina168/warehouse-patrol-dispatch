# 平台测试说明

本文档用于人工测试当前多机器人调度平台。重点是验证算法链路、在线调度链路和恢复链路是否能稳定运行；实验接口和离线基准只在系统诊断或回归需要时使用。

## 1. 测试目标

当前平台不是普通仓储管理系统，测试时应优先看以下能力是否成立：

1. 多机器人任务分配是否能完成巡检、取货送货和突发任务。
2. A* 路径规划是否能为每台机器人生成可执行路径。
3. 开启避碰后，同格冲突和迎面交换冲突是否减少或消除。
4. 在线会话中，时间推进、统一任务接口追加、定时任务生成、封锁、故障和恢复是否能触发重规划。
5. 指标、任务状态、机器人状态和事件日志是否能解释调度过程。
6. 同一综合场景下，基线对比和避碰规划是否能体现冲突差异。
7. 不同机器人移动速度是否能影响分配、到达时间和通道占用。
8. 任务是否只分配给 `capabilities` 支持其类型的机器人，且配送同时满足载重。
9. 运行中机器人接入后能否在完成任务并通过安全检查后永久退役，同时保留退役前历史并在重置后恢复。

## 2. 启动和地址

在项目根目录运行：

```powershell
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

如果不希望自动打开浏览器：

```powershell
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1 -NoBrowser
```

前端页面：

```text
http://127.0.0.1:5174
```

后端健康检查：

```text
http://127.0.0.1:8011/health
```

停止服务：

```powershell
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-dev.ps1
```

完整自动检查：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

聚焦后端回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_session_concurrency.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -k "safety_stall" -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_config.py backend\tests\test_health.py -q
```

依赖锁与安全审计：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend\tests\test_dependency_lock.py
.\.venv\Scripts\python.exe -m pip check
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend ls postcss --all
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend audit --registry=https://registry.npmjs.org
```

后端 lock 必须保留 `backend/requirements.txt` 中每个直接依赖的同一精确版本，`pip check` 必须无依赖冲突；前端审计不得保留 high 或 critical 漏洞。`backend/requirements.lock.txt` 只在有意升级依赖并完成全量前后端验证后刷新，不应因日常安装而改写。

## 3. 页面区域

页面顶部状态条显示：

- 当前时间 `T=<time>`。
- 当前场景。
- 当前策略：避碰规划或基线对比。
- 滚动重规划窗口；启用自适应时显示本轮实际窗口，悬停可查看调整原因。
- 后端连接状态。

主地图区域显示：

- 机器人当前位置。
- 任务点。
- 充电地块。
- 固定障碍和运行时封锁。
- 路径占用。
- 冲突位置。

右侧指标区域显示：

- 当前时间。
- 已完成任务。
- 执行中任务。
- 等待任务。
- 当前路程。
- 当前冲突。
- 已超期任务。
- 最近一次重规划耗时。
- 调度解释信息。

任务队列区域显示：

- 任务 ID、标题、类型、优先级。
- 分配机器人。
- 是否锁定。
- 到达时间、截止时间、作业时间和预计完成或完成时间。
- 当前状态。
- 失败原因。
- 临时失败的恢复按钮。

下方控制区包括：

- 在线任务追加。
- 定时任务生成器；生成任务与人工任务使用同一个任务接口。
- 运行时封锁和解除封锁。
- 机器人故障和恢复。
- 当前机器人管理、永久移除和移除前历史回放。
- 时间播放、暂停和重置。
- 事件日志。

任务优先级范围固定为 `0..5`：`0` 普通/后台、`1` 低、`2` 正常、`3` 高、`4` 紧急、`5` 最高/应急。数值越大越优先，突发任务至少为 `4`，同优先级不触发抢占。测试时应确认前端允许输入 `0`，后端拒绝负数和大于 `5` 的值。

## 4. 固定场景

### 4.1 integrated-demo

中文名：综合调度演示。

用途：

- 主线在线调度测试。
- 在同一张地图中展示巡检、取送、避碰和默认在线任务；手动封锁、机器人故障和恢复动作作为独立扩展测试。
- 在同一场景中切换“基线对比”和“避碰规划”，证明不开避碰会产生预测冲突，开启避碰后预测冲突降为0。

场景特征：

- 4 台机器人。
- 4 台机器人均显式配置 `capabilities: ["inspection", "delivery", "emergency"]`；悬浮提示均为“能力 巡检 / 取送 / 突发”。旧场景省略字段时按全能力兼容。
- 地图尺寸为 `26 × 16`，货架为12组 `3 × 2`，共72个不可通行的货架实体格。
- 每个货架有一个唯一相邻作业格；机器人只在可通行作业格取放，不进入货架实体格。
- 任意货架组或功能点之间至少能看到两个完整可走格。
- 进货点位于顶部、出货点位于底部、机器人和充电口位于右侧。
- 4 台机器人分别配置 `1`、`2`、`3`、`4 tick/格` 的移动耗时；悬浮机器人标识可查看该信息。
- 默认地图包含充电地块，场景 `chargeTime` 为 `4`。
- 默认配送任务中，`T1`、`T4` 为从进货点到空货架作业格的入库，`T2` 为从已有货物货架作业格到出货点的出库。
- `T1` 为 `[2,0] -> [2,5]`（货架 `[2,4]`），`T2` 为 `[13,6] -> [2,15]`（货架 `[13,7]`），`T4` 为 `[8,0] -> [12,9]`（货架 `[12,8]`）。
- 狭窄通道会在基线策略下产生冲突。
- 默认任务队列包含在线任务 `E1`，其到达时间为 `T=12`。
- 默认流程不再自动封锁 `[6,3]`、`[7,5]`。
- 默认流程不再自动标记 `R3` 故障。

重要预期：

- 默认场景下，前端只显示“综合调度演示”。
- 开启“避碰规划”后，主线仿真应保持低冲突或无冲突。
- 切换“基线对比”并重新规划后，同一场景应能看到冲突数量上升。
- 播放到 `T=12` 后，默认队列中的 `E1` 应进入可调度范围，不应出现默认动态封锁或默认机器人故障日志。
- 默认播放到 `T=700` 时，6个场景任务应全部完成；冲突、失败、截止超期和充电访问均为0。
- 默认库存从12个有货货架开始；`T2` 取货后减1，`T1`、`T4` 放货后加2，最终13个货架有货。
- 后端 `shelfStates` 是库存权威来源，状态仅为 `empty`、`inboundReserved`、`occupied`、`outboundReserved`；前端不得自行推断库存。
- 任务队列应始终显示到达时间、截止时间、作业时间、预计完成或完成时间、当前状态。
- 机器人到达任务最后一个目标点后，应按任务的作业时间原地停留；停留期间任务仍为执行中，结束后才进入已完成列表。
- 慢速机器人移动时会在出发格保留中间 tick；其他机器人不得在其离开前进入该格。
- 所有分配任务的类型必须在对应机器人 `capabilities` 中；配送任务还必须满足 `robot.load >= task.demand`。默认、手工和随机配送仍为 `demand: 1`。
- 主演示只要求显示充电地块、机器人电量和容量，不要求默认流程进入充电状态；完整充电闭环按 5.4.2 的低电量场景测试。
- 已完成任务默认折叠，展开后可以查看完整细节。
- 手动封锁、解锁、标记故障或恢复机器人后，应触发重规划并更新事件日志、任务状态和指标。
- 机器人永久移除后，当前地图、规划、分配、冲突预测和安全门不再包含该机器人；移除前路径、事件和指标历史仍可回放，重复操作不新增事件。

共享场景 JSON 只保留综合调度演示。自动化回归会在综合场景的局部变体和最小测试数据上验证动态事件、故障恢复与避碰边界，不再维护额外演示地图。

## 5. 推荐人工测试流程

### 5.0 历史人工确认与当前自动验证

2026-07-11 的人工测试曾确认：

- 默认综合场景不再自动封锁 `(6,3)`、`(7,5)`，不再自动标记 `R3` 故障。
- `E1` 已作为默认任务队列任务出现，而不是场景动态突发任务。
- 连续添加多个相同或相近目标任务时，不会重复分配给同一个忙碌机器人，也不会提前完成后续任务。
- 右键封锁/解锁地块、右键机器人故障/恢复后，地图、事件日志、任务队列和重规划解释能同步更新。
- 随机事件生成器生成的任务没有明显重复，不会导致任务流程回退。
- 冲突未解决时地图持续闪烁；后端结构化冲突状态提供 `resolvedAt`，参与机器人离开重叠格或边交换关系的那一刻，地图冲突提示会停止并消失，右侧解释区显示冲突已解决。

2026-08-27 本轮重新执行了完整自动化检查，结果为前端正式构建通过、前端测试 `233/233`、后端测试 `801 passed, 19 skipped`（共收集 `820` 项）；永久移除相关回归已纳入本轮结果，HTTP 和整套页面人工验收仍按本节后续步骤执行。

### 5.1 基础启动检查

1. 打开前端页面。
2. 确认右上角后端状态为在线。
3. 确认默认场景能加载地图、机器人、任务和指标。
4. 点击播放，观察机器人沿路径移动。
5. 点击暂停，确认时间停止。
6. 点击重新规划，确认会话回到初始状态。

通过标准：

- 页面不报错。
- 地图不是空白。
- 时间能推进。
- 任务队列和指标随时间变化。

### 5.1.1 默认库存流转检查

1. 初始确认12个货架显示库存高亮。
2. 播放到 `T2` 机器人到达 `[13,6]`，确认货架 `S32` 熄灭。
3. 继续播放，确认 `T1`、`T4` 完成后 `S13`、`S43` 亮起。
4. 播放到 `T=700`，确认6个任务完成且最终13个货架有货。
5. 手工尝试从空货架出库或向有货货架入库，确认请求被拒绝且现有会话不变化。
6. 开启随机生成器，确认出库任务只从当前有货且未预订的货架开始。

状态与亮灯时机：

- `empty`、`inboundReserved` 不高亮；入库任务提交成功后先预订，放货和 `serviceTime` 完成后才变为 `occupied` 并亮起。
- `occupied`、`outboundReserved` 保持高亮；出库任务提交成功后先预订，机器人实际到达取货作业格后才变为 `empty` 并熄灭。
- 库存和预订由后端会话维护，刷新或重规划后仍以响应中的 `shelfStates` 为准。

### 5.2 综合场景运行时恢复测试

推荐设置：

- 场景：综合调度演示。
- 策略：避碰规划。
- 场景动态机制：主界面固定启用，无需设置。
- 重规划窗口：24。
- 自适应窗口：默认关闭；专项测试时开启。

步骤：

1. 点击播放，或手动推进到 `T=12`。
2. 查看任务队列，确认 `E1` 已作为默认队列任务出现并按到达时间参与调度。
3. 在地图上右键封锁 `[6,3]` 或 `[7,5]`，观察事件日志和任务状态。
4. 右键机器人标记故障，观察故障机器人退出调度和重规划解释。
5. 通过右键解锁或恢复机器人，观察失败原因、路径和指标是否恢复。
6. 开启“自适应”，确认状态条从“窗口 24T”切换为“自适应 <实际窗口>T”，悬停可查看扩大、收缩或保持原因。

通过标准：

- 默认流程不自动触发封锁或机器人故障。
- 运行时封锁或故障能显示失败原因。
- 恢复动作后失败数应下降或变为 0。
- 任务状态应重新进入运行或完成。

### 5.3 手动任务追加测试

推荐在 `integrated-demo` 中测试。

步骤：

1. 时间推进到 `T=2` 到 `T=12` 之间。
2. 在线任务面板选择任务类型。
3. 推荐先测突发任务：

```text
类型：突发
标题：人工复核
优先级：5
目标：(1, 4)
作业时间：2
截止 T：当前时间 + 12
```

4. 点击加入任务并重规划。

通过标准：

- 任务队列出现 `M<n>` 新任务。
- 事件日志出现手动录入任务。
- 高优先级任务可能触发锁定任务释放或重新分配。
- 指标中的任务数、路径、规划耗时发生变化。
- 机器人到达目标后，在设定的作业时间结束前仍保持执行中，不会提前接收下一任务。
- 手工入库只能从进货点送到当前空且未预订货架的唯一作业格；手工出库只能从当前有货且未预订货架的唯一作业格送到出货点。
- 从空货架出库、向有货货架入库或重复预订应返回后端拒绝，且任务数、库存、会话时间、指标历史和当前规划均不变化。

### 5.4 自动任务流测试

步骤：

1. 打开随机事件生成器。
2. 设置生成间隔后播放时间。
3. 观察是否出现自动生成的新任务。

通过标准：

- 自动生成任务 ID 不应和已有任务冲突。
- 任务队列应出现新增任务。
- 事件日志应出现手动录入或自动生成任务到达记录。
- 系统应重新规划。
- 随机入库只选择当前 `empty` 货架，随机出库只选择当前 `occupied` 货架；两个预订状态均不作为候选。
- 随机任务与手工任务都提交到 `POST /api/sessions/{session_id}/tasks`，后端执行最终库存校验。
- 没有合法库存取送候选时，本轮应改生成巡检或应急任务。

### 5.4.1 机器人速度测试

步骤：

1. 启动综合调度演示，悬浮四台机器人标识，确认分别显示 `每格耗时 1 tick` 到 `每格耗时 4 tick`。
2. 观察 `moveTicks=3` 或 `moveTicks=4` 的机器人开始移动：在对应的中间 tick 内，其图标应继续停留在出发格。
3. 为距离相同的快慢机器人加入一个可达巡检任务，确认后端优先分配给移动耗时更短的机器人。
4. 在慢速机器人通行期间封锁其后续道路，确认事件日志、任务队列和路径同时重规划，且地图不出现持续冲突标识。

通过标准：

- 速度信息来自机器人状态，不随前端播放速度变化。
- 任务到达和完成时间随 `moveTicks` 改变；任务作业时间仍只在最终目标到达后追加。
- 封锁、故障、恢复、手动任务和随机任务在不同速度下保持后端闭环重规划。

### 5.4.1.1 机器人任务类型能力测试

步骤：

1. 悬浮 `integrated-demo` 的四台机器人，确认精确显示“能力 巡检 / 取送 / 突发”。
2. 导入省略 `capabilities` 的旧场景，确认机器人按 `inspection`、`delivery`、`emergency` 全能力兼容。
3. 使用专业分工场景，确认巡检、取送和突发任务分别只分配给包含对应能力的机器人。
4. 让唯一兼容机器人和一个不兼容机器人同时故障，再通过统一任务端点加入该类型任务。
5. 先恢复不兼容机器人，确认任务仍为临时失败；再恢复唯一兼容机器人，确认自动重规划。
6. 使用只有 `delivery` 能力但 `load: 0` 的车队，确认随机任务生成返回空；在混合车队中把唯一配送机器人的载重从0改为1，确认只有后者允许生成 `demand: 1` 配送。
7. 注入类型不兼容或载重不足的陈旧锁并保留一个当前可执行替代，确认直接调度同次改派、在线锁表删除且事件日志包含释放原因；再让替代机器人故障、受封锁或电量不可行，确认陈旧锁不会被该清洗规则误删。

通过标准：

- 显式 `capabilities` 只接受 `inspection`、`delivery`、`emergency` 的非空无重复子集。
- 能力是硬约束，不会被距离、速度、优先级、锁定或软偏好覆盖。
- 配送资格还必须满足 `robot.load >= task.demand`；能力不兼容与载重不足分别解释。
- 未来动态任务因触发时故障或单通道封锁失败时，`failureDetails` 分别给出真实 `restoreRobot`/`blockingRobotIds` 或 `clearBlockedCells`/`blockingCells`，且触发前基础任务与当前状态不受污染。
- 存在可执行替代时，类型或载重非法锁必须在规划前实际释放并成功改派，而不是只返回 `relaxLocksOrReplan`。
- `blockingRobotIds` 只包含恢复后真实可执行任务的兼容故障机器人。
- 手工任务和随机任务仍使用 `POST /api/sessions/{session_id}/tasks`，没有第二套端点。

### 5.4.2 电量与充电测试

步骤：

1. 在综合调度演示中确认地图上存在充电地块，悬浮机器人可看到当前电量和容量。
2. 使用低电量机器人或导入低电量测试场景，推进时间直到机器人进入 `前往充电` 和 `充电中` 状态。
3. 在机器人充电期间加入一个高优先级任务并重规划。
4. 对唯一通往充电格的通道执行运行时封锁，再解除封锁。

通过标准：

- 事件日志只在实际发生时出现“前往充电桩”“开始充电”“完成充电”。
- 机器人实际跨格移动才扣电；等待、作业时间和充电等待不扣电。
- 充电期间新增任务不会抢占该机器人当前锁定任务。
- 充电路线被封锁时，任务失败详情应提示临时恢复动作 `clearBlockedCells`，解除封锁后恢复规划。
- 右键充电地块不应出现普通封锁操作。

### 5.5 运行时封锁测试

步骤：

1. 在地图上点击一个非障碍、非机器人占用的单元，或手动输入坐标。
2. 点击封锁单元并重规划。
3. 观察地图和任务队列。
4. 再点击解除封锁并重规划。

通过标准：

- 地图显示新增封锁。
- 受影响任务可能重新分配或临时失败。
- 事件日志记录封锁。
- 解除封锁后，临时失败应能恢复。

注意：

- 如果封锁的是固定障碍或机器人当前占用单元，后端会拒绝请求并返回具体错误。
- 这类错误属于输入保护，不属于系统故障。

### 5.6 机器人故障测试

步骤：

1. 在突发事件面板选择一个机器人。
2. 点击标记故障并重规划。
3. 观察该机器人状态。
4. 点击恢复机器人并重规划。

通过标准：

- 故障机器人应退出调度。
- 任务可能被其他机器人接管。
- 事件日志记录故障和恢复。
- 恢复后机器人不再显示为故障。

### 5.7 运行中新机器人免训练接入测试

该流程验证正式在线控制台中的“机器人接入”面板和后端事务接口，不新增独立演示入口。

步骤：

1. 使用 `integrated-demo` 和“避碰规划”，创建会话并播放到 `T=12`，点击暂停。
2. 在“机器人接入”面板填写：ID `R5`，名称 `运行时巡检车`，接入位置 `[25,15]`，电量 `512`，电池上限 `512`，载重 `1`，`moveTicks=1`，能力只选“巡检”。接入位置也可以通过地图选点按钮选择；地图只允许选择非固定障碍、非货架、非封锁、非机器人占用格。
3. 提交后确认播放保持暂停，地图和当前机器人配置出现 R5，事件日志出现 `T=12 新机器人接入：R5 运行时巡检车`，响应中的 `joinedAt` 和 `result.pathStartTimes.R5` 均为 `12`。
4. 将时间回放到 `T=11`，确认 R5 不出现在地图；返回最新 T 后，确认 R5 从接入位置开始显示，接入前累计路程和冲突指标没有被它污染。
5. 通过统一在线任务接口追加一个目标为 `[24,15]` 的巡检任务，确认 R5 获得任务并移动；继续 tick 到任务完成。
6. 确认该流程结束时任务为 `completed`，活动冲突、失败任务、截止超期和意外 `safetyIntervention` 均为 `0` 或 `null`；再通过既有故障接口标记 R5 故障、恢复 R5，确认 R5 能继续参与重规划。
7. 点击重置，确认 R5 从 `robotStates`、地图和路径中移除，初始机器人恢复，接入事件和运行时状态被清空。

边界与错误检查：

- 省略 `currentTime` 时使用会话当前 tick；显式过去 tick、重复 ID、第 33 台机器人、越界、固定障碍、货架格、当前封锁格和当前占用格均应被拒绝。
- 接入请求在候选会话中推进、校验、初始化、重规划和构建后才提交；拒绝或重规划失败时，原会话的时间、路径、指标、事件和缓存必须保持不变。
- 历史回放、tick 中、调度未就绪或没有会话时接入控件禁用。4xx 业务错误保持后端在线、保留会话和后端 detail；提交成功与失败均不自动恢复播放。
- 接入后继续复用已有任务能力、配送载重、锁定、故障恢复、冲突检测和执行安全语义；不把本功能解释为任意地图零冲突或完整 MAPF/CBS。

对应自动化回归包括 `backend/tests/test_runtime_robot_onboarding.py`、API contract 测试、前端 `runtimeRobot.test.ts`、`sessionApi.test.ts`、`sessionRequestState.test.ts` 和 `main.test.ts`。

### 5.8 运行中机器人永久移除测试

该流程验证“当前机器人管理”面板、`POST /api/sessions/{session_id}/robots/remove` 和保留历史的永久退役语义。

步骤：

1. 按 5.7 接入 R5，追加目标为 `[24,15]` 的巡检任务并推进到任务完成，确认 R5 状态为“空闲”。
2. 保持在最新会话时间并暂停，在“当前机器人管理”中确认 R5 的 `joinedAt`、当前状态和“永久移除”按钮。
3. 点击按钮并确认二次提示，确认 R5 返回 `status: "removed"`、`removedAt` 等于操作时的会话 tick，事件日志只出现一条 `永久移除机器人` 记录。
4. 回放到 `removedAt - 1`，确认 R5 的退役前路径仍显示；回到最新 T，确认地图隐藏 R5，当前分配、冲突预测和安全门不再使用 R5。
5. 继续推进剩余任务，确认其他机器人仍可完成任务；执行 `reset` 后确认 R5、`removedAt`、移除事件和运行时状态清除，初始四台机器人恢复。

禁止条件与事务检查：

- 分别使用执行中、已取货未送达、前往充电或充电中、存在未完成硬锁、移除后无其他活动机器人等场景请求移除，均应返回 `409`，会话时间、路径、任务状态、指标历史、事件、缓存和偏好不变。
- 等待机器人只有未锁定远期任务时应允许移除；故障机器人完成任务移交且无携货时应允许移除。
- 注入一次重规划异常，确认移除失败后所有会话字段保持原值；再次执行正常移除后成功。
- 对同一机器人重复移除，确认两次都返回当前结果、`removedAt` 不变且事件不重复；通过故障标记或恢复接口操作该机器人应被拒绝，同一会话再次接入相同 ID 也应被拒绝。
- 历史回放、tick 请求中、会话未就绪或无会话时，前端移除控件禁用；后端 `4xx` detail 应保留在面板中，且不会把后端标记为离线。

对应自动化回归包括 `backend/tests/test_runtime_robot_removal.py`、API contract 测试、前端 `runtimeRobot.test.ts`、`sessionApi.test.ts`、`sessionRequestState.test.ts` 和 `main.test.ts`。

## 6. 同场景策略对照

主界面没有实验面板。人工测试时只在综合场景中使用策略切换：

- `避碰规划`：正式演示策略，开启时序避碰。
- `基线对比`：同一综合场景下关闭时序避碰，用来证明不开避碰会产生冲突。

推荐流程：

1. 保持场景为 `integrated-demo`。
2. 选择 `基线对比`，点击重新规划。
3. 查看指标中的“当前冲突”和事件日志中的冲突说明。
4. 切换回 `避碰规划`，点击重新规划。
5. 再次查看冲突数量。

通过标准分三级：

1. 当前执行安全标准（代码强制）：开启避碰的在线会话会在首个预测顶点或反向边冲突 tick 让全车安全等待，不把冲突动作写入实际路径历史；已经执行到的 tick 不允许出现同格顶点冲突或反向边交换冲突。
2. 当前规划质量：固定种子压力场景的预测冲突为零；当前 `integrated-demo` 固定输入中，避碰规划将预测冲突从大于0降为0。
3. 未实现目标：全规划时域零冲突保证。当前结果不是严格的全时域零冲突证明，也不是完整 MAPF/CBS 求解器的可解性承诺。

大跨度 tick 请求会在首个危险 tick 提前返回，`SessionResult.safetyIntervention` 提供绝对时间、冲突类型、机器人和单元格。当安全门拦截冲突时，播放自动暂停，顶部显示结构化拦截状态，地图高亮冲突单元和涉及机器人。检查事件后再次播放会从实际等待位置重新规划；系统不会降级执行带冲突路径。

后端仍保留实验接口和固定种子压力测试，用于自动化回归和内部诊断；基线对比、直接调度和实验接口仍可返回或执行预测冲突，用于对照，且这些接口不出现在前端主界面。当前不以报告取数或图表整理为建设目标。

### 6.1 离线算法边界基准人工复核

离线基准用于记录当前系统范围内的规模、密度和瓶颈边界；它不替代在线界面的人工操作测试，也不证明完整 MAPF 能力。先运行聚焦回归和完整项目检查：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_schema_constraints.py backend/tests/test_experiments.py backend/tests/test_benchmark_process_isolation.py backend/tests/test_algorithm_benchmark.py -q
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

其中实验请求边界应确认滚动窗口和规模 case 每批最少 `1`、最多 `32`，且滚动窗口值必须唯一；`33` 项和重复窗口都应在运行实验前被拒绝。

然后运行标准完整基准：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --repetitions 5 --timeout-seconds 30 --output-dir output/algorithm-boundary-benchmark
```

单独复核时空 A* 密度剪枝时，运行：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --families density --repetitions 5 --timeout-seconds 30 --output-dir output/timed-astar-density
```

密度复核清单：

1. density 三个案例共 15 条运行并全部 stable。
2. 31/43 案例出现 `goalFullyReserved` 拒绝且不再以 exhausted 搜索遍历完整时域。
3. 55 案例保持首个候选成功。
4. 只在相同机器和无并发重型任务下比较前后 `medianReplanTimeMs`、P95 和扩展状态数。
5. 日常 pytest 不断言真实墙钟阈值。`test_dispatch_handles_deterministic_scale_pressure_family`、`test_dispatch_handles_fixed_seed_pressure_family`、`test_seeded_pressure_experiment_returns_compact_performance_cases` 和 `test_seeded_pressure_experiment_can_run_extended_stability_cases` 只验证固定输入的分配、冲突、失败、案例标签/数量及 `replanTimeMs` 的类型和非负域；前两个直接调度测试还固定已有规划诊断。`planningTimeBudgetMs == 2000` 仅保留为实验 API 元数据。真实耗时上限、分布和前后比较只属于本节的离线重复基准证据，不作为日常 pytest 的通过/失败条件。

命令会在 `output/algorithm-boundary-benchmark` 下创建一个 UTC 时间戳结果目录。最终目录必须同时包含 `results.json`、`runs.csv` 和 `case-summaries.csv`；`runs.csv` 与 `case-summaries.csv` 使用 UTF-8 with BOM，可直接按 UTF-8 打开。默认九个案例为 `scale-r4-t15`、`scale-r8-t27`、`scale-r12-t39`、`density-r8-t31`、`density-r8-t43`、`density-r8-t55`、`bottleneck-r4-t4`、`bottleneck-r6-t6`、`bottleneck-r8-t8`，各运行五次。

三个最终文件按一个 bundle 发布。`test_algorithm_final_report_rollback*` 覆盖每个文件的备份和发布失败：已有 bundle 必须完整恢复，原来没有 bundle 时不得留下半套最终文件，`results.partial.json` 必须保留；恢复失败时可恢复的 `.backup` 也必须保留并出现在错误中。隔离进程测试同时要求先接收 Pipe 大载荷再 join，并覆盖超时或 `BaseException` 取消后的 `terminate -> bounded join -> kill -> bounded join` 清理、Pipe 关闭和无残留 worker；无法确认清理成功应按基础设施失败处理。

字段含义：`outcome` 为 `completed`、`timeout` 或 `error`；`correctnessStable` 表示该次运行满足对应案例的稳定性条件；`stableRunRatePercent` 是每案例稳定运行率；`medianWallClockMs`、`p95WallClockMs`、`medianReplanTimeMs` 和 `p95ReplanTimeMs` 分别汇总完成运行的墙钟和规划耗时。`medianWallClockMs` 和 `p95WallClockMs` 仅作当前机器运行分布观察，第一版不设墙钟自动通过或失败阈值，必须结合正确性、稳定运行率和案例上下文人工复核。`predictedConflictCount` 对应 `Metrics.conflictCount`，仅代表规划预测。在线案例的 `executionSafetyEvaluated` 应为 `true`，并应复核 `activeConflictCount` 和 `safetyInterventionCount`；直接规划案例不评价实际执行安全。

人工复核清单：

1. `results.json.runs`、`runs.csv` 和 `case-summaries.csv` 的案例数、运行数、完成数、超时数和错误数一致；默认完整基准应有 45 条运行记录和 9 条案例汇总。
2. 逐案例记录 `completed`、`timeout`、`error`、`stableRunCount`、`stableRunRatePercent`、中位数和 P95；`timeout` 或 `error` 是边界结果，不能不经排查直接认定为代码缺陷。
3. 复核在线 `bottleneck-*` 案例的 `executionSafetyEvaluated`、`activeConflictCount` 和 `safetyInterventionCount` 字段均存在，并将安全门介入次数写入人工结论。
4. 以 UTF-8 打开两份 CSV，确认表头和记录可读；不要只凭自动汇总生成报告结论，也不要把预测零冲突写成完整 MAPF 保证。
5. 运行 `git status --short`，确认 `output/` 基准证据未跟踪且未暂存，不提交结果目录。

### 6.2 离线自适应窗口校准人工复核

该流程比较固定 `4T`、`24T`、`48T` 与当前自适应 `24T`，只记录真实在线重规划。它用于同机候选范围取证，不自动修改生产 `60/40ms`、最近 5 个样本且至少 3 个样本或 `2×` 压力规则，也不证明完整 MAPF 或跨机器阈值。

先运行聚焦回归和完整项目检查，二者都结束后再运行校准；不要让 `npm run check`、其他基准、浏览器压力流程或其他重型命令与校准并发：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py backend\tests\test_sessions.py backend\tests\test_benchmark_process_isolation.py backend\tests\test_algorithm_benchmark.py backend\tests\test_adaptive_replan_calibration.py -q
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

以下压力 smoke 命令必须在同一个 PowerShell 会话中按顺序运行，以便保留 PID baseline 和捕获的精确结果路径。它只运行 `adaptive-pressure-r8-t45` 的四个变体各一次；旧结果目录不得复用为 fresh 证据：

```powershell
$benchmarkWorkerPidsBefore = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq 'python.exe' -and
      $_.CommandLine -like '*multiprocessing.spawn*'
    } |
    Select-Object -ExpandProperty ProcessId
)
$pressureSmokePath = (
  & 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan -- `
    --cases adaptive-pressure-r8-t45 `
    --repetitions 1 `
    --timeout-seconds 30 `
    --output-dir output/adaptive-calibration-evidence-fix-smoke |
    Select-Object -Last 1
).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "压力校准 smoke 失败，退出码: $LASTEXITCODE"
}
if (-not (
  Test-Path -LiteralPath $pressureSmokePath -PathType Container
)) {
  throw "压力校准 smoke 结果目录不存在: $pressureSmokePath"
}
$pressureSmokePath

$smokeCheck = @'
import csv
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
payload = json.loads(
    (result_path / "results.json").read_text(encoding="utf-8")
)
runs = payload["runs"]
assert payload["schemaVersion"] == 1
assert len(runs) == 4
assert len(payload["variantSummaries"]) == 4
assert {run["variantId"] for run in runs} == {
    "fixed-4",
    "fixed-24",
    "fixed-48",
    "adaptive-current-24",
}
for run in runs:
    assert run["caseId"] == "adaptive-pressure-r8-t45"
    assert run["outcome"] == "completed"
    assert run["correctnessStable"] is True
    assert run["releasedTaskCount"] == 45
    assert run["coveredTaskCount"] == 45
    assert run["completedTaskCount"] == 45
    assert run["coverageRatePercent"] == 100
    assert run["actualCompletionRatePercent"] == 100
    assert run["predictedConflictCount"] == 0
    assert run["activeConflictCount"] == 0
    assert run["safetyInterventionCount"] == 0
    assert run["deadlineMissCount"] == 0
    assert run["failureCount"] == 0
    assert run["totalDistance"] > 0
assert not (result_path / "results.partial.json").exists()
assert not list(result_path.glob("*.tmp"))
for file_name in (
    "runs.csv",
    "replan-observations.csv",
    "variant-summaries.csv",
):
    assert (result_path / file_name).read_bytes()[:3] == bytes(
        (239, 187, 191)
    )
with (result_path / "runs.csv").open(
    encoding="utf-8-sig",
    newline="",
) as handle:
    rows = list(csv.DictReader(handle))
assert len(rows) == 4
assert all(float(row["totalDistance"]) > 0 for row in rows)
print(
    json.dumps(
        {
            "runs": len(runs),
            "stable": sum(
                run["correctnessStable"] for run in runs
            ),
            "completedTasks": [
                run["completedTaskCount"] for run in runs
            ],
            "totalDistances": [
                run["totalDistance"] for run in runs
            ],
        },
        ensure_ascii=False,
    )
)
'@
$smokeCheck |
  .\.venv\Scripts\python.exe - $pressureSmokePath
if ($LASTEXITCODE -ne 0) {
  throw "压力校准 smoke artifact 核对失败"
}

$newBenchmarkWorkers = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*multiprocessing.spawn*' -and
    $_.ProcessId -notin $benchmarkWorkerPidsBefore
  }
if ($newBenchmarkWorkers) {
  $newBenchmarkWorkers |
    Select-Object ProcessId,CommandLine |
    Format-Table -AutoSize
  throw "检测到压力校准 smoke 遗留的 spawn worker"
}
```

默认完整校准使用每组五次，共 60 条运行；正式取证不得提高 30 秒超时：

```powershell
$benchmarkWorkerPidsBefore = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq 'python.exe' -and
      $_.CommandLine -like '*multiprocessing.spawn*'
    } |
    Select-Object -ExpandProperty ProcessId
)
$calibrationResultPath = (
  & 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan -- --repetitions 5 --timeout-seconds 30 --output-dir output/adaptive-replan-calibration |
    Select-Object -Last 1
).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "默认自适应窗口校准失败，退出码: $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $calibrationResultPath -PathType Container)) {
  throw "校准结果目录不存在: $calibrationResultPath"
}
$calibrationResultPath
```

命令最后一行是精确结果目录。后续检查必须直接使用 `$calibrationResultPath`，不能按时间戳猜测。以下脚本使用项目虚拟环境 Python 读取 JSON 与三份 CSV，并一次性交叉核对 schema、文件集合、行数、candidate availability、stable/timeout/error、BOM、partial 和临时文件：

```powershell
$artifactCheck = @'
import csv
import json
import sys
from collections import Counter
from pathlib import Path

result_path = Path(sys.argv[1])
payload = json.loads(
    (result_path / "results.json").read_text(encoding="utf-8")
)
known_cases = {
    "adaptive-low-load-r4-t17",
    "adaptive-pressure-r8-t45",
    "adaptive-transition-r4-t6",
}
known_variants = {
    "fixed-4",
    "fixed-24",
    "fixed-48",
    "adaptive-current-24",
}
expected_final_files = {
    "results.json",
    "runs.csv",
    "replan-observations.csv",
    "variant-summaries.csv",
}
expected_headers = {
    "runs.csv": (
        "caseId", "variantId", "runIndex", "robotCount", "taskCount",
        "tickTarget", "outcome", "errorType", "errorMessage",
        "correctnessStable", "releasedTaskCount", "coveredTaskCount",
        "completedTaskCount", "coverageRatePercent",
        "actualCompletionRatePercent", "predictedConflictCount",
        "activeConflictCount", "safetyInterventionCount",
        "safetyStallReached", "maxConsecutiveSafetyInterventionCount",
        "deadlineMissCount", "failureCount", "totalDistance", "makespan",
        "wallClockMs", "replanCount", "windowChangeCount",
    ),
    "replan-observations.csv": (
        "caseId", "variantId", "runIndex", "observationIndex", "time",
        "configuredWindow", "effectiveWindow", "reason", "releasedTaskCount",
        "futureTaskCount", "activeRobotCount", "taskPressureRatio",
        "latencySamplesBeforeMs", "latencyMedianBeforeMs",
        "latencySlowBefore", "replanTimeMs", "latencySlowAfter",
        "pathCandidateCount", "selectedPathCandidateIndex",
        "failedPathCandidateCount", "timedAStarCallCount",
        "timedAStarExpandedStateCount", "maxTimedAStarExpandedStateCount",
        "timedAStarExhaustedSearchCount",
        "timedAStarGoalFullyReservedRejectCount",
    ),
    "variant-summaries.csv": (
        "caseId", "variantId", "runCount", "completedRunCount",
        "timeoutCount", "errorCount", "stableRunCount",
        "stableRunRatePercent", "medianWallClockMs", "p95WallClockMs",
        "medianRunReplanTimeMs", "p95RunReplanTimeMs", "medianReplanCount",
        "medianWindowChangeCount", "medianCoverageRatePercent",
        "medianActualCompletionRatePercent", "maxSafetyInterventionCount",
        "maxConsecutiveSafetyInterventionCount", "windowReasonCounts",
    ),
}

def read_csv(file_name):
    with (result_path / file_name).open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)

def csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)

def expected_csv_rows(records, field_names):
    return [
        {field_name: csv_cell(record[field_name]) for field_name in field_names}
        for record in records
    ]

def assert_csv_matches(file_name, expected_records):
    field_names = expected_headers[file_name]
    actual_header, actual_rows = read_csv(file_name)
    assert actual_header == field_names, (
        f"{file_name} header mismatch: {actual_header!r} != {field_names!r}"
    )
    expected_rows = expected_csv_rows(expected_records, field_names)
    assert len(actual_rows) == len(expected_rows), (
        f"{file_name} row count mismatch: "
        f"{len(actual_rows)} != {len(expected_rows)}"
    )
    for row_index, (actual_row, expected_row) in enumerate(
        zip(actual_rows, expected_rows)
    ):
        for field_name in field_names:
            assert actual_row[field_name] == expected_row[field_name], (
                f"{file_name} row {row_index} field {field_name} mismatch: "
                f"{actual_row[field_name]!r} != {expected_row[field_name]!r}"
            )

runs = payload["runs"]
run_records = [
    {field_name: run[field_name] for field_name in expected_headers["runs.csv"]}
    for run in runs
]
observation_records = [
    observation
    for run in runs
    for observation in run["replanObservations"]
]
summary_records = payload["variantSummaries"]
assert_csv_matches("runs.csv", run_records)
assert_csv_matches("replan-observations.csv", observation_records)
assert_csv_matches("variant-summaries.csv", summary_records)
_, run_rows = read_csv("runs.csv")
_, observation_rows = read_csv("replan-observations.csv")
_, summary_rows = read_csv("variant-summaries.csv")
assert payload["schemaVersion"] == 1
assert len(runs) == 60
assert len(run_rows) == 60
assert len(summary_rows) == 12
assert len(observation_rows) == sum(
    run["replanCount"] for run in runs
)
assert all(run["caseId"] in known_cases for run in runs)
assert all(run["variantId"] in known_variants for run in runs)
assert all(run["outcome"] == "completed" for run in runs)
assert all(run["correctnessStable"] is True for run in runs)
assert all(run["totalDistance"] > 0 for run in runs)
assert {
    path.name for path in result_path.iterdir()
} == expected_final_files
assert not (result_path / "results.partial.json").exists()
assert not any(
    path.name.endswith(".tmp") for path in result_path.iterdir()
)
assert (result_path / "results.json").read_bytes()[:3] != bytes(
    (239, 187, 191)
)

pressure_runs = [
    run
    for run in runs
    if run["caseId"] == "adaptive-pressure-r8-t45"
]
assert len(pressure_runs) == 20
for run in pressure_runs:
    assert run["releasedTaskCount"] == 45
    assert run["coveredTaskCount"] == 45
    assert run["completedTaskCount"] == 45
    assert run["coverageRatePercent"] == 100
    assert run["actualCompletionRatePercent"] == 100
    assert run["predictedConflictCount"] == 0
    assert run["activeConflictCount"] == 0
    assert run["safetyInterventionCount"] == 0
    assert run["deadlineMissCount"] == 0
    assert run["failureCount"] == 0

fixed_24_counts = Counter()
for run in runs:
    if (
        run["variantId"] == "fixed-24"
        and run["outcome"] == "completed"
        and run["correctnessStable"]
    ):
        fixed_24_counts[run["caseId"]] += len(
            run["replanObservations"]
        )
expected_envelope_available = all(
    fixed_24_counts[case_id] >= 3 for case_id in known_cases
)
assert (
    payload["candidateEnvelopeAvailable"]
    is expected_envelope_available
)
assert (payload["candidateEnvelope"] is not None) is (
    expected_envelope_available
)
assert payload["candidateEnvelopeAvailable"] is True
assert payload["candidateEnvelope"] is not None

for file_name in (
    "runs.csv",
    "replan-observations.csv",
    "variant-summaries.csv",
):
    assert (result_path / file_name).read_bytes()[:3] == bytes(
        (239, 187, 191)
    )

outcomes = Counter(run["outcome"] for run in runs)
stable_completed_run_count = sum(
    run["outcome"] == "completed"
    and run["correctnessStable"]
    for run in runs
)
assert stable_completed_run_count == 60
timeout_or_error = [
    {
        "caseId": run["caseId"],
        "variantId": run["variantId"],
        "runIndex": run["runIndex"],
        "outcome": run["outcome"],
        "errorType": run["errorType"],
        "errorMessage": run["errorMessage"],
    }
    for run in runs
    if run["outcome"] in {"timeout", "error"}
]
unstable = [
    {
        "caseId": run["caseId"],
        "variantId": run["variantId"],
        "runIndex": run["runIndex"],
        "predictedConflictCount": run["predictedConflictCount"],
        "activeConflictCount": run["activeConflictCount"],
        "deadlineMissCount": run["deadlineMissCount"],
        "failureCount": run["failureCount"],
        "safetyInterventionCount": run["safetyInterventionCount"],
        "actualCompletionRatePercent": (
            run["actualCompletionRatePercent"]
        ),
    }
    for run in runs
    if run["outcome"] == "completed"
    and not run["correctnessStable"]
]
assert not timeout_or_error
assert not unstable
print(
    json.dumps(
        {
            "runs": len(runs),
            "stable": stable_completed_run_count,
            "observations": len(observation_rows),
            "summaries": len(summary_rows),
            "outcomes": dict(outcomes),
            "pressureDistanceRange": [
                min(run["totalDistance"] for run in pressure_runs),
                max(run["totalDistance"] for run in pressure_runs),
            ],
            "candidateEnvelope": payload["candidateEnvelope"],
        },
        ensure_ascii=False,
    )
)
'@
$artifactCheck |
  .\.venv\Scripts\python.exe - $calibrationResultPath
if ($LASTEXITCODE -ne 0) {
  throw "默认校准 artifact 交叉核对失败，退出码: $LASTEXITCODE"
}

$newBenchmarkWorkers = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*multiprocessing.spawn*' -and
    $_.ProcessId -notin $benchmarkWorkerPidsBefore
  }
if ($newBenchmarkWorkers) {
  $newBenchmarkWorkers |
    Select-Object ProcessId,CommandLine |
    Format-Table -AutoSize
  throw "检测到默认校准遗留的基准 spawn worker"
}
```

最终目录必须精确包含 `results.json`、`runs.csv`、`replan-observations.csv` 和 `variant-summaries.csv`，且不再包含 `results.partial.json` 或 `*.tmp`。脚本还必须确认 60 条运行全部 completed+stable、所有 `totalDistance` 为正、20 条压力运行均完成 45/45、candidate envelope 非空。`replan-observations.csv` 行数必须等于 JSON 中全部 `replanCount` 之和；这些观测只能来自真实 `run_dispatch`，缓存响应和被动 tick 不应产生记录。

`outcome = "timeout"` 或 `"error"` 时，按原始 `caseId`、`variantId`、`runIndex`、错误类型和错误文本报告，停止正式验收排查，不能抬高超时或隐去记录。`outcome = "completed"` 但 `correctnessStable = false` 时仍继续完成批次，并逐条记录 `caseId`、`variantId`、`runIndex`、`predictedConflictCount`、`activeConflictCount`、`deadlineMissCount`、`failureCount`、`safetyInterventionCount` 和 `actualCompletionRatePercent`；不能删除不稳定运行或弱化正确性条件。墙钟和候选范围只作同机人工证据，不是自动推荐。

2026-07-26 修复前的默认复核实际得到 60 completed、40 stable、20 completed-but-unstable、0 timeout、0 error、1,310 条真实重规划观测和 12 条汇总；三份 CSV 编码、JSON/CSV 行数、partial/tmp 清理和 worker 清理均通过。20 条不稳定记录精确来自 `adaptive-pressure-r8-t45` 的四个变体各 runIndex 1–5，均为预测/活动冲突 `0/0`、超期 `1`、失败 `2`、安全介入 `0`、实际完成率 `95.6%`。该案例没有 stable completed `fixed-24` 观测，因此候选 envelope 为不可用和 `null`；这组结果保留为修复前历史诊断。

修复后的 fresh 默认结果位于 `output/adaptive-replan-calibration/20260726T150547Z`：60 completed、60 stable、1,330 条真实重规划观测、12 条汇总，压力运行全部 45/45，累计距离范围 `[671, 671]`，candidate envelope 非空。完整对象与人工结论见 `docs/experiments.md` 的“2026-07-26 修复后默认 60-run 人工复核”；没有应用任何生产阈值。

完成 artifact 与 worker 检查后运行 Git hygiene：

```powershell
git diff --check
git status --short
git diff --name-only d3b5c61
```

`git diff --name-only d3b5c61` 的 expected tracked scope 为：

```text
AGENTS.md
backend/benchmarks/adaptive_runner.py
backend/benchmarks/adaptive_scenarios.py
backend/tests/test_adaptive_replan_calibration.py
docs/algorithm.md
docs/experiments.md
docs/superpowers/plans/2026-07-26-adaptive-calibration-evidence-fix.md
docs/superpowers/specs/2026-07-26-adaptive-calibration-evidence-fix-design.md
docs/testing-guide.md
```

生成的 `output/`、`.superpowers/`、依赖 junction、ACL 目录和缓存必须保持未跟踪、未暂存、未提交。

## 7. 指标解释

常用指标：

- `assignedTaskCount`：当前调度结果中被分配的任务数量。
- `completedTaskCount`：在线会话中当前时间已经完成的任务数量。
- `conflictCount`：整条规划路径中检测到的冲突数量。
- `deadlineMissCount`：超过截止时间的任务数量。
- `failureCount`：当前无法调度的任务数量。
- `totalDistance`：所有机器人计划中相邻坐标实际变化次数之和，不包含原地等待 tick。
- `makespan`：最长机器人时间展开路径的 tick 数，包含释放、服务、充电、避碰和慢速移动等待。
- `loadBalance`：机器人计划移动距离的均衡程度。
- `replanTimeMs`：后端本次规划耗时。

注意：

- 在线压力实验里的“任务覆盖”不是当前时刻已完成任务，而是该在线流程中任务是否被调度覆盖。
- 固定种子压力的 `assignmentRatePercent` 是已分配任务数占总任务数，不是执行完成率。
- 在线压力的 `coverageRatePercent` 是状态不为 `unassigned` 的任务占总任务数；`actualCompletionRatePercent` 是实验结束时已完成任务占已释放任务数。固定时域内未全部完成不等于不稳定。
- 在线压力稳定性要求全覆盖、零冲突、零超期、零失败，不要求已释放任务全部在固定结束 tick 前完成。
- 动态封锁造成的临时失败如果能通过恢复按钮清除，应视为恢复能力展示，不应直接判定为失败演示。

## 8. 事件日志怎么看

重点关注以下日志：

- `加载场景`：说明调度结果已生成。
- `启用优先级避碰规划`：说明当前是避碰策略。
- `启用基线规划，不进行时序避碰`：说明当前是基线对比。
- `完成 <n> 个任务分配`：说明本次规划分配了多少任务。
- `未检测到时空路径冲突`：说明当前路径无冲突。
- `检测到 <n> 次路径冲突`：说明当前策略或场景存在冲突。
- `T=<time> 场景动态事件触发`：说明有非空场景动态任务、封锁或故障被激活；当前综合演示默认不应出现该日志。
- `T=<time> 滚动窗口纳入远期任务`：说明远期任务进入当前规划窗口。
- `手动录入任务`：说明在线新增任务成功。
- `手动封锁单元`：说明运行时封锁成功。
- `手动解除封锁单元`：说明恢复封锁成功。
- `手动标记故障机器人`：说明机器人故障成功。
- `手动恢复机器人`：说明机器人恢复成功。
- `任务 <id> 调度失败原因`：说明后端解释了无法调度的原因。

## 9. 常见现象

### 9.1 页面显示后端离线

检查：

1. 后端是否启动。
2. `http://127.0.0.1:8011/health` 是否返回 `{"status":"ok"}`。
3. 是否误用了旧端口 `8000` 或 `8001`。

当前项目固定使用：

- 后端：`8011`
- 前端：`5174`

### 9.2 封锁请求被拒绝

常见原因：

- 坐标超出地图。
- 坐标是固定障碍。
- 坐标正被机器人占用。
- 请求时间早于当前会话时间。

这是输入保护，页面应显示后端返回的具体原因。

### 9.3 故障或封锁后出现失败任务

先看任务队列里的恢复建议。

如果是 `temporary` 临时失败，可以尝试：

- 点击任务卡片中的恢复按钮。
- 解除指定封锁单元。
- 恢复指定机器人。

如果是 `permanent` 永久失败，通常说明无机器人支持任务类型、配送载重不足、任务定义错误或地图静态不可达。能力相关排查顺序是：先检查任务类型能力，再检查配送载重，然后检查兼容机器人故障、运行时封锁、锁定和静态可达性。

### 9.4 关闭避碰后出现冲突

这是正常的基线对比现象。要证明算法价值，应比较关闭避碰和开启避碰后的冲突数变化。

### 9.5 任务没有立刻执行

常见原因：

- 任务有 `releaseTime`，还没到释放时间。
- 任务在滚动窗口外，等待进入当前规划窗口。
- 任务被临时封锁或故障影响。

## 10. 已知边界

当前系统是竞赛展示范围的多机器人调度平台，不是工业级调度系统。

已知边界：

- 避碰不是完整 CBS/MAPF 最优求解器，而是优先级时空 A* 加多候选规划顺序。
- 完全一维、没有侧向避让空间的对向交换场景，可能无法做到无冲突。
- 冲突视觉提示优先使用后端 `conflictStates` 的 `active` / `resolved` 状态和 `resolvedAt` 解除时刻；旧响应或缺失字段时，前端才根据参与机器人当前路径位置兜底推导。
- 电量和充电已经作为离散网格硬约束参与调度；当前仍不是连续物理能耗模型。
- 会话状态保存在后端内存中，服务重启后会话会丢失。
- 用户导入的自定义 JSON 应尽量按导出模板格式填写；内置模板不会产生 `releaseTime: null` 或 `deadline: null` 这类边界字段。
- 在用户明确参赛和材料要求前，3D 展示、正式 PPT、录屏、竞赛报告和实验图表不属于当前测试阶段。

## 11. 未完成边界与方向确定规则

当前已知但未完成的边界包括：

1. 当前优先级时空 A* 不保证任意输入都能找到无冲突路线，也不提供完整 MAPF/CBS 或全规划时域零冲突证明。
2. 会话状态仍为单进程内存状态；并发串行化不覆盖多个 Uvicorn worker。
3. 自适应窗口候选范围来自同机校准，不能直接视为可移植生产阈值。
4. 持久化、认证、外部监控和连续物理能耗模型尚未实现。

当前没有确定下一项功能、加固或算法升级。后续应从实际系统问题、复现证据和用户目标中选择一个有界方向，再单独确认验收标准；不得把上述边界自动转成实施计划。

## 12. 建议记录的问题格式

你测试时如果发现问题，建议按下面格式发给我：

```text
场景：
当前时间：
操作：
页面现象：
期望现象：
是否能复现：
截图或错误文字：
```

最有用的信息是：

- 场景 ID。
- 当前时间 `T=<time>`。
- 点了哪个按钮。
- 后端错误原文。
- 任务 ID 或机器人 ID。
- 任务队列里的失败原因和恢复动作。

## 13. 当前验证结论

截至 2026-08-27：

- 完整检查通过：前端正式构建成功、前端测试 `232/232`、后端测试 `800 passed, 19 skipped`（共收集 `819` 项）。
- 唯一固定场景仍是 `26 × 16` 的 `integrated-demo`；默认六任务、库存流转和 T=700 闭环由端到端回归保护。
- 在线会话已经覆盖统一任务追加、动态事件、封锁、故障与恢复、任务锁、抢占、充电、指标历史、列表、删除和重置。
- 在线会话支持在真实当前 tick 事务式接入和永久退役机器人；接入时间之前隐藏新机器人，退役时间起隐藏被退役机器人，退役前路径、事件和指标可历史回放，重置恢复初始机器人。
- 同会话操作在单进程内串行化，不同会话可以并行；输入规模、会话容量、事件和指标历史均有上限。
- 开启避碰的在线执行安全门不会写入顶点或反向边冲突动作；连续三次相同拦截通过 `safetyStall` 提供诊断，但不等于求解器能够解除所有停滞。
- 路径规划仍是优先级时空 A* 加多候选顺序；目标全时域预留预检只消除确定性无效搜索，不是完整 MAPF/CBS 证明。
- 生产自适应规则仍为 `60/40ms`、最近5个真实重规划样本且至少3个样本以及 `2×` 任务压力；离线候选范围未应用。
- 实验和校准工具保留为内部诊断与回归能力。当前优先完善系统本体，但尚未确定下一项具体工作。
