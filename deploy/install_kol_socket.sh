#!/usr/bin/env bash
# Explicit KOL-only opt-in; never change Robot units or credentials.
set -euo pipefail
tracker_root="$(cd "$(dirname "$0")/.." && pwd)"
tracker_user="$(id -un)"
case "$tracker_root" in
  *' '*|*'"'*|*$'\n'*) echo 'Use a deployment path without spaces or quotes' >&2; exit 1 ;;
esac
cd "$tracker_root"
test -x "$tracker_root/.venv-kol/bin/python"
test -f "$tracker_root/.env"
# A temporary read-only socket validates app identity; it never wakes a writer.
"$tracker_root/.venv-kol/bin/python" "$tracker_root/kol_slack_socket.py" --probe --seconds 5
test -f /etc/systemd/system/kol-tracker-poll.service
sudo mkdir -p /etc/systemd/system/kol-tracker-poll.service.d
sudo tee /etc/systemd/system/kol-tracker-poll.service.d/socket-mode.conf >/dev/null <<EOF
[Service]
ExecCondition=${tracker_root}/.venv-kol/bin/python ${tracker_root}/kol_slack_socket.py --work-due
EOF
sudo tee /etc/systemd/system/kol-tracker-socket.service >/dev/null <<EOF
[Unit]
Description=Independent KOL Slack Socket Mode receiver
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${tracker_user}
WorkingDirectory=${tracker_root}
ExecStart=${tracker_root}/.venv-kol/bin/python ${tracker_root}/kol_slack_socket.py
Restart=on-failure
RestartSec=30
Environment=PYTHONUNBUFFERED=1
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
# The receiver commits inbox data and touches this file. Daily/poll already share
# task.lock; a busy writer cannot lose events, and the periodic timer is fallback.
state_root="$("$tracker_root/.venv-kol/bin/python" -c 'from dotenv import dotenv_values; from kol_slack_events import state_root; print(state_root(dotenv_values(".env")))')"
case "$state_root" in
  /*) ;;
  *) echo 'KOL_TRACKER_STATE_DIR must be absolute' >&2; exit 1 ;;
esac
case "$state_root" in
  *' '*|*'"'*|*$'\n'*) echo 'Use a state path without spaces or quotes' >&2; exit 1 ;;
esac
sudo tee /etc/systemd/system/kol-tracker-events.path >/dev/null <<EOF
[Unit]
Description=Wake KOL worker for durable Slack events

[Path]
PathChanged=${state_root}/slack-events.wake
Unit=kol-tracker-poll.service

[Install]
WantedBy=multi-user.target
EOF
# Bootstrap receiver BEFORE switching the worker away from legacy history.
sudo systemctl daemon-reload
started="$(date +%s)"
sudo systemctl enable kol-tracker-socket.service
sudo systemctl restart kol-tracker-socket.service
ready=false
for attempt in $(seq 1 40); do
  if "$tracker_root/.venv-kol/bin/python" "$tracker_root/kol_slack_socket.py" --ready-since "$started"; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  echo 'KOL receiver not ready; legacy polling retained'
  exit 1
fi
umask 077
mkdir -p "$state_root"
touch "$state_root/slack-events.enabled"
sudo systemctl enable --now kol-tracker-events.path kol-tracker-poll.timer
sudo systemctl start --no-block kol-tracker-poll.service
systemctl status kol-tracker-socket.service --no-pager
systemctl list-timers 'kol-tracker-*' --no-pager
