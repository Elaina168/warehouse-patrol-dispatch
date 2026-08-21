import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const repositoryRoot = resolve(import.meta.dirname, "..");
const argv = process.argv.slice(2);
const plan = {
  steps: [
    "fullCheck",
    "evidence",
    "portablePackage",
    "documents",
    "recordingGate",
    "sensitiveScan",
    "hashes",
  ],
  task6Default: "blocked",
};
if (argv.includes("--print-plan")) {
  process.stdout.write(`${JSON.stringify(plan)}\n`);
  process.exit(0);
}

function value(name) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
}

const metadata = value("--metadata");
if (!metadata) {
  process.stderr.write("competition:release 必须显式传入 --metadata。\n");
  process.exit(1);
}
const convertedDir = value("--converted-dir");
if (!convertedDir) {
  process.stderr.write("competition:release 必须显式传入 --converted-dir。\n");
  process.exit(1);
}

const npmCli = process.env.npm_execpath;
if (!npmCli) {
  process.stderr.write("无法定位当前 npm CLI。\n");
  process.exit(1);
}

function runNpm(script, forwarded = []) {
  const completed = spawnSync(process.execPath, [npmCli, "run", script, ...forwarded], {
    cwd: repositoryRoot,
    env: process.env,
    stdio: "inherit",
    windowsHide: true,
  });
  if (completed.status !== 0) process.exit(completed.status ?? 1);
}

runNpm("check");
runNpm("competition:evidence");
runNpm("competition:package");
const documentArgs = [
  "--", "--metadata", metadata, "--converted-dir", convertedDir,
];
const approvals = value("--approvals");
if (approvals) documentArgs.push("--approvals", approvals);
runNpm("competition:documents", documentArgs);

const releasePython = process.env.WAREHOUSE_PATROL_RELEASE_PYTHON
  || resolve(repositoryRoot, ".venv", "Scripts", "python.exe");
if (!existsSync(releasePython)) {
  process.stderr.write("正式发布 Python 不存在。\n");
  process.exit(1);
}
const releaseArgs = [
  "-m", "backend.competition.release", "build", "--metadata", metadata,
];
if (approvals) releaseArgs.push("--approvals", approvals);
const video = value("--video");
if (video) releaseArgs.push("--video", video);
const recordingManifest = value("--recording-manifest");
if (recordingManifest) releaseArgs.push("--recording-manifest", recordingManifest);
const completed = spawnSync(releasePython, releaseArgs, {
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
