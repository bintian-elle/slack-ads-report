import unittest
from datetime import date
from google_sheets_service import extract_mtd_summary, MTD_SUMMARY_LABELS
from report_service import format_slack_report
from unittest.mock import Mock, patch
from facebook_followers_service import fetch_followers


class FacebookFollowersTests(unittest.TestCase):
    def responses(self, values):
        return [Mock(ok=True, json=Mock(return_value=p)) for p in [
            {'data': [{'id': '680910961767969', 'access_token': 'page-secret'}]},
            {'followers_count': 17901},
            {'data': [{'values': values}]}]]

    def test_api_end_time_maps_to_previous_report_day(self):
        values = [{'end_time': '2026-11-02T08:00:00+0000', 'value': 4}]
        with patch('facebook_followers_service.requests.get', side_effect=self.responses(values)):
            total, daily = fetch_followers('secret', date(2026, 11, 1))
        self.assertEqual(total, 17901)
        self.assertEqual(daily, {date(2026, 11, 1): 4})

    def test_missing_days_are_not_treated_as_zero(self):
        with patch('facebook_followers_service.requests.get', side_effect=self.responses([])):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                fetch_followers('secret', date(2026, 11, 1))

    def test_total_is_read_from_sheet_and_shown_after_engagement(self):
        rows = [[label, '0'] for label in MTD_SUMMARY_LABELS]
        rows.append(['Current Facebook Followers', '17,901'])
        summary = extract_mtd_summary(rows)
        summary['daily_followers'] = '4'
        self.assertEqual(summary['current_followers'], '17,901')
        report = format_slack_report(date(2026, 11, 16), [], mtd_summary=summary)
        self.assertIn('*Engagement:* - | *Current Followers:* 17,901(+4)', report)
