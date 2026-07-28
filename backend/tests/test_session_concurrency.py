from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock, RLock, Semaphore, local

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.app.schemas import AddTaskRequest, SessionTickRequest, Task
from backend.tests.helpers import scenario_payload


WAIT_TIMEOUT_SECONDS = 5
_lock_role = local()


def _create_session() -> str:
    response = TestClient(app).post("/api/sessions", json={"scenario": scenario_payload()})
    assert response.status_code == 200
    return response.json()["sessionId"]


def _task(task_id: str) -> AddTaskRequest:
    return AddTaskRequest(
        task=Task(
            id=task_id,
            type="inspection",
            title=task_id,
            priority=1,
            targets=[(1, 4)],
        )
    )


def _status(callable_) -> int:
    try:
        callable_()
    except HTTPException as error:
        return error.status_code
    return 200


def _wait(event: Event, description: str) -> None:
    assert event.wait(WAIT_TIMEOUT_SECONDS), f"等待超时：{description}"


def _run_with_lock_role(role: str, callable_, *args):
    _lock_role.value = role
    try:
        return callable_(*args)
    finally:
        del _lock_role.value


@contextmanager
def _registry_lock_for_competition():
    acquired = sessions_module._sessions_lock.acquire(timeout=WAIT_TIMEOUT_SECONDS)
    assert acquired, "等待超时：主线程获取会话注册表锁"
    try:
        yield
    finally:
        sessions_module._sessions_lock.release()


class ObservedRLock:
    def __init__(self, expected_blockers: int = 1) -> None:
        self._lock = RLock()
        self._state_lock = Lock()
        self.expected_blockers = expected_blockers
        self.blocking_wait_count = 0
        self.blocking_wait_started = Event()

    def acquire(self, blocking: bool = True) -> bool:
        if not blocking:
            return self._lock.acquire(blocking=False)
        if self._lock.acquire(blocking=False):
            return True
        with self._state_lock:
            self.blocking_wait_count += 1
            if self.blocking_wait_count >= self.expected_blockers:
                self.blocking_wait_started.set()
        return self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    def wait_for_blockers(self) -> None:
        _wait(
            self.blocking_wait_started,
            f"{self.expected_blockers} 个工作线程尝试获取会话锁",
        )


class RoleControlledRLock:
    def __init__(self) -> None:
        self._lock = RLock()
        self._state_lock = Lock()
        self._wait_counts: dict[str, int] = {}
        self._wait_events: dict[tuple[str, int], Event] = {}
        self._permits: dict[str, Semaphore] = {}
        self._acquisition_state = local()
        self.delete_holds_lock = Event()
        self.allow_delete_release = Event()

    def acquire(self, blocking: bool = True) -> bool:
        if not blocking:
            return self._lock.acquire(blocking=False)
        if self._lock.acquire(blocking=False):
            return True

        role = _lock_role.value
        with self._state_lock:
            wait_count = self._wait_counts.get(role, 0) + 1
            self._wait_counts[role] = wait_count
            wait_event = self._wait_events.setdefault((role, wait_count), Event())
            permit = self._permits.setdefault(role, Semaphore(0))
        wait_event.set()
        assert permit.acquire(timeout=WAIT_TIMEOUT_SECONDS), f"等待超时：允许 {role} 获取会话锁"
        acquired = self._lock.acquire(timeout=WAIT_TIMEOUT_SECONDS)
        assert acquired, f"等待超时：{role} 获取会话锁"
        self._acquisition_state.hold_delete_release = role == "delete" and wait_count == 1
        return True

    def release(self) -> None:
        if getattr(self._acquisition_state, "hold_delete_release", False):
            self.delete_holds_lock.set()
            _wait(self.allow_delete_release, "允许 delete 释放会话锁")
            self._acquisition_state.hold_delete_release = False
        self._lock.release()

    def wait_for_blocker(self, role: str, wait_count: int) -> None:
        with self._state_lock:
            wait_event = self._wait_events.setdefault((role, wait_count), Event())
        _wait(wait_event, f"{role} 第 {wait_count} 次等待会话锁")

    def allow(self, role: str) -> None:
        with self._state_lock:
            permit = self._permits.setdefault(role, Semaphore(0))
        permit.release()

    def unblock_all(self) -> None:
        self.allow_delete_release.set()
        for role in ("delete", "publish"):
            for _ in range(4):
                self.allow(role)


