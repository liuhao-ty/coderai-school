import asyncio
import unittest

from backend.app.request_limits import ReadConcurrencyMiddleware


class ReadConcurrencyMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def _run_requests(self, middleware, count: int, path: str) -> None:
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        async def invoke():
            await middleware(
                {"type": "http", "method": "GET", "path": path, "headers": []},
                receive,
                send,
            )

        await asyncio.gather(*(invoke() for _ in range(count)))

    async def test_limits_concurrent_read_requests(self):
        active = 0
        maximum = 0

        async def app(_scope, _receive, _send):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1

        middleware = ReadConcurrencyMiddleware(app, max_concurrent=3)
        await self._run_requests(middleware, count=12, path="/api/projects")

        self.assertEqual(maximum, 3)

    async def test_exempt_health_request_does_not_wait_for_read_slot(self):
        active = 0
        maximum = 0

        async def app(_scope, _receive, _send):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1

        middleware = ReadConcurrencyMiddleware(
            app,
            max_concurrent=1,
            exempt_paths={"/api/health/live"},
        )
        await self._run_requests(middleware, count=5, path="/api/health/live")

        self.assertEqual(maximum, 5)


if __name__ == "__main__":
    unittest.main()
