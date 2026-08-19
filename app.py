"""Slack bot entry point and local CSV report generator."""

import argparse
import logging
import threading
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import load_settings
from gmail_service import GmailService
from report_service import (
    format_slack_report,
    load_processed_csv,
    process_csv_files,
    read_raw_csv,
    save_processed_csv,
)
from slack_service import SlackService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


settings = load_settings()


def fetch_latest_gmail_report() -> Path:
    """Download the newest Google Ads report linked from Gmail."""
    gmail = GmailService(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        refresh_token=settings.gmail_refresh_token,
        download_dir=settings.raw_data_dir,
    )
    expected_email_date = datetime.now(
        ZoneInfo(settings.report_timezone)
    ).date()
    output_path = gmail.download_latest_report(
        expected_email_date=expected_email_date,
    )
    logging.info("Gmail report saved to %s", output_path)
    return output_path


def generate_daily_report(
    csv_paths: Optional[Iterable[Path]] = None,
    channel_ids=None,
    send_to_slack=False,
) -> Path:
    """Process raw CSV files, save the report, and optionally send it to Slack."""
    selected_paths = list(csv_paths or [])
    if not selected_paths:
        dated_paths = []
        for raw_path in settings.raw_data_dir.glob("*.csv"):
            try:
                raw_date, _ = read_raw_csv(raw_path)
                dated_paths.append((raw_date, raw_path))
            except ValueError as error:
                logging.warning("Skipping %s: %s", raw_path.name, error)

        if not dated_paths:
            raise FileNotFoundError(
                "No raw CSV was found. Run: python app.py --fetch-gmail"
            )
        latest_date = max(raw_date for raw_date, _ in dated_paths)
        selected_paths = [
            raw_path for raw_date, raw_path in dated_paths if raw_date == latest_date
        ]
        logging.info(
            "Processing %s raw files for %s: %s",
            len(selected_paths),
            latest_date,
            ", ".join(path.name for path in selected_paths),
        )

    report_date, metrics = process_csv_files(selected_paths)
    report = format_slack_report(report_date, metrics)

    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.processed_data_dir / f"daily_report_{report_date:%Y-%m-%d}.csv"
    save_processed_csv(output_path, report_date, metrics)
    logging.info("Report saved to %s", output_path)

    if send_to_slack:
        slack = SlackService(settings.slack_bot_token)
        targets = tuple(channel_ids or settings.scheduled_channel_ids)
        if not targets:
            raise ValueError("No Slack channel IDs were configured.")
        for channel_id in targets:
            slack.send_report(channel_id, report)

    return output_path


def run_automated_pipeline() -> Path:
    """Fetch Gmail, process the downloaded CSV, and send it to Slack."""
    logging.info("Starting scheduled Gmail-to-Slack report pipeline")
    raw_path = fetch_latest_gmail_report()
    processed_path = generate_daily_report(
        csv_paths=[raw_path],
        send_to_slack=True,
    )
    logging.info("Scheduled report pipeline completed: %s", processed_path.name)
    return processed_path


