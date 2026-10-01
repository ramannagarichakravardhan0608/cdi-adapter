"""Bound the actual HTTP body before multipart parsing, including chunked uploads."""

from tempfile import SpooledTemporaryFile

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from cdi_adapter.config import get_settings


class RequestSizeLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        limit = get_settings().request_max_bytes
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse({"detail": "invalid Content-Length"}, 400)(scope, receive, send)
            return
        if length > limit or length < 0:
            await JSONResponse({"detail": "request exceeds size limit"}, 413)(scope, receive, send)
            return
        # Spill to a bounded temporary file so chunked requests cannot bypass the header check.
        with SpooledTemporaryFile(max_size=1024 * 1024) as body:
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    await JSONResponse({"detail": "request exceeds size limit"}, 413)(
                        scope, receive, send
                    )
                    return
                await run_in_threadpool(body.write, chunk)
                if not message.get("more_body", False):
                    break
            body.seek(0)
            remaining = size
            delivered = False

            async def bounded_receive():
                nonlocal remaining, delivered
                if delivered:
                    return await receive()
                if remaining:
                    data = await run_in_threadpool(body.read, 64 * 1024)
                    remaining -= len(data)
                    delivered = remaining == 0
                    return {"type": "http.request", "body": data, "more_body": remaining > 0}
                delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}

            await self.app(scope, bounded_receive, send)
