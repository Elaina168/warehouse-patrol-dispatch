# 3S 材料 Markdown 人工转换交接设计

## 1. 决策与目标

Task 5 的材料流水线改为“Markdown 是唯一内容源，用户负责人工转换为 Word 和 PDF，系统继续负责结构校验、PDF 页图渲染、逐页视觉批准、敏感信息扫描、发布组装和哈希”。

这一调整停止使用程序直接生成 DOCX 并通过 Microsoft Word 导出 PDF 的正式路径。旧 `.doc` 官方模板的只读转换器仍保留为独立能力，但没有显式官方原件时不得调用，也不得生成冒充官方表格的文件。

本设计不改变调度算法、后端业务 API、证据格式、便携包或最终提交目录结构。

## 2. 固定文档集合

系统生成以下四份 UTF-8 Markdown：

| documentId | Markdown | 用户返回的 Word | 用户返回的 PDF | 发布位置 |
|---|---|---|---|---|
| `application-summary` | `application-summary.md` | `application-summary.docx` | `application-summary.pdf` | `01-application/` |
| `technical-report` | `technical-report.md` | `technical-report.docx` | `technical-report.pdf` | `02-technical-report/` |
| `run-guide` | `run-guide.md` | `run-guide.docx` | `run-guide.pdf` | `03-software/` |
| `third-party-dependencies` | `third-party-dependencies.md` | `third-party-dependencies.docx` | `third-party-dependencies.pdf` | `07-licenses/` |

`application-summary` 明确标注为非官方信息汇总，不能替代主办方官方申报表。若官方要求指定模板，用户仍需提供并填写官方模板。

## 3. 目录与数据流

### 3.1 生成阶段

根命令保持：

```powershell
npm run competition:documents -- --metadata <被 Git 忽略的本地元数据 JSON>
```

命令原子生成：

```text
output/3s-submission-documents/
  markdown/
    application-summary.md
    technical-report.md
    run-guide.md
    third-party-dependencies.md
  markdown-manifest.json
  conversion-instructions.md
```

`markdown-manifest.json` 记录四个 Markdown 的 SHA-256、项目名、证据目录标识、生成时间和状态 `awaitingUserConversion`。日志不得输出申请人、教师或学校等真实值。

### 3.2 人工转换阶段

用户把八个同名文件放入被 Git 忽略的固定目录：

```text
competition/3s/local/converted-documents/
  application-summary.docx
  application-summary.pdf
  technical-report.docx
  technical-report.pdf
  run-guide.docx
  run-guide.pdf
  third-party-dependencies.docx
  third-party-dependencies.pdf
```

Word/PDF 允许采用用户习惯的清晰正式版式，但必须保持：

- 项目名称、标题、章节顺序和表格内容；
- Markdown 中的能力边界、实验口径和非官方申报表声明；
- PDF 为可读取文本的正常页面，不是把全文转换成截图；
- 不增加未经核实的官方字段、实验数字或能力宣称。

### 3.3 接收与渲染阶段

增加显式接收命令：

```powershell
npm run competition:documents -- --metadata <本地元数据> --converted-dir competition/3s/local/converted-documents
```

该命令：

1. 重新生成四份 Markdown，并校验其 SHA-256；
2. 要求转换目录恰好包含上述八个正式文件，不接受缺失、额外格式、符号链接或空文件；
3. 校验四个 DOCX 是有效 ZIP/OOXML，四个 PDF 具有有效 PDF 文件头和可读取页数；
4. 从 DOCX/PDF 提取可验证文本，检查固定项目名、文档标题、必需章节和能力边界；不要求跨格式逐字符一致，以允许 Word 自动换行和页眉页脚；
5. 把通过结构检查的 DOCX/PDF 原子复制到 `output/3s-submission-documents/` 根目录；
6. 用显式 Poppler 路径把每个 PDF 的全部页面转换为 PNG；
7. 写 `visual-review-request.json`，绑定每一页 PNG 的 SHA-256，状态为 `awaitingVisualApproval`。

接收成功只表示“转换文件已接收并渲染”，不表示文档门通过。

### 3.4 视觉批准与发布阶段

