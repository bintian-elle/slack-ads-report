"""Slack bot entry point and local CSV report generator."""

import argparse
import logging
import threading
import time
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from bing_service import BingAdsService
from config import load_settings
from google_sheets_service import GoogleSheetsService
from google_ads_sheet_service import GoogleAdsSheetService
from meta_service import MetaAdsService
from report_service import (
    ChannelMetrics,
    cleanup_processed_reports,
    format_slack_report,
    load_processed_csv,
    save_processed_csv,
)
from reddit_service import RedditAdsService
from slack_service import SlackService
from shopify_service import ShopifyService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


settings = load_settings()


def fetch_google_ads_metrics(
    report_date: date = None,
) -> tuple:
    """Read one Google Ads Script date directly into normalized metrics."""
    sheets = GoogleAdsSheetService(
        spreadsheet_link=settings.google_sheets_raw,
        credentials_file=settings.google_service_account_file,
        raw_tab_name=settings.google_ads_raw_tab,
    )
    selected_date, metrics = sheets.fetch_daily_metrics(report_date=report_date)
    logging.info(
        "Google Ads Raw loaded directly for %s: %s channels",
        selected_date,
        len(metrics),
    )
    return selected_date, metrics


def generate_daily_report(
    report_date: date,
    metrics: Iterable[ChannelMetrics],
    channel_ids=None,
    send_to_slack=False,
) -> Path:
    """Enrich normalized metrics, save the report, and optionally send Slack."""
    metrics = list(metrics)
    if not any(metric.name == "Reddit" for metric in metrics):
        reddit = RedditAdsService(settings.reddit_credentials_file)
        reddit_metrics = reddit.fetch_daily_metrics(
            report_date=report_date,
        )
        metrics.append(reddit_metrics)
        logging.info(
            "Reddit report loaded for %s: spend=%s roas=%.2f",
            report_date,
            reddit_metrics.spend,
            reddit_metrics.roas,
        )
    if not any(metric.name == "Bing" for metric in metrics):
        bing = BingAdsService(
            client_id=settings.bing_google_client_id,
            client_secret=settings.bing_google_client_secret,
            refresh_token=settings.bing_google_refresh_token,
            developer_token=settings.bing_developer_token,
        )
        bing_metrics = bing.fetch_daily_metrics(report_date)
        metrics.append(bing_metrics)
        logging.info(
            "Bing report loaded for %s: spend=%s roas=%.2f",
            report_date,
            bing_metrics.spend,
            bing_metrics.roas,
        )
    if not any(metric.name == "Meta" for metric in metrics):
        meta = MetaAdsService(
            access_token=settings.meta_access_token,
            ad_account_id=settings.meta_ad_account_id,
        )
        meta_metrics, engagement_metrics = meta.fetch_daily_metrics(report_date)
        metrics.extend((meta_metrics, engagement_metrics))
        logging.info(
            "Meta report loaded for %s: spend=%s roas=%.2f atc=%s engagement=%s",
            report_date,
            meta_metrics.spend,
            meta_metrics.roas,
            meta_metrics.add_to_cart,
            engagement_metrics.spend,
        )
    if not any(metric.name == "Shopify" for metric in metrics):
        shopify = ShopifyService(
            store=settings.shopify_store,
            client_id=settings.shopify_client_id,
            client_secret=settings.shopify_client_secret,
        )
        total_revenue, ro_system_revenue = shopify.fetch_daily_revenue(report_date)
        metrics.extend(
            (
                ChannelMetrics("Shopify", Decimal("0"), total_revenue),
                ChannelMetrics("RO system", Decimal("0"), ro_system_revenue),
            )
        )
        logging.info(
            "Shopify report loaded for %s: total_revenue=%s ro_system=%s",
            report_date,
            total_revenue,
            ro_system_revenue,
        )
    report = format_slack_report(report_date, metrics)

    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.processed_data_dir / f"daily_report_{report_date:%Y-%m-%d}.csv"
    save_processed_csv(output_path, report_date, metrics)
    removed_reports = cleanup_processed_reports(settings.processed_data_dir)
    logging.info("Report saved to %s", output_path)
    if removed_reports:
        logging.info(
            "Removed %s processed reports outside the 7-day retention window",
            len(removed_reports),
        )

    if send_to_slack:
        slack = SlackService(settings.slack_bot_token)
        targets = tuple(channel_ids or settings.scheduled_channel_ids)
        if not targets:
            raise ValueError("No Slack channel IDs were configured.")
        for channel_id in targets:
            slack.send_report(channel_id, report)

    return output_path


