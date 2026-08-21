"""生成 3S 材料的唯一 Markdown 内容源与人工转换交接清单。"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.competition.document_support import (
    DocumentGenerationError,
    load_verified_evidence,
    publish_directory_atomically,
    resolve_latest_evidence,
    sha256_file,
)
from backend.competition.submission import (
    PROJECT_NAME,
    load_verified_submission_metadata,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCUMENT_IDS = (
    "application-summary",
    "technical-report",
    "run-guide",
    "third-party-dependencies",
)
DOCUMENT_TITLES = {
    "application-summary": "参赛信息汇总（非官方申报表）",
    "technical-report": "技术报告",
    "run-guide": "运行说明",
    "third-party-dependencies": "知识产权与第三方依赖清单",
}
DOCUMENT_REQUIRED_PHRASES = {
    "application-summary": (
        PROJECT_NAME,
        "非官方信息汇总",
        "不能替代主办方官方申报表",
        "资格与学校受理",
        "材料必交关系",
    ),
    "technical-report": (
        PROJECT_NAME,
        "问题背景",
        "系统架构",
        "任务分配",
        "时空规划",
        "安全执行",
        "动态恢复",
        "实验方法与结果",
        "同类技术比较",
        "应用价值",
        "限制",
        "参考文献",
        "知识产权",
        "不提供完整 MAPF 保证",
        "不使用机器学习",
    ),
    "run-guide": (
        PROJECT_NAME,
        "启动步骤",
        "主要操作",
        "运行边界",
        "故障排查",
        "127.0.0.1",
    ),
    "third-party-dependencies": (
        PROJECT_NAME,
        "自研与第三方边界",
        "依赖清单",
        "锁文件未声明",
        "正式提交前",
    ),
}


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    """一份可验证的 Markdown 材料。"""

    document_id: str
    title: str
    required_phrases: tuple[str, ...]
    content: str


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace(
        "\n", "<br>"
    )


def _markdown_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
) -> str:
    lines = [
        "| " + " | ".join(_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _source_text(source: dict[str, object]) -> str:
    return (
        f"来源：{source['url']}\n"
        f"附件 SHA-256：{source['attachmentSha256']}\n"
        f"核验时间：{source['verifiedAt']}\n"
        f"书面回复：{source['writtenReply']}"
    )


def _build_application(metadata: dict[str, object]) -> str:
    applicant = metadata["applicant"]
    advisors = metadata["advisors"]
    procedures = metadata["procedures"]
    materials = metadata["requiredMaterials"]
    procedure_labels = (
        ("eligibility", "单人资格"),
        ("majorAndSchoolAcceptance", "专业与学校受理资格"),
        ("advisorAndRecommenderRequirements", "指导教师、推荐者及职称要求"),
        ("collegeAndSchoolRecommendation", "学院与学校推荐"),
        ("signaturesAndSeals", "签字与盖章"),
        ("internalDeadline", "校内截止"),
        ("registrationSystem", "报名系统"),
        ("materialsMailbox", "材料邮箱"),
        ("attachmentConstraints", "附件大小与格式"),
    )
    procedure_rows: list[list[object]] = []
    for key, label in procedure_labels:
        item = procedures[key]
        detail = str(item["statement"])
        if key == "internalDeadline":
            detail += f"\n截止：{item['deadline']}"
        elif key == "registrationSystem":
            detail += f"\n系统：{item['systemUrl']}"
        elif key == "materialsMailbox":
            detail += f"\n邮箱：{item['email']}"
        elif key == "attachmentConstraints":
            detail += (
                f"\n最大字节数：{item['maximumBytes']}"
                f"\n允许格式：{', '.join(item['allowedFormats'])}"
            )
        procedure_rows.append([label, detail, _source_text(item["source"])])

    material_labels = (
        ("sourceCode", "源码"),
        ("runnablePackage", "运行包"),
        ("video", "视频"),
        ("applicationForm", "申报书"),
        ("technicalReport", "技术报告"),
    )
    material_rows = [
        [
            label,
            "必交" if materials[key]["required"] else "非必交",
            materials[key]["relationship"],
            _source_text(materials[key]["source"]),
        ]
        for key, label in material_labels
    ]
    advisor_rows = [
        [advisor["name"], advisor["role"], advisor["professionalTitle"]]
        for advisor in advisors
    ]
    return "\n\n".join(
        (
            "# 参赛信息汇总（非官方申报表）",
            f"**项目名称：** {PROJECT_NAME}",
            (
                "> 本文件是非官方信息汇总，仅用于材料整理；不能替代主办方官方申报表。"
                "如官方要求指定模板，仍须使用并填写主办方原件。"
            ),
            "## 申请人信息",
            _markdown_table(
                ("姓名", "学号", "联系方式", "邮箱", "学校", "专业"),
                ((
                    applicant["name"],
                    applicant["studentId"],
                    applicant["phone"],
                    applicant["email"],
                    applicant["school"],
                    applicant["major"],
                ),),
            ),
            "## 指导与推荐角色",
            _markdown_table(("姓名", "角色", "职称"), advisor_rows),
            "## 资格与学校受理",
            _markdown_table(("核验项", "结论", "可追溯来源"), procedure_rows),
            "## 材料必交关系",
            _markdown_table(("材料", "关系", "说明", "可追溯来源"), material_rows),
        )
    ) + "\n"


def _evidence_run(
    evidence: dict[str, object],
    case_id: str,
) -> dict[str, object] | None:
    runs = evidence.get("runs")
    if not isinstance(runs, list):
        return None
    return next(
        (
            run
            for run in runs
            if isinstance(run, dict) and run.get("caseId") == case_id
        ),
        None,
    )


def _metric(run: dict[str, object] | None, key: str) -> object:
    if not run:
        return "证据未提供"
    metrics = run.get("metrics")
    return metrics.get(key, "证据未提供") if isinstance(metrics, dict) else "证据未提供"


def _build_technical_report(evidence: dict[str, object]) -> str:
    without = _evidence_run(evidence, "integrated-demo-without-conflict-avoidance")
    with_avoidance = _evidence_run(
        evidence, "integrated-demo-with-conflict-avoidance"
    )
    main = _evidence_run(evidence, "main-demo-online")
    safety = _evidence_run(evidence, "safety-gate-boundary")
    pressure_rows = []
    for run in evidence.get("runs", []):
        if not isinstance(run, dict) or not str(run.get("caseId", "")).startswith(
            "seeded-pressure"
        ):
            continue
        pressure_rows.append(
            [
                run.get("caseId", ""),
                run.get("robotCount", "证据未提供"),
                run.get("taskCount", "证据未提供"),
                _metric(run, "replanTimeMs"),
                _metric(run, "conflictCount"),
            ]
        )
    if not pressure_rows:
        pressure_rows.append(["证据未提供", "-", "-", "-", "-"])

    sections = [
        "# 技术报告",
        f"**项目名称：** {PROJECT_NAME}",
        "## 问题背景",
        "动态仓储中的任务持续到达，通道会被临时封锁，机器人也可能故障。系统需要在持续执行中兼顾任务优先级、路径可行性、冲突约束和恢复可解释性。",
        "## 系统架构",
        "系统采用 React + TypeScript + Vite 前端与 FastAPI 后端。后端维护进程内在线会话、任务队列、机器人状态、timed path、事件与指标；前端提供操作面板。本项目是本地 Windows x64 软件仿真，只有单进程并发边界，无持久化、无认证，未接入实体物联网、低空通信或云平台。",
        "## 任务分配",
        "任务分配使用确定性启发式与 beam-style 组合搜索，综合优先级、能力、载荷、可达性、释放时间和软分配偏好；不使用机器学习。",
        "## 时空规划",
        "路径规划以 A*、时间预留和优先级顺序构造 timed path，并检查顶点冲突、边交换冲突、期限与失败。该实现不是完整 CBS，也不提供完整 MAPF 保证。",
        "## 安全执行",
        "timed path 是移动与能耗执行的最终依据。检测到危险移动时，全队安全等待；专项边界演示只证明危险移动没有被提交执行，不把安全等待描述成规划器解决了不可绕行场景。",
        "## 动态恢复",
        "统一运行时接口支持加入任务、阻塞单元格、机器人故障以及对应恢复。重规划保留已完成取货、巡检目标和在途货物语义，并输出 failureCategory、blockingCells、blockingRobotIds 与 recoveryAction。",
        "## 实验方法与结果",
        _markdown_table(
            ("案例", "预测冲突", "失败", "说明"),
            (
                (
                    "integrated-demo 避碰关闭",
                    _metric(without, "conflictCount"),
                    _metric(without, "failureCount"),
                    "同输入对照，不作为安全运行结论",
                ),
                (
                    "integrated-demo 避碰开启",
                    _metric(with_avoidance, "conflictCount"),
                    _metric(with_avoidance, "failureCount"),
                    "预测冲突口径",
                ),
                (
                    "主演示在线流程",
                    _metric(main, "activeConflictCount"),
                    _metric(main, "failureCount"),
                    f"完成任务数：{_metric(main, 'completedTaskCount')}；超期：{_metric(main, 'deadlineMissCount')}",
                ),
                (
                    "安全门专项",
                    "实际执行历史无碰撞",
                    "不适用",
                    f"干预次数：{_metric(safety, 'safetyInterventionCount')}；连续停滞：{_metric(safety, 'safetyStallCount')}",
                ),
            ),
        ),
        "### 固定种子规模结果",
        _markdown_table(
            ("案例", "机器人", "任务", "同机规划耗时毫秒", "预测冲突"),
            pressure_rows,
        ),
        "实验不删除异常值、不挑选最好结果；耗时只表述为同机重复实验结果，不外推到其他机器或任意输入。",
        "## 同类技术比较",
        _markdown_table(
            ("方法", "优势", "本系统边界"),
            (
                ("完整 CBS/MAPF", "可提供更强的完备性或最优性讨论", "本系统未实现完整 CBS/MAPF"),
                ("学习型调度", "可从数据中拟合策略", "本系统不使用机器学习，避免无证据泛化"),
                ("本系统", "在线任务流、动态恢复与执行安全均可解释", "不声称任意输入零冲突"),
            ),
        ),
        "## 应用价值",
        "系统可用于仓储、园区和设施巡检的调度逻辑验证，重点价值是可运行、可解释、可重算的软件仿真证据。",
        "## 限制",
        "当前仅完成软件仿真，未部署实体机器人、传感器、低空通信或云平台。在线状态为进程内会话，存在单进程并发边界，无持久化、无认证；规划不提供完整 MAPF 保证，也不声称任意输入零冲突。",
        "## 参考文献",
        "1. Hart, P. E., Nilsson, N. J., Raphael, B. A Formal Basis for the Heuristic Determination of Minimum Cost Paths.\n2. Sharon, G. et al. Conflict-Based Search for Optimal Multi-Agent Path Finding.\n3. 项目仓库锁文件、测试、证据清单与运行说明。",
        "## 知识产权",
        "业务源码、测试、竞赛清单和材料生成代码属于本项目交付范围；第三方运行时、框架、库与工具继续受其各自许可证约束，正式提交前需保留上游许可证文本或书面授权。",
    ]
    return "\n\n".join(sections) + "\n"


def _build_run_guide() -> str:
    return "\n\n".join(
        (
            "# 运行说明",
            f"**项目名称：** {PROJECT_NAME}",
            "## 启动步骤",
            "1. 完整解压 Windows x64 便携目录，保留 `WarehousePatrol.exe` 与 `_internal` 的相对结构。\n2. 双击 `WarehousePatrol.exe`；自动化验收可使用 `WarehousePatrol.exe --headless --port 8123`。\n3. 浏览器访问启动器给出的本机地址，确认后端状态为在线。\n4. 创建在线会话，按需添加任务、触发动态事件、推进 tick，并查看路径、事件和指标。\n5. 结束后关闭桌面窗口；无头模式使用 `Ctrl+Break`，使进程正常清理并释放端口。",
            "## 主要操作",
            _markdown_table(
                ("操作", "预期结果", "注意事项"),
                (
                    ("创建会话", "初始化机器人、任务、库存和时间线", "历史回放时不允许运行态修改"),
                    ("推进 tick", "机器人按 timed path 执行", "安全门或能量门可能保持等待"),
                    ("添加任务", "统一任务端点接收手工或生成任务", "后端校验能力、载荷与输入上限"),
                    ("阻塞或故障", "触发重规划并暴露恢复建议", "恢复动作只处理实际可恢复条件"),
                ),
            ),
            "## 运行边界",
            "本交付是软件仿真。服务只绑定 `127.0.0.1`；会话保存在单个服务进程内，只有单进程并发保证；服务重启后无持久化恢复，且无认证。规划不提供完整 MAPF 保证。未知占用端口不会被启动器终止或接管。",
            "## 故障排查",
            _markdown_table(
                ("现象", "检查", "处理"),
                (
                    ("端口已占用", "查看启动器给出的端口错误", "关闭已知占用程序或改用新的显式端口"),
                    ("页面无法打开", "确认进程仍在且 `/health` 返回 `ok`", "重新启动便携包，不要删除 `_internal`"),
                    ("任务未分配", "查看 failureCategory、blockingCells、blockingRobotIds", "按 recoveryAction 清障或恢复机器人"),
                    ("播放暂停", "查看 tick 的业务错误详情", "修正输入后继续，不把 4xx 误判为后端离线"),
                ),
            ),
        )
    ) + "\n"


def _dependency_rows() -> list[list[str]]:
    frontend_package = json.loads(
        (REPOSITORY_ROOT / "frontend/package.json").read_text(encoding="utf-8")
    )
    frontend_lock = json.loads(
        (REPOSITORY_ROOT / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    rows: list[list[str]] = []
    for group in ("dependencies", "devDependencies"):
        for name, requested in sorted(frontend_package.get(group, {}).items()):
            locked = frontend_lock.get("packages", {}).get(f"node_modules/{name}", {})
            rows.append(
                [
                    "npm",
                    name,
                    str(locked.get("version", requested)),
                    str(locked.get("license", "锁文件未声明")),
                ]
            )
    for line in (REPOSITORY_ROOT / "backend/requirements.lock.txt").read_text(
        encoding="utf-8"
    ).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, version = stripped.partition("==")
        rows.append(
            [
                "PyPI",
                name,
                version if separator else "锁文件未声明版本",
                "锁文件未声明",
            ]
        )
    return rows


def _build_dependency_inventory() -> str:
    return "\n\n".join(
        (
            "# 知识产权与第三方依赖清单",
            f"**项目名称：** {PROJECT_NAME}",
            "## 自研与第三方边界",
            "仓巡智调的业务源码、测试、竞赛清单和材料生成代码属于本项目交付范围。Python、FastAPI、Uvicorn、React、Vite、PyInstaller、Poppler 和其他依赖仍受各自许可证约束，不会因打包进入便携目录而被描述为自研成果。",
            "## 依赖清单",
            _markdown_table(
                ("生态", "依赖", "锁定版本", "锁文件许可字段"),
                _dependency_rows(),
            ),
            "## 许可与素材核对要求",
            "表中“锁文件未声明”是对输入锁文件的客观描述，不是许可结论。正式提交前应保留上游许可证文本或书面授权；不得猜填锁文件没有声明的许可证。",
        )
    ) + "\n"


def build_markdown_documents(
    metadata: dict[str, object],
    evidence: dict[str, object],
) -> tuple[MarkdownDocument, ...]:
    """按固定顺序构造四份 Markdown 内容。"""

    bodies = {
        "application-summary": _build_application(metadata),
        "technical-report": _build_technical_report(evidence),
        "run-guide": _build_run_guide(),
        "third-party-dependencies": _build_dependency_inventory(),
    }
    return tuple(
        MarkdownDocument(
            document_id=document_id,
            title=DOCUMENT_TITLES[document_id],
            required_phrases=DOCUMENT_REQUIRED_PHRASES[document_id],
            content=bodies[document_id],
        )
        for document_id in DOCUMENT_IDS
    )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _evidence_id(manifest_path: Path) -> str:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        value = manifest["generatedAt"]
        if not isinstance(value, str):
            raise TypeError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise DocumentGenerationError("正式证据清单缺少有效生成时间。") from exc
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def generate_markdown_handoff(
    *,
    metadata_path: Path,
    evidence_directory: Path,
    output_directory: Path,
) -> Path:
    """验证输入并原子发布 Markdown 人工转换交接目录。"""

    metadata = load_verified_submission_metadata(metadata_path, REPOSITORY_ROOT)
    evidence_path = resolve_latest_evidence(evidence_directory)
    evidence = load_verified_evidence(evidence_path)
    evidence_manifest_path = evidence_path / "evidence-manifest.json"
    evidence_id = _evidence_id(evidence_manifest_path)
    documents = build_markdown_documents(metadata, evidence)

    output_directory = output_directory.resolve()
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = output_directory.with_name(
        f".{output_directory.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        markdown_directory = staging / "markdown"
        markdown_directory.mkdir(parents=True)
        manifest_documents: dict[str, dict[str, str]] = {}
        for document in documents:
            destination = markdown_directory / f"{document.document_id}.md"
            _write_text(destination, document.content)
            manifest_documents[document.document_id] = {
                "path": destination.relative_to(staging).as_posix(),
                "sha256": sha256_file(destination),
            }

        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        manifest = {
            "schemaVersion": 1,
            "status": "awaitingUserConversion",
            "projectName": PROJECT_NAME,
            "generatedAtUtc": generated_at,
            "evidenceId": evidence_id,
            "evidenceManifestSha256": sha256_file(evidence_manifest_path),
            "documents": manifest_documents,
        }
        _write_text(
            staging / "markdown-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        returned_files = "\n".join(
            f"- `{document_id}.docx`\n- `{document_id}.pdf`"
            for document_id in DOCUMENT_IDS
        )
        instructions = (
            "# 3S 材料人工转换说明\n\n"
            "请保持 Markdown 的项目名称、标题、章节顺序、表格内容、能力边界和实验口径，"
            "并把以下八个文件放入 `competition/3s/local/converted-documents/`：\n\n"
            f"{returned_files}\n\n"
            "PDF 必须保留可读取文本，不能把全文转换为截图。接收成功后状态仍为 "
            "`awaitingVisualApproval`，逐页视觉批准通过前不得发布。\n\n"
            "使用 task5-anonymous-metadata.json 时属于匿名转换排版演练，禁止正式提交。\n"
        )
        _write_text(staging / "conversion-instructions.md", instructions)
        publish_directory_atomically(staging, output_directory)
        return output_directory
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
