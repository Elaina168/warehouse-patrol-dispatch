from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                content_length = int(value)
            except ValueError:
                continue
            if content_length > self.max_body_size:
                await self._reject(scope, receive, send)
                return

        body_parts: list[bytes] = []
        body_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                await self.app(scope, _replay_receive(message, receive), send)
                return
            if message["type"] != "http.request":
                await self.app(scope, _replay_receive(message, receive), send)
                return

            body = message.get("body", b"")
            body_size += len(body)
            if body_size > self.max_body_size:
                await self._reject(scope, receive, send)
                return
            body_parts.append(body)
            if not message.get("more_body", False):
                break

        complete_message: Message = {
            "type": "http.request",
            "body": b"".join(body_parts),
            "more_body": False,
        }
        await self.app(scope, _replay_receive(complete_message, receive), send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = PlainTextResponse("Request body too large", status_code=413)
        await response(scope, receive, send)


def _replay_receive(first_message: Message, receive: Receive) -> Receive:
    replayed = False

    async def replay() -> Message:
        nonlocal replayed
        if replayed:
            return await receive()
        replayed = True
        return first_message

    return replay
