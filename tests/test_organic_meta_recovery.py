import unittest
from unittest.mock import patch
from kol_organic_meta import code_plan,media_identity_plan,status_plan
from kol_organic_meta_totals import fetch_totals,metric_plan
from test_organic_meta_totals import sheet


class OrganicRecoveryTests(unittest.TestCase):
    def test_business_discovery_follows_cursor_without_next_url(self):
        from kol_organic_meta_totals import creator_media
        target={'id':'123','permalink':'https://instagram.com/reel/ABC/','view_count':42}
        calls=[]
        def read(env,path,params):
            calls.append(params['fields'])
            if '.after(cursor2)' in params['fields']:
                return {'business_discovery':{'media':{'data':[target]}}}
            return {'business_discovery':{'media':{'data':[{'id':'new','timestamp':'2026-10-21'}],
                'paging':{'cursors':{'before':'first','after':'cursor2'}}}}}
        with patch('kol_organic_meta_totals.api_read',side_effect=read):
            self.assertEqual(creator_media({},'actual.user',{'ABC'}),{'ABC':target})
        self.assertEqual(len(calls),2)

    def test_business_discovery_stops_repeated_cursor(self):
        from kol_organic_meta_totals import creator_media
        response={'business_discovery':{'media':{'data':[{'id':'new','timestamp':'2026-10-21'}],
            'paging':{'cursors':{'after':'same'}}}}}
        with patch('kol_organic_meta_totals.api_read',return_value=response) as read:
            self.assertEqual(creator_media({},'actual.user',{'ABC'}),{})
        self.assertEqual(read.call_count,2)

    def test_configured_pause_resolves_with_issues_but_does_not_invent_active(self):
        values=sheet();values[2][5]=''
        ad={'id':'1','status':'PAUSED','effective_status':'WITH_ISSUES'}
        self.assertEqual(status_plan(values,[ad],ad_matches={3:['1']})[0]['changes'],{5:'paused'})
        ad['status']='ACTIVE'
        self.assertEqual(status_plan(values,[ad],ad_matches={3:['1']}),[])
        ad['status']='PAUSED';values[2][5]='scaled'
        self.assertEqual(status_plan(values,[ad],ad_matches={3:['1']}),[])

    def test_same_post_has_one_identity_owner_and_one_metric_row(self):
        values=sheet();values[2][4:6]=['',''];values.append(list(values[2]))
        ad={'id':'1','effective_status':'PAUSED','creative':{'source_instagram_media_id':'123'}}
        resolved,codes,issues=code_plan(values,[],[ad])
        _,matches,_,issues=media_identity_plan(resolved,[ad],{'ABC':{'content_id':'123'}},{x['row'] for x in issues})
        self.assertEqual(matches,{3:['1']})
        self.assertTrue(issues[0]['informational'])
        plans=metric_plan(values,{'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/','view_count':20}})
        self.assertEqual([p['row'] for p in plans],[3])

    def test_shared_code_on_same_post_is_not_a_cross_post_conflict(self):
        values=sheet();values.append(list(values[2]))
        ad={'id':'1','creative':{'branded_content':{'instagram_boost_post_access_token':'code'}}}
        resolved,_,issues=code_plan(values,[],[ad])
        self.assertFalse(issues)
        _,matches,_,_=media_identity_plan(resolved,[ad],{})
        self.assertEqual(matches,{3:['1']})

    def test_exact_discovery_username_overrides_display_name(self):
        values=sheet();values[2][0]='Display Name'
        source=[{'content_id':'123','permalink':'https://instagram.com/reel/ABC/','username':'actual.user'}]
        seen=[]
        def discover(env,name,wanted):
            seen.append(name)
            return {'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/','view_count':42}}
        with patch('kol_organic_meta_totals.creator_media',side_effect=discover):
            content,_=fetch_totals({},values,discovery=source)
        self.assertEqual(seen,['actual.user']);self.assertEqual(content['ABC']['view_count'],42)

    def test_invalid_display_name_with_no_identity_reports_actual_problem(self):
        values=sheet();values[2][0]='Display Name';values[2][4]=''
        with patch('kol_organic_meta_totals.creator_media',return_value={}):
            content,_=fetch_totals({},values)
        self.assertIn('username',content['ABC']['_error'].lower())

    def test_username_lookup_keeps_quota_failure_retryable(self):
        from kol_organic_meta_totals import APIReadError
        from kol_failures import temporary_reason
        values=sheet();values[2][0]='Display Name';values[2]+= ['12345678901']
        with patch('kol_organic_meta_totals.api_read',side_effect=APIReadError('HTTP 400 / Meta code 4',code=4)):
            content,_=fetch_totals({},values)
        self.assertTrue(temporary_reason(content['ABC']['_error']))

    def test_duplicate_notes_preserve_manual_notes(self):
        from kol_organic_meta_totals import duplicate_note_plan
        values=sheet();values[2][16]='Manually checked by team'
        issues=[{'row':3,'reason':'Duplicate post; metrics assigned to row 2','informational':True}]
        self.assertEqual(duplicate_note_plan(values,issues,{3}),[])
        values[2][16]='Public Views unavailable: old error'
        self.assertEqual(duplicate_note_plan(values,issues,{3})[0]['changes'],{16:'Duplicate post; metrics assigned to row 2'})