def run_automated_pipeline() -> Path:
    """Read all platforms, update Sheets, send Slack, and retain seven days."""
    logging.info("Starting scheduled advertising report pipeline")
    report_date, google_metrics = fetch_google_ads_metrics()
    processed_path = generate_daily_report(
        report_date=report_date,
        metrics=google_metrics,
        send_to_slack=False,
    )
    sync_processed_report_to_google_sheet(processed_path)

    report_date, metrics = load_processed_csv(processed_path)
    report = format_slack_report(report_date, metrics)
    if not settings.scheduled_channel_ids:
        raise ValueError("No Slack channel IDs were configured.")
    slack = SlackService(settings.slack_bot_token)
    for channel_id in settings.scheduled_channel_ids:
        slack.send_report(channel_id, report)

    logging.info("Scheduled report pipeline completed: %s", processed_path.name)
    return processed_path


def sync_processed_report_to_google_sheet(processed_path: Path) -> dict:
    """Write one processed report into its monthly Actual Pacing row."""
    report_date, metrics = load_processed_csv(processed_path)
    sheets = GoogleSheetsService(
        spreadsheet_link=settings.google_sheets_link,
        credentials_file=settings.google_service_account_file,
    )
    result = sheets.write_actual_pacing(report_date, metrics)
    logging.info(
        "Google Sheet updated: tab=%s row=%s cells=%s",
        result["tab"],
        result["row"],
        len(result["cells"]),
    )
    return result


def generate_and_sync_dates(report_dates: Iterable[date]) -> list:
    """Process specified dates and update Sheets without sending to Slack."""
    results = []
    for report_date in report_dates:
        logging.info("Starting Sheet-only pipeline for %s", report_date)
        selected_date, google_metrics = fetch_google_ads_metrics(report_date)
        processed_path = generate_daily_report(
            report_date=selected_date,
            metrics=google_metrics,
            send_to_slack=False,
        )
        sheet_result = sync_processed_report_to_google_sheet(processed_path)
        results.append((processed_path, sheet_result))
    return results


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
    return settings.processed_data_dir / ".last_scheduled_report_date"


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
            "No processed report was found. Run: python app.py --run-now"
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
        help="Run the complete Sheets-to-Slack pipeline immediately and exit.",
    )
    parser.add_argument(
        "--sync-sheet",
        action="store_true",
        help="Write the newest processed CSV to Google Sheets and exit.",
    )
    parser.add_argument(
        "--sheet-only-dates",
        nargs="+",
        metavar="YYYY-MM-DD",
        help=(
            "Generate the specified dates and write them to Google Sheets "
            "without sending Slack messages."
        ),
    )
    args = parser.parse_args()
    if args.run_now:
        output_path = run_automated_pipeline()
        print(f"Automated pipeline completed: {output_path}")
        raise SystemExit(0)

    if args.sync_sheet:
        processed_paths = sorted(
            settings.processed_data_dir.glob("daily_report_*.csv")
        )
        if not processed_paths:
            raise FileNotFoundError("No processed report was found to sync.")
        result = sync_processed_report_to_google_sheet(processed_paths[-1])
        print(
            f"Google Sheet updated: {result['tab']} row {result['row']} "
            f"({len(result['cells'])} cells)"
        )
        raise SystemExit(0)

    if args.sheet_only_dates:
        requested_dates = [date.fromisoformat(value) for value in args.sheet_only_dates]
        results = generate_and_sync_dates(requested_dates)
        for processed_path, result in results:
            print(
                f"Google Sheet updated from {processed_path.name}: "
                f"{result['tab']} row {result['row']} "
                f"({len(result['cells'])} cells)"
            )
        raise SystemExit(0)

    start_bot()