def _observed_session_lock(session_id: str, expected_blockers: int) -> ObservedRLock:
    with sessions_module._sessions_lock:
        observed_lock = ObservedRLock(expected_blockers)
        sessions_module._sessions[session_id].lock = observed_lock
    return observed_lock


def test_same_session_duplicate_task_requests_are_serialized() -> None:
    session_id = _create_session()
    observed_lock = _observed_session_lock(session_id, expected_blockers=2)
    lock_held = True
    observed_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    _status,
                    lambda: sessions_module.add_task(session_id, _task("DUP")),
                )
                for _ in range(2)
            ]
            observed_lock.wait_for_blockers()
            observed_lock.release()
            lock_held = False
            statuses = sorted(future.result(timeout=WAIT_TIMEOUT_SECONDS) for future in futures)
    finally:
        if lock_held:
            observed_lock.release()

    assert statuses == [200, 409]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert [task.id for task in session.scenario.tasks].count("DUP") == 1


def test_session_capacity_check_and_append_are_atomic(monkeypatch) -> None:
    session_id = _create_session()
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        current_count = len(sessions_module._all_known_tasks(session))
    monkeypatch.setattr(sessions_module, "MAX_SESSION_TASKS", current_count + 1)
    observed_lock = _observed_session_lock(session_id, expected_blockers=2)
    lock_held = True
    observed_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                _status,
                lambda: sessions_module.add_task(session_id, _task("CAP-A")),
            )
            second = pool.submit(
                _status,
                lambda: sessions_module.add_task(session_id, _task("CAP-B")),
            )
            observed_lock.wait_for_blockers()
            observed_lock.release()
            lock_held = False
            statuses = sorted(
                [
                    first.result(timeout=WAIT_TIMEOUT_SECONDS),
                    second.result(timeout=WAIT_TIMEOUT_SECONDS),
                ]
            )
    finally:
        if lock_held:
            observed_lock.release()

    assert statuses == [200, 422]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert len(sessions_module._all_known_tasks(session)) == current_count + 1


def test_concurrent_ticks_never_move_time_backward() -> None:
    session_id = _create_session()
    observed_lock = _observed_session_lock(session_id, expected_blockers=2)
    lock_held = True
    observed_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                _status,
                lambda: sessions_module.tick_session(
                    session_id,
                    SessionTickRequest(currentTime=1),
                ),
            )
            second = pool.submit(
                _status,
                lambda: sessions_module.tick_session(
                    session_id,
                    SessionTickRequest(currentTime=2),
                ),
            )
            observed_lock.wait_for_blockers()
            observed_lock.release()
            lock_held = False
            statuses = sorted(
                [
                    first.result(timeout=WAIT_TIMEOUT_SECONDS),
                    second.result(timeout=WAIT_TIMEOUT_SECONDS),
                ]
            )
    finally:
        if lock_held:
            observed_lock.release()

    assert statuses in ([200, 200], [200, 422])
    assert sessions_module.get_session(session_id).currentTime == 2


def test_waiting_for_session_a_does_not_block_session_b() -> None:
    session_a_id = _create_session()
    session_b_id = _create_session()
    observed_lock = _observed_session_lock(session_a_id, expected_blockers=1)
    lock_held = True
    observed_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            waiting_a = pool.submit(sessions_module.get_session, session_a_id)
            observed_lock.wait_for_blockers()
            completed_b = pool.submit(sessions_module.get_session, session_b_id)
            assert completed_b.result(timeout=WAIT_TIMEOUT_SECONDS).sessionId == session_b_id
            observed_lock.release()
            lock_held = False
            assert waiting_a.result(timeout=WAIT_TIMEOUT_SECONDS).sessionId == session_a_id
    finally:
        if lock_held:
            observed_lock.release()


