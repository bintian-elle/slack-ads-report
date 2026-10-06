import unittest
import copy
from pathlib import Path
from unittest.mock import patch, MagicMock
from kol_organic_meta import fetch_content, plan, post_key, status_plan, run


HEADERS = ['Creator', 'Organic Launch Date', 'Content Brief', 'Post Link', 'Ad Code',
           'Status', 'KOL Fee', 'Views ', 'Interaction ', 'Likes', 'Comments', 'Shares', 'Saves ', 'CPM ', 'CPE']


class OrganicTests(unittest.TestCase):
    def test_f1_is_atomic_with_metrics_and_verified(self):
        values = self.values()
        native = {'sheets': [{'data': [{'rowData': [
            {'values': [{} for _ in range(15)]} for _ in range(3)]}]}]}
        after = copy.deepcopy(native)
        after['sheets'][0]['data'][0]['rowData'][0]['values'][5] = {
            'userEnteredValue': {'stringValue': '[10/06 update]'}}
        for col, value in [(7, 100), (13, 12000)]:
            after['sheets'][0]['data'][0]['rowData'][2]['values'][col] = {
                'userEnteredValue': {'numberValue': value}}
        session = MagicMock()
        with patch('kol_tracker.sheet_session', return_value=(session, 'test-endpoint')), \
             patch('kol_tracker.read_tab', side_effect=[({'sheetId': 0}, native, values),
                 ({'sheetId': 0}, native, values), ({'sheetId': 0}, after, values)]), \
             patch('kol_tracker.save_json'), patch('kol_tracker.meta_inventory', return_value=({}, [])), \
             patch('kol_organic_meta.fetch_content', return_value={'ABC': {
                 'content_id': '1', 'organic_insights': {'views': 100}}}), \
             patch('kol_organic_meta.datetime') as clock:
            clock.now.return_value.strftime.return_value = '[10/06 update]'
            run({'KOL_ORGANIC_SHEETS_LINK': 'test'}, Path('/tmp'), True)
        self.assertEqual(session.post.call_count, 1)
        requests = session.post.call_args.kwargs['json']['requests']
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[-1]['updateCells']['range']['startRowIndex'], 0)
        self.assertEqual(requests[-1]['updateCells']['range']['startColumnIndex'], 5)

    def test_failed_fetch_never_writes_f1_or_metrics(self):
        session = MagicMock()
        with patch('kol_tracker.sheet_session', return_value=(session, 'test')), \
             patch('kol_tracker.read_tab', return_value=({}, {}, self.values())), \
             patch('kol_organic_meta.fetch_content', side_effect=RuntimeError('API failed')):
            with self.assertRaises(RuntimeError):
                run({'KOL_ORGANIC_SHEETS_LINK': 'test'}, Path('/tmp'), True)
        session.post.assert_not_called()

    def test_uses_shared_meta_token_without_temporary_token(self):
        with patch('kol_organic_meta.requests.Session') as session:
            session.return_value.get.return_value.ok = True
            session.return_value.get.return_value.json.return_value = {'data': []}
            self.assertEqual(fetch_content({'META_ACCESS_TOKEN': 'shared-test-token'},
                                          ['https://www.instagram.com/reel/ABC/']), {})
            session.return_value.headers.__setitem__.assert_called_with(
                'Authorization', 'Bearer shared-test-token')

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
