# 前端依赖安全更新设计

## 目标

清除当前官方 npm registry 审计报告中的 `nanoid` high 和 `postcss`/`vite` moderate 依赖链问题，并防止 lockfile 回退到已确认的受影响版本。

## 已确认根因

`frontend/package.json` 将 `postcss` 精确 override 为 `8.5.18`，`frontend/package-lock.json` 因而解析到 `postcss 8.5.18` 和 `nanoid 3.3.12`。当前 `npm audit --registry=https://registry.npmjs.org` 报告 1 high、2 moderate。`npm audit fix --dry-run` 只会更新 `nanoid`，不会越过手动固定的 `postcss` override。

## 方案比较

1. **精确更新 override 和 lockfile，并增加安全下限测试（采用）**：把 `postcss` 固定到已修复的 `8.5.23`，让 lockfile 解析到 `nanoid 3.3.18`，与项目现有精确锁定策略一致。
2. **删除 `postcss` override**：交给 Vite 的范围自动解析，当前可修复，但未来安装结果更容易漂移。
3. **升级 Vite 主版本**：改动范围和兼容风险明显大于当前传递依赖漏洞所需，不符合最小修复原则。

## 设计

- 将 `frontend/package.json` 的 `postcss` override 从 `8.5.18` 更新为精确版本 `8.5.23`。
- 使用官方 npm registry 刷新 `frontend/package-lock.json` 和本地安装树；最终 lockfile 必须包含 `postcss 8.5.23` 与不低于 `3.3.17` 的 `nanoid`，预期解析版本为 `3.3.18`。
- 在 `backend/tests/test_dependency_lock.py` 增加行为回归，读取真实 `frontend/package-lock.json`，使用 `packaging.version.Version` 比较实际解析版本与本次审计确认的安全下限：`postcss >= 8.5.23`、`nanoid >= 3.3.17`。
- 测试只保护已确认的版本边界；实时漏洞状态仍以官方 registry 的 `npm audit` 为准。

## 兼容性与验证

不修改前端业务代码、Vite 主版本、构建脚本或 API。依次验证安全下限测试、`npm ls postcss nanoid vite`、前端构建与测试、全部后端测试、`pip check`、`npm audit --registry=https://registry.npmjs.org` 和 `git diff --check`。
