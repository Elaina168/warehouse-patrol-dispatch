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


def _return_large_value() -> str:
    return "large-completed-marker-" + "x" * 2_000_000


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


def test_shared_isolation_receives_large_completed_payload_before_join() -> None:
    result = run_isolated_process(_return_large_value, (), 5)
    assert result.outcome == "completed"
    assert isinstance(result.value, str)
    assert len(result.value) == 2_000_023
    assert result.value.startswith("large-completed-marker-")


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
    events: list[str] = []

    class FakeConnection:
        def __init__(self, label: str) -> None:
            self.label = label
            self.closed = False

        def poll(self, timeout: float) -> bool:
            assert timeout == 0.01
            events.append(f"{self.label}.poll")
            return False

        def close(self) -> None:
            events.append(f"{self.label}.close")
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True
            self.join_timeouts: list[float] = []
            self.kill_called = False
            self.closed = False

        def start(self) -> None:
            events.append("process.start")
            return None

        def is_alive(self) -> bool:
            events.append("process.is_alive")
            return self.alive

        def terminate(self) -> None:
            events.append("process.terminate")
            raise OSError("terminate failed")

        def join(self, timeout: float) -> None:
            events.append("process.join")
            self.join_timeouts.append(timeout)

        def kill(self) -> None:
            events.append("process.kill")
            self.kill_called = True
            self.alive = False

        def close(self) -> None:
            events.append("process.close")
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FakeConnection("parent")
            self.child_connection = FakeConnection("child")
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
    assert events == [
        "process.start",
        "child.close",
        "parent.poll",
        "process.is_alive",
        "process.terminate",
        "process.join",
        "process.is_alive",
        "process.kill",
        "process.join",
        "process.is_alive",
        "process.close",
        "parent.close",
    ]


def test_shared_isolation_escalates_join_error_after_process_stops(
    monkeypatch,
) -> None:
    events: list[str] = []

    class FakeConnection:
        def __init__(self, label: str) -> None:
            self.label = label

        def poll(self, timeout: float) -> bool:
            events.append(f"{self.label}.poll")
            return False

        def close(self) -> None:
            events.append(f"{self.label}.close")

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True

        def start(self) -> None:
            events.append("process.start")

        def is_alive(self) -> bool:
            events.append("process.is_alive")
            return self.alive

        def terminate(self) -> None:
            events.append("process.terminate")

        def join(self, timeout: float) -> None:
            events.append("process.join")
            self.alive = False
            raise RuntimeError("join failed")

        def kill(self) -> None:
            events.append("process.kill")

        def close(self) -> None:
            events.append("process.close")

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FakeConnection("parent")
            self.child_connection = FakeConnection("child")
            self.process = FakeProcess()

        def Pipe(self, duplex: bool):
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            return self.process

    context = FakeContext()
    monkeypatch.setattr(isolation_module.multiprocessing, "get_context", lambda method: context)

    with pytest.raises(
        BenchmarkInfrastructureError,
        match="等待子进程停止失败",
    ):
        run_isolated_process(_return_value, ("ok",), 0.01)

    assert events == [
        "process.start",
        "child.close",
        "parent.poll",
        "process.is_alive",
        "process.terminate",
        "process.join",
        "process.is_alive",
        "process.close",
        "parent.close",
    ]


def test_shared_isolation_escalates_process_close_error_and_closes_pipe(
    monkeypatch,
) -> None:
    events: list[str] = []

    class FakeConnection:
        def __init__(self, label: str) -> None:
            self.label = label

        def close(self) -> None:
            events.append(f"{self.label}.close")

    class FakeProcess:
        def start(self) -> None:
            events.append("process.start")
            raise RuntimeError("process start failed")

        def is_alive(self) -> bool:
            return False

        def close(self) -> None:
            events.append("process.close")
            raise RuntimeError("process close failed")

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FakeConnection("parent")
            self.child_connection = FakeConnection("child")
            self.process = FakeProcess()

        def Pipe(self, duplex: bool):
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            return self.process

    context = FakeContext()
    monkeypatch.setattr(isolation_module.multiprocessing, "get_context", lambda method: context)

    with pytest.raises(
        BenchmarkInfrastructureError,
        match="关闭子进程资源失败",
    ):
        run_isolated_process(_return_value, ("ok",), 5)

    assert events == [
        "process.start",
        "process.close",
        "parent.close",
        "child.close",
    ]


def test_shared_isolation_escalates_pipe_close_errors_after_other_cleanup(
    monkeypatch,
) -> None:
    events: list[str] = []

    class FailingConnection:
        def __init__(self, label: str) -> None:
            self.label = label

        def close(self) -> None:
            events.append(f"{self.label}.close")
            raise RuntimeError(f"{self.label} close failed")

    class FakeProcess:
        def start(self) -> None:
            events.append("process.start")

        def is_alive(self) -> bool:
            events.append("process.is_alive")
            return False

        def close(self) -> None:
            events.append("process.close")

    class FakeContext:
        def __init__(self) -> None:
            self.parent_connection = FailingConnection("parent")
            self.child_connection = FailingConnection("child")
            self.process = FakeProcess()

        def Pipe(self, duplex: bool):
            return self.parent_connection, self.child_connection

        def Process(self, *, target, args):
            return self.process

    context = FakeContext()
    monkeypatch.setattr(isolation_module.multiprocessing, "get_context", lambda method: context)

    with pytest.raises(
        BenchmarkInfrastructureError,
        match="关闭进程通信端点失败",
    ):
        run_isolated_process(_return_value, ("ok",), 5)

    assert events == [
        "process.start",
        "child.close",
        "process.is_alive",
        "process.close",
        "parent.close",
        "child.close",
    ]


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
