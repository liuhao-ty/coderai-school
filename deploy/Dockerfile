FROM python:3.12.10-slim-bookworm

ARG CODERAI_APT_MIRROR=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CODERAI_LIBREOFFICE_PATH=/usr/bin/libreoffice

RUN if [ -n "$CODERAI_APT_MIRROR" ]; then \
        sed -i \
            -e "s|http://deb.debian.org|$CODERAI_APT_MIRROR|g" \
            -e "s|https://deb.debian.org|$CODERAI_APT_MIRROR|g" \
            /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-noto-cjk \
        libreoffice-impress \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock ./requirements.lock
ARG CODERAI_PIP_INDEX_URL=
RUN if [ -n "$CODERAI_PIP_INDEX_URL" ]; then \
        python -m pip install --index-url "$CODERAI_PIP_INDEX_URL" --requirement requirements.lock; \
    else \
        python -m pip install --requirement requirements.lock; \
    fi

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
