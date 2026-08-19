"""Tests for report calculations and formatting."""

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from report_service import (
    ChannelMetrics,
    calculate_roas,
    format_slack_report,
    load_processed_csv,
    process_csv_files,
    save_processed_csv,
)


class ReportServiceTests(unittest.TestCase):
    def test_calculate_roas(self):
        self.assertEqual(
            calculate_roas(Decimal("300"), Decimal("100")),
            Decimal("3"),
        )

    def test_zero_spend_returns_zero_roas(self):
        self.assertEqual(
            calculate_roas(Decimal("100"), Decimal("0")),
            Decimal("0"),
        )

    def test_format_slack_report(self):
        report = format_slack_report(
            date(2026, 8, 6),
            [ChannelMetrics("Meta", Decimal("100"), Decimal("500"), 12)],
        )
        self.assertIn("Daily Report 08/06", report)
        self.assertIn("ROAS:* 5.00", report)
        self.assertIn("ATC:* 12", report)

    def test_process_google_ads_csv_with_metadata_rows(self):
        csv_content = """Daily Report
\"August 4, 2026 - August 4, 2026\"
Day,Campaign type,Currency code,Cost,ROAS
2026-08-04,Search,USD,100.00,3.50
2026-08-04,Performance Max,USD,200.00,2.00
"""
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "google_ads.csv"
            csv_path.write_text(csv_content, encoding="utf-8")
            report_date, metrics = process_csv_files([csv_path])

        self.assertEqual(report_date, date(2026, 8, 4))
        self.assertEqual(metrics[0].name, "Google Search")
        self.assertEqual(metrics[0].revenue, Decimal("350.0000"))
        self.assertEqual(metrics[1].name, "Pmax")

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
        self.assertEqual(loaded_metrics, metrics)

    def test_process_reddit_csv_without_renaming(self):
        csv_content = """Campaign Name,Amount Spent (USD),Purchase ROAS (Return on Ad Spend),Currency
Campaign A,10.50,2.00,USD
Campaign B,20.00,3.00,USD
Campaign C,,,USD
"""
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "2026_Daily_Report_2026-08-12_2026-08-12.csv"
            csv_path.write_text(csv_content, encoding="utf-8")
            report_date, metrics = process_csv_files([csv_path])

        self.assertEqual(report_date, date(2026, 8, 12))
        self.assertEqual(metrics[0].name, "Reddit")
        self.assertEqual(metrics[0].spend, Decimal("30.50"))
        self.assertEqual(metrics[0].revenue, Decimal("81.0000"))

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


if __name__ == "__main__":
    unittest.main()
