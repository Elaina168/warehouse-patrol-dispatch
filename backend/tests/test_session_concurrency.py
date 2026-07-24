from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, RLock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.app.schemas import AddTaskRequest, SessionTickRequest, Task
from backend.tests.helpers import scenario_payload


WAIT_TIMEOUT_SECONDS = 5


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
    with ThreadPoolExecutor(max_workers=1) as pool:
        deletion = pool.submit(sessions_module.delete_session, session_id)
        observed_lock.wait_for_blockers()
        replacement = sessions_module.DispatchSession(
            session_id=session_id,
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
        )
        with sessions_module._sessions_lock:
            sessions_module._sessions[session_id] = replacement
        observed_lock.release()
        with pytest.raises(HTTPException) as error:
            deletion.result(timeout=WAIT_TIMEOUT_SECONDS)
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
    with ThreadPoolExecutor(max_workers=1) as pool:
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
        with sessions_module._sessions_lock:
            sessions_module._sessions[session_id] = replacement
            sessions_module._sessions[expired.session_id] = expired
        monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 3)
        observed_lock.release()
        publishing.result(timeout=WAIT_TIMEOUT_SECONDS)

    with sessions_module._sessions_lock:
        assert sessions_module._sessions[session_id] is replacement
        assert sessions_module._sessions["incoming"] is incoming
        assert "expired" not in sessions_module._sessions


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
