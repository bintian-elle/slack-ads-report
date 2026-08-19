"""Application configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    """Centralized settings shared by all application modules."""

    slack_bot_token: str
    slack_app_token: str
    scheduled_channel_ids: Tuple[str, ...]
    report_time: str
    report_timezone: str
    raw_data_dir: Path
    processed_data_dir: Path
    gmail_credentials_file: Path
    gmail_token_file: Path
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str
    google_sheets_link: str
    google_service_account_file: Path


def load_settings() -> Settings:
    """Load and validate application settings from .env."""
    load_dotenv(BASE_DIR / ".env")

    channel_ids = tuple(
        channel_id.strip()
        for channel_id in os.getenv("SLACK_SCHEDULED_CHANNEL_IDS", "").split(",")
        if channel_id.strip()
    )

    return Settings(
        slack_bot_token=_required_env("SLACK_BOT_TOKEN"),
        slack_app_token=_required_env("SLACK_APP_TOKEN"),
        scheduled_channel_ids=channel_ids,
        report_time=os.getenv("REPORT_TIME", "09:00").strip(),
        report_timezone=os.getenv("REPORT_TIMEZONE", "America/Chicago").strip(),
        raw_data_dir=BASE_DIR / "data" / "raw",
        processed_data_dir=BASE_DIR / "data" / "processed",
        gmail_credentials_file=BASE_DIR / "credentials" / "credentials.json",
        gmail_token_file=BASE_DIR / "credentials" / "token.json",
        gmail_client_id=os.getenv("CLIENT_ID", "").strip(),
        gmail_client_secret=os.getenv("CLIENT_SECRET", "").strip(),
        gmail_refresh_token=os.getenv("REFRESH_TOKEN", "").strip(),
        google_sheets_link=os.getenv("GOOGLE_SHEETS_LINK", "").strip(),
        google_service_account_file=BASE_DIR
        / os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_FILE",
            "credentials/google-service-account.json",
        ).strip(),
    )
