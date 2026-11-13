#!/usr/bin/env bash
set -euo pipefail
daily_root="$(cd "$(dirname "$0")" && pwd)"
daily_python="${1:-/home/ubuntu/work/bintian/slack-ads-report/.venv/bin/python}"
case "$daily_root$daily_python" in *' '*|*'"'*|*$'\n'*) echo 'Unsupported path' >&2; exit 1;; esac
test -f "$daily_root/config.json"
"$daily_python" -m unittest discover -s "$daily_root/tests" -q
"$daily_python" -m py_compile "$daily_root/daily.py" "$daily_root/failure_alert.py" "$daily_root/ai_worker.py" "$daily_root/ai_report.py"
systemd-analyze calendar '*-*-* 09:00:00 America/New_York'
daily_user="$(id -un)"
sudo tee /etc/systemd/system/biceek-daily-report.service >/dev/null <<UNIT
[Unit]
Description=Independent Biceek USD daily sheet and Slack report
After=network-online.target
Wants=network-online.target
OnFailure=biceek-daily-report-failure.service
StartLimitIntervalSec=1h
StartLimitBurst=4

[Service]
Type=oneshot
User=$daily_user
WorkingDirectory=$daily_root
ExecStart=$daily_python $daily_root/daily.py --mode send
Environment=PYTHONUNBUFFERED=1
UMask=0077
TimeoutStartSec=20min
Restart=on-failure
RestartSec=5min
NoNewPrivileges=true
PrivateTmp=true
UNIT
sudo tee /etc/systemd/system/biceek-daily-report.timer >/dev/null <<'UNIT'
[Unit]
Description=Biceek daily report at 09:00 America/New_York
[Timer]
OnCalendar=*-*-* 09:00:00 America/New_York
Persistent=false
AccuracySec=1s
Unit=biceek-daily-report.service
[Install]
WantedBy=timers.target
UNIT
for daily_mode in retry watchdog alerts; do
case "$daily_mode" in
  retry) daily_calendar='*-*-* *:05/15:00' ;;
  watchdog) daily_calendar='*-*-* *:10/15:00' ;;
  alerts) daily_calendar='*-*-* *:00/15:00' ;;
esac
sudo tee "/etc/systemd/system/biceek-daily-report-$daily_mode.service" >/dev/null <<UNIT
[Unit]
Description=Biceek daily report $daily_mode
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$daily_user
WorkingDirectory=$daily_root
ExecStart=$daily_python $daily_root/daily.py --mode $daily_mode
UMask=0077
TimeoutStartSec=20min
NoNewPrivileges=true
PrivateTmp=true
UNIT
if [ "$daily_mode" = alerts ]; then
  sudo mkdir -p /etc/systemd/system/biceek-daily-report-alerts.service.d
  sudo tee /etc/systemd/system/biceek-daily-report-alerts.service.d/crash-retry.conf >/dev/null <<UNIT
[Service]
ExecStartPre=$daily_python $daily_root/failure_alert.py --retry
UNIT
fi
sudo tee "/etc/systemd/system/biceek-daily-report-$daily_mode.timer" >/dev/null <<UNIT
[Unit]
Description=Biceek daily report $daily_mode every fifteen minutes
[Timer]
OnCalendar=$daily_calendar
Persistent=false
Unit=biceek-daily-report-$daily_mode.service
[Install]
WantedBy=timers.target
UNIT
done
sudo tee /etc/systemd/system/biceek-daily-report-failure.service >/dev/null <<UNIT
[Unit]
Description=Biceek daily report independent crash notification
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$daily_user
WorkingDirectory=$daily_root
ExecStart=$daily_python $daily_root/failure_alert.py
UMask=0077
TimeoutStartSec=2min
NoNewPrivileges=true
PrivateTmp=true
UNIT
sudo tee /etc/systemd/system/biceek-daily-report-prepare.service >/dev/null <<UNIT
[Unit]
Description=Remote AI Biceek short report preparation
After=network-online.target
Wants=network-online.target
OnFailure=biceek-daily-report-failure.service
[Service]
Type=oneshot
User=$daily_user
WorkingDirectory=$daily_root
ExecStart=$daily_python $daily_root/daily.py --mode prepare
Environment=PYTHONUNBUFFERED=1
UMask=0077
TimeoutStartSec=40min
NoNewPrivileges=true
PrivateTmp=true
UNIT
sudo tee /etc/systemd/system/biceek-daily-report-prepare.timer >/dev/null <<'UNIT'
[Unit]
Description=Prepare remote AI Biceek daily report at 08:25 Eastern
[Timer]
OnCalendar=*-*-* 08:25:00 America/New_York
Persistent=false
AccuracySec=1s
Unit=biceek-daily-report-prepare.service
[Install]
WantedBy=timers.target
UNIT
# Native Codex's home socket is a symlink into this host directory.
# Preserve PrivateTmp, but expose only its socket directory for AI callers.
for daily_ai_mode in prepare retry; do
  sudo mkdir -p "/etc/systemd/system/biceek-daily-report-$daily_ai_mode.service.d"
  sudo tee "/etc/systemd/system/biceek-daily-report-$daily_ai_mode.service.d/shared-ai-runtime.conf" >/dev/null <<UNIT
[Service]
BindReadOnlyPaths=/tmp/codex-daemon-$(id -u)
UNIT
done
sudo systemctl daemon-reload
echo 'Installed only Biceek units; timers are not yet enabled.'
