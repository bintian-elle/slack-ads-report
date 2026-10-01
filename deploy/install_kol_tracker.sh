#!/usr/bin/env bash
# Install only the dedicated KOL units, never modify/restart the existing app.
set -euo pipefail
tracker_root="$(cd "$(dirname "$0")/.." && pwd)"
tracker_user="$(id -un)"
case "$tracker_root" in
  *' '*|*'"'*|*$'\n'*) echo 'Use a deployment path without spaces or quotes' >&2; exit 1 ;;
esac
test -x "$tracker_root/.venv-kol/bin/python"
test -f "$tracker_root/.env"
"$tracker_root/.venv-kol/bin/python" "$tracker_root/kol_tracker.py" check
systemd-analyze calendar '*-*-* 08:00:00 America/Chicago'
for tracker_task in poll daily; do
  sudo tee "/etc/systemd/system/kol-tracker-${tracker_task}.service" >/dev/null <<EOF
[Unit]
Description=Independent KOL tracker ${tracker_task}
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=${tracker_user}
WorkingDirectory=${tracker_root}
ExecStart=${tracker_root}/.venv-kol/bin/python ${tracker_root}/kol_tracker.py ${tracker_task} --apply
Environment=PYTHONUNBUFFERED=1
UMask=0077
TimeoutStartSec=30min
NoNewPrivileges=true
PrivateTmp=true

EOF
done
sudo tee /etc/systemd/system/kol-tracker-poll.timer >/dev/null <<'EOF'
[Unit]
Description=Read Slack KOL content every five minutes

[Timer]
OnCalendar=*-*-* *:0/5:00
Persistent=true
AccuracySec=1s
Unit=kol-tracker-poll.service

[Install]
WantedBy=timers.target
EOF
sudo tee /etc/systemd/system/kol-tracker-daily.timer >/dev/null <<'EOF'
[Unit]
Description=Update KOL ads daily at Chicago 08:00

[Timer]
OnCalendar=*-*-* 08:00:00 America/Chicago
Persistent=true
AccuracySec=1s
Unit=kol-tracker-daily.service

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now kol-tracker-poll.timer kol-tracker-daily.timer
systemctl list-timers 'kol-tracker-*'