逐页检查后生成独立的视觉批准 JSON。既有命令继续执行：

```powershell
npm run competition:documents -- --metadata <本地元数据> --verify-only --approvals <视觉批准 JSON>
```

只有当四份 PDF 的每一页都明确批准，且批准中的 PNG SHA-256 与当前页图完全一致时，文档门才通过。Markdown、DOCX、PDF 或页图任一变化都会使旧批准失效。

发布器只复制经过验证的 DOCX/PDF，不把含个人信息的 Markdown 内容源或本地转换目录放入正式提交目录。最终发布仍要求外部手续、软件、证据、文档、录屏和哈希六门全部通过。

## 4. 文档内容

### 4.1 `application-summary`

- 非官方信息汇总与使用边界；
- 申请人姓名、学号、联系方式、学校和专业；
- 指导教师或推荐者的姓名、角色和职称；
- 资格、学校受理、推荐、签章、校内截止、报名系统、邮箱和附件限制；
- 源码、运行包、视频、申报书和技术报告的必交关系及来源。

### 4.2 `technical-report`

- 问题背景；
- 系统架构；
- 任务分配；
- 时空规划；
- 安全执行；
- 动态恢复；
- 实验方法与结果；
- 同类技术比较；
- 应用价值；
- 限制；
- 参考文献；
- 知识产权。

报告必须披露软件仿真、进程内会话、单进程并发边界、无持久化、无认证和非完整 MAPF 保证，不得宣称机器学习或任意输入零冲突。

### 4.3 `run-guide`

- Windows x64 便携包启动步骤；
- 创建会话、推进 tick、添加任务、阻塞和故障操作；
- 本机回环监听、单进程会话和无认证等运行边界；
- 端口占用、页面无法打开、任务未分配和播放暂停的排查方法。

### 4.4 `third-party-dependencies`

- 自研代码与第三方依赖边界；
- 从前后端锁文件读取的依赖名称和锁定版本；
- 仅记录锁文件明确提供的许可证字段，缺失时写“锁文件未声明”，不得猜填；
- 正式提交前保留上游许可证文本或书面授权的要求。

## 5. 失败与隐私边界

- 元数据缺失、未核实、含占位符、来源不足或路径未被 Git 忽略时，不生成 Markdown。
- 生成和接收都在临时目录完成，失败不得替换已有输出。
- 错误只报告 `documentId` 和安全原因码，不输出正文、个人值或本地元数据内容。
- 用户转换文件中的申请人信息允许出现在正式申请材料中，但原始元数据 JSON、转换工作目录、Markdown 内容源和日志不得进入发布包。
- 没有视觉批准、录屏、官方手续或最终哈希时，`release-manifest.json` 不得声称“正式提交就绪”。

## 6. 测试与验收

实现必须以 TDD 覆盖：

- 四份 Markdown 的固定文件名、必需章节、项目名和边界声明；
- Markdown manifest 的 SHA-256 可重算，产物变化使旧 manifest 失效；
- Markdown 生成失败的原子回滚与隐私不落盘；
- 转换目录缺少文件、出现额外文件、符号链接、空文件、损坏 DOCX/PDF时拒绝；
- 转换文件缺少项目名、标题、必需章节或能力边界时拒绝；
- 接收后四份 PDF 全页 Poppler 渲染与页数一致；
- 逐页视觉批准绑定当前 PNG SHA-256，变化后旧批准失败；
- 发布器只复制已验证 DOCX/PDF，不复制 Markdown、本地元数据或转换目录；
- Task 6 未完成时发布门仍明确失败；
- 根命令参数转发、完整测试和 `git diff --check`。

## 7. 迁移与保留

- 移除正式材料路径对 `python-docx -> Word PDF export` 的依赖，不再把 Word 普通打开故障作为 Markdown 生成的阻塞。
- 旧 `.doc` 只读转换、Word 自有 PID 清理和安全诊断代码保留，供未来显式官方模板使用。
- 当前失败的程序生成 DOCX/PDF 不作为正式产物，不进入提交目录。
- Task 5 只有在 Markdown 生成、用户转换接收、全部页图视觉批准、敏感扫描、发布回滚和哈希验证均通过后才能标记完成。
