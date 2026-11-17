import unittest
from datetime import date
from report_service import format_slack_report


class NovemberGoalTests(unittest.TestCase):
    def report(self, day, revenue):
        summary = dict(paid_media_spend='$1', total_revenue=revenue,
                       roas='1', avg_daily_budget_remaining='$1',
                       follower_growth='0', spend_pacing='100%', revenue_pacing='100%')
        return format_slack_report(day, [], mtd_summary=summary)

    def test_remaining_days_exclude_report_date(self):
        self.assertTrue(self.report(date(2026, 11, 16), '$303,099.03').endswith(
            '• *Avg Daily Sales Needed to Reach $1M (Nov):* $49,778.64'))

    def test_achieved_goal_is_zero(self):
        self.assertTrue(self.report(date(2026, 11, 30), '$1,010,000').endswith(
            '• *Avg Daily Sales Needed to Reach $1M (Nov):* $0.00'))

    def test_month_end_reports_shortfall(self):
        self.assertTrue(self.report(date(2026, 11, 30), '$900,000').endswith(
            '• *Avg Daily Sales Needed to Reach $1M (Nov):* Month ended — $100,000.00 short'))

    def test_other_months_do_not_show_goal(self):
        self.assertNotIn('Reach $1M', self.report(date(2026, 12, 1), '$100'))

    def test_invalid_revenue_is_unavailable(self):
        self.assertTrue(self.report(date(2026, 11, 16), 'N/A').endswith(
            '• *Avg Daily Sales Needed to Reach $1M (Nov):* N/A'))
