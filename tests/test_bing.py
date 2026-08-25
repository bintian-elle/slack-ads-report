"""Tests for Bing Ads OAuth refresh and report normalization."""

import io
import unittest
import zipfile
from decimal import Decimal
from unittest.mock import Mock, patch

from bing_service import BingAdsService


class BingAdsServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = BingAdsService("client", "secret", "refresh", "developer")

    @patch("bing_service.requests.post")
    def test_refreshes_google_access_token(self, mock_post):
        response = Mock(ok=True)
        response.json.return_value = {"access_token": "fresh-token"}
        mock_post.return_value = response

        self.assertEqual(self.service._refresh_access_token(), "fresh-token")
        self.assertEqual(
            mock_post.call_args.kwargs["data"]["grant_type"], "refresh_token"
        )

    def test_parses_zipped_bing_report(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "report.csv",
                "TimePeriod,AccountId,Spend,Revenue\n"
                "2026-08-18,123,104.35,148.10\n",
            )

        metrics = self.service._parse_report(buffer.getvalue())
        self.assertEqual(metrics.spend, Decimal("104.35"))
        self.assertEqual(metrics.revenue, Decimal("148.10"))
        self.assertEqual(metrics.roas.quantize(Decimal("0.01")), Decimal("1.42"))


if __name__ == "__main__":
    unittest.main()
