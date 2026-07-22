from __future__ import annotations

import asyncio
import os
from typing import Any, Callable

from fastapi import BackgroundTasks

from backend.app.db import CLOUD_MODE, SessionLocal
from backend.app.tenancy import current_organization_code, current_organization_id, organization_context


BROKER_URL = os.environ.get("CODERAI_REDIS_URL", "redis://redis:6379/0").strip()
RESULT_BACKEND = os.environ.get("CODERAI_CELERY_RESULT_BACKEND", BROKER_URL).strip()
TASK_QUEUE_ENABLED = CLOUD_MODE and os.environ.get("CODERAI_TASK_QUEUE_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}

celery_app = None
if TASK_QUEUE_ENABLED:
    from celery import Celery

    celery_app = Celery("coderai", broker=BROKER_URL, backend=RESULT_BACKEND)
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        task_track_started=True,
        result_expires=86400,
        beat_schedule={
            "daily-retention-scan": {
                "task": "coderai.scan_retention",
                "schedule": 24 * 60 * 60,
            },
        },
    )


def queue_health() -> dict[str, Any]:
    if not TASK_QUEUE_ENABLED or celery_app is None:
        return {"enabled": False, "ready": not CLOUD_MODE, "broker": "local-background-tasks"}
    try:
        connection = celery_app.connection_for_read()
        connection.ensure_connection(max_retries=1)
        connection.release()
        return {"enabled": True, "ready": True, "broker": "redis"}
    except Exception as exc:
        return {"enabled": True, "ready": False, "broker": "redis", "error": str(exc)[:240]}


def enqueue_slides_conversion(
    background_tasks: BackgroundTasks,
    local_callable: Callable[..., Any],
    material_id: int,
) -> str:
    organization_id = current_organization_id()
    organization_code = current_organization_code()
    if TASK_QUEUE_ENABLED and celery_app is not None:
        celery_app.send_task(
            "coderai.convert_slides",
            args=[material_id, organization_id, organization_code],
        )
        return "celery"
    background_tasks.add_task(local_callable, material_id)
    return "background"


def enqueue_workflow_run(
    background_tasks: BackgroundTasks,
    local_callable: Callable[..., Any],
    run_id: int,
    retry_from_node: str | None,
    bind: Any,
) -> str:
    organization_id = current_organization_id()
    organization_code = current_organization_code()
    if TASK_QUEUE_ENABLED and celery_app is not None:
        celery_app.send_task(
            "coderai.execute_workflow",
            args=[run_id, retry_from_node, organization_id, organization_code],
        )
        return "celery"
    background_tasks.add_task(local_callable, run_id, retry_from_node, bind)
    return "background"


def enqueue_video_poll(background_tasks: BackgroundTasks, task_id: int) -> str:
    organization_id = current_organization_id()
    organization_code = current_organization_code()
    if TASK_QUEUE_ENABLED and celery_app is not None:
        celery_app.send_task(
            "coderai.poll_video",
            args=[task_id, organization_id, organization_code],
            countdown=max(1, int(os.environ.get("CODERAI_VIDEO_POLL_SECONDS", "10"))),
        )
        return "celery"
    if os.environ.get("CODERAI_LOCAL_VIDEO_AUTOPOLL", "false").lower() in {"1", "true", "yes"}:
        background_tasks.add_task(_poll_video_once, task_id, organization_id, organization_code)
        return "background"
    return "manual"


def _poll_video_once(task_id: int, organization_id: int, organization_code: str) -> None:
    from backend.app.models import VideoTask
    from backend.app.services import refresh_video_task

    with organization_context(organization_id, organization_code):
        db = SessionLocal()
        try:
            task = db.get(VideoTask, task_id)
            if task:
                asyncio.run(refresh_video_task(db, task))
        finally:
            db.close()


if celery_app is not None:

    @celery_app.task(name="coderai.convert_slides", acks_late=True)
    def convert_slides_task(material_id: int, organization_id: int, organization_code: str) -> None:
        from backend.app.curriculum import convert_slides_material

        convert_slides_material(material_id, organization_id, organization_code)


    @celery_app.task(name="coderai.execute_workflow", acks_late=True)
    def execute_workflow_task(
        run_id: int,
        retry_from_node: str | None,
        organization_id: int,
        organization_code: str,
    ) -> None:
        from backend.app.main import execute_workflow_run_background

        asyncio.run(
            execute_workflow_run_background(
                run_id,
                retry_from_node,
                None,
                organization_id,
                organization_code,
            )
        )


    @celery_app.task(bind=True, name="coderai.poll_video", acks_late=True, max_retries=180)
    def poll_video_task(
        self,
        task_id: int,
        organization_id: int,
        organization_code: str,
    ) -> None:
        from backend.app.models import VideoTask
        from backend.app.services import VIDEO_ACTIVE_STATUSES, refresh_video_task

        with organization_context(organization_id, organization_code):
            db = SessionLocal()
            try:
                task = db.get(VideoTask, task_id)
                if not task or task.status not in VIDEO_ACTIVE_STATUSES:
                    return
                try:
                    task = asyncio.run(refresh_video_task(db, task))
                except Exception as exc:
                    raise self.retry(
                        exc=exc,
                        countdown=min(60, 5 + int(self.request.retries) * 5),
                    )
                if task.status in VIDEO_ACTIVE_STATUSES:
                    raise self.retry(
                        countdown=max(1, int(os.environ.get("CODERAI_VIDEO_POLL_SECONDS", "10")))
                    )
            finally:
                db.close()


    @celery_app.task(name="coderai.scan_retention", acks_late=True)
    def scan_retention_task() -> int:
        from backend.app.models import Organization
        from backend.app.retention import create_retention_request
        from backend.app.tenancy import without_tenant_filter

        db = SessionLocal()
        try:
            with without_tenant_filter():
                organizations = [
                    (item.id, item.code)
                    for item in db.query(Organization).filter(Organization.active.is_(True)).all()
                ]
        finally:
            db.close()
        created = 0
        for organization_id, organization_code in organizations:
            with organization_context(organization_id, organization_code):
                tenant_db = SessionLocal()
                try:
                    before = create_retention_request(tenant_db)
                    if before.status == "pending":
                        created += 1
                finally:
                    tenant_db.close()
        return created
