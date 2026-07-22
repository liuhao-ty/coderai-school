#!/bin/sh
set -eu

ENV_FILE="${1:-/srv/coderai/config/deploy.env}"
APP_DIR="${CODERAI_APP_DIR:-/srv/coderai/app}"
CERTBOT_IMAGE="certbot/certbot:v5.4.0@sha256:c23159d30afdd9c97960578aa4654f5901de6cae394958f894074dedd55e599d"

if [ ! -r "$ENV_FILE" ]; then
    echo "Deployment environment file is not readable: $ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${CODERAI_PUBLIC_IP:?CODERAI_PUBLIC_IP is required}"
LETSENCRYPT_DIR="${CODERAI_LETSENCRYPT_DIR:-/etc/letsencrypt}"
WEBROOT="${CODERAI_CERTBOT_WEBROOT:-/srv/coderai/acme}"
METRICS_DIR="${CODERAI_MONITORING_METRICS_DIR:-/srv/coderai/metrics}"
COMPOSE_FILE="$APP_DIR/deploy/docker-compose.yml"

mkdir -p "$WEBROOT" "$METRICS_DIR"

docker run --rm \
    -v "$LETSENCRYPT_DIR:/etc/letsencrypt" \
    -v /var/lib/letsencrypt:/var/lib/letsencrypt \
    -v /var/log/letsencrypt:/var/log/letsencrypt \
    -v "$WEBROOT:/var/www/certbot" \
    "$CERTBOT_IMAGE" renew \
    --cert-name coderai-ip \
    --webroot \
    --webroot-path /var/www/certbot \
    --quiet

if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q caddy | grep -q .; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T caddy \
        caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
fi

if docker run --rm --entrypoint python \
    -v "$LETSENCRYPT_DIR:/etc/letsencrypt:ro" \
    "$CERTBOT_IMAGE" -c \
    'import ssl, sys, time; cert = ssl._ssl._test_decode_cert("/etc/letsencrypt/live/coderai-ip/fullchain.pem"); sys.exit(0 if ssl.cert_time_to_seconds(cert["notAfter"]) - time.time() > 172800 else 1)' \
    >/dev/null 2>&1; then
    VALID_BEYOND_48H=1
else
    VALID_BEYOND_48H=0
fi

METRICS_TMP="$METRICS_DIR/coderai_tls.prom.tmp"
printf 'coderai_tls_certificate_valid_beyond_48h %s\n' "$VALID_BEYOND_48H" > "$METRICS_TMP"
mv "$METRICS_TMP" "$METRICS_DIR/coderai_tls.prom"

if [ "$VALID_BEYOND_48H" -ne 1 ]; then
    echo "CoderAI public IP certificate expires within 48 hours." >&2
    exit 1
fi

echo "CoderAI public IP certificate renewal check completed."
