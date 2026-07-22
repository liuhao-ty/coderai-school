#!/bin/sh
set -eu

config=/tmp/coderai-alertmanager.yml
webhook=${CODERAI_ALERT_WEBHOOK_URL:-}

if [ -n "$webhook" ]; then
  case "$webhook" in
    https://*) ;;
    *) echo "CODERAI_ALERT_WEBHOOK_URL must use HTTPS." >&2; exit 1 ;;
  esac
  case "$webhook" in
    *\"*) echo "CODERAI_ALERT_WEBHOOK_URL contains unsupported characters." >&2; exit 1 ;;
  esac
  cat > "$config" <<EOF
route:
  receiver: operations-webhook
  group_by: [alertname]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
receivers:
  - name: operations-webhook
    webhook_configs:
      - url: "$webhook"
        send_resolved: true
EOF
else
  echo "CODERAI_ALERT_WEBHOOK_URL is empty; external alert delivery is disabled." >&2
  cat > "$config" <<'EOF'
route:
  receiver: discard
  group_by: [alertname]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
receivers:
  - name: discard
EOF
fi

exec /bin/alertmanager --config.file="$config"
