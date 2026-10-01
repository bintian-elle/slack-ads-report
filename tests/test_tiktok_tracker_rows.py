import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from add_tiktok_tracker_rows import parse_tiktok_announcements, plan_new_rows, post_key, insertion_requests

CODE = 'AbCd1234+/EFgh5678IJkl9012MNop3456QRst7890UVwxYZ=='
LINK = 'https://www.tiktok.com/@joe/video/1234567890123456789'


class TikTokRowTests(unittest.TestCase):
    def parse(self, text):
        return parse_tiktok_announcements([{'ts': '1', 'text': text}])

    def test_platforms_are_not_mixed(self):
        text = "[KOL Content is Live]\nJoe's content live on\nhttps://www.instagram.com/p/AAA/ - Q9jTMeta\n" + LINK + ' - #' + CODE
        result, issues = self.parse(text)
        self.assertFalse(issues)
        self.assertEqual(result[0]['ad_code'], CODE)
        self.assertEqual(result[0]['creator'], 'Joe')

    def test_multiple_creators_and_short_links(self):
        text = "[KOL Content is Live]\n1. Joe's content is live\n" + LINK + ' - #' + CODE + "\n2. Jane's content is live\nhttps://www.tiktok.com/t/ZTShort/ - #" + CODE.replace('AbCd', 'EfGh')
        result, issues = self.parse(text)
        self.assertFalse(issues)
        self.assertEqual([c['creator'] for c in result], ['Joe', 'Jane'])

    def test_unmarked_messages_never_create(self):
        self.assertEqual(self.parse("Joe's content is live\n" + LINK + ' #' + CODE), ([], []))

    def test_profile_display_name_and_html_unescape(self):
        result, _ = self.parse('[KOL Content is Live]\n<https://www.tiktok.com/@joe|Joe>’s content is live\n<' + LINK + '?a=1&amp;b=2|TT>\nad code: #' + CODE)
        self.assertEqual(result[0]['creator'], 'Joe')
        self.assertIn('&b=2', result[0]['post_link'])

    def test_cannot_pair_code_across_another_platform(self):
        result, issues = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + '\nhttps://youtube.com/shorts/test\n#' + CODE)
        self.assertFalse(result)
        self.assertTrue(issues)

    def test_unique_code_only_reply_completes_parent(self):
        messages = [{'ts': '1', 'text': "[KOL Content is Live]\nJoe's content is live\n" + LINK},
                    {'ts': '2', 'thread_ts': '1', 'text': 'TT code: #' + CODE}]
        result, issues = parse_tiktok_announcements(messages)
        self.assertFalse(issues)
        self.assertEqual(result[0]['ad_code'], CODE)

    def test_multi_post_reply_is_not_guessed(self):
        messages = [{'ts': '1', 'text': "[KOL Content is Live]\n1. Joe's content is live\n" + LINK + "\n2. Jane's content is live\nhttps://www.tiktok.com/@jane/video/2222222222222222222"},
                    {'ts': '2', 'thread_ts': '1', 'text': '#' + CODE}]
        result, issues = parse_tiktok_announcements(messages)
        self.assertFalse(result)
        self.assertEqual(len(issues), 2)

    def test_missing_code_never_adds(self):
        result, issues = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + ' - no need for ad code')
        self.assertFalse(result)
        self.assertTrue(issues)

    def test_code_without_link_reconciles_but_does_not_insert(self):
        result, _ = self.parse("[KOL Content is Live]\nJoe's content is live\nTT #" + CODE)
        new, decisions = plan_new_rows([[], []], result)
        self.assertFalse(new)
        self.assertEqual(decisions[0]['decision'], 'review_missing_post_link')
        new, decisions = plan_new_rows([[], [], ['Joe', '', '#' + CODE, '']], result)
        self.assertFalse(new)
        self.assertEqual(decisions[0]['decision'], 'existing_code')

    def test_existing_manual_values_untouched_and_replay_deduplicated(self):
        values = [[], [], ['Alias', 'brief', '#' + CODE, LINK, 46000, 'T0', 'manual note']]
        result, _ = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + ' #' + CODE)
        before = [list(r) for r in values]
        new, decisions = plan_new_rows(values, result + result)
        self.assertFalse(new)
        self.assertEqual(values, before)
        self.assertEqual(decisions[0]['decision'], 'existing_code')

    def test_same_post_different_code_is_reviewed(self):
        result, _ = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + ' #' + CODE)
        new, decisions = plan_new_rows([[], [], ['Joe', '', 'another', LINK]], result)
        self.assertFalse(new)
        self.assertEqual(decisions[0]['decision'], 'review_conflict')

    def test_profile_urls_and_other_hosts_are_not_posts(self):
        self.assertFalse(post_key('https://www.tiktok.com/@joe'))
        self.assertFalse(post_key('https://evil.example/video/123'))
        self.assertEqual(post_key(LINK + '?a=1'), 'video:1234567890123456789')

    def test_conflicting_announcements_do_not_partially_insert(self):
        result, _ = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + ' #' + CODE)
        conflict = dict(result[0], ad_code=CODE.replace('AbCd', 'EfGh'))
        new, decisions = plan_new_rows([[], []], result + [conflict])
        self.assertFalse(new)
        self.assertTrue(all(d['decision'] == 'review_conflict' for d in decisions))

    def test_insertion_never_copies_other_creators_status_or_values(self):
        result, _ = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + ' #' + CODE)
        template = [{}] * 13
        template[5] = {'userEnteredValue': {'stringValue': 'T0'}}
        requests, values = insertion_requests(0, 66, result, template)
        self.assertEqual(values[0][5], '')
        self.assertEqual(values[0][4], (datetime.fromtimestamp(1, ZoneInfo('America/Chicago')).date() - datetime(1899, 12, 30).date()).days)
        self.assertEqual(values[0][7:], [''] * 6)
        self.assertEqual(requests[0]['insertDimension']['range']['startIndex'], 67)
        copies = [r['copyPaste']['pasteType'] for r in requests if 'copyPaste' in r]
        self.assertEqual(copies, ['PASTE_FORMAT', 'PASTE_DATA_VALIDATION'])
        update = requests[-1]['updateCells']
        self.assertEqual(update['range']['startRowIndex'], 67)
        self.assertEqual(update['fields'], 'userEnteredValue')

    def test_reply_date_does_not_replace_content_announcement_date(self):
        result, _ = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + ' #' + CODE)
        result[0].update(announcement_ts='1784678400', ts='1786752000')
        _, values = insertion_requests(0, 66, result, [{}] * 13)
        expected = datetime.fromtimestamp(1784678400, ZoneInfo('America/Chicago')).date()
        self.assertEqual(values[0][4], (expected - datetime(1899, 12, 30).date()).days)

    def test_verified_api_date_takes_precedence(self):
        result, _ = self.parse("[KOL Content is Live]\nJoe's content is live\n" + LINK + ' #' + CODE)
        result[0]['api_launch_date'] = '2026-08-14'
        _, values = insertion_requests(0, 66, result, [{}] * 13)
        self.assertEqual(values[0][4], (datetime(2026, 8, 14).date() - datetime(1899, 12, 30).date()).days)


if __name__ == '__main__':
    unittest.main()
