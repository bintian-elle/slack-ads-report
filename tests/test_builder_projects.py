import importlib.util
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from datetime import date
from pathlib import Path
spec=importlib.util.spec_from_file_location('builder_projects',Path(__file__).resolve().parents[1]/'scripts/cache-builder-projects.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class BuilderProjectTests(unittest.TestCase):
 def result(self,source,rows):
  account,identity=m.ACCOUNTS['biceek'][source]
  return {'account_ref':account,'resource_id':identity,'identity_verified':True,'currency':'USD','timezone':'America/Los_Angeles','status':'ok','coverage':{'complete':True},'data':rows,'count':len(rows)}
 def test_last_week_changes_only_tuesday(self):
  self.assertEqual(m.last_week(date(2026,10,26)),('2026-10-13','2026-10-19'))
  self.assertEqual(m.last_week(date(2026,10,27)),('2026-10-20','2026-10-26'))
  self.assertEqual(m.last_week(date(2026,10,25)),('2026-10-13','2026-10-19'))
 def test_ga4_preserves_native_period_sessions_not_daily_sum(self):
  daily=self.result('ga4',[{'date':'20261013','sessions':'8','ecommercePurchases':'1','purchaseRevenue':'30'},{'date':'20261014','sessions':'9','ecommercePurchases':'2','purchaseRevenue':'50'}])
  native=self.result('ga4',[{'sessions':'16','ecommercePurchases':'3','purchaseRevenue':'80'}])
  channel=self.result('ga4',[{'sessionDefaultChannelGroup':'Direct','sessions':'16','ecommercePurchases':'3','purchaseRevenue':'80'}])
  out=m.normalize('biceek','ga4','2026-10-13','2026-10-19',{'daily':daily,'totals':native,'channel':channel})
  self.assertEqual(out['totals']['sessions'],16);self.assertEqual(sum(r['sessions'] for r in out['rows']),17)
  self.assertEqual(out['breakdowns']['channel'][0]['label'],'Direct')
 def test_foreign_account_and_empty_ads_never_produce_zero_spend(self):
  empty=self.result('google_ads',[]);empty['status']='empty'
  out=m.normalize('biceek','google_ads','2026-10-13','2026-10-19',{'daily':empty})
  self.assertEqual(out['status'],'unavailable');self.assertNotIn('totals',out)
  wrong=self.result('google_ads',[]);wrong['resource_id']='another-brand'
  out=m.normalize('biceek','google_ads','2026-10-13','2026-10-19',{'daily':wrong});self.assertFalse(out['complete'])
 def test_verified_sparse_google_query_has_native_totals(self):
  r=self.result('google_ads',[{'segments':{'date':'2026-10-26'},'metrics':{'costMicros':'3916823','impressions':'24','clicks':'10','conversions':0,'conversionsValue':0}}])
  out=m.normalize('biceek','google_ads','2026-10-20','2026-10-26',{'daily':r})
  self.assertAlmostEqual(out['totals']['spend'],3.916823)
  self.assertEqual(len(out['rows']),7)
  self.assertEqual(out['rows'][0]['spend'],0)
  r['coverage']['complete']=False;r['status']='partial'
  partial=m.normalize('biceek','google_ads','2026-10-20','2026-10-26',{'daily':r})
  self.assertNotIn('totals',partial);self.assertEqual(len(partial['rows']),1)
 def test_shopify_explicit_zero_days_are_valid_and_income_is_separate(self):
  r=self.result('store',[]);r['data']={'rows':[{'day':'2026-10-13','orders':'0','total_sales':'0'},{'day':'2026-10-14','orders':'1','total_sales':'91.25'}]};r['count']=2
  out=m.normalize('biceek','store','2026-10-13','2026-10-19',{'daily':r});self.assertEqual(out['totals'],{'revenue':91.25,'conversions':1.0});self.assertEqual(out['rows'][0]['revenue'],0)
 def test_klaviyo_empty_and_partial_are_not_filled_with_zero(self):
  r=self.result('edm',[]);r['data']={k:{'data':[],'count':0,'coverage':{'complete':True}} for k in ['campaign','flow']}
  out=m.normalize('biceek','edm','2026-10-13','2026-10-19',{'daily':r});self.assertEqual(out['status'],'unavailable');self.assertNotIn('totals',out)
  r['data']['flow']['coverage']['complete']=False
  out=m.normalize('biceek','edm','2026-10-13','2026-10-19',{'daily':r});self.assertEqual(out['status'],'unavailable')
 def test_normalizing_cached_source_retains_capture_time(self):
  daily=self.result('ga4',[{'date':'20261019','sessions':'1','ecommercePurchases':'0','purchaseRevenue':'0'}]);daily['executed_at']='2026-10-19T15:00:00Z'
  native=self.result('ga4',[{'sessions':'1','ecommercePurchases':'0','purchaseRevenue':'0'}]);channel=self.result('ga4',[])
  out=m.normalize('biceek','ga4','2026-10-13','2026-10-19',{'daily':daily,'totals':native,'channel':channel})
  self.assertEqual(out['fetchedAt'],'2026-10-19T15:00:00Z')
  self.assertFalse(m.closure_metadata(out,'2026-10-19','America/Los_Angeles')['periodClosed'])
 def test_failed_forced_refresh_preserves_existing_ready_snapshot_input(self):
  with TemporaryDirectory() as directory,patch.object(m,'DASH',Path(directory)):
   target=Path(directory)/'.state/ai-dashboard/projects/ringconn/overview/ga4-2026-10-13-2026-10-19.json'
   good={'status':'ready','complete':True,'rows':[{'date':'2026-10-13','sessions':20}],'fetchedAt':'2026-10-20T12:00:00Z','timezone':'America/Los_Angeles'}
   m.private_write(target,good)
   with patch.object(m,'call',return_value={'status':'transport_failed','message':'Reader unavailable'}):m.collect('ringconn',[('2026-10-13','2026-10-19')],force=True)
   import json
   self.assertEqual(json.loads(target.read_text()),good)
   self.assertTrue((target.parent/'.refresh-errors'/target.name).exists())
 def test_rejects_large_or_reversed_fetch_ranges(self):
  for value in [[['2026-10-20','2026-10-13']],[['2026-01-01','2026-10-20']]]:
   with self.assertRaises(ValueError):m.validate_ranges(value)

if __name__=='__main__':unittest.main()
