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
    processed_data_dir: Path
    google_sheets_raw: str
    google_sheets_link: str
    google_service_account_file: Path
    reddit_credentials_file: Path
    bing_google_client_id: str
    bing_google_client_secret: str
    bing_google_refresh_token: str
    bing_developer_token: str
    shopify_store: str
    shopify_client_id: str
    shopify_client_secret: str
    meta_access_token: str
    meta_ad_account_id: str
    google_ads_raw_tab: str


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
        processed_data_dir=BASE_DIR / "data" / "processed",
        google_sheets_raw=_required_env("GOOGLE_SHEETS_RAW"),
        google_sheets_link=os.getenv("GOOGLE_SHEETS_LINK", "").strip(),
        google_service_account_file=BASE_DIR
        / os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_FILE",
            "credentials/google-service-account.json",
        ).strip(),
        reddit_credentials_file=BASE_DIR
        / os.getenv(
            "REDDIT_CREDENTIALS_FILE",
            "credentials/reddit.json",
        ).strip(),
        bing_google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        bing_google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        bing_google_refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN", "").strip(),
        bing_developer_token=os.getenv("BING_ADS_DEVELOPER_TOKEN", "").strip(),
        shopify_store=os.getenv("SHOPIFY_STORE", "").strip(),
        shopify_client_id=os.getenv("SHOPIFY_CLIENT_ID", "").strip(),
        shopify_client_secret=os.getenv("SHOPIFY_CLIENT_SECRET", "").strip(),
        meta_access_token=os.getenv("META_ACCESS_TOKEN", "").strip(),
        meta_ad_account_id=os.getenv("META_AD_ACCOUNT_ID", "").strip(),
        google_ads_raw_tab=os.getenv("GOOGLE_ADS_RAW_TAB", "Google Ads Raw").strip(),
    )
