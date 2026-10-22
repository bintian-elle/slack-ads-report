import tempfile
import unittest
from pathlib import Path


class RowQueueTests(unittest.TestCase):
    def test_successful_row_not_repeated_after_process_restart_or_sort(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            rows=[{'key':'ig:A','row':3,'status':'ACTIVE'}, {'key':'ig:B','row':4,'status':None}]
            def writer(item):
                calls.append(item['key'])
                if item['key']=='ig:B':
                    import requests
                    raise requests.Timeout('temporary write failure')
                return True
            q.run(rows,writer)
            self.assertEqual(calls,['ig:A','ig:B'])
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792652400)
            rows.reverse();rows[0]['row']=3;rows[1]['row']=4
            q.run(rows,lambda item:calls.append(item['key']) or True)
            self.assertEqual(calls,['ig:A','ig:B','ig:B'])

    def test_active_then_unknown_then_recent_paused_and_weekly_old_paused(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            # Oct 22; pause observation, not launch date, determines the grace.
            q.state['rows']['old']={'paused_since':'2026-10-01','fetched_on':'2026-10-20'}
            q.state['rows']['due']={'paused_since':'2026-10-01','fetched_on':'2026-10-14'}
            q.state['rows']['recent']={'paused_since':'2026-10-20','fetched_on':'2026-10-21'}
            seen=[]
            q.run([{'key':k,'status':s,'row':i} for i,(k,s) in enumerate([
                ('old','PAUSED'),('due','PAUSED'),('recent','PAUSED'),('unknown',None),('active','ACTIVE')])],
                lambda item:seen.append(item['key']) or True)
            self.assertEqual(seen,['active','unknown','recent','due'])

    def test_partial_row_not_marked_complete_and_next_row_can_succeed(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            q.run([{'key':'a','status':'ACTIVE'},{'key':'b','status':'ACTIVE'}],lambda item:item['key']=='b')
            self.assertNotIn('cycle',q.state['rows']['a'])
            self.assertEqual(q.state['rows']['b']['cycle'],'2026-10-22')

    def test_reactivation_overrides_same_day_paused_checkpoint(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            q.run([{'key':'a','status':'PAUSED'}],lambda _:True)
            seen=[]
            q.run([{'key':'a','status':'ACTIVE'}],lambda item:seen.append(item['key']) or True)
            self.assertEqual(seen,['a'])

    def test_quota_stops_before_next_row_without_losing_current_checkpoint(self):
        from kol_row_updates import RowQueue
        from kol_meta_runtime import MetaDeferred
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            seen=[]
            def writer(item):
                seen.append(item['key']);raise MetaDeferred('quota')
            q.run([{'key':'a','status':'ACTIVE'},{'key':'b','status':'ACTIVE'}],writer)
            self.assertEqual(seen,['a'])
            self.assertNotIn('cycle',q.state['rows']['a'])

    def test_metric_hold_expires_at_eastern_daily_boundary(self):
        from kol_row_updates import metrics_allowed,hold_until_daily
        with tempfile.TemporaryDirectory() as directory:
            env={'KOL_TRACKER_STATE_DIR':directory}
            until=hold_until_daily(env,now=1792602000)
            self.assertFalse(metrics_allowed(env,now=until-1))
            self.assertTrue(metrics_allowed(env,now=until))

    def test_failed_reactivation_does_not_reuse_old_complete_marker(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            q.run([{'key':'a','status':'PAUSED'}],lambda _:True)
            q.run([{'key':'a','status':'ACTIVE'}],lambda _:False)
            self.assertEqual(q.remaining,0)
            self.assertEqual(q.state['rows']['a']['unavailable_cycle'],'2026-10-22')
            self.assertNotIn('cycle',q.state['rows']['a'])

    def test_live_dispatch_finishes_one_post_before_inserting_next(self):
        from kol_live_updates import enqueue_jobs,dispatch_jobs
        state={};enqueue_jobs(state,[{'platform':'YouTube','creator':'a','post_link':'https://youtu.be/m3voSsAfZQw','ts':'100','ad_code':''},
            {'platform':'YouTube','creator':'b','post_link':'https://youtu.be/ETVZaHw6gpc','ts':'100','ad_code':''}],100)
        events=[]
        def writer(jobs):
            events.append('write:'+jobs[0]['creator'])
            return {'yt:'+job['post_link'].rsplit('/',1)[1] for job in jobs}
        dispatch_jobs(state,{'organic-youtube':writer},lambda kind,jobs:events.append('insert:'+jobs[0]['creator']),lambda:None,100)
        self.assertEqual(events,['insert:a','write:a','insert:b','write:b'])

    def test_live_hold_inserts_but_never_calls_metric_handler(self):
        from kol_live_updates import enqueue_jobs,dispatch_jobs
        state={};enqueue_jobs(state,[{'platform':'YouTube','creator':'a','post_link':'https://youtu.be/m3voSsAfZQw','ts':'100','ad_code':''}],100)
        events=[]
        dispatch_jobs(state,{'organic-youtube':lambda jobs:self.fail('metric called during hold')},
            lambda kind,jobs:events.append('insert'),lambda:None,100,metric_not_before=200)
        self.assertEqual(events,['insert'])
        self.assertEqual(next(iter(state['jobs'].values()))['retry_at'],200)

    def test_pending_resume_respects_hold_without_calling_any_writer(self):
        from unittest.mock import patch
        from kol_meta_runtime import MetaRuntime,resume_pending
        from kol_row_updates import hold_until_daily
        with tempfile.TemporaryDirectory() as directory:
            env={'KOL_TRACKER_STATE_DIR':directory}
            until=hold_until_daily(env,now=1792602000)
            runtime=MetaRuntime(Path(directory),'test',now=lambda:until-10)
            runtime.state['pending']['organic-meta']={'retry_at':0}
            with patch('kol_organic_meta_totals.run',side_effect=AssertionError('writer during hold')):
                resume_pending(runtime,env,Path(directory),True)
            self.assertIn('organic-meta',runtime.pending())

    def test_missing_natural_counter_does_not_mark_youtube_row_complete(self):
        from kol_organic_youtube import run
        from unittest.mock import patch,Mock
        values=[['Creator','Organic Launch Date','Post Link','KOL Fee','Views','Interaction','Likes','Comments','Saves','Shares','CPM','CPE','Reason for data update failure'],
                ['a',0,'https://youtu.be/m3voSsAfZQw',10]]
        native={'sheets':[{'data':[{'rowData':[{'values':[{}]*13},{'values':[{}]*13}]}]}]}
        with tempfile.TemporaryDirectory() as directory,patch('kol_tracker.sheet_session',return_value=(Mock(),'endpoint')),\
             patch('kol_tracker.read_tab',return_value=({'sheetId':1},native,values)),\
             patch('kol_organic_youtube.fetch_videos',return_value={'m3voSsAfZQw':{'statistics':{'viewCount':'100','likeCount':'5'}}}):
            result=run({'KOL_ORGANIC_SHEETS_LINK':'sheet'},Path(directory),False,link_aliases={})
        self.assertFalse(result)

    def test_automatic_status_respects_manual_values_and_dropdown_vocabulary(self):
        from kol_row_updates import automatic_status
        paused=[{'effective_status':'ADSET_PAUSED'}]
        rule={'dataValidation':{'condition':{'type':'ONE_OF_LIST','values':[{'userEnteredValue':'pause'},{'userEnteredValue':'testing'}]}}}
        self.assertEqual(automatic_status('testing',paused,rule),'pause')
        self.assertIsNone(automatic_status('T0',paused,rule))
        self.assertEqual(automatic_status('',[{'effective_status':'ACTIVE'}],rule),'testing')
        self.assertIsNone(automatic_status('testing',[],rule))

    def test_real_coordinator_calls_verified_writer_one_post_at_a_time(self):
        from kol_row_updates import run_rows
        from unittest.mock import patch
        values=[['header'],['a',0,'https://youtu.be/m3voSsAfZQw'],['b',0,'https://youtu.be/ETVZaHw6gpc']]
        calls=[]
        with tempfile.TemporaryDirectory() as directory,patch('kol_live_insert.open_tab',return_value=(None,'sheet-endpoint',None,None,values)),patch('kol_row_updates.time.sleep'):
            env={'KOL_TRACKER_STATE_DIR':directory}
            def write(env,out,apply,target_keys,link_aliases):
                calls.append(set(target_keys));return set(target_keys)
            run_rows(env,Path(directory),True,'organic-youtube',write,link_aliases={})
            run_rows(env,Path(directory),True,'organic-youtube',write,link_aliases={})
        self.assertEqual(calls,[{'yt:m3voSsAfZQw'},{'yt:ETVZaHw6gpc'}])

    def test_pause_grace_observation_saved_even_if_metrics_fail(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            q.run([{'key':'a','status':'PAUSED'}],lambda _:False)
            self.assertEqual(q.state['rows']['a']['paused_since'],'2026-10-22')
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792735200)
            q.run([{'key':'a','status':'PAUSED'}],lambda _:False)
            self.assertEqual(q.state['rows']['a']['paused_since'],'2026-10-22')

    def test_daily_row_targeting_keeps_yesterday_but_live_uses_today(self):
        from update_meta_tracker import metric_cutoff
        from datetime import date
        today=date(2026,10,22)
        self.assertEqual(metric_cutoff({'_KOL_ROW_MODE':True},True,today),'2026-10-21')
        self.assertEqual(metric_cutoff({'_KOL_ROW_MODE':True,'_KOL_LIVE_METRICS':True},True,today),'2026-10-22')

    def test_live_tiktok_posts_share_inventory_and_report(self):
        from kol_row_updates import run_rows
        from unittest.mock import patch
        values=[[],[],['a','','','https://www.tiktok.com/@a/video/123']]
        with tempfile.TemporaryDirectory() as directory,patch('kol_live_insert.open_tab',return_value=(None,'sheet',None,None,values)),\
             patch('kol_tracker.fetch_ad_details',return_value=[]) as details,patch('kol_tracker.fetch_report',return_value=[]) as report,\
             patch('kol_tracker.tiktok_bindings',return_value=([],[])):
            env={'KOL_TRACKER_STATE_DIR':directory}
            for _ in range(2):run_rows(env,Path(directory),False,'ads-tiktok',lambda *args,**kwargs:set(),link_aliases={})
        self.assertEqual(details.call_count,1)
        self.assertEqual(report.call_count,1)

    def test_new_daily_window_retries_yesterdays_backed_off_row(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as directory:
            q=RowQueue(Path(directory)/'rows.json','sheet/tab',now=lambda:1792648800)
            q.state['rows']['a']={'observed_status':'ACTIVE','retry_at':1792670000,
                'attempt_cycle':'2026-10-21','attempts':8}
            seen=[]
            q.run([{'key':'a','status':'ACTIVE'}],lambda item:seen.append(item['key']) or True)
            self.assertEqual(seen,['a'])
