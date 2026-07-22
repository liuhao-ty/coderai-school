FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CODERAI_LIBREOFFICE_PATH=/usr/bin/libreoffice

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-noto-cjk \
        libreoffice-impress \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -m pip install --requirement requirements.lock

COPY alembic.ini ./alembic.ini
COPY backend ./backend
COPY deploy ./deploy

RUN useradd --create-home --uid 10001 coderai \
    && mkdir -p /app/runtime \
    && chmod +x /app/deploy/docker-entrypoint.sh \
    && chown -R coderai:coderai /app
USER coderai

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["api"]
