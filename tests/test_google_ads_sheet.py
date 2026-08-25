"""Tests for Google Ads Script raw-tab parsing."""

import unittest
from datetime import date

from google_ads_sheet_service import GoogleAdsSheetError, parse_google_ads_raw_rows
from report_service import CAMPAIGN_TYPE_NAMES


class GoogleAdsSheetTests(unittest.TestCase):
    def test_selects_latest_date(self):
        report_date, rows = parse_google_ads_raw_rows(
            [
                ["Date", "Campaign Type", "Spend", "Revenue", "ROAS", "Updated At"],
                ["2026-08-19", "Search", 100, 300, 3, "2026-08-20T03:00:00-07:00"],
                ["2026-08-20", "Search", 120, 360, 3, "2026-08-21T03:00:00-07:00"],
                ["2026-08-20", "Performance Max", 200, 500, 2.5, "2026-08-21T03:00:00-07:00"],
            ]
        )
        self.assertEqual(report_date, date(2026, 8, 20))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["Campaign Type"], "Performance Max")

    def test_rejects_wrong_headers(self):
        with self.assertRaisesRegex(GoogleAdsSheetError, "headers"):
            parse_google_ads_raw_rows([["Date", "Wrong"]])

    def test_accepts_google_sheets_date_serial(self):
        report_date, rows = parse_google_ads_raw_rows(
            [
                ["Date", "Campaign Type", "Spend", "Revenue", "ROAS", "Updated At"],
                [46254, "Search", 120, 360, 3, "2026-08-21T03:00:00-07:00"],
            ]
        )
        self.assertEqual(report_date, date(2026, 8, 20))
        self.assertEqual(rows[0]["Campaign Type"], "Search")

    def test_selects_requested_date(self):
        report_date, rows = parse_google_ads_raw_rows(
            [
                ["Date", "Campaign Type", "Spend", "Revenue", "ROAS", "Updated At"],
                ["2026-08-21", "Search", "10", "20", "2", "now"],
                ["2026-08-22", "Search", "30", "60", "2", "now"],
            ],
            report_date=date(2026, 8, 21),
        )
        self.assertEqual(report_date, date(2026, 8, 21))
        self.assertEqual(rows[0]["Spend"], "10")

    def test_requested_date_must_exist(self):
        with self.assertRaises(GoogleAdsSheetError):
            parse_google_ads_raw_rows(
                [
                    ["Date", "Campaign Type", "Spend", "Revenue", "ROAS", "Updated At"],
                    ["2026-08-22", "Search", "30", "60", "2", "now"],
                ],
                report_date=date(2026, 8, 21),
            )

    def test_google_campaign_types_map_to_report_channels(self):
        self.assertEqual(CAMPAIGN_TYPE_NAMES["Performance Max"], "Pmax")
        self.assertEqual(CAMPAIGN_TYPE_NAMES["Search"], "Google Search")
        self.assertEqual(CAMPAIGN_TYPE_NAMES["Demand Gen"], "Google DG")


if __name__ == "__main__":
    unittest.main()
