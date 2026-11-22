"""ASGI middleware: request IDs + access log, and CSRF protection for state-changing requests.

Pure ASGI (not BaseHTTPMiddleware) so streaming responses such as SSE pass through untouched.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.logging import bind_context, clear_context, get_logger

log = get_logger("src.api.access")

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Assigns a request ID (or accepts a well-formed incoming one), logs every request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        clear_context()
        bind_context(request_id=request_id)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            clear_context()


class CsrfMiddleware:
    """State-changing /api requests must carry a custom header.

    Browsers cannot add custom headers to cross-site form posts, and cross-origin fetches with
    them need a CORS preflight that this API never grants. Together with SameSite=Strict
    session cookies this blocks cross-site request forgery.
    """

    def __init__(self, app: ASGIApp, header_name: str) -> None:
        self.app = app
        self.header = header_name.lower().encode("latin-1")
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in _SAFE_METHODS
            and scope["path"].startswith("/api/")
            and not dict(scope["headers"]).get(self.header)
        ):
            body = json.dumps(
                {
                    "error": {
                        "code": "csrf_header_missing",
                        "message": (
                            f"state-changing requests must send the {self.header_name} header"
                        ),
                    }
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
