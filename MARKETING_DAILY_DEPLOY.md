# Marketing Daily Report layout repair

The October Budget Pacing tab adds ChatGPT Spend/ROAS before Meta ATC.
The repair accepts both September and October layouts, preserves manually
maintained ChatGPT cells, includes ChatGPT spend in Total Spend, and reads
through AZ so October's MTD summary in AJ:AK is visible. No token or env
changes are needed. Unknown layouts still fail before writing.

Run on the migrated EC2. This update restarts the robot because its Python
module changed. Startup can automatically retry the unfinished daily report,
update the Sheet and send Slack. Do not manually replay the pipeline or remove
its completion marker. Preserve runtime data, credentials and both venvs.

```bash
ssh EMW3_ANC_AI
cd /home/ubuntu/work/bintian/slack-ads-report
git status --short
```

If tracked files have local modifications, inspect them before continuing.
Keep the existing untracked data/raw/ directory.

```bash
(
set -e
cd /home/ubuntu/work/bintian/slack-ads-report
sudo systemctl stop kol-tracker-poll.timer kol-tracker-daily.timer
while :; do
  tracker_busy=0
  for tracker_unit in kol-tracker-poll.service kol-tracker-daily.service; do
    tracker_state=$(systemctl show "$tracker_unit" -p ActiveState --value)
    case "$tracker_state" in
      active|activating|deactivating) tracker_busy=1 ;;
    esac
  done
  [ "$tracker_busy" -eq 0 ] && break
  sleep 5
done
sudo systemctl stop slack-ads-report.service
git pull --ff-only origin main
.venv/bin/python -m unittest discover -s tests -q
sudo systemctl start slack-ads-report.service
sudo systemctl start kol-tracker-poll.timer kol-tracker-daily.timer
systemctl status slack-ads-report --no-pager
systemctl list-timers --all 'kol-tracker-*' --no-pager
)
```

No dependency changes are required for this repair. If a command fails, the
robot/timers may remain stopped; fix the failure before restarting them.
Restoring persistent KOL timers may catch up missed runs. This does not install
new timers or manually repeat the completed Organic write.

```bash
journalctl -u slack-ads-report --since '10 minutes ago' --no-pager
cat data/processed/.last_scheduled_report_date
```

Expect `Google Sheet updated` and `Scheduled report pipeline completed`, with
the marker set to the run's local date. Confirm the 10/5 report in Slack and
October row 73: Meta ATC in AG, Google Ads totals in AH:AI, ChatGPT AE:AF
unchanged. A separate seven-day historical refresh failure does not retry a
report already delivered to Slack.
