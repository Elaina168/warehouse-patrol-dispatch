import multiprocessing
import time

import pytest

from backend.benchmarks import process_isolation as isolation_module
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
)


def _return_value(value: str) -> str:
    return value


def _raise_error() -> str:
    raise RuntimeError("worker failed\r\nwith detail")


def _sleep() -> None:
    time.sleep(2)


class _UnpicklableArgument:
    def __reduce__(self):
        raise RuntimeError("worker argument serialization failed")


def _active_child_pids() -> set[int]:
    return {
        process.pid
        for process in multiprocessing.active_children()
        if process.pid is not None
    }


def test_shared_isolation_returns_completed_value() -> None:
    result = run_isolated_process(_return_value, ("ok",), 5)
    assert result.outcome == "completed"
    assert result.value == "ok"
    assert result.error_type is None
    assert result.wall_clock_ms >= 0


def test_shared_isolation_sanitizes_worker_error() -> None:
    result = run_isolated_process(_raise_error, (), 5)
    assert result.outcome == "error"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "worker failedwith detail"


def test_shared_isolation_times_out_without_worker_residue() -> None:
    children_before = _active_child_pids()
    result = run_isolated_process(_sleep, (), 0.05)
    assert result.outcome == "timeout"
    assert result.error_type == "TimeoutError"
    assert _active_child_pids() <= children_before


def test_shared_isolation_validates_worker_and_argument_tuple_before_spawn() -> None:
    children_before = _active_child_pids()
    result = run_isolated_process(
        _return_value,
        (_UnpicklableArgument(),),
        5,
    )
    assert result.outcome == "error"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "worker argument serialization failed"
    assert _active_child_pids() <= children_before


def test_shared_isolation_cleans_resources_after_start_error(
    monkeypatch,
) -> None:
    class FailingConnection:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FailingProcess:
        def __init__(self) -> None:
            self.closed = False

        def start(self) -> None:
            raise RuntimeError("process start failed")

        def is_alive(self) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    class FailingContext:
        def __init__(self) -> None:
            self.parent_connection = FailingConnection()
            self.child_connection = FailingConnection()
            self.process = FailingProcess()

        def Pipe(self, duplex: bool):
            assert duplex is False
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            assert target is isolation_module._guarded_worker_entry
            assert args[0] is self.child_connection
            return self.process

    context = FailingContext()
    monkeypatch.setattr(isolation_module.multiprocessing, "get_context", lambda method: context)

    result = run_isolated_process(_return_value, ("ok",), 5)

    assert result.outcome == "error"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "process start failed"
    assert context.process.closed is True
    assert context.parent_connection.closed is True
    assert context.child_connection.closed is True


def test_shared_isolation_uses_kill_fallback_after_terminate_error(
    monkeypatch,
) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def poll(self, timeout: float) -> bool:
            assert timeout == 0.01
            return False

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True
            self.join_timeouts: list[float] = []
            self.kill_called = False
            self.closed = False

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            raise OSError("terminate failed")

        def join(self, timeout: float) -> None:
            self.join_timeouts.append(timeout)

        def kill(self) -> None:
            self.kill_called = True
            self.alive = False

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FakeConnection()
            self.child_connection = FakeConnection()
            self.process = FakeProcess()

        def Pipe(self, duplex: bool):
            assert duplex is False
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            return self.process

    context = FakeContext()
    monkeypatch.setattr(isolation_module.multiprocessing, "get_context", lambda method: context)

    result = run_isolated_process(_return_value, ("ok",), 0.01)

    assert result.outcome == "timeout"
    assert context.process.kill_called is True
    assert context.process.join_timeouts
    assert all(timeout > 0 for timeout in context.process.join_timeouts)
    assert context.process.closed is True
    assert context.parent_connection.closed is True
    assert context.child_connection.closed is True


def test_shared_isolation_propagates_unstoppable_child_cleanup_failure(
    monkeypatch,
) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def poll(self, timeout: float) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.join_timeouts: list[float] = []
            self.closed = False

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            return None

        def join(self, timeout: float) -> None:
            self.join_timeouts.append(timeout)

        def kill(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FakeConnection()
            self.child_connection = FakeConnection()
            self.process = FakeProcess()

        def Pipe(self, duplex: bool):
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            return self.process

    context = FakeContext()
    monkeypatch.setattr(isolation_module.multiprocessing, "get_context", lambda method: context)

    with pytest.raises(
        BenchmarkInfrastructureError,
        match="无法确认基准子进程已停止",
    ):
        run_isolated_process(_return_value, ("ok",), 0.01)

    assert len(context.process.join_timeouts) == 2
    assert all(timeout > 0 for timeout in context.process.join_timeouts)
    assert context.process.closed is False
    assert context.parent_connection.closed is True
    assert context.child_connection.closed is True
