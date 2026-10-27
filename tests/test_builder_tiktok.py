import unittest
from builder_tiktok import normalize_reports
class BuilderTikTokTests(unittest.TestCase):
 def test_reuses_daily_spend_weighted_purchase_revenue_and_real_zeroes(self):
  values=[{'dimensions':{'stat_time_day':'2026-10-20 00:00:00'},'metrics':{'spend':'50','complete_payment_roas':'4','impressions':'100','clicks':'4','complete_payment':'2'}},{'dimensions':{'stat_time_day':'2026-10-21'},'metrics':{'spend':'0','complete_payment_roas':'0','impressions':'0','clicks':'0','complete_payment':'0'}}]
  result=normalize_reports([{'code':0,'data':{'list':values}}]);self.assertEqual(result[0]['revenue'],200);self.assertEqual(result[0]['conversions'],2);self.assertEqual(result[1]['spend'],0)
 def test_missing_values_are_not_invented_zeroes(self):
  with self.assertRaisesRegex(ValueError,'missing'):normalize_reports([{'code':0,'data':{'list':[{'metrics':{}}]}}])
 def test_empty_historical_response_remains_empty(self):
  self.assertEqual(normalize_reports([{'code':0,'data':{'list':[]}}]),[])
 def test_api_errors_are_not_zero_activity(self):
  with self.assertRaises(ValueError):normalize_reports([{'code':40001,'data':{}}])
