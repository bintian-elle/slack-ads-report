import unittest

from update_meta_tracker import unavailable_metrics
from tracker_failure_notes import HEADER, plan_notes


class UnavailableMetricsTests(unittest.TestCase):
    def test_no_purchases_aov_is_expected_blank(self):
        self.assertEqual(unavailable_metrics({'purchase':0,'aov':None}),[])

    def test_no_clicks_cpc_is_expected_blank(self):
        self.assertEqual(unavailable_metrics({'clicks':0,'cpc':None}),[])

    def test_other_missing_metrics_still_reported(self):
        self.assertEqual(unavailable_metrics({'purchase':0,'aov':None,'clicks':0,'cpc':None,'roas':None}),['roas'])

    def test_unexpected_missing_ratio_still_reported(self):
        self.assertEqual(unavailable_metrics({'purchase':2,'aov':None,'clicks':3,'cpc':None}),['aov','cpc'])

    def test_success_clears_old_automatic_reason_only(self):
        values=[['','Creator',HEADER],[],['','Alice','Update failed: Metric unavailable: aov, cpc'],['','Bob','Manual note']]
        self.assertEqual(plan_notes(values,{3,4},[],1,3),[{'row':3,'changes':{2:''}}])
