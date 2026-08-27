"""Tests for Google Sheets Budget Pacing tab selection."""

import unittest
from datetime import date
from decimal import Decimal

from google_sheets_service import (
    build_actual_pacing_values,
    extract_mtd_summary,
    locate_actual_pacing_row,
    parse_budget_pacing_tab,
    parse_optional_tiktok_values,
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

    def test_extracts_mtd_summary_by_label_from_any_columns(self):
        summary = extract_mtd_summary(
            [
                ["", "MTD Paid Media Spend", "$93,313.83"],
                ["", "MTD Total Revenue", "$330,581.75"],
                ["", "MTD ROAS", "3.54"],
                ["", "Avg Daily Budget Remaining", "$5,848.27"],
                ["", "MTD Follower Growth", "0"],
                ["", "MTD Spend Pacing %", "89.77%"],
                ["", "MTD Total Revenue Pacing %", "86.09%"],
            ]
        )
        self.assertEqual(summary["paid_media_spend"], "$93,313.83")
        self.assertEqual(summary["total_revenue"], "$330,581.75")
        self.assertEqual(summary["roas"], "3.54")
        self.assertEqual(summary["avg_daily_budget_remaining"], "$5,848.27")
        self.assertEqual(summary["follower_growth"], "0")
        self.assertEqual(summary["spend_pacing"], "89.77%")
        self.assertEqual(summary["revenue_pacing"], "86.09%")

    def test_mtd_summary_requires_all_seven_unique_labels(self):
        with self.assertRaises(LookupError):
            extract_mtd_summary(
                [
                    ["MTD Paid Media Spend", "$1"],
                    ["MTD Total Revenue", "$2"],
                ]
            )

    def test_reads_optional_tiktok_spend_and_roas(self):
        metric = parse_optional_tiktok_values([["54.53", "7.49"]])
        self.assertEqual(metric.name, "TikTok")
        self.assertEqual(metric.spend, Decimal("54.53"))
        self.assertEqual(metric.roas, Decimal("7.49"))

    def test_blank_tiktok_spend_is_not_available(self):
        self.assertIsNone(parse_optional_tiktok_values([]))
        self.assertIsNone(parse_optional_tiktok_values([[""]]))


if __name__ == "__main__":
    unittest.main()
