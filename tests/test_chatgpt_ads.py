"""Tests for ChatGPT Ads daily reporting."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from chatgpt_ads_service import ChatGPTAdsService, parse_chatgpt_ads_insights


class ChatGPTAdsServiceTests(unittest.TestCase):
    def test_aggregates_spend_and_attributed_sales(self):
        metric = parse_chatgpt_ads_insights(
            [
                {"spend": "10.25", "order_created_attributed_sales": "30.75"},
                {"spend": "4.75", "order_created_roas": "2"},
            ]
        )
        self.assertEqual(metric.name, "ChatGPT")
        self.assertEqual(metric.spend, Decimal("15.00"))
        self.assertEqual(metric.revenue, Decimal("40.25"))

    @patch("chatgpt_ads_service.requests.get")
    def test_uses_ad_account_timezone_for_daily_insights(self, get):
        account = Mock(ok=True)
        account.json.return_value = {"timezone": "America/New_York"}
        insights = Mock(ok=True)
        insights.json.return_value = {
            "data": [{"spend": "34.17", "order_created_roas": "0"}]
        }
        get.side_effect = [account, insights]

        metric = ChatGPTAdsService("secret").fetch_daily_metrics(date(2026, 10, 5))

        self.assertEqual(metric.spend, Decimal("34.17"))
        params = dict(get.call_args_list[1].kwargs["params"])
        self.assertIn('"timezone":"America/New_York"', params["time_ranges[]"])


if __name__ == "__main__":
    unittest.main()
