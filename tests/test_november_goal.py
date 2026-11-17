import unittest
from datetime import date
from report_service import format_slack_report
from google_sheets_service import extract_mtd_summary, MTD_SUMMARY_LABELS


class SheetRevenueTargetTests(unittest.TestCase):
    def test_reads_sheet_display_and_appends_without_recalculation(self):
        rows = [[label, '1'] for label in MTD_SUMMARY_LABELS]
        rows.append(['Avg Daily Revenue to Hit', '$51,082.21'])
        summary = extract_mtd_summary(rows)
        self.assertEqual(summary['avg_daily_revenue_to_hit'], '$51,082.21')
        report = format_slack_report(date(2026, 11, 16), [], mtd_summary=summary)
        self.assertTrue(report.endswith('• *Avg Daily Revenue to Hit:* $51,082.21'))
        self.assertNotIn('Reach $1M', report)

    def test_duplicate_target_fails_instead_of_selecting_wrong_value(self):
        rows = [[label, '1'] for label in MTD_SUMMARY_LABELS]
        rows.extend([['Avg Daily Revenue to Hit', '$1']] * 2)
        with self.assertRaises(LookupError):
            extract_mtd_summary(rows)
