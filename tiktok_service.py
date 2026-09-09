"""Retrieve daily TikTok Ads spend and purchase ROAS."""

import json
import re
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, Tuple

import requests

from report_service import ChannelMetrics


TIKTOK_REPORT_URL = (
    "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"
)


class TikTokAdsApiError(RuntimeError):
    """Raised when TikTok authentication or reporting fails."""


def parse_advertiser_ids(value: str) -> Tuple[str, ...]:
    """Accept comma-separated, JSON, or Python-style advertiser ID lists."""
    advertiser_ids = tuple(re.findall(r"\d+", value or ""))
    if not advertiser_ids:
        raise TikTokAdsApiError("TIKTOK_ADVERTISER_IDS does not contain an ID.")
    return advertiser_ids


def parse_tiktok_daily_reports(payloads: Iterable[Dict]) -> ChannelMetrics:
    """Combine advertiser reports using spend-weighted purchase ROAS."""
    spend = Decimal("0")
    revenue = Decimal("0")
    for payload in payloads:
        rows = payload.get("data", {}).get("list", [])
        if not isinstance(rows, list):
            raise TikTokAdsApiError("TikTok report response did not contain a list.")
        for row in rows:
            metrics = row.get("metrics", {})
            row_spend = Decimal(str(metrics.get("spend") or "0"))
            row_roas = Decimal(
                str(metrics.get("complete_payment_roas") or "0")
            )
            spend += row_spend
            revenue += row_spend * row_roas
    return ChannelMetrics(name="TikTok", spend=spend, revenue=revenue)


class TikTokAdsService:
    """Request one report day in each TikTok advertiser account's timezone."""

    def __init__(self, access_token: str, advertiser_ids: str) -> None:
        if not access_token.strip():
            raise TikTokAdsApiError("TIKTOK_ACCESS_TOKEN is required.")
        self.access_token = access_token.strip()
        self.advertiser_ids = parse_advertiser_ids(advertiser_ids)

    def fetch_daily_metrics(self, report_date: date) -> ChannelMetrics:
        """Fetch Spend and complete-payment ROAS for all configured accounts."""
        payloads = []
        for advertiser_id in self.advertiser_ids:
            response = requests.get(
                TIKTOK_REPORT_URL,
                headers={"Access-Token": self.access_token},
                params={
                    "advertiser_id": advertiser_id,
                    "report_type": "BASIC",
                    "data_level": "AUCTION_ADVERTISER",
                    "dimensions": json.dumps(["stat_time_day"]),
                    "metrics": json.dumps(
                        ["spend", "complete_payment_roas"]
                    ),
                    "start_date": report_date.isoformat(),
                    "end_date": report_date.isoformat(),
                    "page": 1,
                    "page_size": 100,
                },
                timeout=30,
            )
            try:
                payload = response.json()
            except ValueError as error:
                raise TikTokAdsApiError(
                    "TikTok report returned a non-JSON response."
                ) from error
            if not response.ok or payload.get("code") != 0:
                raise TikTokAdsApiError(
                    f"TikTok report failed for advertiser {advertiser_id} "
                    f"with HTTP {response.status_code}: "
                    f"{payload.get('message', response.text[:500])}"
                )
            payloads.append(payload)
        return parse_tiktok_daily_reports(payloads)
