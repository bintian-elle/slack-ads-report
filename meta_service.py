"""Retrieve daily Meta advertising metrics by campaign."""

from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, Tuple

import requests

from report_service import ChannelMetrics


META_GRAPH_API = "https://graph.facebook.com/v23.0"


class MetaAdsApiError(RuntimeError):
    """Raised when Meta account or Insights API access fails."""


def _action_value(row: Dict, names: Iterable[str]) -> Decimal:
    values = {
        item.get("action_type"): Decimal(str(item.get("value") or "0"))
        for item in row.get("action_values", [])
    }
    for name in names:
        if name in values:
            return values[name]
    return Decimal("0")


def parse_meta_campaigns(rows: Iterable[Dict]) -> Tuple[ChannelMetrics, ChannelMetrics]:
    """Split Meta campaigns into core spend, ATC spend, and engagement spend."""
    meta_spend = Decimal("0")
    meta_revenue = Decimal("0")
    atc_spend = Decimal("0")
    engagement_spend = Decimal("0")

    for row in rows:
        name = str(row.get("campaign_name") or "")
        normalized = name.lower()
        spend = Decimal(str(row.get("spend") or "0"))
        if "_atc" in normalized or normalized.endswith(" atc"):
            atc_spend += spend
            # ATC is a subset of Meta spend, not an additional spend channel.
            meta_spend += spend
            meta_revenue += _action_value(
                row,
                (
                    "omni_purchase",
                    "offsite_conversion.fb_pixel_purchase",
                    "purchase",
                ),
            )
        elif "engagement" in normalized or normalized.startswith("instagram post:"):
            engagement_spend += spend
        else:
            meta_spend += spend
            meta_revenue += _action_value(
                row,
                (
                    "omni_purchase",
                    "offsite_conversion.fb_pixel_purchase",
                    "purchase",
                ),
            )

    return (
        ChannelMetrics(
            name="Meta",
            spend=meta_spend,
            revenue=meta_revenue,
            add_to_cart=atc_spend,
        ),
        ChannelMetrics(
            name="Engagement",
            spend=engagement_spend,
            revenue=Decimal("0"),
        ),
    )


class MetaAdsService:
    """Request account-local daily Meta campaign insights."""

    def __init__(self, access_token: str, ad_account_id: str) -> None:
        if not access_token.strip() or not ad_account_id.strip():
            raise MetaAdsApiError(
                "META_ACCESS_TOKEN and META_AD_ACCOUNT_ID are required."
            )
        self.access_token = access_token.strip()
        account_id = ad_account_id.strip()
        self.ad_account_id = (
            account_id if account_id.startswith("act_") else f"act_{account_id}"
        )

    def fetch_daily_metrics(
        self, report_date: date
    ) -> Tuple[ChannelMetrics, ChannelMetrics]:
        """Fetch one date; Meta interprets it in the ad account's timezone."""
        response = requests.get(
            f"{META_GRAPH_API}/{self.ad_account_id}/insights",
            params={
                "access_token": self.access_token,
                "level": "campaign",
                "time_range": (
                    '{"since":"%s","until":"%s"}'
                    % (report_date.isoformat(), report_date.isoformat())
                ),
                "fields": "campaign_id,campaign_name,spend,action_values",
                "limit": 500,
            },
            timeout=30,
        )
        if not response.ok:
            error = response.json().get("error", {})
            raise MetaAdsApiError(
                f"Meta Insights failed with HTTP {response.status_code}: "
                f"{error.get('message', response.text[:500])}"
            )
        payload = response.json()
        rows = list(payload.get("data") or [])
        while payload.get("paging", {}).get("next"):
            response = requests.get(payload["paging"]["next"], timeout=30)
            if not response.ok:
                raise MetaAdsApiError(
                    f"Meta Insights pagination failed with HTTP {response.status_code}."
                )
            payload = response.json()
            rows.extend(payload.get("data") or [])
        return parse_meta_campaigns(rows)
