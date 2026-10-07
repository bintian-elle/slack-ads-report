import unittest
from unittest.mock import patch, Mock
from datetime import datetime, timezone
from get_tiktok_public_data import extract_video_id, get_tiktok_metrics, TikTokBlocked
from kol_organic_tiktok import metric_changes, backoff_until
from kol_organic_tiktok import run
import tempfile
from pathlib import Path


class PublicTikTokTests(unittest.TestCase):
    def test_daily_dedupe_interval_and_restart(self):
        headers = ['Creator','Organic Launch Date','Post Link','KOL Fee','Views',
                   'Interaction','Likes','Comments','Saves','Shares','CPM','CPE']
        values = [headers,['a','','https://www.tiktok.com/@a/video/123',100],
                  ['duplicate','','https://www.tiktok.com/@a/video/123',100],
                  ['b','','https://www.tiktok.com/@b/video/456',100]]
        def metrics(url):
            return dict(video_id=extract_video_id(url),views=100,likes=10,comments=2,saves=3,shares=4)
        with tempfile.TemporaryDirectory() as directory, \
             patch('kol_tracker.sheet_session',return_value=(Mock(),'endpoint')), \
             patch('kol_tracker.read_tab',return_value=({}, {},values)), \
             patch('kol_organic_tiktok.get_tiktok_metrics',side_effect=metrics) as get, \
             patch('kol_organic_tiktok.time.sleep') as sleep, \
             patch('kol_organic_tiktok.random.uniform',return_value=5):
            env = {'KOL_ORGANIC_SHEETS_LINK':'test','KOL_TRACKER_STATE_DIR':directory}
            output = Path(directory)/'run'
            run(env,output,False)
            self.assertEqual(get.call_count,2)
            sleep.assert_called_once_with(5)
            run(env,output,False)
            self.assertEqual(get.call_count,2)

    def test_block_stops_remaining_videos_and_next_run(self):
        headers = ['Creator','Organic Launch Date','Post Link','KOL Fee','Views',
                   'Interaction','Likes','Comments','Saves','Shares','CPM','CPE']
        values = [headers,['a','','https://www.tiktok.com/@a/video/123',100],
                  ['b','','https://www.tiktok.com/@b/video/456',100]]
        with tempfile.TemporaryDirectory() as directory, \
             patch('kol_tracker.sheet_session',return_value=(Mock(),'endpoint')), \
             patch('kol_tracker.read_tab',return_value=({}, {},values)), \
             patch('kol_organic_tiktok.get_tiktok_metrics',side_effect=TikTokBlocked(429)) as get:
            env = {'KOL_ORGANIC_SHEETS_LINK':'test','KOL_TRACKER_STATE_DIR':directory}
            output = Path(directory)/'run'
            with self.assertRaises(RuntimeError):
                run(env,output,False)
            self.assertEqual(get.call_count,1)
            run(env,output,False)
            self.assertEqual(get.call_count,1)

    def test_url_rejects_external_hosts(self):
        self.assertEqual(extract_video_id('https://www.tiktok.com/@a/video/123?x=1'), '123')
        for url in ('http://www.tiktok.com/@a/video/123', 'https://evil.com/video/123',
                    'https://www.tiktok.com.evil.com/@a/video/123'):
            with self.assertRaises(ValueError):
                extract_video_id(url)

    def test_403_429_stop_without_retry(self):
        for status in (403,429):
            response = Mock(status_code=status, headers={'Retry-After':'60'})
            with patch('get_tiktok_public_data.requests.get',return_value=response) as get:
                with self.assertRaises(TikTokBlocked):
                    get_tiktok_metrics('https://www.tiktok.com/@a/video/123')
                self.assertEqual(get.call_count,1)

    def test_exact_json_and_formula(self):
        response = Mock(status_code=200,headers={},text='<script type="application/json">'
            '{"id":"123","stats":{"playCount":100,"diggCount":10,"commentCount":2,'
            '"collectCount":3,"shareCount":4}}</script>')
        with patch('get_tiktok_public_data.requests.get',return_value=response):
            metrics = get_tiktok_metrics('https://www.tiktok.com/@a/video/123')
        changes = metric_changes(metrics,200)
        self.assertEqual(changes[5],19)
        self.assertEqual(changes[10],2000)
        self.assertAlmostEqual(changes[11],200/19)
        metrics['likes'] = None
        with self.assertRaises(ValueError):
            metric_changes(metrics,200)

    def test_backoff_honors_long_retry_after(self):
        now = datetime(2026,10,7,tzinfo=timezone.utc)
        self.assertEqual(backoff_until(now,'60'),'2026-10-08T00:00:00+00:00')
        self.assertEqual(backoff_until(now,'172800'),'2026-10-09T00:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
