"""Tests for Google Sheets Budget Pacing tab selection."""

import unittest
from datetime import date
from decimal import Decimal

from google_sheets_service import (
    build_actual_pacing_values,
    locate_actual_pacing_row,
    parse_budget_pacing_tab,
    select_budget_pacing_tab,
    validate_actual_pacing_headers,
)
from report_service import ChannelMetrics


class GoogleSheetsTabSelectionTests(unittest.TestCase):
    def test_parses_abbreviated_month(self):
        self.assertEqual(
            parse_budget_pacing_tab("26 Aug - Budget Pacing"),
            (2026, 8),
        )

    def test_parses_full_month(self):
        self.assertEqual(
            parse_budget_pacing_tab("26 June - Budget Pacing"),
            (2026, 6),
        )

    def test_selects_tab_from_report_date(self):
        tabs = [
            "26 July - Budget Pacing",
            "26 Aug - Budget Pacing",
            "26 Sep - Budget Pacing",
        ]
        self.assertEqual(
            select_budget_pacing_tab(tabs, date(2026, 9, 1)),
            "26 Sep - Budget Pacing",
        )

    def test_accepts_sept_spelling(self):
        self.assertEqual(
            select_budget_pacing_tab(
                ["26 Sept - Budget Pacing"],
                date(2026, 9, 30),
            ),
            "26 Sept - Budget Pacing",
        )

    def test_missing_month_fails_instead_of_using_wrong_tab(self):
        with self.assertRaisesRegex(LookupError, "2026-09"):
            select_budget_pacing_tab(
                ["26 Aug - Budget Pacing"],
                date(2026, 9, 1),
            )

    def test_locates_date_only_below_actual_pacing(self):
        rows = [
            ["8/16/2026", "Budget Plan"],
            ["26 August Actual Pacing"],
            ["Date", "Day"],
            ["8/16/2026", "Sunday"],
        ]
        self.assertEqual(locate_actual_pacing_row(rows, date(2026, 8, 16)), 4)

    def test_validates_actual_pacing_headers(self):
        header = [""] * 33
        header[5] = "Total Shopify Revenue(RO System Only)"
        header[6] = "Total Shopify Revenue"
        header[12] = "PMAX Spend"
        header[13] = "PMAX ROAS"
        header[14] = "Search Spend"
        header[15] = "Search ROAS"
        header[16] = "Shopping Spend"
        header[17] = "Shopping ROAS"
        header[24] = "Google DG Spend"
        header[25] = "DG ROAS"
        header[28] = "Reddit Spend"
        header[29] = "Reddit ROAS"
        header[31] = "google ads spend"
        header[32] = "google ads ROAS"
        validate_actual_pacing_headers([["Actual Pacing"], header])

    def test_builds_google_and_reddit_cell_values(self):
        values = build_actual_pacing_values(
            [
                ChannelMetrics("Pmax", Decimal("100"), Decimal("300")),
                ChannelMetrics("Google Search", Decimal("50"), Decimal("100")),
                ChannelMetrics("Google Video", Decimal("10"), Decimal("0")),
                ChannelMetrics("Google DG", Decimal("40"), Decimal("80")),
                ChannelMetrics("Reddit", Decimal("25"), Decimal("50")),
                ChannelMetrics(
                    "Meta",
                    Decimal("75"),
                    Decimal("225"),
                    Decimal("94.26"),
                ),
                ChannelMetrics("Shopify", Decimal("0"), Decimal("15028.80")),
                ChannelMetrics("RO system", Decimal("0"), Decimal("10497.37")),
            ]
        )
        self.assertEqual(values["M"], 100.0)
        self.assertEqual(values["N"], 3.0)
        self.assertEqual(values["Y"], 50.0)
        self.assertEqual(values["Z"], 1.6)
        self.assertEqual(values["AC"], 25.0)
        self.assertEqual(values["AD"], 2.0)
        self.assertEqual(values["AE"], 94.26)
        self.assertEqual(values["F"], 10497.37)
        self.assertEqual(values["G"], 15028.80)
        self.assertEqual(values["AF"], 200.0)
        self.assertEqual(values["AG"], 2.4)

    def test_does_not_write_tiktok_columns(self):
        values = build_actual_pacing_values(
            [ChannelMetrics("TikTok", Decimal("50"), Decimal("100"))]
        )
        self.assertNotIn("AA", values)
        self.assertNotIn("AB", values)


if __name__ == "__main__":
    unittest.main()