def test_tick_and_reset_leave_one_complete_state() -> None:
    session_id = _create_session()
    observed_lock = _observed_session_lock(session_id, expected_blockers=2)
    lock_held = True
    observed_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            tick = pool.submit(
                sessions_module.tick_session,
                session_id,
                SessionTickRequest(currentTime=1),
            )
            reset = pool.submit(sessions_module.reset_session, session_id)
            observed_lock.wait_for_blockers()
            observed_lock.release()
            lock_held = False
            tick.result(timeout=WAIT_TIMEOUT_SECONDS)
            reset.result(timeout=WAIT_TIMEOUT_SECONDS)
    finally:
        if lock_held:
            observed_lock.release()

    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert all(
            len(path) == session.current_time + 1
            for path in session.robot_path_history.values()
        )
        if session.current_time == 0:
            assert session.runtime_task_count == 0
            assert session.runtime_blocked_cells == []
            assert session.runtime_failed_robot_ids == []
        else:
            assert session.current_time == 1


def test_delete_waits_for_an_already_running_tick(monkeypatch) -> None:
    session_id = _create_session()
    observed_lock = _observed_session_lock(session_id, expected_blockers=1)
    entered = Event()
    release = Event()
    real_advance = sessions_module._advance_session

    def blocking_advance(session, target_time):
        entered.set()
        _wait(release, "允许正在执行的 tick 继续")
        return real_advance(session, target_time)

    monkeypatch.setattr(sessions_module, "_advance_session", blocking_advance)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tick = pool.submit(
            sessions_module.tick_session,
            session_id,
            SessionTickRequest(currentTime=1),
        )
        _wait(entered, "tick 进入推进阶段")
        delete = pool.submit(sessions_module.delete_session, session_id)
        observed_lock.wait_for_blockers()
        release.set()
        assert tick.result(timeout=WAIT_TIMEOUT_SECONDS).currentTime == 1
        assert delete.result(timeout=WAIT_TIMEOUT_SECONDS).deleted is True

    with pytest.raises(HTTPException) as error:
        sessions_module.get_session(session_id)
    assert error.value.status_code == 404


