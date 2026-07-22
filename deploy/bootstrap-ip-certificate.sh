#!/bin/sh
set -eu

ENV_FILE="${1:-/srv/coderai/config/deploy.env}"
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
: "${CODERAI_CERTBOT_EMAIL:?CODERAI_CERTBOT_EMAIL is required}"

if ! printf '%s\n' "$CODERAI_PUBLIC_IP" | awk -F. '
    NF != 4 { exit 1 }
    { for (i = 1; i <= 4; i++) if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1 }
'; then
    echo "CODERAI_PUBLIC_IP must be a valid IPv4 address." >&2
    exit 1
fi

LETSENCRYPT_DIR="${CODERAI_LETSENCRYPT_DIR:-/etc/letsencrypt}"
WEBROOT="${CODERAI_CERTBOT_WEBROOT:-/srv/coderai/acme}"
mkdir -p "$LETSENCRYPT_DIR" /var/lib/letsencrypt /var/log/letsencrypt "$WEBROOT"

if [ -f "$LETSENCRYPT_DIR/live/coderai-ip/fullchain.pem" ]; then
    echo "CoderAI IP certificate already exists; use renew-ip-certificate.sh." >&2
    exit 1
fi

docker run --rm --network host \
    -v "$LETSENCRYPT_DIR:/etc/letsencrypt" \
    -v /var/lib/letsencrypt:/var/lib/letsencrypt \
    -v /var/log/letsencrypt:/var/log/letsencrypt \
    "$CERTBOT_IMAGE" certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --email "$CODERAI_CERTBOT_EMAIL" \
    --preferred-profile shortlived \
    --ip-address "$CODERAI_PUBLIC_IP" \
    --cert-name coderai-ip

test -s "$LETSENCRYPT_DIR/live/coderai-ip/fullchain.pem"
test -s "$LETSENCRYPT_DIR/live/coderai-ip/privkey.pem"
echo "CoderAI public IP certificate was issued successfully."