def configured_report_time() -> datetime_time:
    """Parse REPORT_TIME as a local wall-clock time."""
    try:
        hour, minute = (int(part) for part in settings.report_time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError as error:
        raise RuntimeError(
            "REPORT_TIME must use HH:MM format, for example 09:00."
        ) from error
    return datetime_time(hour=hour, minute=minute)


def _scheduler_state_path() -> Path:
    """Return the persistent marker used to prevent duplicate daily sends."""
    return settings.processed_data_dir / ".last_scheduled_email_date"


def _read_last_scheduled_date() -> Optional[date]:
    state_path = _scheduler_state_path()
    if not state_path.exists():
        return None
    try:
        return date.fromisoformat(state_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        logging.warning("Ignoring invalid scheduler state file: %s", state_path)
        return None


def _write_last_scheduled_date(completed_date: date) -> None:
    state_path = _scheduler_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(completed_date.isoformat() + "\n", encoding="utf-8")


def scheduled_pipeline_loop() -> None:
    """Run after REPORT_TIME, including immediately after wake or restart."""
    timezone = ZoneInfo(settings.report_timezone)
    report_time = configured_report_time()
    last_attempt_at: Optional[datetime] = None
    logging.info(
        "Daily pipeline is scheduled for %s in %s",
        settings.report_time,
        settings.report_timezone,
    )

    while True:
        now = datetime.now(timezone)
        last_completed = _read_last_scheduled_date()
        is_due = now.time().replace(tzinfo=None) >= report_time
        already_completed = last_completed == now.date()
        retry_is_due = (
            last_attempt_at is None
            or (now - last_attempt_at).total_seconds() >= 300
        )

        if is_due and not already_completed and retry_is_due:
            last_attempt_at = now
            try:
                run_automated_pipeline()
                _write_last_scheduled_date(now.date())
                logging.info("Daily pipeline marked complete for %s", now.date())
            except Exception:
                logging.exception(
                    "Scheduled report pipeline failed; retrying in five minutes"
                )

        # A short wall-clock poll recovers promptly after laptop sleep or restart.
        time.sleep(30)


def load_latest_processed_report() -> str:
    """Load the newest processed CSV and add Slack formatting at runtime."""
    report_paths = list(settings.processed_data_dir.glob("daily_report_*.csv"))
    if not report_paths:
        raise FileNotFoundError(
            "No processed report was found. Run: python app.py --generate"
        )

    latest_report = max(report_paths, key=lambda path: path.stat().st_mtime)
    logging.info("Using processed report %s", latest_report.name)
    report_date, metrics = load_processed_csv(latest_report)
    return format_slack_report(report_date, metrics)


def create_bolt_app() -> App:
    """Create the Slack app and register its command handlers."""
    bolt_app = App(token=settings.slack_bot_token)

    @bolt_app.command("/daily-report")
    def handle_daily_report(ack, command, respond):
        """Return the latest processed report to the command's Slack channel."""
        ack()
        try:
            report = load_latest_processed_report()
            delivered = SlackService(settings.slack_bot_token).send_report(
                command["channel_id"],
                report,
            )
            if not delivered:
                respond(
                    "Report delivery failed. Please confirm that the bot is in this channel.",
                    response_type="ephemeral",
                )
        except (FileNotFoundError, OSError) as error:
            logging.exception("Could not load the processed report")
            respond(str(error), response_type="ephemeral")

    return bolt_app


def start_bot() -> None:
    """Start Slack Socket Mode and the daily report scheduler."""
    latest_report = max(
        settings.processed_data_dir.glob("daily_report_*.csv"),
        key=lambda path: path.stat().st_mtime,
        default=None,
    )
    if latest_report:
        logging.info("/daily-report will return %s", latest_report.name)
    else:
        logging.warning("No processed report is currently available")

    if settings.scheduled_channel_ids:
        threading.Thread(target=scheduled_pipeline_loop, daemon=True).start()
    else:
        logging.warning(
            "SLACK_SCHEDULED_CHANNEL_IDS is empty; automated delivery is disabled"
        )

    logging.info("Slack bot and daily scheduler are running; press Ctrl+C to stop")
    bolt_app = create_bolt_app()
    SocketModeHandler(bolt_app, settings.slack_app_token).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Slack report bot.")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run the complete Gmail-to-Slack pipeline immediately and exit.",
    )
    parser.add_argument(
        "--fetch-gmail",
        action="store_true",
        help="Download the newest Google Ads report from Gmail into data/raw.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate a processed report from CSV files in data/raw and exit.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="When generating, send the report to configured Slack channels.",
    )
    args = parser.parse_args()
    if args.run_now:
        output_path = run_automated_pipeline()
        print(f"Automated pipeline completed: {output_path}")
        raise SystemExit(0)

    downloaded_path = None
    if args.fetch_gmail:
        downloaded_path = fetch_latest_gmail_report()
    if args.generate:
        selected_paths = [downloaded_path] if downloaded_path else None
        output_path = generate_daily_report(
            csv_paths=selected_paths,
            send_to_slack=args.send,
        )
        print(f"Processed CSV generated: {output_path}")
    elif downloaded_path:
        print(f"Raw CSV downloaded: {downloaded_path}")
    else:
        start_bot()
