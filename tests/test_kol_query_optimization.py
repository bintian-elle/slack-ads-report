import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from test_organic_meta_totals import sheet


class QueryOptimizationTests(unittest.TestCase):
    def test_latest_eligible_row_first_not_row_number(self):
        from kol_row_updates import RowQueue
        with tempfile.TemporaryDirectory() as d:
            seen=[]
            queue=RowQueue(Path(d)/'rows.json','scope')
            queue.run([{'key':'old','row':3,'status':'ACTIVE','launch':46000},
                       {'key':'new','row':50,'status':'ACTIVE','launch':46300}],lambda x:seen.append(x['key']) or True)
        self.assertEqual(seen,['new','old'])

    def test_latest_creator_first_and_all_its_posts_in_one_lookup(self):
        from kol_organic_meta_totals import fetch_totals
        values=sheet();values[2][0]='older';values[2][1]=46000
        values.append(list(values[2]));values[3][0]='newer';values[3][1]=46300;values[3][3]='https://instagram.com/reel/NEW/'
        values.append(list(values[3]));values[4][3]='https://instagram.com/reel/NEW2/'
        calls=[]
        with patch('kol_organic_meta_totals.creator_media',side_effect=lambda e,n,keys:calls.append((n,keys)) or {}):
            fetch_totals({'_KOL_BD_ONLY':True},values)
        self.assertEqual(calls,[('newer',{'NEW','NEW2'}),('older',{'ABC'})])

    def test_same_inventory_reused_by_status_and_paid_interaction_consumer(self):
        from kol_organic_meta_totals import ad_inventory
        from kol_organic_meta import fetch_ad_interactions
        env={'META_ACCESS_TOKEN':'test','META_AD_ACCOUNT_ID':'123'}
        ads=[{'id':'1','created_time':'2026-01-01T00:00:00+0000','effective_status':'ACTIVE','creative':{'source_instagram_media_id':'media'}}]
        calls=[]
        def pages(s,url,params):
            calls.append(url)
            return ads if url.endswith('/ads') else []
        with patch('sync_creator_tracker.graph_pages',side_effect=pages), \
             patch('sync_creator_tracker.get_json',return_value={'timezone_name':'UTC'}):
            self.assertEqual(ad_inventory(env),ads)
            fetch_ad_interactions(dict(env),{'ABC':{'content_id':'media'}})
        self.assertEqual(sum(x.endswith('/ads') for x in calls),1)

    def test_incomplete_views_phase_reuses_its_successes_on_continuation(self):
        from kol_business_discovery_phase import prepare_views_phase
        from kol_meta_runtime import reporting_window
        import json
        values=sheet();values.append(list(values[2]));values[3][3]='https://instagram.com/reel/MISSING/'
        media={'ABC':{'id':'123','permalink':values[2][3],'view_count':42},'MISSING':{'_error':'Meta quota'}}
        seen=[]
        def fetch(env,*args):seen.append(env.get('_KOL_BD_SEED'));return media,{}
        with tempfile.TemporaryDirectory() as d:
            env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'}
            with patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},values)), \
                 patch('kol_organic_meta_totals.fetch_totals',side_effect=fetch), \
                 patch('kol_organic_meta_totals.write_updates',return_value={3:{7,13}}):
                prepare_views_phase(env,Path(d),True)
                prepare_views_phase(env,Path(d),True)
        self.assertEqual(seen[1]['ABC']['view_count'],42)

    def test_successful_field_seed_does_not_restart_remaining_creator_walk(self):
        from kol_organic_meta_totals import creator_media,APIReadError
        from kol_meta_runtime import MetaRuntime
        page={'business_discovery':{'media':{'data':[{'id':'1','permalink':'https://instagram.com/reel/ABC/','timestamp':'2026-04-01','view_count':42}],
            'paging':{'cursors':{'after':'deep'}}}}}
        with tempfile.TemporaryDirectory() as d:
            runtime=MetaRuntime(d,'test');env={'_KOL_META_RUNTIME':runtime}
            with patch('kol_organic_meta_totals.api_read',side_effect=[page,APIReadError('Network request failed')]):
                with self.assertRaises(APIReadError):creator_media(env,'actual.user',{'ABC','MISSING'})
            def read(env,path,params):
                self.assertIn('.after(deep)',params['fields'])
                return {'business_discovery':{'media':{'data':[]}}}
            with patch('kol_organic_meta_totals.api_read',side_effect=read):creator_media(env,'actual.user',{'MISSING'})

    def test_verified_username_survives_next_reporting_cycle_without_stale_views(self):
        from kol_business_discovery_phase import prepare_views_phase
        values=sheet();media={'ABC':{'id':'123','permalink':values[2][3],'username':'real.user','view_count':42}}
        calls=[]
        def fetch(env,rows,discovery=()):calls.append((env.get('_KOL_BD_SEED'),list(discovery)));return media,{}
        with tempfile.TemporaryDirectory() as d:
            env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'}
            with patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},values)), \
                 patch('kol_organic_meta_totals.fetch_totals',side_effect=fetch), \
                 patch('kol_organic_meta_totals.write_updates',return_value={3:{7,13}}), \
                 patch('kol_business_discovery_phase.reporting_window',side_effect=['2026-10-25','2026-10-26']):
                prepare_views_phase(env,Path(d),True);prepare_views_phase(env,Path(d),True)
        self.assertIsNone(calls[1][0])
        self.assertEqual(calls[1][1][0]['username'],'real.user')
        self.assertNotIn('view_count',calls[1][1][0])

    def test_views_batch_reserves_budget_for_completing_matched_post_ids(self):
        from kol_business_discovery_phase import prepare_views_phase
        from kol_meta_runtime import MetaRuntime
        import requests,json
        rows=sheet()[:2]
        for i in range(8):
            row=list(sheet()[2]);row[0]='creator'+str(i);row[3]='https://instagram.com/reel/P%d/'%i;rows.append(row)
        def network(session,method,url,**kwargs):
            response=requests.Response();response.status_code=200
            response._content=json.dumps({'data':[]} if '/insights' in url else {'business_discovery':{'media':{'data':[]}}}).encode()
            return response
        with tempfile.TemporaryDirectory() as d,patch('requests.sessions.Session.request',network), \
             patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},rows)), \
             patch('kol_organic_meta_totals.write_updates',return_value={}):
            with MetaRuntime(d,'test',budget=40) as runtime,runtime.lane('organic-meta',20):
                env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet','META_ACCESS_TOKEN':'test','_KOL_META_RUNTIME':runtime}
                prepare_views_phase(env,Path(d),True)
                self.assertEqual(runtime.sent,5)
                requests.get('https://graph.facebook.com/v23.0/ad/insights')
                self.assertEqual(runtime.sent,6)
