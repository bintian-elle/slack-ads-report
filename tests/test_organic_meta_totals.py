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
    def test_fetch_binds_business_views_by_both_media_id_and_permalink(self):
        from kol_organic_meta_totals import fetch_totals
        values=sheet();values[1]=values[1]+['Reel ID'];values[2]=values[2]+['12345678901']
        node={'id':'12345678901','permalink':'https://instagram.com/reel/ABC/','username':'eva.hooft','total_like_count':10}
        with patch('kol_organic_meta_totals.api_read',return_value={'12345678901':node}), \
             patch('kol_organic_meta_totals.creator_media',return_value={'ABC':dict(node,view_count=5442)}):
            content,_=fetch_totals({},values)
        self.assertEqual(content['ABC']['view_count'],5442)
        with patch('kol_organic_meta_totals.api_read',return_value={'12345678901':node}), \
             patch('kol_organic_meta_totals.creator_media',return_value={'ABC':dict(node,id='OTHER',view_count=99999)}):
            content,_=fetch_totals({},values)
        self.assertNotIn('view_count',content['ABC'])

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
        self.assertEqual(rows[0]['missing'],['Saves'])
        self.assertNotIn('Interaction',rows[0]['reason'])

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
        self.assertIn('Saves',rows[0]['reason'])

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
