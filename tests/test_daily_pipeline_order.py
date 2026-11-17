import unittest
from unittest.mock import patch, Mock
from datetime import date
from pathlib import Path
import app


class DailyPipelineOrderTests(unittest.TestCase):
    def test_historical_failure_notifies_and_daily_still_sends(self):
        events = []
        with patch.object(app, 'fetch_google_ads_metrics', return_value=(date(2026,11,16), [])), \
             patch.object(app, 'refresh_historical_sheet', side_effect=lambda d: (events.append('history'), (_ for _ in ()).throw(RuntimeError()))[-1]), \
             patch.object(app, 'notify_pipeline_failure', side_effect=lambda *a: events.append('notify'), create=True), \
             patch.object(app, 'generate_daily_report', side_effect=lambda **kw: events.append('daily') or Path('report.csv')), \
             patch.object(app, 'sync_processed_report_to_google_sheet', return_value={'mtd_summary':{}}), \
             patch.object(app, 'load_processed_csv', return_value=(date(2026,11,16), [])), \
             patch.object(app, 'format_slack_report', return_value='report'), \
             patch.object(app, 'settings', Mock(scheduled_channel_ids=['channel'], slack_bot_token='secret')), \
             patch.object(app, 'SlackService') as slack:
            slack.return_value.send_report.side_effect=lambda *a: events.append('send') or True
            app.run_automated_pipeline()
        self.assertEqual(events, ['history', 'notify', 'daily', 'send'])
