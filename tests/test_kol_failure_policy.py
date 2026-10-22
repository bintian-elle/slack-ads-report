import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


class FailurePolicyTests(unittest.TestCase):
    def test_no_data_is_terminal_for_this_cycle_not_success_or_retry(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet',now=lambda:1792648800)
            item={'key':'a','row':3,'status':'ACTIVE','fingerprint':'old'}
            q.run([item],lambda _:False)
            self.assertEqual(q.remaining,0)
            self.assertNotIn('cycle',q.state['rows']['a'])
            self.assertEqual(q.state['rows']['a']['unavailable_cycle'],'2026-10-22')
            q.run([item],lambda _:self.fail('terminal row retried'))
            item['fingerprint']='fixed'
            q.run([item],lambda _:True)
            self.assertEqual(q.state['rows']['a']['cycle'],'2026-10-22')

    def test_timeout_is_retryable_but_bad_schema_is_not(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet',now=lambda:1792648800)
            def write(item):
                if item['key']=='timeout':raise requests.Timeout('private URL')
                raise RuntimeError('schema changed')
            q.run([{'key':'timeout','status':'ACTIVE'},{'key':'schema','status':'ACTIVE'}],write)
            self.assertEqual(q.remaining,1)
            self.assertTrue(q.state['rows']['timeout']['retryable'])
            self.assertEqual(q.state['rows']['schema']['unavailable_cycle'],'2026-10-22')

    def test_duplicate_post_has_one_attempt_and_consistent_status(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet',now=lambda:1792648800)
            items=[{'key':'a','row':90,'status':'PAUSED'}, {'key':'a','row':91,'status':None}]
            seen=[]
            q.run(items,lambda item:seen.append(item['key']) or False)
            q.run(items,lambda _:self.fail('same post retried'))
            self.assertEqual(seen,['a'])

    def test_exception_classification_uses_temporary_status_not_all_failures(self):
        from kol_failures import classify_exception
        from kol_meta_runtime import MetaDeferred
        self.assertTrue(classify_exception(MetaDeferred('Meta batch budget reached'))[0])
        self.assertTrue(classify_exception(RuntimeError('YouTube API HTTP 503'))[0])
        self.assertFalse(classify_exception(RuntimeError('Business Discovery HTTP 400 / Meta code 110'))[0])
        self.assertFalse(classify_exception(ValueError('private source text'))[0])

    def test_migration_preserves_success_cache_and_transient_work(self):
        from kol_retry_migration import migrate
        from kol_tracker import save_json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state={'scope':'sheet|ads-meta','rows':{
                'done':{'row':3,'cycle':'2026-10-22','fetched_on':'2026-10-22'},
                'bad':{'row':4,'attempts':8},'timeout':{'row':5,'attempts':2}}}
            save_json(root/'row-progress-test.json',state)
            run=root/'runs'/'20261022T060001347762Z';run.mkdir(parents=True)
            (run/'20261022T060001-plan.json').write_text(json.dumps({'skipped':[
                {'row':4,'reason':'No exact Creative Ad Code match'},
                {'row':5,'reason':'Meta rate limit; remaining requests deferred'}]}))
            meta={'cache':{'test':{'data':{'keep':True}}},'pending':{'ads-meta':{'retry_at':0,'reason':'row queue'}}}
            save_json(root/'meta-request-progress.json',meta)
            migrate(root,apply=True,now=1792648800)
            result=json.loads((root/'row-progress-test.json').read_text())
            self.assertEqual(result['rows']['done'],state['rows']['done'])
            self.assertEqual(result['rows']['bad']['unavailable_cycle'],'2026-10-22')
            self.assertTrue(result['rows']['timeout']['retryable'])
            after=json.loads((root/'meta-request-progress.json').read_text())
            self.assertEqual(after['cache'],meta['cache'])
            self.assertIn('ads-meta',after['pending'])

    def test_live_unavailable_job_does_not_reenqueue_same_announcement(self):
        from kol_live_updates import enqueue_jobs,dispatch_jobs
        from kol_failures import RowResult
        event={'platform':'YouTube','creator':'a','post_link':'https://youtu.be/m3voSsAfZQw','ts':'100','ad_code':''}
        state={};enqueue_jobs(state,[event],100)
        def writer(jobs):return RowResult(unavailable={'yt:m3voSsAfZQw':'Video unavailable in YouTube API'})
        dispatch_jobs(state,{'organic-youtube':writer},lambda *a:None,lambda:None,100)
        self.assertFalse(state['jobs']);self.assertTrue(state['unavailable'])
        enqueue_jobs(state,[event],400)
        self.assertFalse(state['jobs'])
        event['creator']='corrected.username'
        enqueue_jobs(state,[event],500)
        self.assertTrue(state['jobs'])

    def test_permanent_field_plus_separate_transient_failure_remains_retryable(self):
        from kol_failures import temporary_reason
        self.assertTrue(temporary_reason('Views HTTP 400 / Meta code 110; Engagement HTTP 503'))
        self.assertFalse(temporary_reason('HTTP 500 / Meta code 100'))
        self.assertFalse(temporary_reason('Engagement request failed: HTTP 400 / Meta code 110'))

    def test_ambiguous_legacy_live_failure_is_not_discarded(self):
        from kol_retry_migration import migrate
        from kol_tracker import save_json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            save_json(root/'live-metric-jobs.json',{'jobs':{'legacy':{'attempts':2,'reason':'Immediate update failed (RuntimeError); existing values retained'}},'completed':{}})
            migrate(root,apply=True,now=1792648800)
            self.assertIn('legacy',json.loads((root/'live-metric-jobs.json').read_text())['jobs'])

    def test_stage_with_only_unavailable_rows_does_not_stay_pending(self):
        from kol_row_updates import run_rows
        from kol_meta_runtime import MetaRuntime,run_stage
        from kol_failures import record_failure
        values=[['header'],['creator',0,'https://youtu.be/m3voSsAfZQw']]
        with tempfile.TemporaryDirectory() as directory,patch('kol_live_insert.open_tab',return_value=(None,'sheet',None,None,values)),patch('kol_row_updates.time.sleep'):
            root=Path(directory);runtime=MetaRuntime(root,'test')
            env={'KOL_TRACKER_STATE_DIR':directory,'_KOL_META_RUNTIME':runtime}
            def writer(env,*a,**kw):
                record_failure(env,'Video unavailable in YouTube API',False)
                return set()
            result=run_stage(runtime,'organic-youtube',lambda:run_rows(env,root,True,'organic-youtube',writer,link_aliases={}))
            self.assertFalse(runtime.pending())
            self.assertEqual(result.unavailable,{'yt:m3voSsAfZQw':'Video unavailable in YouTube API'})

    def test_migration_clears_finished_stage_without_marking_missing_data_success(self):
        from kol_retry_migration import migrate
        from kol_tracker import save_json
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',side_effect=AssertionError('migration must be offline')):
            root=Path(directory)
            save_json(root/'row-progress-test.json',{'scope':'sheet|ads-meta','rows':{'bad':{'row':19,'attempts':9,'reason':'No exact Creative Ad Code match'}}})
            save_json(root/'meta-request-progress.json',{'cache':{'unchanged':1},'pending':{'ads-meta':{'reason':'KOL row queue incomplete'}}})
            result=migrate(root,apply=True,now=1792648800)
            self.assertEqual(result['stages_removed'],['ads-meta'])
            state=json.loads((root/'row-progress-test.json').read_text())
            self.assertNotIn('cycle',state['rows']['bad'])
            self.assertFalse(json.loads((root/'meta-request-progress.json').read_text())['pending'])