def test_list_waits_for_complete_task_append(monkeypatch) -> None:
    session_id = _create_session()
    observed_lock = _observed_session_lock(session_id, expected_blockers=1)
    entered = Event()
    release = Event()
    real_invalidate = sessions_module._invalidate_plan

    def blocking_invalidate(session):
        real_invalidate(session)
        entered.set()
        _wait(release, "允许任务追加完成")

    monkeypatch.setattr(sessions_module, "_invalidate_plan", blocking_invalidate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        add = pool.submit(sessions_module.add_task, session_id, _task("ATOMIC"))
        _wait(entered, "任务追加进入失效计划阶段")
        listed = pool.submit(sessions_module.list_sessions)
        observed_lock.wait_for_blockers()
        release.set()
        assert add.result(timeout=WAIT_TIMEOUT_SECONDS).runtimeTaskCount == 1
        summary = next(
            item
            for item in listed.result(timeout=WAIT_TIMEOUT_SECONDS)
            if item.sessionId == session_id
        )
    assert summary.runtimeTaskCount == 1


def test_delete_never_removes_replacement_with_same_id() -> None:
    session_id = _create_session()
    with sessions_module._sessions_lock:
        original = sessions_module._sessions[session_id]
        observed_lock = ObservedRLock()
        original.lock = observed_lock
    observed_lock.acquire()
    lock_held = True
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        deletion = pool.submit(sessions_module.delete_session, session_id)
        observed_lock.wait_for_blockers()
        replacement = sessions_module.DispatchSession(
            session_id=session_id,
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
        )
        with _registry_lock_for_competition():
            sessions_module._sessions[session_id] = replacement
        observed_lock.release()
        lock_held = False
        with pytest.raises(HTTPException) as error:
            deletion.result(timeout=WAIT_TIMEOUT_SECONDS)
    finally:
        if lock_held:
            observed_lock.release()
        pool.shutdown(wait=True)
    assert error.value.status_code == 404
    with sessions_module._sessions_lock:
        assert sessions_module._sessions[session_id] is replacement


def test_capacity_publish_rechecks_identity_capacity_and_ttl(monkeypatch) -> None:
    clock = {"now": sessions_module.SESSION_TTL_SECONDS + 100}
    monkeypatch.setattr(sessions_module, "_session_now", lambda: clock["now"])
    session_id = _create_session()
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    with sessions_module._sessions_lock:
        original = sessions_module._sessions[session_id]
        observed_lock = ObservedRLock()
        original.lock = observed_lock
        original.last_accessed_at = 0
    incoming = sessions_module.DispatchSession(
        session_id="incoming",
        scenario=original.scenario.model_copy(deep=True),
        options=original.options.model_copy(deep=True),
    )
    observed_lock.acquire()
    lock_held = True
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        publishing = pool.submit(sessions_module._publish_session, incoming)
        observed_lock.wait_for_blockers()
        replacement = sessions_module.DispatchSession(
            session_id=session_id,
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
            last_accessed_at=clock["now"],
        )
        expired = sessions_module.DispatchSession(
            session_id="expired",
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
            last_accessed_at=0,
        )
        with _registry_lock_for_competition():
            sessions_module._sessions[session_id] = replacement
            sessions_module._sessions[expired.session_id] = expired
        monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 3)
        observed_lock.release()
        lock_held = False
        publishing.result(timeout=WAIT_TIMEOUT_SECONDS)
    finally:
        if lock_held:
            observed_lock.release()
        pool.shutdown(wait=True)

    with sessions_module._sessions_lock:
        assert sessions_module._sessions[session_id] is replacement
        assert sessions_module._sessions["incoming"] is incoming
        assert "expired" not in sessions_module._sessions


def test_publish_never_clears_closing_owned_by_concurrent_delete(monkeypatch) -> None:
    session_id = _create_session()
    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    controlled_lock = RoleControlledRLock()
    with sessions_module._sessions_lock:
        original = sessions_module._sessions[session_id]
        original.lock = controlled_lock
    incoming = sessions_module.DispatchSession(
        session_id="incoming",
        scenario=original.scenario.model_copy(deep=True),
        options=original.options.model_copy(deep=True),
    )

    publish_retry_entered = Event()
    allow_publish_retry = Event()
    observed_retry_closing: list[bool] = []
    publish_cleanup_count = 0
    real_cleanup = sessions_module._cleanup_sessions_locked

    def observed_cleanup(now=None):
        nonlocal publish_cleanup_count
        real_cleanup(now)
        if getattr(_lock_role, "value", None) != "publish":
            return
        publish_cleanup_count += 1
        if publish_cleanup_count == 2:
            observed_retry_closing.append(original.closing)
            publish_retry_entered.set()
            _wait(allow_publish_retry, "允许 publisher 完成首次等待后的重试")

    monkeypatch.setattr(sessions_module, "_cleanup_sessions_locked", observed_cleanup)
    controlled_lock.acquire()
    initial_lock_held = True
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        deletion = pool.submit(
            _run_with_lock_role,
            "delete",
            sessions_module.delete_session,
            session_id,
        )
        controlled_lock.wait_for_blocker("delete", 1)

        with pytest.raises(HTTPException) as error:
            sessions_module.get_session(session_id)
        assert error.value.status_code == 404

        publishing = pool.submit(
            _run_with_lock_role,
            "publish",
            sessions_module._publish_session,
            incoming,
        )
        controlled_lock.wait_for_blocker("publish", 1)
        controlled_lock.release()
        initial_lock_held = False
        controlled_lock.allow("publish")
        assert publish_retry_entered.wait(WAIT_TIMEOUT_SECONDS), (
            "等待超时：publisher 进入首次等待后的重试；"
            f"future={publishing.exception() if publishing.done() else 'running'}"
        )

        controlled_lock.allow("delete")
        _wait(controlled_lock.delete_holds_lock, "delete 持有会话锁")
        allow_publish_retry.set()
        controlled_lock.wait_for_blocker("publish", 2)
        controlled_lock.allow_delete_release.set()

        assert deletion.result(timeout=WAIT_TIMEOUT_SECONDS).deleted is True
        controlled_lock.allow("publish")
        publishing.result(timeout=WAIT_TIMEOUT_SECONDS)
    finally:
        allow_publish_retry.set()
        controlled_lock.unblock_all()
        if initial_lock_held:
            controlled_lock.release()
        pool.shutdown(wait=True)

    assert observed_retry_closing == [True]
    with sessions_module._sessions_lock:
        assert session_id not in sessions_module._sessions
        assert sessions_module._sessions["incoming"] is incoming


