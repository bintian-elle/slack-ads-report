"""Tests for Reddit Ads report normalization."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from reddit_service import RedditAdsApiError, RedditAdsService, parse_reddit_daily_report


class RedditAdsServiceTests(unittest.TestCase):
    def test_parses_only_requested_date_and_converts_units(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "date": "2026-08-18",
                        "spend": 106980447,
                        "conversion_purchase_total_value": 12345,
                    },
                    {
                        "date": "2026-08-19",
                        "spend": 54910082,
                        "conversion_purchase_total_value": 99999,
                    },
                ]
            }
        }
        metric = parse_reddit_daily_report(payload, date(2026, 8, 18))
        self.assertEqual(metric.name, "Reddit")
        self.assertEqual(metric.spend, Decimal("106.980447"))
        self.assertEqual(metric.revenue, Decimal("123.45"))

    def test_missing_requested_date_is_an_error(self):
        payload = {"data": {"metrics": [{"date": "2026-08-19"}]}}
        with self.assertRaisesRegex(RedditAdsApiError, "2026-08-18"):
            parse_reddit_daily_report(payload, date(2026, 8, 18))

    @patch("reddit_service.requests.get")
    def test_reads_reporting_timezone_from_ad_account(self, mock_get):
        response = Mock(ok=True)
        response.json.return_value = {
            "data": {"time_zone_id": "America/Los_Angeles"}
        }
        mock_get.return_value = response

        service = RedditAdsService.__new__(RedditAdsService)
        service.ad_account_id = "a2_test"
        timezone_name = service._fetch_account_timezone("access-token")

        self.assertEqual(timezone_name, "America/Los_Angeles")
        mock_get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
