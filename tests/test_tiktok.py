"""Tests for TikTok advertiser configuration and daily metrics."""

import unittest
from decimal import Decimal

from tiktok_service import parse_advertiser_ids, parse_tiktok_daily_reports


class TikTokAdsServiceTests(unittest.TestCase):
    def test_parses_python_style_advertiser_ids(self):
        self.assertEqual(
            parse_advertiser_ids("['123456789', '987654321']"),
            ("123456789", "987654321"),
        )

    def test_combines_advertisers_using_spend_weighted_roas(self):
        metric = parse_tiktok_daily_reports(
            [
                {
                    "data": {
                        "list": [
                            {
                                "metrics": {
                                    "spend": "50.00",
                                    "complete_payment_roas": "4.00",
                                }
                            }
                        ]
                    }
                },
                {
                    "data": {
                        "list": [
                            {
                                "metrics": {
                                    "spend": "100.00",
                                    "complete_payment_roas": "1.00",
                                }
                            }
                        ]
                    }
                },
            ]
        )

        self.assertEqual(metric.name, "TikTok")
        self.assertEqual(metric.spend, Decimal("150.00"))
        self.assertEqual(metric.revenue, Decimal("300.0000"))
        self.assertEqual(metric.roas, Decimal("2.00"))


if __name__ == "__main__":
    unittest.main()
