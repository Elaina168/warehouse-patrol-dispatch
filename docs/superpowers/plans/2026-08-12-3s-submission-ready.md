# 仓巡智调 3S 杯正式提交就绪实施计划

> 执行基线：`main` 的 `7c334f2`；实现分支 `codex/3s-submission-ready`。所有中文文件保持 UTF-8。

## 全局约束

- 保持现有调度算法和后端业务 API 的请求/响应模型不变；竞赛能力作为薄层复用现有会话接口。
- 项目名固定为“仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统”。
- 不宣称机器学习、完整 CBS/MAPF、任意输入零冲突、实体物联网部署、低空通信或云平台。
- `competition/3s/` 存放可复现源码；`submission/warehouse-patrol-3s/` 和本地真实元数据必须被 Git 忽略。
- 任何“正式提交就绪”判断必须同时通过外部手续、软件、证据、文档、录屏、哈希六个门。外部信息缺失时失败，不生成占位结论。
- 所有新行为遵循测试先行：测试必须先因缺少行为而失败，再实现最小代码使其通过。

## Task 1：共享清单、主演示执行器和安全门夹具

- 新建 `competition/3s/manifests/main-demo.json`，`schemaVersion` 精确为 `1`。场景固定为 `integrated-demo`；选项固定为 `avoidConflicts=true`、`includeDynamic=true`、`assignmentReplanWindow=24`、`adaptiveReplanWindow=false`。
- 主演示步骤精确为：T=12 添加 `DEMO-URGENT-12`（`emergency`、`高优先级冷链复核`、优先级5、releaseTime 12、deadline 120、serviceTime 2、target `[1,4]`）；T=20 封锁 `[10,6]`；T=28 故障 `R2`；T=36 先解除 `[10,6]` 再恢复 `R2`；T=700 完成验收。
- 新建 `competition/3s/manifests/safety-demo.json`，把 `backend/tests/test_sessions.py::_forced_safety_gate_scenario()` 的现有3×1场景逐字段迁移为唯一数据源；测试改为读取该清单，不保留场景副本。
- 在 `backend/competition/` 实现严格清单加载与版本/字段校验、主演示执行器、安全门执行器。清单未知版本、错误步骤顺序或标识符不一致必须拒绝。
- 主演示执行器只调用现有 `create_session`、`tick_session`、`add_task`、`add_blocked_cell`、`fail_robot`、`remove_blocked_cell`、`restore_robot`、`get_session` 和 `delete_session`；每步验证结构化后置条件，并保证清理会话。
- 回归精确验证 T=12/20/28/36/700，七项任务完成、最终活动冲突/失败/超期为零、主演示无 `safetyIntervention`；安全门验证 T=2 拦截、位置唯一、`activeConflictCount=0`、第三次同类拦截的 `safetyStall.consecutiveCount=3`，并验证实际轨迹无顶点/边交换碰撞。

## Task 2：35 次正式证据套件、统计、图表和原子输出

- 在 `backend/competition/evidence.py` 提供 CLI，默认 `--repetitions 5`、每次隔离子进程、单次超时30秒；复用现有 `backend.benchmarks.process_isolation` 的终止/有界等待/kill 清理语义。
- 固定7个案例：`integrated-demo-without-conflict-avoidance`、`integrated-demo-with-conflict-avoidance`、`main-demo-online`、`seed-17`、`seed-29`、`seed-31`、`safety-gate-boundary`。默认合计35条运行记录。
- 三个种子案例必须复用 `SEEDED_PRESSURE_CASES` 的精确标签、种子、机器人和任务数量以及当前2000ms规划预算，不复制数字到第二份生产常量。
- 每条失败、超时和异常值都写入结果；任意未完成运行或不满足案例验收都令命令非零退出，但仍原子发布完整诊断结果。
- 输出同一原子保护包：`results.json`、`runs.csv`、`case-summaries.csv`、`evidence-manifest.json`，并生成避碰对照、动态事件时间线、4/6/8规模表现、安全门执行轨迹四组 SVG 与 PNG。PNG/SVG只由原始记录生成。
- `evidence-manifest.json` 记录 Git 提交、dirty 状态、操作系统、CPU、Python版本、精确参数、UTC生成时间和每个文件 SHA-256。CSV统计必须能独立重算 JSON 摘要。
- 增加测试覆盖35条完整记录、统计重算、异常保留、超时/子进程清理、图表值来源和发布回滚。

## Task 3：前端竞赛模式、同源 API 和生产静态托管

- API 基址默认精确为 `/api`，保留 `VITE_API_BASE_URL` 覆盖；既有 API 函数需要接受 `/api` 这种相对基址。Vite 开发服务器把 `/api` 代理至 `http://127.0.0.1:8011`，继续使用5174/8011双服务。
- TypeScript 直接导入 Task 1 的 JSON 清单，定义 `CompetitionDemoManifest`、`CompetitionDemoStep`、`CompetitionRunState`；状态枚举精确为 `idle / preparing / running / paused / completed / failed`，未知清单版本拒绝。
- 新增 `?competition=3s&demo=main` 和 `?competition=3s&demo=safety` 入口。默认面板不改变；竞赛入口展示明确标题、当前步骤、后置条件、暂停原因、重置/单步/自动操作。`record=1` 隐藏开发控件并采用固定讲解停顿和加速节奏。
- 控制器对不确定的写请求先 GET 会话核对是否已生效再决定是否重试；HTTP失败、后置条件失败或主演示出现 `safetyIntervention` 时暂停/失败，不盲目重复写操作。
- FastAPI 在全部现有 API 路由之后托管 `frontend/dist`；开发时目录缺失不能阻止 API 启动；未知 `/api/*` 仍返回404，不回退到 HTML。
- 测试状态转换、单步/自动等价、失败暂停、重置、相对 API、开发代理、查询入口和生产静态文件/未知 API 行为。

