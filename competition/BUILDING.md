# Windows x64 竞赛构建

构建机必须是 Windows x64，并使用 64 位 Python、Node.js 和 PowerShell 7。脚本会在前端构建前拒绝非 Windows 或非 64 位 Python，并在 PyInstaller 完成后验证 `WarehousePatrol.exe` 的 PE Machine 精确为 AMD64（`0x8664`）。

## Git 仓库维护者：正式包与源码 ZIP

此路径只用于包含 `.git` 和项目 `.tools` 的正式仓库。首次构建前安装精确锁定的构建依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.lock.txt -r .\competition\requirements-build.lock.txt
```

确认 Git 工作树干净后，使用项目内 PowerShell 7 和 nvm-windows 的真实 npm 运行根命令：

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run competition:package
```

该命令先构建前端，再使用锁定的 PyInstaller 依赖生成 Windows x64 `onedir`，最后通过 `git archive HEAD` 生成源码 ZIP：

```text
output/3s-competition-build/WarehousePatrol/WarehousePatrol.exe
output/3s-competition-build/WarehousePatrol-source.zip
```

## 从源码 ZIP 独立重建 onedir

解压 `WarehousePatrol-source.zip` 后不需要还原 `.git` 或 `.tools`。在能联网安装构建依赖的 Windows x64 构建机上，从解压目录运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.lock.txt -r .\competition\requirements-build.lock.txt
$npmPath = (Get-Command npm.cmd -CommandType Application -ErrorAction Stop).Source
& $npmPath --prefix frontend ci
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\competition\build-windows-package.ps1 -NpmPath $npmPath -PythonPath .\.venv\Scripts\python.exe -SkipSourcePackage
```

`-SkipSourcePackage` 只跳过需要 Git 元数据的二次源码归档；前端构建、Windows/64 位 Python 前置门、PyInstaller onedir 和最终 AMD64 PE 验证仍全部执行。输出为 `output/3s-competition-build/WarehousePatrol/WarehousePatrol.exe` 和同级 `README.md`。如果 `py -3` 选择了 32 位解释器，脚本会在运行 npm 或 PyInstaller 前清晰拒绝；应改用已安装的 64 位 Python 重新创建 `.venv`。

生成后的可执行文件以 `127.0.0.1` 提供本机服务。自动化可使用：

```powershell
.\output\3s-competition-build\WarehousePatrol\WarehousePatrol.exe --headless --port 8123
```

无头模式可通过 Windows `Ctrl+Break`（`CTRL_BREAK_EVENT`）正常停止，退出后会释放自己的监听端口。目标目录已包含 Python 运行时、后端和前端正式产物；目标机可直接运行，不需要另行准备 Node.js、Python 或构建依赖。

便携软件目录采用 `onedir` 结构，`WarehousePatrol/` 内含可执行文件及 `_internal/` 运行组件，外层 README 提供平台介绍和操作说明；源码 ZIP 单独提供源代码和构建文件。

正式仓库路径生成的源码包只允许来自干净 Git 工作树；路径白名单包含前端源代码、后端运行代码、前后端依赖清单、构建脚本、启动脚本、运行说明和 `competition/3s/package-README.md`。源码包保持源代码与重建所需文件的边界，评委运行说明统一使用包根目录的 README。
