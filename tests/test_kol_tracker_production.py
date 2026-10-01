import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from kol_tracker import dedup_new, new_row, parse_messages, read_incremental_slack, tiktok_bindings
from test_tiktok_name_matching import ad

CODE = 'AbCd1234+/EFgh5678IJkl9012MNop3456QRst7890UVwxYZ=='
LINK = 'https://www.tiktok.com/@joe/video/1234567890123456789'


class ProductionTrackerTests(unittest.TestCase):
    def test_platforms_and_marked_reply(self):
        messages = [{'ts': '1786752000', 'text': "[KOL Content is Live]\nJoe's content live on\nhttps://www.instagram.com/p/AAA/ - Q9jTMetaCode\n" + LINK},
                    {'ts': '1786838400', 'thread_ts': '1786752000', 'text': 'TT code #' + CODE}]
        result, issues = parse_messages(messages)
        self.assertEqual(len(result), 2)
        self.assertFalse(issues)
        self.assertEqual(result[1]['announcement_ts'], '1786752000')
        row = new_row('TikTok', result[1])
        day = datetime.fromtimestamp(1786752000, ZoneInfo('America/Chicago')).date()
        self.assertEqual(row[4], (day - datetime(1899, 12, 30).date()).days)
        self.assertEqual(row[5], '')
        self.assertEqual(row[13], '')

    def test_unmarked_never_inserts(self):
        self.assertEqual(parse_messages([{'ts': '1', 'text': LINK + ' #' + CODE}]), ([], []))

    def test_conflict_rejects_both(self):
        item = {'creator': 'Joe', 'post_link': LINK, 'post_key': 'video:123', 'ad_code': CODE}
        new, skipped = dedup_new('TikTok', [[], []], [item, dict(item, ad_code=CODE + 'a')])
        self.assertFalse(new)
        self.assertEqual(len(skipped), 2)

    def test_replay_dedup_and_manual_fields(self):
        item = {'creator': 'Joe', 'post_link': LINK, 'post_key': 'video:1234567890123456789', 'ad_code': CODE}
        values = [[], [], ['Different alias', '', '#' + CODE, LINK, 46000, 'T0']]
        self.assertFalse(dedup_new('TikTok', values, [item])[0])
        self.assertEqual(values[2][5], 'T0')

    def test_n_binding_needs_no_audit_file(self):
        record = ad('260101_ROPOT_Spark_Josh_HP', '1873538721081441')
        rows = [[], [], ['Josh', '', '', '', '', 'testing', '', '', '', '', '', '', '', '1873538721081441']]
        planned, skipped = tiktok_bindings(rows, [record])
        self.assertEqual(planned[0]['ad_ids'], ['1873538721081441'])
        self.assertFalse(skipped)

    def test_unmapped_paused_skipped(self):
        self.assertFalse(tiktok_bindings([[], [], ['Josh', '', '', '', 46023, 'paused']], [ad('260101_ROPOT_Spark_Josh_HP')])[0])

    def test_missing_bound_ad_preserves_whole_row(self):
        row = ['Josh', '', '', '', '', 'testing', '', 99, 8, 3, 80, 50, .1,
               '1873538721081441\n1873538721081442']
        planned, _ = tiktok_bindings([[], [], row], [ad('260101_ROPOT_Spark_Josh_HP', '1873538721081441')])
        self.assertFalse(planned)
        self.assertEqual(row[7], 99)

    def test_invalid_binding_never_falls_back_to_name(self):
        serial = (datetime(2026, 1, 1).date() - datetime(1899, 12, 30).date()).days
        row = ['Josh', '', '', '', serial, 'testing', '', '', '', '', '', '', '', 'bad ID']
        self.assertFalse(tiktok_bindings([[], [], row], [ad('260101_ROPOT_Spark_Josh_HP')])[0])

    def test_new_row_name_date_binds(self):
        serial = (datetime(2026, 1, 1).date() - datetime(1899, 12, 30).date()).days
        planned, _ = tiktok_bindings([[], [], ['Josh', '', '', '', serial, '']], [ad('260101_ROPOT_Spark_Josh_HP')])
        self.assertTrue(planned[0]['new_binding'])

    def test_shared_ad_never_claimed_twice(self):
        serial = (datetime(2026, 1, 1).date() - datetime(1899, 12, 30).date()).days
        row = ['Josh', '', '', '', serial, '']
        self.assertFalse(tiktok_bindings([[], [], row, row], [ad('260101_ROPOT_Spark_Josh_HP')])[0])

    def test_multiple_ads_preserve_reach_roas(self):
        row = ['Josh', '', '', '', '', '', '', '', '', 3, 80, '', '', '1873538721081441\n1873538721081442']
        planned, _ = tiktok_bindings([[], [], row], [ad('260101_ROPOT_Spark_Josh_HP', '1873538721081441'), ad('260101_ROPOT_Spark_Josh_HP', '1873538721081442')])
        self.assertEqual(planned[0]['preserve'], [9, 10])
        self.assertEqual(planned[0]['metrics'][2:4], [3, 80])

    def test_known_old_thread_is_reread(self):
        root = {'ts': '100', 'text': '[KOL Content is Live]'}
        with patch('kol_tracker.slack_pages', side_effect=[[], [root, {'ts': '200', 'thread_ts': '100', 'text': 'new code'}]]) as pages:
            messages, state = read_incremental_slack({'KOL_TRACKER_SLACK_BOT_TOKEN': 'test', 'KOL_TRACKER_SLACK_CHANNEL_ID': 'channel'}, {'cursor': 190, 'threads': {'100': root}}, 210)
        self.assertEqual(pages.call_count, 2)
        self.assertEqual(len(messages), 2)
        self.assertEqual(state['cursor'], 210)
