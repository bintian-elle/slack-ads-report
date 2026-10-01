import unittest
from normalize_tracker_status import tracker_status


class StatusTests(unittest.TestCase):
    def test_any_effectively_active_is_testing(self):
        self.assertEqual(tracker_status([{'effective_status':'ACTIVE'},{'effective_status':'PAUSED'}]),'testing')

    def test_parent_pauses_count_as_paused(self):
        self.assertEqual(tracker_status([{'effective_status':'ADSET_PAUSED'},{'effective_status':'CAMPAIGN_PAUSED'}]),'paused')

    def test_no_data_or_other_state_is_not_assumed_paused(self):
        self.assertIsNone(tracker_status([]))
        self.assertIsNone(tracker_status([{'effective_status':'DISAPPROVED'}]))


if __name__=='__main__':unittest.main()
