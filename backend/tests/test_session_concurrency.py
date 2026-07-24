from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, RLock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.app.sessions as sessions_module
from backend.app.main import app
from backend.app.schemas import AddTaskRequest, SessionTickRequest, Task
from backend.tests.helpers import scenario_payload


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


def test_same_session_duplicate_task_requests_are_serialized() -> None:
    session_id = _create_session()
    with sessions_module._sessions_lock:
        session = sessions_module._sessions[session_id]
    start = Barrier(3)
    lock_held = True
    session.lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    lambda: (
                        start.wait(),
                        _status(lambda: sessions_module.add_task(session_id, _task("DUP"))),
                    )[1]
                )
                for _ in range(2)
            ]
            start.wait()
            session.lock.release()
            lock_held = False
            statuses = sorted(future.result(timeout=5) for future in futures)
    finally:
        if lock_held:
            session.lock.release()

    assert statuses == [200, 409]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert [task.id for task in session.scenario.tasks].count("DUP") == 1


def test_session_capacity_check_and_append_are_atomic(monkeypatch) -> None:
    session_id = _create_session()
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        current_count = len(sessions_module._all_known_tasks(session))
    monkeypatch.setattr(
        sessions_module,
        "MAX_SESSION_TASKS",
        current_count + 1,
    )
    start = Barrier(3)
    with sessions_module._sessions_lock:
        session = sessions_module._sessions[session_id]
    lock_held = True
    session.lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                lambda: (
                    start.wait(),
                    _status(
                        lambda: sessions_module.add_task(
                            session_id,
                            _task("CAP-A"),
                        )
                    ),
                )[1]
            )
            second = pool.submit(
                lambda: (
                    start.wait(),
                    _status(
                        lambda: sessions_module.add_task(
                            session_id,
                            _task("CAP-B"),
                        )
                    ),
                )[1]
            )
            start.wait()
            session.lock.release()
            lock_held = False
            statuses = sorted(
                [first.result(timeout=10), second.result(timeout=10)]
            )
    finally:
        if lock_held:
            session.lock.release()

    assert statuses == [200, 422]
    with sessions_module._locked_session(session_id, touch_access=False) as session:
        assert len(sessions_module._all_known_tasks(session)) == current_count + 1


def test_concurrent_ticks_never_move_time_backward() -> None:
    session_id = _create_session()
    start = Barrier(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            lambda: (start.wait(), _status(
                lambda: sessions_module.tick_session(
                    session_id, SessionTickRequest(currentTime=1)
                )
            ))[1]
        )
        second = pool.submit(
            lambda: (start.wait(), _status(
                lambda: sessions_module.tick_session(
                    session_id, SessionTickRequest(currentTime=2)
                )
            ))[1]
        )
        start.wait()
        statuses = sorted([first.result(timeout=10), second.result(timeout=10)])

    assert statuses in ([200, 200], [200, 422])
    assert sessions_module.get_session(session_id).currentTime == 2


def test_waiting_for_session_a_does_not_block_session_b() -> None:
    session_a_id = _create_session()
    session_b_id = _create_session()
    with sessions_module._sessions_lock:
        session_a = sessions_module._sessions[session_a_id]
    started = Barrier(2)
    lock_held = True
    session_a.lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            waiting_a = pool.submit(
                lambda: (started.wait(), sessions_module.get_session(session_a_id))[1]
            )
            started.wait()
            completed_b = pool.submit(sessions_module.get_session, session_b_id)
            assert completed_b.result(timeout=5).sessionId == session_b_id
            session_a.lock.release()
            lock_held = False
            assert waiting_a.result(timeout=5).sessionId == session_a_id
    finally:
        if lock_held:
            session_a.lock.release()


def test_tick_and_reset_leave_one_complete_state() -> None:
    session_id = _create_session()
    start = Barrier(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tick = pool.submit(
            lambda: (
                start.wait(),
                sessions_module.tick_session(
                    session_id,
                    SessionTickRequest(currentTime=1),
                ),
            )[1]
        )
        reset = pool.submit(
            lambda: (start.wait(), sessions_module.reset_session(session_id))[1]
        )
        start.wait()
        tick.result(timeout=10)
        reset.result(timeout=10)

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
    entered = Barrier(2)
    release = Barrier(2)
    real_advance = sessions_module._advance_session

    def blocking_advance(session, target_time):
        entered.wait()
        release.wait()
        return real_advance(session, target_time)

    monkeypatch.setattr(sessions_module, "_advance_session", blocking_advance)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tick = pool.submit(
            sessions_module.tick_session,
            session_id,
            SessionTickRequest(currentTime=1),
        )
        entered.wait()
        delete = pool.submit(sessions_module.delete_session, session_id)
        release.wait()
        assert tick.result(timeout=10).currentTime == 1
        assert delete.result(timeout=10).deleted is True

    with pytest.raises(HTTPException) as error:
        sessions_module.get_session(session_id)
    assert error.value.status_code == 404


def test_list_waits_for_complete_task_append(monkeypatch) -> None:
    session_id = _create_session()
    entered = Barrier(2)
    release = Barrier(2)
    real_invalidate = sessions_module._invalidate_plan

    def blocking_invalidate(session):
        real_invalidate(session)
        entered.wait()
        release.wait()

    monkeypatch.setattr(sessions_module, "_invalidate_plan", blocking_invalidate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        add = pool.submit(sessions_module.add_task, session_id, _task("ATOMIC"))
        entered.wait()
        listed = pool.submit(sessions_module.list_sessions)
        release.wait()
        assert add.result(timeout=10).runtimeTaskCount == 1
        summary = next(
            item
            for item in listed.result(timeout=10)
            if item.sessionId == session_id
        )
    assert summary.runtimeTaskCount == 1


class ObservedRLock:
    def __init__(self) -> None:
        self._lock = RLock()
        self.blocking_wait_started = Barrier(2)

    def acquire(self, blocking: bool = True) -> bool:
        if not blocking:
            return self._lock.acquire(blocking=False)
        if self._lock.acquire(blocking=False):
            return True
        self.blocking_wait_started.wait()
        return self._lock.acquire()

    def release(self) -> None:
        self._lock.release()


def test_delete_never_removes_replacement_with_same_id() -> None:
    session_id = _create_session()
    with sessions_module._sessions_lock:
        original = sessions_module._sessions[session_id]
        observed_lock = ObservedRLock()
        original.lock = observed_lock
    observed_lock.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        deletion = pool.submit(sessions_module.delete_session, session_id)
        observed_lock.blocking_wait_started.wait()
        replacement = sessions_module.DispatchSession(
            session_id=session_id,
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
        )
        with sessions_module._sessions_lock:
            sessions_module._sessions[session_id] = replacement
        observed_lock.release()
        with pytest.raises(HTTPException) as error:
            deletion.result(timeout=5)
    assert error.value.status_code == 404
    with sessions_module._sessions_lock:
        assert sessions_module._sessions[session_id] is replacement


def test_capacity_publish_rechecks_identity_and_capacity(monkeypatch) -> None:
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
        observed_lock.blocking_wait_started.wait()
        replacement = sessions_module.DispatchSession(
            session_id=session_id,
            scenario=original.scenario.model_copy(deep=True),
            options=original.options.model_copy(deep=True),
            last_accessed_at=1,
        )
        with sessions_module._sessions_lock:
            sessions_module._sessions[session_id] = replacement
        monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 2)
        observed_lock.release()
        publishing.result(timeout=5)

    with sessions_module._sessions_lock:
        assert sessions_module._sessions[session_id] is replacement
        assert sessions_module._sessions["incoming"] is incoming


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
