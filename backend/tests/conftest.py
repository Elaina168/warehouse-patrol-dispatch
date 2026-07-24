from collections.abc import Iterator

import pytest

import backend.app.sessions as sessions_module


@pytest.fixture(autouse=True)
def clear_session_store() -> Iterator[None]:
    with sessions_module._sessions_lock:
        sessions_module._sessions.clear()
    yield
    with sessions_module._sessions_lock:
        sessions_module._sessions.clear()
