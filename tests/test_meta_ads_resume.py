import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AdsResumeTests(unittest.TestCase):
    def test_failed_group_does_not_starve_following_group_and_remains_pending(self):
        from update_meta_tracker import resumable_insights
        from kol_meta_runtime import MetaRuntime
        ads=[{'id':'1','effective_status':'ACTIVE'},{'id':'2','effective_status':'ACTIVE'}]
        def pages(session,url,params):
            ids=json.loads(params['filtering'])[0]['value']
            if ids==['1']:raise RuntimeError('rejected')
            return [{'ad_id':'2','spend':'5'}]
        with tempfile.TemporaryDirectory() as directory,patch('update_meta_tracker.graph_pages',side_effect=pages):
            runtime=MetaRuntime(directory,'test')
            with runtime.stage('ads-meta'):
                got=resumable_insights(None,'base','act',ads,{'1','2'},'2026-01-01','2026-10-20',Path(directory)/'ads.json',batch_size=1,runtime=runtime)
                runtime.complete('ads-meta')
            self.assertEqual(got,([{'ad_id':'2','spend':'5'}],{'2'}))
            self.assertIn('ads-meta',runtime.pending())

    def test_reporting_boundary_and_reactivation_invalidate_same_day_cache(self):
        from update_meta_tracker import resumable_insights
        ads=[{'id':'1','effective_status':'PAUSED'}]
        with tempfile.TemporaryDirectory() as directory,patch('update_meta_tracker.graph_pages',return_value=[{'ad_id':'1','spend':'6'}]) as api:
            p=Path(directory)/'ads.json'
            with patch('kol_meta_runtime.reporting_window',return_value='first'):
                resumable_insights(None,'base','act',ads,{'1'},'2026-01-01','2026-10-20',p)
            with patch('kol_meta_runtime.reporting_window',return_value='second'):
                resumable_insights(None,'base','act',ads,{'1'},'2026-01-01','2026-10-20',p)
                ads[0]['effective_status']='ACTIVE'
                resumable_insights(None,'base','act',ads,{'1'},'2026-01-01','2026-10-20',p)
            self.assertEqual(api.call_count,3)
            self.assertIsNone(json.loads(p.read_text())['ads']['1']['paused_since'])
    def test_ads_metrics_prioritize_active_and_resume_successful_groups(self):
        from update_meta_tracker import resumable_insights
        from kol_meta_runtime import MetaDeferred
        ads=[{'id':'1','effective_status':'PAUSED'},{'id':'2','effective_status':'ACTIVE'}]
        calls=[]
        def pages(session,url,params):
            ids=json.loads(params['filtering'])[0]['value'];calls.append(ids)
            if ids==['1']:raise MetaDeferred('deferred')
            return [{'ad_id':'2','spend':'10'}]
        with tempfile.TemporaryDirectory() as directory,patch('update_meta_tracker.graph_pages',side_effect=pages):
            p=Path(directory)/'ads.json'
            got=resumable_insights(None,'https://graph.facebook.com/v26.0','act_1',ads,{'1','2'},'2026-01-01','2026-10-20',p,batch_size=1)
            self.assertEqual(got,([{'ad_id':'2','spend':'10'}],{'2'}))
            got=resumable_insights(None,'https://graph.facebook.com/v26.0','act_1',ads,{'1','2'},'2026-01-01','2026-10-20',p,batch_size=1)
            self.assertEqual(calls,[['2'],['1'],['1']])

    def test_weekly_paused_retains_cached_metrics_until_due(self):
        from update_meta_tracker import resumable_insights
        ads=[{'id':'1','effective_status':'PAUSED'}]
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'ads.json'
            p.write_text(json.dumps({'scope':['https://graph.facebook.com/v26.0','act_1','2026-01-01'],
                'ads':{'1':{'paused_since':'2026-10-01','fetched_on':'2026-10-14','rows':[{'ad_id':'1','spend':'5'}]}}}))
            with patch('update_meta_tracker.graph_pages',return_value=[{'ad_id':'1','spend':'6'}]) as api:
                got=resumable_insights(None,'https://graph.facebook.com/v26.0','act_1',ads,{'1'},'2026-01-01','2026-10-20',p)
                self.assertEqual(got,([{'ad_id':'1','spend':'5'}],{'1'}));self.assertEqual(api.call_count,0)
                got=resumable_insights(None,'https://graph.facebook.com/v26.0','act_1',ads,{'1'},'2026-01-01','2026-10-21',p)
                self.assertEqual(got,([{'ad_id':'1','spend':'6'}],{'1'}));self.assertEqual(api.call_count,1)
