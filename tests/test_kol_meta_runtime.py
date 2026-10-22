import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import requests


def response(data, status=200, usage=None):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(data).encode()
    r.headers['Content-Type'] = 'application/json'
    if usage is not None:
        r.headers['x-app-usage'] = json.dumps(usage)
    return r


class MetaRuntimeTests(unittest.TestCase):
    def test_cooldown_only_invocation_does_not_erase_engagement_priority(self):
        from kol_meta_runtime import MetaDeferred
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',return_value=response({'error':{'code':4}},403)):
            with self.runtime(directory) as runtime,runtime.stage('organic-meta'),runtime.lane('views',1):
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/views')
            with self.runtime(directory) as runtime,runtime.stage('organic-meta'):
                for lane in runtime.metric_order():
                    with runtime.lane(lane,1):
                        with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/'+lane)
                self.assertEqual(runtime.sent,0)
            self.assertEqual(self.runtime(directory,now=lambda:1792584000).metric_order(),['engagement','views'])
    def test_lane_budget_yields_to_other_metrics_without_global_cooldown(self):
        from kol_meta_runtime import MetaDeferred
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',return_value=response({'id':'ok'})) as network:
            with self.runtime(directory) as runtime,runtime.stage('organic-meta'):
                with runtime.lane('views',1):
                    requests.get('https://graph.facebook.com/view1')
                    with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/view2')
                with runtime.lane('engagement',1):requests.get('https://graph.facebook.com/likes')
                self.assertNotIn('blocked_until',runtime.state)
            self.assertEqual(network.call_count,2)

    def test_rate_limit_in_views_gives_engagement_first_turn_after_restart(self):
        from kol_meta_runtime import MetaDeferred
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',return_value=response({'error':{'code':4}},403)):
            with self.runtime(directory) as runtime,runtime.stage('organic-meta'),runtime.lane('views',5):
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/views')
            restarted=self.runtime(directory,now=lambda:1792584000)
            self.assertEqual(restarted.metric_order(),['engagement','views'])
    def test_cooldown_resume_reuses_inventory_snapshot_without_restarting_pages(self):
        calls=[]
        def network(session,method,url,**kwargs):
            calls.append(url);return response({'data':[]})
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',network):
            with self.runtime(directory) as runtime,runtime.stage('organic-meta'):
                requests.get('https://graph.facebook.com/act_1/ads')
            with self.runtime(directory,now=lambda:1792584000) as runtime,runtime.stage('organic-meta',resume=True):
                requests.get('https://graph.facebook.com/act_1/ads')
            self.assertEqual(len(calls),1)
    def test_interruption_leaves_inflight_stage_for_poll_resume(self):
        from kol_meta_runtime import run_stage
        def interrupted():raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            runtime=self.runtime(directory)
            with self.assertRaises(KeyboardInterrupt):run_stage(runtime,'organic-meta',interrupted)
            restarted=self.runtime(directory,now=lambda:1792580400)
            self.assertIn('organic-meta',restarted.pending(due=True))
    def test_plan_only_does_not_create_persistent_retry_work(self):
        from kol_meta_runtime import MetaDeferred
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',return_value=response({'error':{'code':4}},403)):
            with self.runtime(directory,persist=False) as runtime,runtime.stage('organic-meta'):
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/x')
            self.assertFalse((Path(directory)/'meta-request-progress.json').exists())

    def test_nested_business_usage_also_blocks_other_endpoints(self):
        from kol_meta_runtime import MetaDeferred
        r=response({'id':'1'});r.headers['x-business-use-case-usage']=json.dumps({'business':[{'type':'instagram','call_count':81,'total_time':0}]})
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',return_value=r) as network:
            with self.runtime(directory) as runtime:
                requests.get('https://graph.facebook.com/1')
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/2')
            self.assertEqual(network.call_count,1)

    def test_reporting_window_changes_at_eastern_two_not_midnight(self):
        from kol_meta_runtime import reporting_window
        from datetime import datetime,timezone
        self.assertEqual(reporting_window(datetime(2026,10,21,5,59,tzinfo=timezone.utc).timestamp()),'2026-10-20')
        self.assertEqual(reporting_window(datetime(2026,10,21,6,0,tzinfo=timezone.utc).timestamp()),'2026-10-21')

    def runtime(self, directory, **options):
        from kol_meta_runtime import MetaRuntime
        return MetaRuntime(Path(directory), 'secret-token', now=options.pop('now',lambda:1792580000), **options)

    def test_success_survives_restart_and_budget_resumes_only_missing_request(self):
        from kol_meta_runtime import MetaDeferred
        calls = []
        def network(session, method, url, **kwargs):
            calls.append(url)
            return response({'id': url.rsplit('/', 1)[-1]})
        with tempfile.TemporaryDirectory() as directory, patch('requests.sessions.Session.request', network):
            with self.runtime(directory, budget=1) as runtime, runtime.stage('organic-meta'):
                self.assertEqual(requests.get('https://graph.facebook.com/v26.0/1').json(), {'id': '1'})
                with self.assertRaises(MetaDeferred):
                    requests.get('https://graph.facebook.com/v26.0/2')
            # A new process can reuse a successful response even during cooldown.
            with self.runtime(directory, budget=1, now=lambda:1792580400) as runtime, runtime.stage('organic-meta'):
                self.assertEqual(requests.get('https://graph.facebook.com/v26.0/1').json(), {'id': '1'})
                self.assertEqual(requests.get('https://graph.facebook.com/v26.0/2').json(), {'id': '2'})
            self.assertEqual(calls, ['https://graph.facebook.com/v26.0/1', 'https://graph.facebook.com/v26.0/2'])

    def test_usage_stops_other_meta_paths_and_persists_but_never_blocks_google(self):
        from kol_meta_runtime import MetaDeferred
        calls = []
        def network(session, method, url, **kwargs):
            calls.append(url)
            return response({'data': []}, usage={'total_time': 81})
        with tempfile.TemporaryDirectory() as directory, patch('requests.sessions.Session.request', network):
            with self.runtime(directory) as runtime, runtime.stage('organic-meta'):
                self.assertEqual(requests.Session().get('https://graph.facebook.com/v26.0/ads').status_code,200)
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/v26.0/media')
                self.assertEqual(requests.get('https://sheets.googleapis.com/test').status_code,200)
            with self.runtime(directory) as runtime, runtime.stage('ads-meta'):
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/v26.0/insights')
            self.assertEqual(len(calls),2)
            self.assertEqual(set(runtime.pending()), {'organic-meta','ads-meta'})

    def test_credentials_are_not_persisted_and_patch_is_restored(self):
        original=requests.sessions.Session.request
        with tempfile.TemporaryDirectory() as directory, patch('requests.sessions.Session.request', return_value=response({'data':[], 'paging':{'next':'https://graph.facebook.com/x?access_token=secret-token&after=a'}})):
            with self.runtime(directory) as runtime, runtime.stage('organic-meta'):
                requests.get('https://graph.facebook.com/x',params={'access_token':'secret-token'})
            self.assertNotIn('secret-token', ''.join(p.read_text() for p in Path(directory).glob('meta-*json')))
        self.assertIs(requests.sessions.Session.request,original)

    def test_rate_error_blocks_without_retry_and_records_stage(self):
        from kol_meta_runtime import MetaDeferred
        with tempfile.TemporaryDirectory() as directory, patch('requests.sessions.Session.request',return_value=response({'error':{'code':4}},403)) as network:
            with self.runtime(directory) as runtime, runtime.stage('organic-meta'):
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/x')
                with self.assertRaises(MetaDeferred):requests.get('https://graph.facebook.com/y')
            self.assertEqual(network.call_count,1)
            self.assertIn('organic-meta',runtime.pending())

    def test_permanent_lookup_failure_does_not_repeat_or_starve_other_posts(self):
        calls=[]
        def network(session,method,url,**kwargs):
            calls.append(url)
            return response({'error':{'code':110}},400) if url.endswith('/bad') else response({'id':'good'})
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',network):
            with self.runtime(directory,budget=1) as runtime:
                self.assertEqual(requests.get('https://graph.facebook.com/bad').status_code,400)
            with self.runtime(directory,budget=1) as runtime:
                self.assertEqual(requests.get('https://graph.facebook.com/bad').status_code,400)
                self.assertEqual(requests.get('https://graph.facebook.com/good').json(),{'id':'good'})
            self.assertEqual(len(calls),2)

    def test_inventory_expiry_does_not_expire_metric_progress(self):
        calls=[]
        def network(session,method,url,**kwargs):
            calls.append(url);return response({'data':[]})
        with tempfile.TemporaryDirectory() as directory,patch('requests.sessions.Session.request',network):
            with self.runtime(directory) as runtime:
                requests.get('https://graph.facebook.com/act_1/ads')
                requests.get('https://graph.facebook.com/1/insights')
            with self.runtime(directory,now=lambda:1792581000) as runtime:
                requests.get('https://graph.facebook.com/act_1/ads')
                requests.get('https://graph.facebook.com/1/insights')
            self.assertEqual(calls,['https://graph.facebook.com/act_1/ads','https://graph.facebook.com/1/insights','https://graph.facebook.com/act_1/ads'])

    def test_resume_invokes_only_due_kol_rows_and_clears_on_success(self):
        from kol_meta_runtime import resume_pending
        with tempfile.TemporaryDirectory() as directory:
            runtime=self.runtime(directory)
            runtime.state['pending']={'organic-views':{'retry_at':0},'organic-meta':{'retry_at':9999999999},'robot':{'retry_at':0}}
            with patch('kol_row_updates.run_kind',return_value=set()) as rows:
                resume_pending(runtime,{'KOL_TRACKER_STATE_DIR':directory},Path(directory),True)
            self.assertEqual(rows.call_count,1)
            self.assertEqual(rows.call_args.args[-1],'organic-meta')
            self.assertNotIn('organic-views',runtime.pending())
            self.assertIn('organic-meta',runtime.pending())
