# Online Pressure Session Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让在线压力实验使用独立 session registry，确保实验不会淘汰或改变生产全局会话。

**Architecture:** 在 session 模块中引入显式可注入的 `SessionRegistry`，默认调用继续使用原全局 store。在线压力实验为每次请求创建私有 registry，并把它传给全部 session 操作。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、pytest。

## Global Constraints

- 不改变任何 HTTP 路由、请求模型、响应模型或 OpenAPI 契约。
- 不复制在线调度流程，不临时交换模块级全局变量，不快照恢复全局会话。
- 默认 session TTL、容量淘汰、锁和关闭协议保持现有行为。
- 所有源码与文档保持 UTF-8，新增代码注释使用中文。

---

### Task 1: 用失败回归固定全局会话不变性

**Files:**
- Modify: `backend/tests/test_experiments.py`
- Test: `backend/tests/test_experiments.py`

**Interfaces:**
- Consumes: `sessions_module.MAX_SESSIONS`、`sessions_module._sessions`、`POST /api/experiments/online-pressure`。
- Produces: `test_online_pressure_experiment_preserves_full_global_session_registry`。

- [x] **Step 1: 写入失败测试**

创建一个普通生产会话，把 `MAX_SESSIONS` 临时设为 `1`，保存 `_sessions` 的键和值对象，调用在线压力实验 API，然后逐项断言全局 registry 未改变：

```python
def test_online_pressure_experiment_preserves_full_global_session_registry(monkeypatch) -> None:
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    existing = sessions_module.create_session(
        CreateSessionRequest(
            scenario=seeded_pressure_scenario("existing", 17, 4, 12),
            options=DispatchOptions(),
        )
    )
    before = dict(sessions_module._sessions)

    response = TestClient(app).post(
        "/api/experiments/online-pressure",
        json={"options": {"avoidConflicts": True, "includeDynamic": True, "assignmentReplanWindow": 120}},
    )

    assert response.status_code == 200
    assert sessions_module._sessions == before
    assert sessions_module._sessions[existing.sessionId] is before[existing.sessionId]
```

- [x] **Step 2: 验证 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_experiments.py -k "preserves_full_global_session_registry"
```

Expected: FAIL；旧实现创建实验会话时淘汰唯一的生产会话。

---

### Task 2: 实现显式 registry 注入

**Files:**
- Modify: `backend/app/sessions.py`
- Test: `backend/tests/test_sessions.py`
- Test: `backend/tests/test_session_concurrency.py`

**Interfaces:**
- Produces: `SessionRegistry(max_sessions: int | None = None)`。
- Produces: session 公共函数新增仅限关键字参数 `registry: SessionRegistry | None = None`。
- Preserves: `_sessions` 与 `_sessions_lock` 继续引用默认 registry 的容器和锁。

- [x] **Step 1: 增加 registry 类型和默认解析函数**

```python
@dataclass
class SessionRegistry:
    sessions: dict[str, DispatchSession] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock, repr=False)
    max_sessions: int | None = None


_default_session_registry = SessionRegistry()
_sessions = _default_session_registry.sessions
_sessions_lock = _default_session_registry.lock


def _session_registry(registry: SessionRegistry | None) -> SessionRegistry:
    return _default_session_registry if registry is None else registry
```

- [x] **Step 2: 让 registry helpers 只访问传入实例**

给 `_locked_session`、`_cleanup_sessions_locked`、`_cleanup_sessions` 和 `_publish_session` 增加 `registry` 参数。容量计算使用：

```python
capacity = MAX_SESSIONS if registry is _default_session_registry else registry.max_sessions
```

`registry.max_sessions is None` 表示不额外限制私有 registry；默认 registry 仍读取可被现有测试 monkeypatch 的 `MAX_SESSIONS`。

- [x] **Step 3: 向公共 session 操作传递 registry**

在 `create_session`、`get_session`、`list_sessions`、`delete_session`、`reset_session`、`add_task`、`add_blocked_cell`、`remove_blocked_cell`、`fail_robot`、`restore_robot` 和 `tick_session` 增加仅限关键字的 `registry` 参数，并把解析后的同一实例传给 helper。

- [x] **Step 4: 运行 session 回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_sessions.py backend/tests/test_session_concurrency.py
```

Expected: 全部通过；默认 registry 行为未改变。

---

### Task 3: 将在线压力实验绑定到私有 registry

**Files:**
- Modify: `backend/app/experiments.py`
- Modify: `backend/tests/test_experiments.py`
- Test: `backend/tests/test_experiments.py`

**Interfaces:**
- Consumes: `SessionRegistry` 及所有 session 操作的 `registry` 参数。
- Produces: 与原响应完全一致、但不接触默认 registry 的 `run_online_pressure_experiment`。

- [x] **Step 1: 创建并传递私有 registry**

在实验函数开始处创建：

```python
registry = SessionRegistry(max_sessions=1)
```

向创建、tick、任务插入、封锁、故障、恢复、解除封锁和删除操作传递 `registry=registry`。

- [x] **Step 2: 更新现有观察测试包装器**

现有 `record_experiment_tick` 接受并转发关键字 registry：

```python
def record_experiment_tick(session_id, request, *, registry=None):
    result = real_tick_session(session_id, request, registry=registry)
```

- [x] **Step 3: 验证 GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_experiments.py -k "online_pressure"
```

Expected: 新增隔离测试和既有在线压力测试全部通过。

- [x] **Step 4: 检查补丁**

Run:

```powershell
git diff --check
git diff -- backend/app/sessions.py backend/app/experiments.py backend/tests/test_experiments.py
```

Expected: 无空白错误；无路由、schema 或调度算法改动。
