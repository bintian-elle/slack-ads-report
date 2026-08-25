"""Retrieve daily Reddit Ads spend and purchase revenue through OAuth2."""

import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

import requests

from report_service import ChannelMetrics


REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_ADS_API_BASE = "https://ads-api.reddit.com/api/v3"
USER_AGENT = "slack-ads-report/1.0"


class RedditAdsApiError(RuntimeError):
    """Raised when Reddit authentication or reporting fails."""


def parse_reddit_daily_report(payload: Dict, report_date: date) -> ChannelMetrics:
    """Normalize only the requested date from a Reddit v3 report response."""
    metrics = payload.get("data", {}).get("metrics", [])
    if not isinstance(metrics, list):
        raise RedditAdsApiError("Reddit report response did not contain metrics.")

    matching_rows = [
        row
        for row in metrics
        if isinstance(row, dict) and row.get("date") == report_date.isoformat()
    ]
    if not matching_rows:
        raise RedditAdsApiError(
            f"Reddit report did not contain a row for {report_date:%Y-%m-%d}."
        )

    spend_micros = sum(
        (Decimal(str(row.get("spend") or 0)) for row in matching_rows),
        Decimal("0"),
    )
    purchase_value_cents = sum(
        (
            Decimal(str(row.get("conversion_purchase_total_value") or 0))
            for row in matching_rows
        ),
        Decimal("0"),
    )

    return ChannelMetrics(
        name="Reddit",
        spend=spend_micros / Decimal("1000000"),
        revenue=purchase_value_cents / Decimal("100"),
    )


class RedditAdsService:
    """Refresh Reddit OAuth tokens and request one ad-account-local report day."""

    def __init__(self, credentials_file: Path) -> None:
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"Reddit credentials file was not found: {credentials_file}"
            )
        credentials = json.loads(credentials_file.read_text(encoding="utf-8"))
        required_fields = (
            "CLIENT_ID",
            "CLIENT_SECRET",
            "refresh_token",
            "Ad_Account_ID",
        )
        missing = [
            field
            for field in required_fields
            if not str(credentials.get(field, "")).strip()
        ]
        if missing:
            raise RedditAdsApiError(
                "Missing Reddit credential fields: " + ", ".join(missing)
            )

        self.client_id = credentials["CLIENT_ID"].strip()
        self.client_secret = credentials["CLIENT_SECRET"].strip()
        self.refresh_token = credentials["refresh_token"].strip()
        self.ad_account_id = credentials["Ad_Account_ID"].strip()

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        return response.text[:1000]

    def _refresh_access_token(self) -> str:
        response = requests.post(
            REDDIT_TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if not response.ok:
            raise RedditAdsApiError(
                f"Reddit token refresh failed with HTTP {response.status_code}: "
                f"{self._error_detail(response)}"
            )
        access_token = response.json().get("access_token")
        if not access_token:
            raise RedditAdsApiError(
                "Reddit token response did not contain an access token."
            )
        return access_token

    def _fetch_account_timezone(self, access_token: str) -> str:
        """Read the reporting timezone configured on the Reddit ad account."""
        response = requests.get(
            f"{REDDIT_ADS_API_BASE}/ad_accounts/{self.ad_account_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=30,
        )
        if not response.ok:
            raise RedditAdsApiError(
                "Reddit ad account lookup failed with HTTP "
                f"{response.status_code}: {self._error_detail(response)}"
            )

        timezone_name = str(
            response.json().get("data", {}).get("time_zone_id", "")
        ).strip()
        if not timezone_name:
            raise RedditAdsApiError(
                "Reddit ad account response did not contain time_zone_id."
            )
        try:
            ZoneInfo(timezone_name)
        except Exception as error:
            raise RedditAdsApiError(
                f"Reddit returned an invalid account timezone: {timezone_name}"
            ) from error
        return timezone_name

    def fetch_daily_metrics(
        self,
        report_date: date,
    ) -> ChannelMetrics:
        """Fetch one calendar day in the Reddit ad account's own timezone."""
        access_token = self._refresh_access_token()
        timezone_name = self._fetch_account_timezone(access_token)
        report_timezone = ZoneInfo(timezone_name)
        local_start = datetime.combine(
            report_date,
            time.min,
            tzinfo=report_timezone,
        )
        local_end = datetime.combine(
            report_date + timedelta(days=1),
            time.min,
            tzinfo=report_timezone,
        )
        starts_at = local_start.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        ends_at = local_end.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        response = requests.post(
            f"{REDDIT_ADS_API_BASE}/ad_accounts/{self.ad_account_id}/reports",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={
                "data": {
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "time_zone_id": timezone_name,
                    "breakdowns": ["DATE"],
                    "fields": [
                        "SPEND",
                        "CONVERSION_PURCHASE_TOTAL_VALUE",
                        "CONVERSION_ROAS",
                    ],
                }
            },
            timeout=30,
        )
        if not response.ok:
            raise RedditAdsApiError(
                f"Reddit report failed with HTTP {response.status_code}: "
                f"{self._error_detail(response)}"
            )
        return parse_reddit_daily_report(response.json(), report_date)
