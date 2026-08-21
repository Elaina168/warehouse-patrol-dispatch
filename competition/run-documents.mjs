import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const repositoryRoot = resolve(import.meta.dirname, "..");
const forwarded = process.argv.slice(2);
const configuredPython = process.env.WAREHOUSE_PATROL_DOCUMENTS_PYTHON;
const defaultPython = resolve(repositoryRoot, ".documents-venv", "Scripts", "python.exe");
const python = configuredPython || defaultPython;

if (!configuredPython && !existsSync(defaultPython)) {
  process.stderr.write(
    "文档运行环境不存在。请用 Python 3 创建 .documents-venv，并安装 competition/requirements-documents.lock.txt；也可显式设置 WAREHOUSE_PATROL_DOCUMENTS_PYTHON。\n",
  );
  process.exit(1);
}

if (!forwarded.includes("--help")) {
  const dependencyCheck = spawnSync(python, ["-c", "import docx, pypdf"], {
    cwd: repositoryRoot,
    stdio: "ignore",
    windowsHide: true,
  });
  if (dependencyCheck.status !== 0) {
    process.stderr.write(
      "文档 Python 缺少锁定依赖。请安装 competition/requirements-documents.lock.txt。\n",
    );
    process.exit(1);
  }
}

const args = ["-m", "backend.competition.documents", ...forwarded];
if (process.env.WAREHOUSE_PATROL_PDFTOPPM && !forwarded.includes("--pdftoppm")) {
  args.push("--pdftoppm", process.env.WAREHOUSE_PATROL_PDFTOPPM);
}
if (process.env.WAREHOUSE_PATROL_PDFINFO && !forwarded.includes("--pdfinfo")) {
  args.push("--pdfinfo", process.env.WAREHOUSE_PATROL_PDFINFO);
}
const completed = spawnSync(python, args, {
  cwd: repositoryRoot,
  env: {
    ...process.env,
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  },
  stdio: "inherit",
  windowsHide: true,
});
process.exit(completed.status ?? 1);
