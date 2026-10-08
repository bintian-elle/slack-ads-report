# ANC EC2: robot + Ads Tracker + Organic Tracker

2026-10-06 read-only verification: `ssh EMW3_ANC_AI` reaches the migrated host;
project is `/home/ubuntu/work/bintian/slack-ads-report`. The robot service is
active and both KOL timers are installed. Do not clone into another directory,
delete runtime data, or run both old and new hosts' schedules.

## Update the migrated installation

Run on the new EC2. Keep the existing `.env`, credentials and data directories.
Update these keys in `.env` using an editor, without printing secrets:
`KOL_ORGANIC_SHEETS_LINK`, the shared `META_ACCESS_TOKEN`, and, if needed,
`KOL_TRACKER_TIKTOK_ACCESS_TOKEN`. The shared Meta token must be valid for Ads
and Organic in production; no separate `KOL_ORGANIC_META_ACCESS_TOKEN` is used.
Optional Organic business/Instagram IDs are documented in KOL_TRACKER_DEPLOY.md.
Do not replace the robot's existing configuration wholesale.

```bash
ssh EMW3_ANC_AI
cd /home/ubuntu/work/bintian/slack-ads-report
nano .env
chmod 600 .env
```

Then run this block. Read-only daily checks query all three platform tasks and
can take several minutes. If any command fails, stop, inspect the error and
repair before enabling timers. A failed block can leave timers stopped.

```bash
(
set -e
cd /home/ubuntu/work/bintian/slack-ads-report
sudo systemctl stop kol-tracker-poll.timer kol-tracker-daily.timer
while systemctl is-active --quiet kol-tracker-poll.service || systemctl is-active --quiet kol-tracker-daily.service; do
  sleep 5
done
git pull --ff-only origin main
.venv-kol/bin/python -m pip install -r requirements-kol-tracker.txt
.venv-kol/bin/python -m unittest discover -s tests -q
.venv-kol/bin/python kol_tracker.py check
.venv-kol/bin/python kol_tracker.py daily
bash deploy/install_kol_tracker.sh
systemctl status slack-ads-report --no-pager
systemctl list-timers --all 'kol-tracker-*' --no-pager
)
```

`slack-ads-report` is the existing continuous Slack robot with its own daily
schedule. It is not restarted by the KOL installation. Ads and Organic are two
logical sheet refreshers sharing the independent `.venv-kol` environment and
daily service, not two duplicate timers. `kol-tracker-poll` scans Slack every
five minutes for new Ads rows. `kol-tracker-daily` updates Meta Ads, TikTok Ads
and Organic Meta/TikTok at 02:00 America/New_York. Organic does not insert Slack rows;
Organic Meta discovers 2026 Reels with an exact Accepted Bluevua collaborator
relationship, inserting them in Organic Launch Date order through the Meta API and Sheets API.
The persistent timer may catch up a missed run when enabled.

## Verify after the next scheduled run

```bash
systemctl status slack-ads-report --no-pager
systemctl list-timers --all 'kol-tracker-*' --no-pager
journalctl -u kol-tracker-poll.service -n 60 --no-pager
journalctl -u kol-tracker-daily.service -n 100 --no-pager
```

One-shot KOL services being inactive between runs is normal. Check last-run
exit status and timer NEXT/LAST, not whether the one-shot process is always
active. Preserve `data/processed/kol_tracker_runtime`, including Slack cursor
and snapshots, and the robot's scheduler state. No credentials or state are
stored in GitHub. Migration from the old EC2 was already recorded in the ANC
project; confirm the old schedules remain disabled before any cutover.
