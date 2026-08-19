"""Tests for Gmail email parsing and report-date extraction."""

import unittest
from datetime import date

from gmail_service import _extract_csv_report_date, _find_view_report_url


class GmailServiceTests(unittest.TestCase):
    def test_finds_view_report_link(self):
        html = '<html><a href="https://example.com/report.csv">View report</a></html>'
        self.assertEqual(
            _find_view_report_url([html]),
            "https://example.com/report.csv",
        )

    def test_extracts_report_date_from_google_ads_csv(self):
        csv_data = b"""Daily Report
August 6
Day,Campaign type,Currency code,Cost,ROAS
2026-08-06,Search,USD,100.00,3.00
"""
        self.assertEqual(
            _extract_csv_report_date(csv_data, date(2026, 1, 1)),
            date(2026, 8, 6),
        )


if __name__ == "__main__":
    unittest.main()
