from __future__ import annotations

import json
import logging
import os
import time
import uuid

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status_code", "duration_ms", "organization_code"):
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    if os.environ.get("CODERAI_JSON_LOGS", "false").lower() not in {"1", "true", "yes"}:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("CODERAI_LOG_LEVEL", "INFO").upper())


class CoderAIMetricsCollector:
    def collect(self):
        from backend.app.db import SessionLocal
        from backend.app.models import RetentionRequest, StudentSession, TeacherSession, UsageLog, VideoTask, WorkflowRun, now
        from backend.app.tenancy import without_tenant_filter

        db = SessionLocal()
        try:
            with without_tenant_filter():
                usage_metric = GaugeMetricFamily(
                    "coderai_ai_calls_total",
                    "Recorded AI calls grouped by organization, feature and status.",
                    labels=["organization_id", "feature", "status"],
                )
                for organization_id, feature, status, count in db.query(
                    UsageLog.organization_id,
                    UsageLog.feature,
                    UsageLog.status,
                    func.count(UsageLog.id),
                ).group_by(UsageLog.organization_id, UsageLog.feature, UsageLog.status).all():
                    usage_metric.add_metric([str(organization_id), feature, status], float(count))
                yield usage_metric

                active_metric = GaugeMetricFamily(
                    "coderai_active_sessions",
                    "Active unique login sessions.",
                    labels=["organization_id", "role"],
                )
                current = now()
                for model, role in ((TeacherSession, "staff"), (StudentSession, "student")):
                    rows = db.query(model.organization_id, func.count(func.distinct(model.user_id))).filter(
                        model.revoked_at.is_(None), model.expires_at > current
                    ).group_by(model.organization_id).all()
                    for organization_id, count in rows:
                        active_metric.add_metric([str(organization_id), role], float(count))
                yield active_metric

                queue_metric = GaugeMetricFamily(
                    "coderai_domain_jobs",
                    "Pending or running domain jobs stored in the database.",
                    labels=["organization_id", "kind"],
                )
                for model, kind in ((WorkflowRun, "workflow"), (VideoTask, "video")):
                    rows = db.query(model.organization_id, func.count(model.id)).filter(
                        model.status.in_(("pending", "running", "submitted", "processing"))
                    ).group_by(model.organization_id).all()
                    for organization_id, count in rows:
                        queue_metric.add_metric([str(organization_id), kind], float(count))
                yield queue_metric

                retention_metric = GaugeMetricFamily(
                    "coderai_retention_requests",
                    "Retention requests waiting for administrator action.",
                    labels=["organization_id", "status"],
                )
                rows = db.query(
                    RetentionRequest.organization_id,
                    RetentionRequest.status,
                    func.count(RetentionRequest.id),
                ).filter(RetentionRequest.status.in_(("pending", "approved"))).group_by(
                    RetentionRequest.organization_id, RetentionRequest.status
                ).all()
                for organization_id, status, count in rows:
                    retention_metric.add_metric([str(organization_id), status], float(count))
                yield retention_metric
        except Exception:
            return
        finally:
            db.close()


_collector_registered = False
HTTP_REQUESTS = Counter(
    "coderai_http_requests_total",
    "HTTP requests grouped by method, route and status.",
    labelnames=("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "coderai_http_request_duration_seconds",
    "HTTP request duration grouped by method and route.",
    labelnames=("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


def _request_route(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    if path:
        return str(path)
    return "unmatched"


def configure_observability(app: FastAPI) -> None:
    global _collector_registered
    configure_logging()
    access_logger = logging.getLogger("coderai.access")

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "").strip()[:80] or uuid.uuid4().hex
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            access_logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "organization_code": request.headers.get("X-CoderAI-Organization-Code", ""),
                },
            )
            raise
        finally:
            elapsed = time.perf_counter() - started
            route_path = _request_route(request)
            HTTP_REQUESTS.labels(request.method, route_path, str(status_code)).inc()
            HTTP_DURATION.labels(request.method, route_path).observe(elapsed)
        duration_ms = round(elapsed * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        access_logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "organization_code": request.headers.get("X-CoderAI-Organization-Code", ""),
            },
        )
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if not _collector_registered:
        REGISTRY.register(CoderAIMetricsCollector())
        _collector_registered = True
