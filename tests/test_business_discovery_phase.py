import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kol_organic_meta_totals import creator_media, fetch_totals
from kol_meta_runtime import MetaRuntime
from test_organic_meta_totals import sheet


class BusinessDiscoveryPhaseTests(unittest.TestCase):
    def test_more_than_ten_pages_until_exact_post(self):
        def read(env,path,params):
            f=params['fields'];page=int(f.split('.after(')[1].split(')')[0]) if '.after(' in f else 1
            item={'id':str(page),'permalink':'https://instagram.com/reel/'+('ABC' if page==12 else 'other')+'/','timestamp':'2026-04-01','view_count':42}
            return {'business_discovery':{'media':{'data':[item],'paging':{'cursors':{'after':str(page+1)}}}}}
        with patch('kol_organic_meta_totals.api_read',side_effect=read):
            self.assertEqual(creator_media({},'actual.user',{'ABC'})['ABC']['view_count'],42)

    def test_stops_after_launch_boundary_not_arbitrary_page_count(self):
        data={'business_discovery':{'media':{'data':[{'id':'1','timestamp':'2026-03-15'}],'paging':{'cursors':{'after':'older'}}}}}
        with patch('kol_organic_meta_totals.api_read',return_value=data) as read:
            self.assertEqual(creator_media({'_KOL_BD_CUTOFF':'2026-03-16'},'actual.user',{'ABC'}),{})
        self.assertEqual(read.call_count,1)

    def test_resume_from_last_completed_page_after_temporary_failure(self):
        from kol_organic_meta_totals import APIReadError
        with tempfile.TemporaryDirectory() as d:
            runtime=MetaRuntime(d,'test');env={'_KOL_META_RUNTIME':runtime}
            first={'business_discovery':{'media':{'data':[{'id':'1','timestamp':'2026-04-01'}],'paging':{'cursors':{'after':'nextpage'}}}}}
            with patch('kol_organic_meta_totals.api_read',side_effect=[first,APIReadError('Network request failed')]):
                with self.assertRaises(APIReadError):creator_media(env,'actual.user',{'ABC'})
            runtime=MetaRuntime(d,'test');env['_KOL_META_RUNTIME']=runtime
            def resumed(env,path,params):
                self.assertIn('.after(nextpage)',params['fields'])
                return {'business_discovery':{'media':{'data':[{'id':'2','permalink':'https://instagram.com/reel/ABC/','view_count':77}]}}}
            with patch('kol_organic_meta_totals.api_read',side_effect=resumed):
                self.assertEqual(creator_media(env,'actual.user',{'ABC'})['ABC']['view_count'],77)

    def test_josh2_backend_alias_never_changes_creator_cell(self):
        values=sheet();values[2][0]='Josh-2';values[2][4]=''
        with patch('kol_organic_meta_totals.creator_media',return_value={}) as read:
            fetch_totals({},values)
        self.assertEqual(read.call_args.args[1],'josh.otusanya')
        self.assertEqual(values[2][0],'Josh-2')

    def test_id_stage_reads_prepared_views_without_discovery_requests(self):
        values=sheet();media={'id':'123','permalink':'https://instagram.com/reel/ABC/','view_count':19}
        with patch('kol_organic_meta_totals.api_read',side_effect=AssertionError('No Meta request allowed')):
            content,_=fetch_totals({'_KOL_BD_CONTENT':{'ABC':media}},values)
        self.assertEqual(content['ABC']['view_count'],19)

    def test_views_are_verified_before_phase_completion_and_reused(self):
        from kol_business_discovery_phase import prepare_views_phase
        values=sheet();events=[]
        media={'ABC':{'id':'123','permalink':values[2][3],'view_count':42}}
        with tempfile.TemporaryDirectory() as d:
            env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'}
            def write(*args):
                events.append('verified_write');values[2][7]=42;values[2][13]=3000/42*1000
                return {3:{7,13}}
            with patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},values)), \
                 patch('kol_organic_meta_totals.fetch_totals',return_value=(media,{})), \
                 patch('kol_organic_meta_totals.write_updates',side_effect=write):
                self.assertTrue(prepare_views_phase(env,Path(d),True))
                self.assertEqual(events,['verified_write'])
                self.assertTrue(prepare_views_phase(env,Path(d),True))
                self.assertEqual(events,['verified_write'])
            self.assertEqual(env['_KOL_BD_CONTENT']['ABC']['view_count'],42)

    def test_protected_returned_views_do_not_unlock_id_metrics(self):
        from kol_business_discovery_phase import prepare_views_phase
        with tempfile.TemporaryDirectory() as d:
            env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'}
            media={'ABC':{'id':'123','permalink':sheet()[2][3],'view_count':42}}
            with patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},sheet())), \
                 patch('kol_organic_meta_totals.fetch_totals',return_value=(media,{})), \
                 patch('kol_organic_meta_totals.write_updates',return_value={}):
                self.assertFalse(prepare_views_phase(env,Path(d),True))

    def test_discovery_only_does_not_probe_media_ids_for_aliases(self):
        values=sheet();values[2][0]='Display Name';values[2].append('12345678901')
        with patch('kol_organic_meta_totals.api_read',side_effect=AssertionError('ID lookup ahead of Views')):
            content,_=fetch_totals({'_KOL_BD_ONLY':True},values)
        self.assertIn('username',content['ABC']['_error'].lower())

    def test_temporary_views_failure_keeps_checkpoint_without_hiding_other_metrics(self):
        from kol_business_discovery_phase import prepare_views_phase
        values=sheet()
        with tempfile.TemporaryDirectory() as d:
            env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'}
            with patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},values)), \
                 patch('kol_organic_meta_totals.fetch_totals',return_value=({'ABC':{'_error':'Meta rate limit'}},{})), \
                 patch('kol_organic_meta_totals.write_updates'):
                self.assertFalse(prepare_views_phase(env,Path(d),True))
            self.assertIn('_KOL_BD_CONTENT',env)
            self.assertFalse(json.loads((Path(d)/'business-discovery-phase.json').read_text())['done'])

    def test_missing_views_do_not_block_other_metric_inventory(self):
        from kol_row_updates import run_rows
        with tempfile.TemporaryDirectory() as d, \
             patch('kol_business_discovery_phase.prepare_views_phase',return_value=False), \
             patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},[[],[],['','Creator']])), \
             patch('kol_organic_meta_totals.ad_inventory',side_effect=RuntimeError('inventory reached')) as inventory:
            run_rows({'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'},Path(d),False,'ads-meta',lambda *a,**kw:set())
            self.assertEqual(inventory.call_count,1)

    def test_unrelated_post_failure_does_not_block_id_stage_forever(self):
        from kol_business_discovery_phase import prepare_views_phase
        with tempfile.TemporaryDirectory() as d:
            env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'}
            with patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},sheet())), \
                 patch('kol_organic_meta_totals.fetch_totals',return_value=({'ABC':{'_error':'Exact post not returned by Business Discovery'}},{})), \
                 patch('kol_organic_meta_totals.write_updates'):
                self.assertTrue(prepare_views_phase(env,Path(d),True))

    def test_cached_views_repair_cleared_sheet_cell_without_new_api(self):
        from kol_business_discovery_phase import prepare_views_phase
        values=sheet();media={'ABC':{'id':'123','permalink':values[2][3],'view_count':42}}
        with tempfile.TemporaryDirectory() as d:
            env={'KOL_TRACKER_STATE_DIR':d,'KOL_ORGANIC_SHEETS_LINK':'sheet'}
            def write(*args):
                values[2][7]=42;values[2][13]=3000/42*1000
                return {3:{7,13}}
            with patch('kol_live_insert.open_tab',return_value=(None,'endpoint',{}, {},values)), \
                 patch('kol_organic_meta_totals.fetch_totals',return_value=(media,{})) as read, \
                 patch('kol_organic_meta_totals.write_updates',side_effect=write):
                self.assertTrue(prepare_views_phase(env,Path(d),True))
                values[2][7]=''
                self.assertTrue(prepare_views_phase(env,Path(d),True))
                self.assertEqual(values[2][7],42)
            self.assertEqual(read.call_count,1)

    def test_unfinished_cursor_survives_reporting_boundary(self):
        from kol_organic_meta_totals import APIReadError
        with tempfile.TemporaryDirectory() as d:
            r=MetaRuntime(d,'test',now=lambda:1792648800)
            first={'business_discovery':{'media':{'data':[{'id':'1','timestamp':'2026-04-01'}],'paging':{'cursors':{'after':'saved'}}}}}
            with patch('kol_organic_meta_totals.api_read',side_effect=[first,APIReadError('Network request failed')]):
                with self.assertRaises(APIReadError):creator_media({'_KOL_META_RUNTIME':r},'actual.user',{'ABC'})
            r=MetaRuntime(d,'test',now=lambda:1792735200)
            def resumed(env,path,params):
                self.assertIn('.after(saved)',params['fields'])
                return {'business_discovery':{'media':{'data':[]}}}
            with patch('kol_organic_meta_totals.api_read',side_effect=resumed):creator_media({'_KOL_META_RUNTIME':r},'actual.user',{'ABC'})

    def test_new_slack_post_does_not_requery_already_successful_posts(self):
        values=sheet();values.append(list(values[2]));values[3][3]='https://instagram.com/reel/NEW/'
        seed={'ABC':{'id':'123','permalink':values[2][3],'view_count':42}}
        seen=[]
        def discover(env,username,wanted):
            seen.append(wanted)
            return {'NEW':{'id':'456','permalink':values[3][3],'view_count':7}}
        with patch('kol_organic_meta_totals.creator_media',side_effect=discover):
            content,_=fetch_totals({'_KOL_BD_SEED':seed,'_KOL_BD_ONLY':True},values)
        self.assertEqual(seen,[{'NEW'}])
        self.assertEqual(content['ABC']['view_count'],42)
        self.assertEqual(content['NEW']['view_count'],7)

    def test_cached_counter_cannot_override_corrected_media_binding(self):
        values=sheet();values[2].append('99999999999')
        seed={'ABC':{'id':'123','permalink':values[2][3],'view_count':42}}
        with patch('kol_organic_meta_totals.creator_media',return_value={}):
            content,_=fetch_totals({'_KOL_BD_SEED':seed,'_KOL_BD_ONLY':True},values)
        self.assertNotIn('view_count',content['ABC'])

    def test_resume_keeps_cursor_but_refreshes_yesterdays_matched_counter(self):
        from kol_organic_meta_totals import APIReadError
        with tempfile.TemporaryDirectory() as d:
            old={'id':'1','permalink':'https://instagram.com/reel/OLD/','timestamp':'2026-04-01','view_count':10}
            page={'business_discovery':{'media':{'data':[old],'paging':{'cursors':{'after':'deep'}}}}}
            r=MetaRuntime(d,'test',now=lambda:1792648800)
            with patch('kol_organic_meta_totals.api_read',side_effect=[page,APIReadError('Network request failed')]):
                with self.assertRaises(APIReadError):creator_media({'_KOL_META_RUNTIME':r},'actual.user',{'OLD','ABC'})
            r=MetaRuntime(d,'test',now=lambda:1792735200)
            seen=[]
            def read(env,path,params):
                seen.append(params['fields'])
                if '.after(deep)' in params['fields']:
                    return {'business_discovery':{'media':{'data':[{'id':'2','permalink':'https://instagram.com/reel/ABC/','view_count':20}]}}}
                return {'business_discovery':{'media':{'data':[dict(old,view_count=99)]}}}
            with patch('kol_organic_meta_totals.api_read',side_effect=read):
                found=creator_media({'_KOL_META_RUNTIME':r},'actual.user',{'OLD','ABC'})
            self.assertEqual(found['OLD']['view_count'],99)
            self.assertIn('.after(deep)',seen[0])
