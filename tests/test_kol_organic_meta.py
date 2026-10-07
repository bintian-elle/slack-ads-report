import unittest
import copy
from pathlib import Path
from unittest.mock import patch, MagicMock
from kol_organic_meta import fetch_content, fetch_ad_interactions, paid_cache_decision, plan, post_key, status_plan, code_plan, note_plan, NOTE_HEADER, run
from datetime import date
from kol_organic_meta import media_identity_plan
import tempfile
import json


HEADERS = ['Creator', 'Organic Launch Date', 'Content Brief', 'Post Link', 'Ad Code',
           'Status', 'KOL Fee', 'Views ', 'Interaction ', 'Likes', 'Comments', 'Shares', 'Saves ', 'CPM ', 'CPE']


class OrganicTests(unittest.TestCase):
    def test_media_identity_status_and_unique_code_without_tracker(self):
        values = self.values()
        values[2][4:6] = ['', '']
        ad = {'id': '1', 'effective_status': 'ACTIVE', 'creative': {
            'source_instagram_media_id': '123', 'branded_content': {
                'instagram_boost_post_access_token': 'code'}}}
        content = {'ABC': {'content_id': '123'}}
        resolved, matches, codes, issues = media_identity_plan(values, [ad], content)
        self.assertEqual(matches, {3: ['1']})
        self.assertEqual(codes[0]['changes'], {4: 'code'})
        self.assertEqual(status_plan(resolved, [ad], ad_matches=matches)[0]['changes'], {5: 'testing'})
        self.assertFalse(issues)
        values[2][5] = 'T0'
        self.assertEqual(status_plan(values, [ad], ad_matches=matches), [])
        ad['creative'].pop('branded_content')
        _, matches, codes, _ = media_identity_plan(values, [ad], content)
        self.assertEqual(matches, {3: ['1']})
        self.assertEqual(codes, [])

    def test_media_identity_conflicts_and_no_effective_media_fallback(self):
        values = self.values()
        values[2][4:6] = ['', '']
        content = {'ABC': {'content_id': '123'}}
        ad = {'id': '1', 'creative': {'source_instagram_media_id': '123'}}
        values.append(list(values[2]))
        _, matches, codes, issues = media_identity_plan(values, [ad], content)
        self.assertFalse(matches)
        self.assertFalse(codes)
        self.assertEqual({item['row'] for item in issues}, {3, 4})
        values.pop()
        ad['creative'] = {'effective_instagram_media_id': '123'}
        self.assertFalse(media_identity_plan(values, [ad], content)[1])
        ad['creative'] = {'source_instagram_media_id': '123'}
        other = {'id': '2', 'creative': {'source_instagram_media_id': '456',
                 'branded_content': {'instagram_boost_post_access_token': 'other'}}}
        values[2][4] = 'other'
        self.assertFalse(media_identity_plan(values, [ad, other], content)[1])

    def test_paid_cache_reuses_only_paused_and_refreshes_active(self):
        from kol_organic_meta import AD_INTERACTION_FIELDS
        ad = {'id': '1', 'created_time': '2026-01-01T12:00:00+0000',
              'effective_status': 'PAUSED', 'creative': {'source_instagram_media_id': '123'}}
        def pages(session, url, params):
            if url.endswith('/ads'):
                return [ad]
            return [{'publisher_platform': 'instagram', 'actions': [
                {'action_type': 'post', 'value': '8'}]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            path.write_text(json.dumps({'scope': {'account': 'act_1',
                'base': 'https://graph.facebook.com/v23.0', 'timezone': 'America/Chicago',
                'fields': list(AD_INTERACTION_FIELDS), 'schema': 1}, 'ads': {'1': {
                    'value': 7, 'since': '2026-01-01', 'fetched_on': '2026-10-06',
                    'paused_since': '2026-01-01'}}}))
            with patch('kol_organic_meta.datetime') as clock, \
                 patch('sync_creator_tracker.get_json', return_value={'timezone_name': 'America/Chicago'}), \
                 patch('sync_creator_tracker.graph_pages', side_effect=pages) as api:
                from datetime import datetime
                clock.strptime.side_effect = datetime.strptime
                clock.now.return_value = datetime(2026, 10, 7)
                env = {'META_ACCESS_TOKEN': 'test', 'META_AD_ACCOUNT_ID': '1'}
                content = {'ABC': {'content_id': '123'}}
                _, paid = fetch_ad_interactions(env, content, path)
                self.assertEqual(paid['123']['value'], 7)
                self.assertEqual(api.call_count, 1)
                ad['effective_status'] = 'ACTIVE'
                _, paid = fetch_ad_interactions(env, content, path)
                self.assertEqual(paid['123']['value'], 8)
                self.assertIsNone(json.loads(path.read_text())['ads']['1']['paused_since'])

    def test_paid_cache_grace_weekly_and_reactivation(self):
        entry = {'paused_since': '2026-08-01', 'fetched_on': '2026-09-30', 'value': 12}
        self.assertEqual(paid_cache_decision(entry, 'PAUSED', date(2026, 10, 6)),
                         (False, '2026-08-01'))
        self.assertTrue(paid_cache_decision(entry, 'PAUSED', date(2026, 10, 7))[0])
        for status in ['ACTIVE', 'DISAPPROVED', None]:
            self.assertEqual(paid_cache_decision(entry, status, date(2026, 10, 6)), (True, None))
        entry['paused_since'] = '2026-10-01'
        self.assertTrue(paid_cache_decision(entry, 'CAMPAIGN_PAUSED', date(2026, 10, 6))[0])
        self.assertEqual(paid_cache_decision({}, 'PAUSED', date(2026, 10, 6)),
                         (True, '2026-10-06'))
        entry['value'] = float('nan')
        self.assertTrue(paid_cache_decision(entry, 'PAUSED', date(2026, 10, 6))[0])

    def test_method_a_fallback_and_cpe(self):
        content = {'ABC': {'content_id': '123', 'organic_insights': {
            'views': 100, 'interaction': None, 'likes': 20,
            'comments': 3, 'shares': 2, 'saves': 1}}}
        result, _ = plan(self.values(), content, {'123': {'value': 4}})
        self.assertEqual(result[0]['changes'][8], 30)
        self.assertEqual(result[0]['changes'][14], 40)
        self.assertNotIn('interaction', result[0]['missing'])
        self.assertNotIn('estimate', str(note_plan(self.values(), result)).lower())
        content['ABC']['organic_insights']['interaction'] = 40
        self.assertEqual(plan(self.values(), content, {'123': {'value': 4}})[0][0]['changes'][8], 44)

    def test_method_a_missing_parts_or_paid_never_uses_stale_values(self):
        content = {'ABC': {'content_id': '123', 'organic_insights': {
            'interaction': None, 'likes': 20, 'comments': 3, 'shares': 2}}}
        self.assertNotIn(8, plan(self.values(), content, {'123': {'value': 4}})[0][0]['changes'])
        content['ABC']['organic_insights']['interaction'] = 40
        result = plan(self.values(), content, {})[0][0]
        self.assertNotIn(8, result['changes'])
        self.assertNotIn(14, result['changes'])

    def test_legacy_notes_migrate_and_recovered_notes_clear(self):
        values = self.values()
        values[1].extend(['', '数据更新说明（自动）'])
        values[2].extend([''] * 10)
        values[2][16] = '自动：API未返回Interaction；CPE缺少最新分母'
        result = note_plan(values, [{'row': 3, 'missing': [], 'changes': {7: 100, 8: 30}}])
        self.assertEqual(result, [{'row': 2, 'changes': {16: NOTE_HEADER}},
                                  {'row': 3, 'changes': {16: ''}}])
        values[1][16] = NOTE_HEADER
        values[2][16] = 'API did not return the matching post; H:O unchanged'
        self.assertEqual(note_plan(values, [{'row': 3, 'missing': [], 'changes': {7: 100, 8: 30}}]),
                         [{'row': 3, 'changes': {16: ''}}])

    def test_paid_exact_source_instagram_only_dedup_and_no_gross(self):
        ads = [{'id': '1', 'created_time': '2026-09-01T12:00:00+0000',
                'creative': {'source_instagram_media_id': '123'}},
               {'id': '1', 'created_time': '2026-09-01T12:00:00+0000',
                'creative': {'source_instagram_media_id': '123'}},
               {'id': '2', 'creative': {'effective_instagram_media_id': '123'}}]
        actions = [{'action_type': 'onsite_conversion.post_net_like', 'value': '2'},
                   {'action_type': 'onsite_conversion.post_net_comment', 'value': '3'},
                   {'action_type': 'post', 'value': '4'},
                   {'action_type': 'post_interaction_gross', 'value': '99'},
                   {'action_type': 'onsite_conversion.post_net_save', 'value': '10'}]
        def pages(session, url, params):
            if url.endswith('/ads'):
                return ads
            self.assertTrue(url.endswith('/1/insights'))
            return [{'publisher_platform': 'instagram', 'actions': actions},
                    {'publisher_platform': 'facebook', 'actions': actions}]
        with patch('sync_creator_tracker.get_json', return_value={'timezone_name': 'America/Chicago'}), \
             patch('sync_creator_tracker.graph_pages', side_effect=pages):
            _, paid = fetch_ad_interactions({'META_ACCESS_TOKEN': 'test', 'META_AD_ACCOUNT_ID': '1'},
                                           {'ABC': {'content_id': '123'}, 'DEF': {'content_id': '456'}})
        self.assertEqual(paid['123']['value'], 9)
        self.assertEqual(paid['123']['ad_ids'], ['1'])
        self.assertEqual(paid['456']['value'], 0)

    def test_notes_explain_gaps_and_clear_after_recovery(self):
        values = self.values()
        missing = note_plan(values, [])
        self.assertIn('API did not return the matching post', missing[1]['changes'][16])
        values[2].extend([''] * 10)
        values[2][16] = missing[1]['changes'][16]
        complete = {'row': 3, 'missing': [], 'changes': {7: 100, 8: 10}}
        self.assertEqual(note_plan(values, [complete])[-1]['changes'], {16: ''})
        values[2][6] = ''
        self.assertIn('requires manual entry', note_plan(values, [complete])[-1]['changes'][16])
        values[2][16] = '人工备注'
        self.assertEqual(len(note_plan(values, [])), 1)

    def test_notes_zero_partial_invalid_link_and_summary(self):
        values = self.values()
        result = note_plan(values, [{'row': 3, 'missing': ['interaction'], 'changes': {7: 0}}])
        self.assertIn('Interaction', result[-1]['changes'][16])
        self.assertIn('denominator is zero', result[-1]['changes'][16])
        values[2][3] = ''
        values.append(['Summary'])
        self.assertEqual(len(note_plan(values, [])), 2)
        self.assertIn('Missing or unsupported post link', note_plan(values, [])[-1]['changes'][16])
        values[1].extend(['', '人工列'])
        with self.assertRaises(RuntimeError):
            note_plan(values, [])

    def test_exact_post_code_backfill_preserves_manual_status(self):
        ads = [{'id': '1', 'effective_status': 'ACTIVE', 'creative': {
            'branded_content': {'instagram_boost_post_access_token': 'CODE'}}}]
        tracker = [[], [], ['', 'leen', '', '', '', '',
                              'https://www.instagram.com/p/ABC/', 'adcode-CODE']]
        resolved, codes, issues = code_plan(self.values(), tracker, ads)
        self.assertEqual(codes[0]['changes'], {4: 'adcode-CODE'})
        self.assertEqual(resolved[2][5], 'T0')
        self.assertFalse(status_plan(resolved, ads))
        self.assertFalse(issues)
        resolved[2][5] = ''
        self.assertEqual(status_plan(resolved, ads)[0]['changes'], {5: 'testing'})

    def test_ambiguous_unverified_and_shared_codes_never_fill(self):
        ads = [{'id': '1', 'creative': {'branded_content': {
            'instagram_boost_post_access_token': 'CODE'}}}]
        first = ['', '', '', '', '', '', 'https://www.instagram.com/reel/ABC/', 'CODE']
        second = first.copy()
        second[7] = 'OTHER'
        self.assertFalse(code_plan(self.values(), [[], [], first, second], ads)[1])
        self.assertFalse(code_plan(self.values(), [[], [], first], [])[1])
        duplicated = self.values() + [self.values()[2].copy()]
        resolved, codes, issues = code_plan(duplicated, [[], [], first], ads)
        self.assertFalse(codes)
        self.assertEqual(len(issues), 2)
        self.assertFalse(status_plan(resolved, ads))

    def test_existing_code_is_not_overwritten_on_link_conflict(self):
        values = self.values()
        values[2][4:6] = ['EXISTING', 'testing']
        tracker = [[], [], ['', '', '', '', '', '',
                              'https://www.instagram.com/reel/ABC/', 'OTHER']]
        resolved, codes, issues = code_plan(values, tracker, [])
        self.assertEqual(values[2][4], 'EXISTING')
        self.assertFalse(codes)
        self.assertTrue(issues)
        self.assertEqual(resolved[2][4], '')

    def test_f1_is_atomic_with_metrics_and_verified(self):
        values = self.values()
        native = {'sheets': [{'data': [{'rowData': [
            {'values': [{} for _ in range(15)]} for _ in range(3)]}]}]}
        after = copy.deepcopy(native)
        after['sheets'][0]['data'][0]['rowData'][1]['values'].extend([{},
            {'userEnteredValue': {'stringValue': NOTE_HEADER}}])
        after['sheets'][0]['data'][0]['rowData'][2]['values'].extend([{},
            {'userEnteredValue': {'stringValue': 'API did not return Interaction/Likes/Comments/Shares/Saves; CPE denominator unavailable'}}])
        after['sheets'][0]['data'][0]['rowData'][0]['values'][5] = {
            'userEnteredValue': {'stringValue': '[10/06 update]'}}
        for col, value in [(7, 100), (13, 12000)]:
            after['sheets'][0]['data'][0]['rowData'][2]['values'][col] = {
                'userEnteredValue': {'numberValue': value}}
        session = MagicMock()
        with patch('kol_tracker.sheet_session', return_value=(session, 'test-endpoint')), \
             patch('kol_tracker.read_tab', side_effect=[({'sheetId': 0}, native, values),
                 ({'sheetId': 0}, native, values), ({'sheetId': 0}, after, values)]), \
             patch('kol_tracker.save_json'), patch('kol_organic_meta.fetch_ad_interactions', return_value=([], {})), \
             patch('kol_organic_meta.fetch_content', return_value={'ABC': {
                 'content_id': '1', 'organic_insights': {'views': 100}}}), \
             patch('kol_organic_meta.datetime') as clock:
            clock.now.return_value.strftime.return_value = '[10/06 update]'
            run({'KOL_ORGANIC_SHEETS_LINK': 'test'}, Path('/tmp'), True)
        self.assertEqual(session.post.call_count, 1)
        requests = session.post.call_args.kwargs['json']['requests']
        self.assertEqual(len(requests), 5)
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
        return [[], HEADERS.copy(), ['leen', '', '', 'https://www.instagram.com/reels/ABC/?x=1', '', 'T0', 1200]]

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
        self.assertEqual(status_plan(values, [ad])[0]['changes'], {5: 'paused'})
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
        cells[2]['values'][5]['dataValidation']['condition']['values'] = [{'userEnteredValue': 'pause'}]
        self.assertEqual(status_plan(values, ads, cells)[0]['changes'], {5: 'pause'})
        cells[2]['values'][5]['dataValidation']['condition']['values'] = [{'userEnteredValue': 'T0'}]
        self.assertEqual(status_plan(values, ads, cells), [])
