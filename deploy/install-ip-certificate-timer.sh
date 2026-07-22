#!/bin/sh
set -eu

APP_DIR="${CODERAI_APP_DIR:-/srv/coderai/app}"

install -m 0644 "$APP_DIR/deploy/systemd/coderai-cert-renew.service" /etc/systemd/system/coderai-cert-renew.service
install -m 0644 "$APP_DIR/deploy/systemd/coderai-cert-renew.timer" /etc/systemd/system/coderai-cert-renew.timer
systemctl daemon-reload
systemctl enable --now coderai-cert-renew.timer
systemctl list-timers coderai-cert-renew.timer --no-pager
