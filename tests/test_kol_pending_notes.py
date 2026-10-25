import unittest
from tracker_failure_notes import plan_notes
from kol_organic_meta_totals import note_changes
from test_organic_meta_totals import sheet


class PendingNoteTests(unittest.TestCase):
    def test_generic_pending_does_not_become_failure(self):
        values=[['Creator','Post Link','Reason for data update failure'],['Alice','link','']]
        self.assertEqual(plan_notes(values,set(),[{'row':2,'reason':'Meta batch budget reached; remaining requests queued'}],0,2),[])

    def test_meta_pending_dependent_ratios_do_not_become_failure(self):
        reason='Public Views unavailable: Meta usage near limit; remaining Views deferred; existing values retained; CPM not refreshed: total Views unavailable or zero'
        self.assertEqual(note_changes(sheet(),[{'row':3,'reason':reason}]),[])

    def test_legacy_pending_note_is_cleared_but_permanent_problem_remains(self):
        values=sheet();values[2][16]='remaining requests queued; Public Views unavailable: Meta usage near limit; remaining Views deferred; existing values retained; CPM not refreshed: total Views unavailable or zero'
        self.assertEqual(note_changes(values,[{'row':3,'reason':'API did not return matching post'}]),[{'row':3,'changes':{16:'API did not return matching post'}}])

    def test_pending_omission_preserves_manual_notes(self):
        values=sheet();values[2][16]='Waiting for Shanshan to confirm the contract'
        self.assertEqual(note_changes(values,[{'row':3,'reason':'Meta batch pending'}]),[])

    def test_mixed_generic_error_keeps_only_real_failure(self):
        values=[['Creator','Post Link','Reason for data update failure'],['Alice','link','']]
        failures=[{'row':2,'reason':'Meta quota; remaining requests queued'},{'row':2,'reason':'No exact Post ID match'}]
        self.assertEqual(plan_notes(values,set(),failures,0,2),[{'row':2,'changes':{2:'Update failed: No exact Post ID match'}}])

    def test_organic_tiktok_backoff_is_not_a_failure_note(self):
        from kol_organic_tiktok import note_plan
        values=[['Creator','Reason for data update failure'],['Alice','']]
        self.assertEqual(note_plan(values,set(),{2:'Skipped due to TikTok backoff; existing metrics retained'})[1],[])

    def test_live_pending_does_not_open_sheet_to_write_failure(self):
        from unittest.mock import patch
        from kol_live_insert import write_failure
        with patch('kol_live_insert.open_tab',side_effect=AssertionError('pending write attempted')):
            write_failure({},'organic-meta',[],'Meta quota; retry queued',True,{})

    def test_new_row_note_policy_omits_waiting_placeholder(self):
        from unittest.mock import Mock,patch
        from pathlib import Path
        from kol_live_insert import simple_organic_insert
        values=[['Creator','Launch','Link','Fee','Views','Interaction','Likes','Comments','Shares','Saves','CPM','CPE','Reason for data update failure'],
                ['old'],['Summary']]
        native={'sheets':[{'data':[{'rowData':[{}, {}, {}]}]}]}
        item={'creator':'new','date':'2026-10-25','post_link':'https://youtu.be/test','note':'Metrics pending immediate API update'}
        with patch('kol_tracker.save_json'):
            body=simple_organic_insert(Mock(),'endpoint',{'sheetId':1},native,values,'organic-youtube',[item],Path('/tmp'),False)
        note=[x['updateCells']['rows'][0]['values'][0].get('userEnteredValue',{}) for x in body if 'updateCells' in x and x['updateCells']['range']['startColumnIndex']==12]
        self.assertEqual(note,[{}])

    def test_retryable_tiktok_connection_error_is_not_written(self):
        import requests
        from kol_organic_tiktok import failure_reason,note_plan
        reason=failure_reason(requests.ConnectionError('connection dropped'))
        values=[['Creator','Reason for data update failure'],['Alice','']]
        self.assertEqual(note_plan(values,set(),{2:reason})[1],[])

    def test_permanent_tiktok_http_error_retains_status(self):
        import requests
        from kol_organic_tiktok import failure_reason,note_plan
        response=requests.Response();response.status_code=403
        reason=failure_reason(requests.HTTPError('forbidden',response=response))
        values=[['Creator','Reason for data update failure'],['Alice','']]
        self.assertIn('HTTP 403',str(note_plan(values,set(),{2:reason})[1]))
