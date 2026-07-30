import multiprocessing
import pickle
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal


IsolatedOutcome = Literal["completed", "timeout", "error"]
_PROCESS_STOP_TIMEOUT_SECONDS = 1.0


class BenchmarkInfrastructureError(RuntimeError):
    """父进程无法可靠管理基准子进程时抛出。"""


@dataclass(frozen=True, slots=True)
class IsolatedExecution:
    outcome: IsolatedOutcome
    value: object | None
    error_type: str | None
    error_message: str | None
    wall_clock_ms: float


def sanitize_error_text(text: str) -> str:
    return text.replace("\r", "").replace("\n", "")[:500]


def _write_traceback(traceback_text: str) -> None:
    sys.stderr.write(traceback_text)
    sys.stderr.flush()


def _cleanup_isolated_resources(
    process,
    connections: tuple[object | None, ...],
    *,
    process_started: bool,
    force_stop: bool,
) -> None:
    resource_errors: list[str] = []
    fatal_resource_errors: list[str] = []

    def record_resource_error(label: str, *, fatal: bool = False) -> None:
        traceback_text = traceback.format_exc()
        _write_traceback(traceback_text)
        detail = f"{label}: {traceback_text.splitlines()[-1]}"
        resource_errors.append(detail)
        if fatal:
            fatal_resource_errors.append(detail)

    def process_is_alive() -> bool | None:
        try:
            return process.is_alive()
        except Exception:
            record_resource_error("检查子进程状态失败")
            return None

    def bounded_join() -> None:
        try:
            process.join(_PROCESS_STOP_TIMEOUT_SECONDS)
        except Exception:
            record_resource_error("等待子进程停止失败", fatal=True)

    stopped = process is None
    if process is not None:
        if not process_started:
            stopped = True
        else:
            alive = process_is_alive()
            stopped = alive is False
            if alive is True and not force_stop:
                bounded_join()
                alive = process_is_alive()
                stopped = alive is False
            if not stopped:
                try:
                    process.terminate()
                except Exception:
                    record_resource_error("终止子进程失败")
                bounded_join()
                alive = process_is_alive()
                stopped = alive is False
            if not stopped:
                try:
                    process.kill()
                except Exception:
                    record_resource_error("强制终止子进程失败")
                bounded_join()
                alive = process_is_alive()
                stopped = alive is False

        if stopped:
            try:
                process.close()
            except Exception:
                record_resource_error("关闭子进程资源失败", fatal=True)

    for connection in connections:
        if connection is None:
            continue
        try:
            connection.close()
        except Exception:
            record_resource_error("关闭进程通信端点失败", fatal=True)

    if process is not None and not stopped:
        details = "; ".join(resource_errors)
        suffix = f": {details}" if details else ""
        raise BenchmarkInfrastructureError(f"无法确认基准子进程已停止{suffix}")
    if fatal_resource_errors:
        raise BenchmarkInfrastructureError(
            "基准子进程资源清理失败: " + "; ".join(fatal_resource_errors)
        )


def _guarded_worker_entry(
    result_connection,
    worker_callable: Callable[..., object],
    worker_args: tuple[object, ...],
) -> None:
    try:
        result_connection.send(("completed", worker_callable(*worker_args)))
    except Exception as exc:
        traceback_text = traceback.format_exc()
        _write_traceback(traceback_text)
        result_connection.send(
            (
                "error",
                sanitize_error_text(type(exc).__name__),
                sanitize_error_text(str(exc)),
            )
        )
    finally:
        result_connection.close()


def run_isolated_process(
    worker_callable: Callable[..., object],
    worker_args: tuple[object, ...],
    timeout_seconds: float,
) -> IsolatedExecution:
    started_at = perf_counter()
    parent_connection = None
    child_connection = None
    process = None
    process_started = False
    execution: IsolatedExecution | None = None
    parent_error: tuple[str, str, str] | None = None
    pending_exception: BaseException | None = None
    try:
        pickle.dumps((worker_callable, worker_args))
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_guarded_worker_entry,
            args=(child_connection, worker_callable, worker_args),
        )
        process.start()
        process_started = True
        child_connection.close()
        child_connection = None
        payload_available = parent_connection.poll(timeout_seconds)
        wall_clock_ms = round((perf_counter() - started_at) * 1000, 2)
        if not payload_available:
            execution = IsolatedExecution(
                outcome="timeout",
                value=None,
                error_type="TimeoutError",
                error_message=None,
                wall_clock_ms=wall_clock_ms,
            )
        else:
            payload = parent_connection.recv()
            if not isinstance(payload, tuple) or not payload:
                raise ValueError("子进程返回了无效的隔离执行结果载荷")
            if payload[0] == "error" and len(payload) == 3:
                execution = IsolatedExecution(
                    outcome="error",
                    value=None,
                    error_type=payload[1],
                    error_message=sanitize_error_text(payload[2]),
                    wall_clock_ms=wall_clock_ms,
                )
            elif payload[0] == "completed" and len(payload) == 2:
                execution = IsolatedExecution(
                    outcome="completed",
                    value=payload[1],
                    error_type=None,
                    error_message=None,
                    wall_clock_ms=wall_clock_ms,
                )
            else:
                raise ValueError("子进程返回了未知的隔离执行结果载荷")
    except Exception as exc:
        parent_error = (type(exc).__name__, str(exc), traceback.format_exc())
    except BaseException as exc:
        pending_exception = exc
    finally:
        try:
            _cleanup_isolated_resources(
                process,
                (parent_connection, child_connection),
                process_started=process_started,
                force_stop=execution is None or execution.outcome == "timeout",
            )
        except BenchmarkInfrastructureError as cleanup_error:
            if parent_error is not None:
                _write_traceback(parent_error[2])
            if pending_exception is not None:
                raise cleanup_error from pending_exception
            raise
    if pending_exception is not None:
        raise pending_exception
    if parent_error is not None:
        _write_traceback(parent_error[2])
        return IsolatedExecution(
            outcome="error",
            value=None,
            error_type=parent_error[0],
            error_message=sanitize_error_text(parent_error[1]),
            wall_clock_ms=round((perf_counter() - started_at) * 1000, 2),
        )
    if execution is None:
        return IsolatedExecution(
            outcome="error",
            value=None,
            error_type="ChildProcessError",
            error_message="子进程未返回隔离执行结果",
            wall_clock_ms=round((perf_counter() - started_at) * 1000, 2),
        )
    return execution
