"""Tests for report calculations, formatting, storage, and retention."""

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from report_service import (
    ChannelMetrics,
    calculate_roas,
    cleanup_processed_reports,
    format_slack_report,
    load_processed_csv,
    save_processed_csv,
)


class ReportServiceTests(unittest.TestCase):
    def test_calculate_roas(self):
        self.assertEqual(calculate_roas(Decimal("300"), Decimal("100")), Decimal("3"))

    def test_zero_spend_returns_zero_roas(self):
        self.assertEqual(calculate_roas(Decimal("100"), Decimal("0")), Decimal("0"))

    def test_format_slack_report(self):
        report = format_slack_report(
            date(2026, 8, 6),
            [ChannelMetrics("Meta", Decimal("100"), Decimal("500"), 12)],
        )
        self.assertIn("📊 *Bluevua Daily Report 08/06*", report)
        self.assertIn("• *Meta Spend:* $100.00 | *ROAS:* 5.00", report)
        self.assertIn("*ATC:* $12.00", report)
        self.assertIn("• *Pmax Spend:* - | *ROAS:* -", report)
        self.assertIn("*RO system Revenue:* -", report)
        self.assertIn("--------------------------------------------------", report)

    def test_shopify_revenue_and_ro_system_placement(self):
        report = format_slack_report(
            date(2026, 8, 18),
            [
                ChannelMetrics("Shopify", Decimal("0"), Decimal("15028.80")),
                ChannelMetrics("RO system", Decimal("0"), Decimal("10497.37")),
            ],
        )
        self.assertIn("*Total Revenue:* $15,028.80", report)
        divider = report.index("--------------------------------------------------")
        ro_system = report.index("• *RO system Revenue:* $10,497.37")
        self.assertLess(divider, ro_system)

    def test_totals_are_calculated_without_tiktok(self):
        report = format_slack_report(
            date(2026, 8, 24),
            [
                ChannelMetrics("Pmax", Decimal("10"), Decimal("20")),
                ChannelMetrics("Google Search", Decimal("10"), Decimal("20")),
                ChannelMetrics("Shopping", Decimal("10"), Decimal("20")),
                ChannelMetrics("Meta", Decimal("10"), Decimal("20")),
                ChannelMetrics("Bing", Decimal("10"), Decimal("20")),
                ChannelMetrics("Engagement", Decimal("10"), Decimal("0")),
                ChannelMetrics("Google DG", Decimal("10"), Decimal("20")),
                ChannelMetrics("Reddit", Decimal("10"), Decimal("20")),
                ChannelMetrics("Shopify", Decimal("0"), Decimal("400")),
            ],
        )
        self.assertIn(
            "*Total Spend:* $80.00 | *Total Revenue:* $400.00 | *ROAS:* 5.00",
            report,
        )
        self.assertIn("• *TikTok Spend:* - | *ROAS:* -", report)

    def test_processed_csv_contains_data_without_slack_markup(self):
        metrics = [
            ChannelMetrics("Google Search", Decimal("100"), Decimal("350")),
            ChannelMetrics("Pmax", Decimal("200"), Decimal("400")),
        ]
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "daily_report.csv"
            save_processed_csv(csv_path, date(2026, 8, 4), metrics)
            content = csv_path.read_text(encoding="utf-8")
            loaded_date, loaded_metrics = load_processed_csv(csv_path)

        self.assertIn("Date,Total Spend,Total Revenue,Total ROAS", content)
        self.assertNotIn("*", content)
        self.assertNotIn("•", content)
        self.assertEqual(loaded_date, date(2026, 8, 4))
        self.assertEqual(
            {row.name: row.spend for row in loaded_metrics},
            {row.name: row.spend for row in metrics},
        )
        self.assertNotIn("Google Search Revenue", content)
        self.assertIn("Google Ads Spend", content)

    def test_processed_reddit_only_saves_spend_and_roas(self):
        metrics = [ChannelMetrics("Reddit", Decimal("100"), Decimal("250"))]
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "daily_report.csv"
            save_processed_csv(csv_path, date(2026, 8, 12), metrics)
            header = csv_path.read_text(encoding="utf-8").splitlines()[0]
            loaded_date, loaded_metrics = load_processed_csv(csv_path)

        self.assertIn("Reddit Spend", header)
        self.assertIn("Reddit ROAS", header)
        self.assertNotIn("Reddit Revenue", header)
        self.assertNotIn("Reddit ATC", header)
        self.assertEqual(loaded_date, date(2026, 8, 12))
        self.assertEqual(loaded_metrics[0].roas, Decimal("2.50"))

    def test_processed_retention_keeps_latest_seven_calendar_days(self):
        with TemporaryDirectory() as temp_dir:
            processed_dir = Path(temp_dir)
            for day in range(1, 11):
                (processed_dir / f"daily_report_2026-08-{day:02d}.csv").write_text(
                    "test", encoding="utf-8"
                )
            unrelated = processed_dir / "notes.csv"
            unrelated.write_text("keep", encoding="utf-8")

            removed = cleanup_processed_reports(processed_dir)
            remaining = sorted(
                path.name for path in processed_dir.glob("daily_report_*.csv")
            )
            unrelated_still_exists = unrelated.exists()

        self.assertEqual(len(removed), 3)
        self.assertEqual(remaining[0], "daily_report_2026-08-04.csv")
        self.assertEqual(remaining[-1], "daily_report_2026-08-10.csv")
        self.assertTrue(unrelated_still_exists)


if __name__ == "__main__":
    unittest.main()
