"""Tests for Meta campaign classification and metrics."""

import unittest
from decimal import Decimal

from meta_service import parse_meta_campaigns


class MetaAdsServiceTests(unittest.TestCase):
    def test_splits_atc_and_engagement_without_double_counting(self):
        rows = [
            {
                "campaign_name": "Prospecting_SalesConversion",
                "spend": "100.00",
                "action_values": [
                    {"action_type": "purchase", "value": "300"},
                    {"action_type": "omni_purchase", "value": "300"},
                ],
            },
            {"campaign_name": "Prospecting_ATC", "spend": "12.34"},
            {"campaign_name": "Instagram post: test", "spend": "5.67"},
            {"campaign_name": "Prospecting_Traffic", "spend": "20.00"},
        ]

        meta, engagement = parse_meta_campaigns(rows)

        self.assertEqual(meta.spend, Decimal("132.34"))
        self.assertEqual(meta.revenue, Decimal("300"))
        self.assertEqual(meta.add_to_cart, Decimal("12.34"))
        self.assertEqual(engagement.spend, Decimal("5.67"))


if __name__ == "__main__":
    unittest.main()
