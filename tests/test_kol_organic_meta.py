import unittest
from kol_organic_meta import plan, post_key, status_plan


HEADERS = ['Creator', 'Organic Launch Date', 'Content Brief', 'Post Link', 'Ad Code',
           'Status', 'KOL Fee', 'Views ', 'Interaction ', 'Likes', 'Comments', 'Shares', 'Saves ', 'CPM ', 'CPE']


class OrganicTests(unittest.TestCase):
    def values(self):
        return [[], HEADERS, ['leen', '', '', 'https://www.instagram.com/reels/ABC/?x=1', '', 'T0', 1200]]

    def test_natural_metrics_and_fee(self):
        content = {'ABC': {'content_id': '123', 'organic_insights': {'views': 3451, 'interaction': 87,
                   'likes': 69, 'comments': 11, 'shares': 2, 'saves': 0}}}
        result, skipped = plan(self.values(), content)
        changes = result[0]['changes']
        self.assertEqual(changes[7], 3451)
        self.assertEqual(changes[8], 87)
        self.assertAlmostEqual(changes[13], 347.725297, places=5)
        self.assertAlmostEqual(changes[14], 1200 / 87)
        self.assertFalse(skipped)
        self.assertTrue(all(7 <= c <= 14 for c in changes))

    def test_null_not_zero_and_no_stale_cpe(self):
        result, _ = plan(self.values(), {'ABC': {'content_id': '123', 'organic_insights': {
            'views': 0, 'interaction': None, 'likes': -1, 'comments': True, 'shares': float('nan'), 'saves': 0}}})
        self.assertEqual(result[0]['changes'], {7: 0, 12: 0})

    def test_missing_post_preserves_row(self):
        result, skipped = plan(self.values(), {})
        self.assertFalse(result)
        self.assertEqual(skipped[0]['row'], 3)

    def test_schema_and_link(self):
        self.assertEqual(post_key('https://www.instagram.com/p/ABC/#advertiser'), 'ABC')
        with self.assertRaises(RuntimeError):
            plan([[], ['wrong']], {})

    def test_status_only_automatic_or_blank(self):
        ads = [{'id': '1', 'effective_status': 'ACTIVE', 'creative': {'branded_content': {
            'instagram_boost_post_access_token': 'code'}}}]
        for old in ['', 'pause', 'testing', 'T0', 'T1', 'paused']:
            values = self.values()
            values[2][4:6] = ['adcode-code', old]
            result = status_plan(values, ads)
            self.assertEqual(bool(result), old in ['', 'pause', 'paused'])
            if result:
                self.assertEqual(result[0]['changes'], {5: 'testing'})

    def test_status_paused_unknown_and_shared(self):
        values = self.values()
        values[2][4:6] = ['code', 'testing']
        ad = {'id': '1', 'effective_status': 'CAMPAIGN_PAUSED', 'creative': {
            'branded_content': {'instagram_boost_post_access_token': 'code'}}}
        self.assertEqual(status_plan(values, [ad])[0]['changes'], {5: 'pause'})
        ad['effective_status'] = 'DISAPPROVED'
        self.assertEqual(status_plan(values, [ad]), [])
        ad['effective_status'] = 'ACTIVE'
        values.append(list(values[2]))
        self.assertEqual(status_plan(values, [ad]), [])

    def test_status_preserves_live_dropdown(self):
        values = self.values()
        values[2][4:6] = ['code', 'testing']
        ads = [{'id': '1', 'effective_status': 'PAUSED', 'creative': {
            'branded_content': {'instagram_boost_post_access_token': 'code'}}}]
        cells = [{}, {}, {'values': [{}, {}, {}, {}, {}, {'dataValidation': {
            'condition': {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': 'paused'}]}}}]}]
        self.assertEqual(status_plan(values, ads, cells)[0]['changes'], {5: 'paused'})
        cells[2]['values'][5]['dataValidation']['condition']['values'] = [{'userEnteredValue': 'T0'}]
        self.assertEqual(status_plan(values, ads, cells), [])
