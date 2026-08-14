from __future__ import annotations

import asyncio
from collections.abc import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send


class ReadConcurrencyMiddleware:
    def __init__(self, app: ASGIApp, max_concurrent: int, exempt_paths: Iterable[str] = ()):
        self.app = app
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrent)))
        self._exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method", "").upper() not in {"GET", "HEAD"}
            or scope.get("path", "") in self._exempt_paths
        ):
            await self.app(scope, receive, send)
            return
        async with self._semaphore:
            await self.app(scope, receive, send)
