import unittest

from record_tiktok_ad_ids import reverse_matches, sufficiently_matches
from test_tiktok_name_matching import ad


class ReverseMatchingTests(unittest.TestCase):
    def test_single_exact_match_requires_identity_support(self):
        row = ['Josh', '', '', '', '', '', '', 10, 2, 3.25, 80, 90, .2]
        matches = reverse_matches(row, [ad('260101_ROPOT_Spark_Josh_HP')], set())
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0]['identity_supported'])

    def test_group_match(self):
        row = ['Josh', '', '', '', '', '', '', 20, 4, 3.25, '', 180, .2]
        matches = reverse_matches(row, [ad('260101_ROPOT_Spark_Josh_HP'),
                                      ad('260101_ROPOT_Spark_Josh_HP', '2')], {'1', '2'})
        self.assertEqual(matches[0]['ad_ids'], ['1', '2'])
        self.assertTrue(matches[0]['identity_supported'])
        self.assertTrue(matches[0]['comparison']['reach_not_additive'])

    def test_zero_spend_does_not_establish_binding(self):
        self.assertFalse(sufficiently_matches({'checks': [{'metric': 'spend', 'sheet': 0, 'display_match': True},
                                                         {'metric': 'views', 'sheet': 90, 'display_match': True}]}))

    def test_close_not_exact_rejected(self):
        row = ['Josh', '', '', '', '', '', '', 10.02, 2, 3.25, 80, 91, .2]
        self.assertFalse(reverse_matches(row, [ad('260101_ROPOT_Spark_Josh_HP')], {'1'}))
