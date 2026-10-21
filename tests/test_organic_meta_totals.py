import unittest
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch
from kol_organic_meta_totals import metric_plan, note_changes, verified_media


HEADERS = ['Creator','Organic Launch Date','Content Brief','Post Link','Ad Code',
           'Status','KOL Fee','Views','Interaction','Likes','Comments','Shares',
           'Saves','CPM','CPE','Note','Reason for data update failure']


def sheet():
    return [[],HEADERS, ['eva.hooft',46302,'','https://www.instagram.com/p/ABC/',
                         'code','T0',3000,100,20,5,3,2,0,30000,150,'','']]


class TotalMetricTests(unittest.TestCase):
    def test_views_api_usage_guard_retains_current_response_and_stops_following_calls(self):
        from kol_organic_meta_totals import api_read,APIReadError
        from threading import Event
        from unittest.mock import Mock
        stop=Event();env={'META_ACCESS_TOKEN':'test','_META_VIEWS_STOP_EVENT':stop}
        response=Mock(ok=True,status_code=200,headers={'x-app-usage':'{"total_time":80}'})
        response.json.return_value={'id':'123'}
        with patch('kol_organic_meta_totals.requests.get',return_value=response) as get:
            self.assertEqual(api_read(env,'123'),{'id':'123'})
            with self.assertRaises(APIReadError):api_read(env,'456')
        self.assertEqual(get.call_count,1)

    def test_views_alone_do_not_complete_recoverable_interaction_job(self):
        from kol_organic_meta_totals import completed_metric_keys
        values=sheet()
        self.assertEqual(completed_metric_keys([{'row':3,'changes':{7:100,9:1,10:2,11:3},'reason':'Interaction unavailable'}],values),set())
        self.assertEqual(completed_metric_keys([{'row':3,'changes':{7:100,8:6,9:1,10:2,11:3},'reason':''}],values),{'ig:ABC'})

    def test_original_counters_independent_of_public_views_and_total_counters(self):
        natural={'ABC':{'content_id':'123','permalink':'https://instagram.com/reel/ABC/',
                        'organic_insights':{'likes':42,'comments':16,'shares':9,'saves':0}}}
        public={'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/',
                       'view_count':1764,'total_like_count':999,'total_comments_count':999,'shares_count':999}}
        item=metric_plan(sheet(),public,{3:675},natural_content=natural)[0]
        self.assertEqual({c:item['changes'][c] for c in (7,8,9,10,11,12)},
                         {7:1764,8:675,9:42,10:16,11:9,12:0})
        self.assertAlmostEqual(item['changes'][14],3000/675)
        self.assertEqual(item['reason'],'')
        item=metric_plan(sheet(),{'ABC':{'_error':'HTTP 500'}},{3:675},natural_content=natural)[0]
        self.assertEqual([item['changes'][c] for c in (9,10,11)],[42,16,9])
        self.assertNotIn(7,item['changes'])
        self.assertNotIn('Likes',item['reason'])
        self.assertNotIn('Comments',item['reason'])
        self.assertNotIn('Shares',item['reason'])

    def test_original_counters_reject_different_post(self):
        natural={'ABC':{'content_id':'123','permalink':'https://instagram.com/reel/OTHER/',
                        'organic_insights':{'likes':42,'comments':16,'shares':9}}}
        item=metric_plan(sheet(),{},method_a={},natural_content=natural)[0]
        self.assertFalse(any(c in item['changes'] for c in (9,10,11)))

    def test_natural_counters_survive_paid_interaction_failure(self):
        from kol_organic_meta_totals import method_a_values
        natural={'ABC':{'content_id':'123','permalink':'https://instagram.com/reel/ABC/',
                        'organic_insights':{'likes':42,'comments':16,'shares':9}}}
        collected={}
        with tempfile.TemporaryDirectory() as directory, \
             patch('kol_organic_meta.fetch_content',return_value=natural), \
             patch('kol_organic_meta.fetch_ad_interactions',side_effect=RuntimeError('Unavailable')):
            result=method_a_values({},sheet(),Path(directory),Path(directory)/'cache',natural_out=collected)
        self.assertEqual(result,{})
        self.assertEqual(collected,natural)

    def test_fetch_binds_business_views_by_both_media_id_and_permalink(self):
        from kol_organic_meta_totals import fetch_totals
        values=sheet();values[1]=values[1]+['Reel ID'];values[2]=values[2]+['12345678901']
        node={'id':'12345678901','permalink':'https://instagram.com/reel/ABC/','username':'eva.hooft','total_like_count':10}
        with patch('kol_organic_meta_totals.api_read',side_effect=AssertionError('Views must not depend on media metadata')) as read, \
             patch('kol_organic_meta_totals.creator_media',return_value={'ABC':dict(node,view_count=5442)}):
            content,_=fetch_totals({},values)
        self.assertEqual(content['ABC']['view_count'],5442)
        self.assertEqual(read.call_count,0)
        with patch('kol_organic_meta_totals.api_read',return_value=node), \
             patch('kol_organic_meta_totals.creator_media',return_value={'ABC':dict(node,id='OTHER',view_count=99999)}):
            content,_=fetch_totals({},values)
        self.assertNotIn('view_count',content['ABC'])

    def test_views_error_is_actual_business_discovery_failure(self):
        from kol_organic_meta_totals import fetch_totals,APIReadError
        values=sheet();values[1]+= ['Reel ID'];values[2]+= ['12345678901']
        with patch('kol_organic_meta_totals.creator_media',side_effect=APIReadError('HTTP 403 / Meta code 4',code=4)), \
             patch('kol_organic_meta_totals.api_read',side_effect=AssertionError('Stop metadata requests after rate limit')):
            content,_=fetch_totals({},values)
        self.assertIn('HTTP 403 / Meta code 4',content['ABC']['_error'])
        self.assertNotIn('500',content['ABC']['_error'])

    def test_alias_fallback_uses_single_media_get_not_ids_and_checks_post(self):
        from kol_organic_meta_totals import fetch_totals
        values=sheet();values[2][0]='CreatorAlias';values[2]+= ['12345678901']
        node={'id':'12345678901','permalink':'https://instagram.com/reel/ABC/','username':'real.creator'}
        def discover(env,username,wanted):
            return {'ABC':dict(node,view_count=42)} if username=='real.creator' else {}
        with patch('kol_organic_meta_totals.creator_media',side_effect=discover), \
             patch('kol_organic_meta_totals.api_read',return_value=node) as read:
            content,_=fetch_totals({},values)
        self.assertEqual(content['ABC']['view_count'],42)
        self.assertEqual(read.call_args.args[1],'12345678901')
        self.assertNotIn('ids',read.call_args.args[2])

    def test_views_only_plan_preserves_unrelated_failures_and_metrics(self):
        from kol_organic_meta_totals import views_only_plan
        values=sheet();values[2][16]='Public Views unavailable: HTTP 500 / Meta code 100; existing values retained; CPM not refreshed: total Views unavailable or zero; API did not return Interaction'
        content={'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/','view_count':200}}
        item=views_only_plan(values,content)[0]
        self.assertEqual(item['changes'],{7:200,13:15000})
        self.assertEqual(item['reason'],'API did not return Interaction')
        self.assertNotIn(8,item['changes']);self.assertNotIn(14,item['changes'])

    def test_creator_lookup_stops_before_posts_outside_target_year(self):
        from kol_organic_meta_totals import creator_media
        data={'business_discovery':{'media':{'data':[{'id':'1','permalink':'https://instagram.com/reel/OLD/','timestamp':'2025-12-01T00:00:00+0000'}],
                    'paging':{'next':'https://graph.facebook.com/next'}}}}
        with patch('kol_organic_meta_totals.api_read',return_value=data) as read:
            self.assertEqual(creator_media({},'eva.hooft',{'ABC'}),{})
        self.assertEqual(read.call_count,1)

    def test_total_views_is_not_business_discovery_view_count(self):
        rows=metric_plan(sheet(),{'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/',
                                      'total_views_count':4841}},method_a={3:336})
        self.assertNotIn(7,rows[0]['changes'])
        self.assertEqual(rows[0]['changes'][8],336)

    def test_method_a_interaction_independent_of_public_counter_sum(self):
        rows=metric_plan(sheet(),{'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/',
            'view_count':39099,'total_like_count':242,'total_comments_count':295,'shares_count':133}},
            method_a={3:675})
        self.assertEqual(rows[0]['changes'][8],675)
        self.assertAlmostEqual(rows[0]['changes'][14],3000/675)
        self.assertEqual(rows[0]['missing'],[])
        self.assertNotIn('Interaction',rows[0]['reason'])
        self.assertEqual(rows[0]['reason'],'')
        self.assertNotIn(12,rows[0]['changes'])

    def test_method_a_still_updates_when_public_counters_fail(self):
        rows=metric_plan(sheet(),{},method_a={3:675})
        self.assertEqual(rows[0]['changes'][8],675)
        self.assertEqual(rows[0]['changes'][14],3000/675)
        self.assertNotIn(7,rows[0]['changes'])

    def test_method_a_uses_existing_source_calculation(self):
        from kol_organic_meta_totals import method_a_values
        natural={'ABC':{'content_id':'123','permalink':'https://instagram.com/reel/ABC/',
            'organic_insights':{'interaction':100}}}
        with tempfile.TemporaryDirectory() as directory, \
             patch('kol_organic_meta.fetch_content',return_value=natural), \
             patch('kol_organic_meta.fetch_ad_interactions',return_value=([],{'123':{'value':7}})):
            values=method_a_values({},sheet(),Path(directory),Path(directory)/'cache.json')
        self.assertEqual(values,{3:107})

    def test_public_totals_replace_natural_not_add_paid(self):
        media={'id':'123','permalink':'https://www.instagram.com/reel/ABC/',
               'view_count':26628,'total_like_count':241,
               'total_comments_count':295,'shares_count':133,'saved_count':6,
               'organic_insights':{'views':1,'likes':1}}
        rows=metric_plan(sheet(),{'ABC':media})
        self.assertEqual(rows[0]['changes'][7],26628)
        self.assertEqual(rows[0]['changes'][8],675)
        self.assertAlmostEqual(rows[0]['changes'][13],3000/26628*1000)
        self.assertAlmostEqual(rows[0]['changes'][14],3000/675)

    def test_missing_saved_never_zero_or_stale_interaction(self):
        rows=metric_plan(sheet(),{'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/',
            'view_count':1574,'total_like_count':42,'total_comments_count':16,'shares_count':9}})
        self.assertEqual(rows[0]['changes'][9],42)
        self.assertNotIn(8,rows[0]['changes'])
        self.assertNotIn(12,rows[0]['changes'])
        self.assertNotIn(14,rows[0]['changes'])
        self.assertIn('existing values retained',rows[0]['reason'])
        self.assertNotIn('Saves',rows[0]['reason'])

    def test_no_natural_fallback(self):
        rows=metric_plan(sheet(),{'ABC':{'id':'123','permalink':'https://instagram.com/reel/ABC/',
            'organic_insights':{'views':100,'interaction':10,'likes':5}}})
        self.assertEqual(rows[0]['changes'],{})

    def test_exact_post_not_creator_name(self):
        self.assertFalse(verified_media({'id':'1','permalink':'https://instagram.com/reel/OTHER/'},'ABC'))
        rows=metric_plan(sheet(),{'ABC':{'id':'1','permalink':'https://instagram.com/reel/OTHER/','view_count':100}})
        self.assertFalse(rows[0]['changes'])

    def test_zero_is_real_missing_is_not(self):
        rows=metric_plan(sheet(),{'ABC':{'id':'1','permalink':'https://instagram.com/reel/ABC/',
            'view_count':0,'total_like_count':0,'total_comments_count':0,'shares_count':0,'saved_count':0}})
        self.assertEqual(rows[0]['changes'][8],0)
        self.assertNotIn(13,rows[0]['changes'])
        self.assertNotIn(14,rows[0]['changes'])

    def test_invalid_counter_preserves_old(self):
        rows=metric_plan(sheet(),{'ABC':{'id':'1','permalink':'https://instagram.com/reel/ABC/',
            'view_count':True,'total_like_count':-1}})
        self.assertEqual(rows[0]['changes'],{})

    def test_manual_note_preserved_managed_note_cleared(self):
        values=sheet();values[2][16]='Manual note'
        self.assertEqual(note_changes(values,[{'row':3,'reason':'Total Views unavailable'}]),[])
        values[2][16]='API did not return Views'
        self.assertEqual(note_changes(values,[{'row':3,'reason':''}]),[{'row':3,'changes':{16:''}}])

    def test_missing_fee_does_not_stop_counters(self):
        values=sheet();values[2][6]=''
        rows=metric_plan(values,{'ABC':{'id':'1','permalink':'https://instagram.com/reel/ABC/','view_count':100}})
        self.assertEqual(rows[0]['changes'][7],100)
        self.assertNotIn(13,rows[0]['changes'])
        self.assertIn('fee',rows[0]['reason'])

    def test_native_writer_preserves_manual_status_format_and_missing_interaction(self):
        from kol_organic_meta_totals import write_updates
        values=sheet();values[0]=['']*6;values[0][5]='old date'
        raw=[]
        for row in values:
            cells=[]
            for value in row:
                key='numberValue' if isinstance(value,(int,float)) else 'stringValue'
                cells.append({'userEnteredValue':{key:value}} if value!='' else {})
            raw.append({'values':cells})
        raw[2]['values'][5]['dataValidation']={'condition':{'type':'ONE_OF_LIST','values':[{'userEnteredValue':'T0'}]}}
        raw[2]['values'][7]['userEnteredFormat']={'numberFormat':{'type':'NUMBER','pattern':'#,##0'}}
        raw[2]['values'][8]={'userEnteredValue':{'formulaValue':'=J3+K3+L3+M3'},'effectiveValue':{'numberValue':20}}
        native={'sheets':[{'data':[{'rowData':raw}]}]};after=copy.deepcopy(native)
        class Reply:
            def raise_for_status(self):pass
        class Session:
            def post(self,url,json,timeout):
                for request in json['requests']:
                    update=request['updateCells'];region=update['range']
                    after['sheets'][0]['data'][0]['rowData'][region['startRowIndex']]['values'][region['startColumnIndex']]['userEnteredValue']=update['rows'][0]['values'][0]['userEnteredValue']
                return Reply()
        content={'ABC':{'id':'1','permalink':'https://instagram.com/reel/ABC/',
                       'view_count':200,'total_like_count':10,'total_comments_count':8,'shares_count':7}}
        with tempfile.TemporaryDirectory() as directory, patch('kol_tracker.read_tab',side_effect=[({},native,values),({},after,values)]):
            write_updates(Session(),'endpoint',{'sheetId':0},native,values,metric_plan(values,content),[],Path(directory),True)
        result=after['sheets'][0]['data'][0]['rowData'][2]['values']
        self.assertEqual(result[8]['userEnteredValue'],{'numberValue':20})
        self.assertEqual(result[5],raw[2]['values'][5])
        self.assertEqual(result[7]['userEnteredFormat'],raw[2]['values'][7]['userEnteredFormat'])
        self.assertEqual(result[7]['userEnteredValue'],{'numberValue':200})
        self.assertEqual(result[14]['userEnteredValue'],{'numberValue':150})