def test_future_block_candidate_never_reopens_session_during_concurrent_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = _create_session()
    observed_lock = _observed_session_lock(session_id, expected_blockers=1)
    with sessions_module._sessions_lock:
        session = sessions_module._sessions[session_id]

    commit_entered = Event()
    allow_commit = Event()
    observed_closing: list[bool] = []
    observed_request_statuses: list[int] = []
    real_commit = sessions_module._commit_session_candidate

    def paused_commit(authoritative, candidate, observations):
        commit_entered.set()
        _wait(allow_commit, "允许 future block 提交候选")
        real_commit(authoritative, candidate, observations)
        observed_closing.append(authoritative.closing)
        observed_request_statuses.append(
            _status(lambda: sessions_module.get_session(session_id))
        )

    monkeypatch.setattr(
        sessions_module,
        "_commit_session_candidate",
        paused_commit,
    )
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        future_block = pool.submit(
            sessions_module.add_blocked_cell,
            session_id,
            sessions_module.AddBlockRequest(
                cell=(1, 1),
                currentTime=2,
            ),
        )
        _wait(commit_entered, "future block 构造待提交候选")
        deletion = pool.submit(sessions_module.delete_session, session_id)
        observed_lock.wait_for_blockers()
        with sessions_module._sessions_lock:
            assert session.closing is True
        allow_commit.set()

        block_result = future_block.result(timeout=WAIT_TIMEOUT_SECONDS)
        delete_result = deletion.result(timeout=WAIT_TIMEOUT_SECONDS)
    finally:
        allow_commit.set()
        pool.shutdown(wait=True)

    assert block_result.currentTime == 2
    assert delete_result.deleted is True
    assert observed_closing == [True]
    assert observed_request_statuses == [404]
    with sessions_module._sessions_lock:
        assert session_id not in sessions_module._sessions


def test_ttl_cleanup_skips_busy_expired_session_until_next_cleanup() -> None:
    session_id = _create_session()
    with sessions_module._sessions_lock:
        session = sessions_module._sessions[session_id]
        session.last_accessed_at = 0
    session.lock.acquire()
    try:
        sessions_module._cleanup_sessions(sessions_module.SESSION_TTL_SECONDS + 1)
        with sessions_module._sessions_lock:
            assert sessions_module._sessions[session_id] is session
    finally:
        session.lock.release()
    sessions_module._cleanup_sessions(sessions_module.SESSION_TTL_SECONDS + 1)
    with sessions_module._sessions_lock:
        assert session_id not in sessions_module._sessions