## Task 4：Windows 启动器、PyInstaller onedir 和源码包

- 新增 Python 启动器：普通模式用轻量 Tk 窗口启动仅绑定 `127.0.0.1` 的 Uvicorn、打开默认浏览器并提供停止按钮；`--headless --port <port>` 阻塞运行供自动化使用。
- 启动前精确探测绑定地址/端口；占用即清晰失败，绝不停止未知进程。正常关闭必须停止自己的服务线程和监听端口。
- 新增 PyInstaller spec 与 PowerShell 构建脚本，先构建前端，再生成 `WarehousePatrol.exe` 的 Windows x64 `onedir` 目录；打包 FastAPI、前端 `dist` 和运行所需资源，不要求目标机安装 Node/Python或联网下载。
- 新增源码包脚本，要求 Git 工作树干净并通过 `git archive HEAD` 生成 ZIP；包含锁文件、构建说明和竞赛生成器，自然排除 `.git`、虚拟环境、`node_modules`、缓存、运行输出、个人元数据、API密钥和已填写申报表。
- 新增锁定的竞赛构建依赖和根命令 `competition:package`。测试启动参数、端口占用、服务停止、spec资源映射、脏工作树拒绝和ZIP内容。

## Task 5：手续闸门、材料生成、敏感信息扫描和发布哈希

- 新建严格的本地元数据/外部手续 JSON schema 与无个人信息示例；字段覆盖资格、专业/学校受理、指导教师/推荐者、签章、校内截止、报名系统、邮箱、附件限制、必交关系、来源URL/附件SHA-256/核验时间/书面回复。任何必填缺失、`verified != true`、占位文本或来源不足都失败。
- 真实姓名、学号、联系方式、教师/学校信息只允许从被忽略的本地元数据路径读入；生成文件和日志不得输出这些值。
- 新增只读旧 `.doc` 转换脚本：先记录原件SHA-256，使用隔离且可超时的 Word 进程另存为新 DOCX，不覆盖原件，并保证退出 Word。
- 使用 `documents`/`pdf` 工作流生成申报材料、技术报告、运行说明和知识产权/第三方依赖清单。报告必须包含问题背景、架构、任务分配、时空规划、安全执行、动态恢复、实验方法/结果、同类比较、应用价值、限制、参考文献和知识产权，并明确软件仿真、进程内单进程会话、无持久化/认证和非完整 MAPF 边界。
- DOCX 独立导出 PDF；逐页 PNG 视觉检查的结果作为机器可读门。未完成视觉检查不得通过文档门。
- 发布器在临时目录组装规定的 `submission/warehouse-patrol-3s/` 结构，扫描密钥、个人绝对路径、`.env`、本地元数据、缓存和未授权素材，写 `release-manifest.json`；任一步失败不得替换已有正式目录。
- 最后生成覆盖除自身外全部文件的 `SHA256SUMS.txt`，在独立进程重验；产物变更后旧哈希必须失败。根命令包括 `competition:documents -- --metadata <path>` 和 `competition:release`。
- 测试元数据拒绝、隐私不落盘、转换不覆盖、文档字段、视觉门、扫描、原子回滚、哈希覆盖和独立重验。

## Task 6：Playwright 无声录屏、字幕和配音稿

- 将 Playwright 作为锁定开发依赖，新增 `competition:record`。
- 录屏器启动 Task 4 的真实 `WarehousePatrol.exe --headless --port <空闲端口>`，等待 `/health` 后仅访问本机，使用1920×1080上下文打开 `?competition=3s&demo=main&record=1`，执行自动模式并等待完成。
- 保存无声 WebM 母版、与清单步骤对应的 UTF-8 SRT、Markdown 配音讲稿和录制元数据；失败时保留诊断截图/日志并非零退出，始终停止自己的进程。
- 测试 SRT 时间单调、讲稿步骤完整、只允许loopback URL、失败清理和录制元数据。实际录制作为发布门执行。

## Task 7：全量集成验证与最终交付

- 运行 `scripts/test-all.ps1`，重新记录前端构建、前端测试和后端测试结果。
- 运行默认35次证据；核验四组图表和全部输出哈希。
- 构建便携包并在带空格和中文的临时路径做无Node/Python冒烟：启动、健康检查、主演示、安全专项、关闭、无残留端口/子进程、无非本机请求。
- 执行材料、录屏和完整发布命令；外部手续/个人元数据缺失时，确认发布门明确阻断且不声称正式提交就绪，同时交付已完成的可复现技术产物。
- 对整条分支做最终代码审查；不合并、不推送，等待用户选择集成方式。
