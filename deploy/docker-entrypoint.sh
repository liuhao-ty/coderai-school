#!/bin/sh
set -eu

case "${1:-api}" in
  migrate)
    exec python -m alembic upgrade head
    ;;
  api)
    exec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers "${CODERAI_API_WORKERS:-2}" --proxy-headers --forwarded-allow-ips="${CODERAI_FORWARDED_ALLOW_IPS:-*}"
    ;;
  worker)
    exec celery -A backend.app.task_queue:celery_app worker --loglevel="${CODERAI_LOG_LEVEL:-INFO}" --concurrency="${CODERAI_CELERY_CONCURRENCY:-2}" --max-tasks-per-child=100
    ;;
  beat)
    exec celery -A backend.app.task_queue:celery_app beat --loglevel="${CODERAI_LOG_LEVEL:-INFO}" --schedule=/app/runtime/celerybeat-schedule
    ;;
  backup)
    shift
    exec python /app/deploy/backup.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
