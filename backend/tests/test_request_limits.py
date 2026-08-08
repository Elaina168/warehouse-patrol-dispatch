import asyncio
from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


APPROVED_MAX_REQUEST_BODY_BYTES = 2_097_152
AsgiMessage = dict[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


def _request(body: bytes, *, more_body: bool = False) -> AsgiMessage:
    return {
        "type": "http.request",
        "body": body,
        "more_body": more_body,
    }


def _response_status(messages: list[AsgiMessage]) -> int:
    return next(
        message["status"]
        for message in messages
        if message["type"] == "http.response.start"
    )


async def _invoke_middleware(
    request_messages: list[AsgiMessage],
    received_bodies: list[bytes],
    *,
    content_length: int | None = None,
) -> list[AsgiMessage]:
    try:
        middleware_type = import_module(
            "backend.app.request_limits"
        ).RequestBodyLimitMiddleware
    except (AttributeError, ModuleNotFoundError) as exc:
        pytest.fail(f"RequestBodyLimitMiddleware is unavailable: {exc}")

    pending_messages = list(request_messages)
    sent_messages: list[AsgiMessage] = []
    downstream_called = False

    async def receive() -> AsgiMessage:
        if not pending_messages:
            raise AssertionError("middleware read more request messages than provided")
        return pending_messages.pop(0)

    async def send(message: AsgiMessage) -> None:
        sent_messages.append(message)

    async def downstream(scope: dict[str, Any], receive: AsgiReceive, send: AsgiSend) -> None:
        nonlocal downstream_called
        downstream_called = True
        message = await receive()
        received_bodies.append(message.get("body", b""))
        assert message.get("more_body", False) is False
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers: list[tuple[bytes, bytes]] = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/test",
        "raw_path": b"/test",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8011),
    }
    middleware = middleware_type(downstream, APPROVED_MAX_REQUEST_BODY_BYTES)

    await middleware(scope, receive, send)

    if _response_status(sent_messages) == 413:
        assert downstream_called is False
    return sent_messages


def test_request_body_limit_accepts_exact_boundary() -> None:
    received: list[bytes] = []

    sent = asyncio.run(
        _invoke_middleware(
            [_request(b"a" * APPROVED_MAX_REQUEST_BODY_BYTES)],
            received,
        )
    )

    assert received == [b"a" * APPROVED_MAX_REQUEST_BODY_BYTES]
    assert _response_status(sent) == 204


def test_request_body_limit_rejects_content_length_over_boundary_without_reading() -> None:
    received: list[bytes] = []

    sent = asyncio.run(
        _invoke_middleware(
            [],
            received,
            content_length=APPROVED_MAX_REQUEST_BODY_BYTES + 1,
        )
    )

    assert received == []
    assert _response_status(sent) == 413


def test_request_body_limit_rejects_chunked_body_over_boundary() -> None:
    received: list[bytes] = []

    sent = asyncio.run(
        _invoke_middleware(
            [
                _request(b"a" * APPROVED_MAX_REQUEST_BODY_BYTES, more_body=True),
                _request(b"b"),
            ],
            received,
        )
    )

    assert received == []
    assert _response_status(sent) == 413


def test_request_body_limit_keeps_cors_outside_rejection_response() -> None:
    response = TestClient(app).post(
        "/api/dispatch",
        content=b"",
        headers={
            "Content-Length": str(APPROVED_MAX_REQUEST_BODY_BYTES + 1),
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:5174",
        },
    )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"
