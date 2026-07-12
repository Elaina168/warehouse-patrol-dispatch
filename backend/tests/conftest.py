from collections.abc import Iterator

import pytest

import backend.app.sessions as sessions_module


@pytest.fixture(autouse=True)
def clear_session_store() -> Iterator[None]:
    sessions_module._sessions.clear()
    yield
    sessions_module._sessions.clear()
