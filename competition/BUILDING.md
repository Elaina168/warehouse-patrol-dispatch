# Windows 竞赛构建

首次构建前，在仓库根目录安装精确锁定的构建依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\competition\requirements-build.lock.txt
```

确认 Git 工作树干净后，使用项目内 PowerShell 7 和 nvm-windows 的真实 npm 运行：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run competition:package
```

该命令先构建前端，再使用 `competition/requirements-build.lock.txt` 中锁定的 PyInstaller 依赖生成 Windows x64 `onedir` 目录：

```text
output/3s-competition-build/WarehousePatrol/WarehousePatrol.exe
```

生成后的可执行文件以 `127.0.0.1` 提供本机服务。自动化可使用：

```powershell
.\output\3s-competition-build\WarehousePatrol\WarehousePatrol.exe --headless --port 8123
```

无头模式可通过 Windows `Ctrl+Break`（`CTRL_BREAK_EVENT`）正常停止，退出后会释放自己的监听端口。目标目录已包含 Python 运行时、后端、前端产物和竞赛资源；目标机不需要安装 Node.js、Python，也不需要联网下载依赖。

构建同时调用 `git archive HEAD` 生成 `output/3s-competition-build/WarehousePatrol-source.zip`。源码包只允许在 Git 工作树干净时生成；它包含已提交的锁文件、构建说明、竞赛清单和证据生成器，并自然排除未跟踪或被忽略的 `.git`、虚拟环境、`node_modules`、缓存、运行输出、`.env`、个人元数据和已填写申报表。
