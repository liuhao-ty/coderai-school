from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass
class RequestResult:
    operation: str
    status: int
    elapsed_ms: float
    ok: bool
    error: str = ""


class Recorder:
    def __init__(self) -> None:
        self.results: list[RequestResult] = []
        self._lock = asyncio.Lock()

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        operation: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response | None:
        started = time.perf_counter()
        response: httpx.Response | None = None
        error = ""
        try:
            response = await client.request(method, path, headers=headers, json=payload)
            ok = 200 <= response.status_code < 300
            if not ok:
                error = response.text[:300]
        except Exception as exc:
            ok = False
            error = str(exc)[:300]
        elapsed_ms = (time.perf_counter() - started) * 1000
        result = RequestResult(
            operation=operation,
            status=response.status_code if response is not None else 0,
            elapsed_ms=elapsed_ms,
            ok=ok,
            error=error,
        )
        async with self._lock:
            self.results.append(result)
        return response if ok else None


def expand_accounts(raw_accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    for raw in raw_accounts:
        count = int(raw.get("count") or 1)
        start = int(raw.get("start") or 1)
        pattern = str(raw.get("username_pattern") or "")
        if count < 1 or count > 500:
            raise ValueError("Each account group count must be between 1 and 500.")
        if pattern:
            for index in range(start, start + count):
                item = {key: value for key, value in raw.items() if key not in {"count", "start", "username_pattern"}}
                item["username"] = pattern.format(index=index)
                accounts.append(item)
        else:
            if count != 1:
                raise ValueError("username_pattern is required when count is greater than one.")
            accounts.append(dict(raw))
    return accounts


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


async def run_account(
    client: httpx.AsyncClient,
    recorder: Recorder,
    semaphore: asyncio.Semaphore,
    account: dict[str, Any],
    organization_code: str,
    rounds: int,
    write_actions: bool,
) -> None:
    async with semaphore:
        role = str(account.get("role") or "student")
        if role not in {"student", "teacher", "admin"}:
            raise ValueError(f"Unsupported account role: {role}")
        username = str(account.get("username") or "").strip()
        password = str(account.get("password") or "")
        if not username or not password:
            raise ValueError("Every load-test account requires username and password.")

        base_headers = {"X-CoderAI-Organization-Code": organization_code}
        login_path = "/api/auth/student-login" if role == "student" else "/api/auth/teacher-login"
        login = await recorder.request(
            client,
            "POST",
            login_path,
            operation=f"{role}.login",
            headers=base_headers,
            payload={
                "organization_code": organization_code,
                "username": username,
                "password": password,
                "device_name": "pilot-load-test",
            },
        )
        if login is None:
            return
        token = str(login.json().get("token") or "")
        token_header = "X-CoderAI-Student-Token" if role == "student" else "X-CoderAI-Teacher-Token"
        headers = {**base_headers, token_header: token}

        for _ in range(rounds):
            await recorder.request(client, "GET", "/api/course-packages", operation=f"{role}.courses", headers=headers)
            await recorder.request(client, "GET", "/api/course-schedules", operation=f"{role}.schedules", headers=headers)
            if role == "student":
                await recorder.request(client, "GET", "/api/projects", operation="student.projects", headers=headers)
            else:
                await recorder.request(client, "GET", "/api/submissions", operation=f"{role}.submissions", headers=headers)

        if not write_actions:
            return
        if role == "student":
            project = await recorder.request(
                client,
                "POST",
                "/api/projects",
                operation="student.project_create",
                headers=headers,
                payload={
                    "title": f"内测压测作品 {username} {int(time.time())}",
                    "project_type": "text",
                    "summary": "# 内测压测作品\n\n由上线前非 AI 接口压测生成，可在测试完成后统一清理。",
                },
            )
            schedule_id = account.get("schedule_id")
            if project is not None and schedule_id:
                await recorder.request(
                    client,
                    "POST",
                    f"/api/course-schedules/{int(schedule_id)}/submissions",
                    operation="student.submission_create",
                    headers=headers,
                    payload={"project_id": int(project.json()["project"]["id"])},
                )
        else:
            submission_id = account.get("review_submission_id")
            if submission_id:
                await recorder.request(
                    client,
                    "PUT",
                    f"/api/submissions/{int(submission_id)}/review",
                    operation=f"{role}.submission_review",
                    headers=headers,
                    payload={"status": "reviewed", "score": int(account.get("review_score") or 80), "feedback": "上线前压测批改"},
                )


def summarize(results: list[RequestResult]) -> dict[str, Any]:
    groups: dict[str, list[RequestResult]] = {}
    for result in results:
        groups.setdefault(result.operation, []).append(result)
    total = len(results)
    failed = sum(not item.ok for item in results)
    return {
        "requests": total,
        "failures": failed,
        "error_rate": failed / total if total else 1.0,
        "p95_ms": round(percentile([item.elapsed_ms for item in results], 0.95), 2),
        "operations": {
            name: {
                "requests": len(items),
                "failures": sum(not item.ok for item in items),
                "p95_ms": round(percentile([item.elapsed_ms for item in items], 0.95), 2),
            }
            for name, items in sorted(groups.items())
        },
        "failure_samples": [asdict(item) for item in results if not item.ok][:20],
    }


async def execute(args: argparse.Namespace) -> int:
    config = json.loads(Path(args.accounts).read_text(encoding="utf-8"))
    accounts = expand_accounts(list(config.get("accounts") or []))
    if len(accounts) < args.minimum_accounts:
        raise ValueError(f"At least {args.minimum_accounts} accounts are required; found {len(accounts)}.")
    organization_code = str(config.get("organization_code") or args.organization_code).strip().lower()
    if not organization_code:
        raise ValueError("organization_code is required.")

    parsed = urlparse(args.base_url)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Load tests require HTTPS except for localhost targets.")

    recorder = Recorder()
    semaphore = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=timeout, limits=limits) as client:
        await asyncio.gather(*[
            run_account(
                client,
                recorder,
                semaphore,
                account,
                organization_code,
                args.rounds,
                args.write_actions,
            )
            for account in accounts
        ])

    report = {
        "target": args.base_url,
        "organization_code": organization_code,
        "accounts": len(accounts),
        "concurrency": args.concurrency,
        "rounds": args.rounds,
        "write_actions": args.write_actions,
        **summarize(recorder.results),
    }
    report["passed"] = report["error_rate"] < args.max_error_rate and report["p95_ms"] < args.max_p95_ms
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="CoderAI 50-account non-AI pilot load test")
    result.add_argument("--base-url", default=os.environ.get("CODERAI_LOAD_TEST_URL", ""), required=False)
    result.add_argument("--accounts", required=True, help="Path to the private account JSON file")
    result.add_argument("--organization-code", default="coderai-pilot")
    result.add_argument("--concurrency", type=int, default=50)
    result.add_argument("--rounds", type=int, default=3)
    result.add_argument("--timeout", type=float, default=15.0)
    result.add_argument("--minimum-accounts", type=int, default=50)
    result.add_argument("--max-error-rate", type=float, default=0.01)
    result.add_argument("--max-p95-ms", type=float, default=1000.0)
    result.add_argument("--write-actions", action="store_true")
    result.add_argument("--report", default="")
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.base_url:
        raise SystemExit("--base-url or CODERAI_LOAD_TEST_URL is required.")
    if args.concurrency < 1 or args.concurrency > 200:
        raise SystemExit("--concurrency must be between 1 and 200.")
    try:
        return asyncio.run(execute(args))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
