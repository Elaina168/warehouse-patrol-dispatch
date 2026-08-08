# Frontend Dependency Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `postcss` 和 `nanoid` 提升到当前审计确认的安全版本，并通过 lockfile 回归防止回退。

**Architecture:** 保留项目现有精确 override 与 lockfile 策略，只升级受影响的传递依赖。静态回归保护已确认的安全下限，官方 registry 的实时 `npm audit` 负责最终漏洞验证。

**Tech Stack:** npm、Vite 6、PostCSS 8、pytest、`packaging.version.Version`。

## Global Constraints

- 不升级 Vite 主版本，不修改前端业务代码或构建脚本。
- `postcss` override 使用精确版本，不改成浮动范围。
- lockfile 必须由 npm 生成，不手工编辑完整依赖图。
- 官方审计源固定为 `https://registry.npmjs.org`。
- 所有文件保持 UTF-8。

---

### Task 1: 用失败回归固定安全版本下限

**Files:**
- Modify: `backend/tests/test_dependency_lock.py`
- Test: `backend/tests/test_dependency_lock.py`

**Interfaces:**
- Consumes: `frontend/package-lock.json` 的 `packages` 映射。
- Produces: `test_frontend_lock_preserves_reviewed_security_floors`。

- [ ] **Step 1: 写入失败测试**

```python
import json


def test_frontend_lock_preserves_reviewed_security_floors() -> None:
    lock = json.loads(
        (PROJECT_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    packages = lock["packages"]

    assert Version(packages["node_modules/postcss"]["version"]) >= Version("8.5.23")
    assert Version(packages["node_modules/nanoid"]["version"]) >= Version("3.3.17")
```

该测试会在 `postcss 8.5.18` 或 `nanoid 3.3.12` 回退时失败。

- [ ] **Step 2: 验证 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dependency_lock.py -k "frontend_lock"
```

Expected: FAIL，报告当前 lockfile 版本低于安全下限。

---

### Task 2: 更新精确 override 和 lockfile

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Produces: `postcss 8.5.23` 和安全的 `nanoid` 解析版本。

- [ ] **Step 1: 更新 package override**

将：

```json
"postcss": "8.5.18"
```

改为：

```json
"postcss": "8.5.23"
```

- [ ] **Step 2: 使用官方 registry 刷新安装树和 lockfile**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend install --registry=https://registry.npmjs.org
```

Expected: `frontend/package-lock.json` 与 `node_modules` 同步更新，安装命令退出 0。

- [ ] **Step 3: 验证 GREEN 和实际解析版本**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dependency_lock.py
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend ls postcss nanoid vite
```

Expected: 依赖锁测试通过；实际树包含 `postcss 8.5.23` 和 `nanoid >= 3.3.17`。

---

### Task 3: 安全闭合与完整验证

**Files:**
- Verify only: all changed files

**Interfaces:**
- Consumes: 更新后的 package files 和安全下限测试。
- Produces: 当前官方 registry 审计、构建、测试和差异证据。

- [ ] **Step 1: 运行实时安全审计**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend audit --registry=https://registry.npmjs.org
```

Expected: `found 0 vulnerabilities`，退出码 0。

- [ ] **Step 2: 运行完整项目检查**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: 前端构建、全部前后端测试、Python 依赖检查和补丁检查全部退出 0。

- [ ] **Step 3: 检查最终范围**

Run:

```powershell
git status --short
git diff -- frontend/package.json frontend/package-lock.json backend/tests/test_dependency_lock.py
```

Expected: 依赖修复只修改两个 package 文件和安全下限回归；`output/` 保持未跟踪且不进入提交。

