"""Retrieve daily ChatGPT Ads spend and attributed purchase ROAS."""

import json
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable

import requests

from report_service import ChannelMetrics


CHATGPT_ADS_BASE_URL = "https://api.ads.openai.com/v1"


class ChatGPTAdsApiError(RuntimeError):
    """Raised when ChatGPT Ads authentication or reporting fails."""


def parse_chatgpt_ads_insights(rows: Iterable[Dict]) -> ChannelMetrics:
    """Aggregate spend and attributed sales returned by Ads Insights."""
    spend = Decimal("0")
    revenue = Decimal("0")
    for row in rows:
        row_spend = Decimal(str(row.get("spend") or "0"))
        sales = row.get("order_created_attributed_sales")
        roas = row.get("order_created_roas")
        spend += row_spend
        if sales is not None:
            revenue += Decimal(str(sales))
        elif roas is not None:
            revenue += row_spend * Decimal(str(roas))
    return ChannelMetrics(name="ChatGPT", spend=spend, revenue=revenue)


class ChatGPTAdsService:
    """Read one report day from the account-scoped Advertiser API."""

    def __init__(self, api_key: str) -> None:
        if not api_key.strip():
            raise ChatGPTAdsApiError("CHATGPT_ADS_TOKEN is required.")
        self.api_key = api_key.strip()
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    @staticmethod
    def _payload(response, action: str) -> Dict:
        try:
            payload = response.json()
        except ValueError as error:
            raise ChatGPTAdsApiError(
                f"ChatGPT Ads {action} returned a non-JSON response."
            ) from error
        if not response.ok:
            message = payload.get("message") or payload.get("error") or "Unknown error"
            raise ChatGPTAdsApiError(
                f"ChatGPT Ads {action} failed with HTTP "
                f"{response.status_code}: {message}"
            )
        return payload

    def fetch_daily_metrics(self, report_date: date) -> ChannelMetrics:
        """Fetch spend and purchase value for one account-local calendar day."""
        account_response = requests.get(
            f"{CHATGPT_ADS_BASE_URL}/ad_account",
            headers=self.headers,
            timeout=30,
        )
        account = self._payload(account_response, "account request")
        timezone = account.get("timezone")
        if not timezone:
            raise ChatGPTAdsApiError(
                "ChatGPT Ads account response did not contain a timezone."
            )

        time_range = json.dumps(
            {
                "type": "date_range",
                "since": report_date.isoformat(),
                "until": report_date.isoformat(),
                "timezone": timezone,
            },
            separators=(",", ":"),
        )
        response = requests.get(
            f"{CHATGPT_ADS_BASE_URL}/ad_account/insights",
            headers=self.headers,
            params=[
                ("aggregation_level", "ad_account"),
                ("time_granularity", "daily"),
                ("time_ranges[]", time_range),
                ("fields[]", "metadata.readable_time"),
                ("fields[]", "ad_account.spend"),
                ("fields[]", "order_created_attributed_sales"),
                ("fields[]", "order_created_attributed_sales_currency"),
                ("fields[]", "order_created_roas"),
            ],
            timeout=30,
        )
        payload = self._payload(response, "insights request")
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise ChatGPTAdsApiError(
                "ChatGPT Ads insights response did not contain a data list."
            )
        return parse_chatgpt_ads_insights(rows)
