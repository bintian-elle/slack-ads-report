import os, unittest
from datetime import datetime,timezone
from unittest.mock import patch
from builder_cache_policy import period_closed,captured_closed_period,reusable_cache,force_refresh_requested,closure_metadata,microsoft_reporting_credentials
class CachePolicyTests(unittest.TestCase):
 def test_website_login_credentials_do_not_override_microsoft_report_credentials(self):
  report={'GOOGLE_CLIENT_ID':'report-client','GOOGLE_CLIENT_SECRET':'report-secret','GOOGLE_REFRESH_TOKEN':'report-refresh','BING_ADS_DEVELOPER_TOKEN':'report-developer'}
  website={'GOOGLE_CLIENT_ID':'website-login-client','GOOGLE_CLIENT_SECRET':'website-login-secret'}
  self.assertEqual(microsoft_reporting_credentials(report,website),report)
  self.assertEqual(microsoft_reporting_credentials({},report),report)

 def test_monday_seven_rows_are_not_closed_even_after_tuesday_arrives(self):
  old={'status':'ready','fetchedAt':'2026-10-26T15:00:00Z'}
  now=datetime(2026,10,27,9,tzinfo=timezone.utc)
  self.assertFalse(captured_closed_period(old,'2026-10-26','America/Los_Angeles'))
  self.assertFalse(reusable_cache(old,'2026-10-26','America/Los_Angeles',True,now=now))
 def test_closed_cache_is_reused_unless_force_was_requested(self):
  old={'status':'ready','fetchedAt':'2026-10-27T08:00:00Z'};now=datetime(2026,10,27,9,tzinfo=timezone.utc)
  self.assertTrue(reusable_cache(old,'2026-10-26','America/Los_Angeles',True,now=now))
  self.assertFalse(reusable_cache(old,'2026-10-26','America/Los_Angeles',True,force=True,now=now))
  with patch.dict(os.environ,{'AI_DASHBOARD_FORCE_REFRESH':'1'}):
   self.assertTrue(force_refresh_requested());self.assertFalse(reusable_cache(old,'2026-10-26','America/Los_Angeles',True,now=now))
 def test_one_am_eastern_is_still_monday_for_pacific_sources(self):
  run=datetime(2026,10,27,5,tzinfo=timezone.utc)
  self.assertTrue(period_closed('2026-10-26','America/New_York',run))
  self.assertFalse(period_closed('2026-10-26','America/Los_Angeles',run))
  self.assertTrue(period_closed('2026-10-26','America/Los_Angeles',datetime(2026,10,27,7,tzinfo=timezone.utc)))
 def test_unknown_timezone_or_invalid_capture_is_not_frozen(self):
  self.assertFalse(period_closed('2026-10-26','Unknown'))
  self.assertEqual(closure_metadata({'fetchedAt':'2026-10-27T12:00:00Z'},'2026-10-26','TikTok account-local reporting dates'),{})
  self.assertFalse(captured_closed_period({'fetchedAt':'invalid'},'2026-10-26','America/Los_Angeles'))
if __name__=='__main__':unittest.main()
