import unittest

from update_tiktok_tracker import cross_verified_plan
from test_tiktok_name_matching import ad


class CrossMatchingTests(unittest.TestCase):
    def fixture(self, spend_match=True):
        baseline = {'values': [[], [], ['Josh', '', 'code', 'link', 46015]]}
        comparison = {'checks': [{'metric': 'spend', 'display_match': spend_match},
                                 {'metric': 'views', 'display_match': True}],
                      'match_count': 2 if spend_match else 1}
        audit = {'rows': [{'row': 3, 'candidates': [{'ad_id': '1', 'comparison': comparison,
                  'launch_date_equal': True, 'reasons': ['exact_normalized_alias']}],
                  'group_comparisons': []}]}
        return baseline, audit

    def test_cross_verified_identity(self):
        baseline, audit = self.fixture()
        result = cross_verified_plan(baseline['values'], [ad('260101_ROPOT_Spark_Josh_HP')], audit, baseline)
        self.assertEqual(result[0]['decision'], 'proposed_update')

    def test_date_does_not_override_contradictory_data(self):
        baseline, audit = self.fixture(False)
        result = cross_verified_plan(baseline['values'], [ad('260101_ROPOT_Spark_Josh_HP')], audit, baseline)
        self.assertEqual(result[0]['decision'], 'skip_cross_verification_ambiguous')

    def test_identity_changed(self):
        baseline, audit = self.fixture()
        changed = [[], [], ['Josh', '', 'code', 'different-link', 46015]]
        self.assertEqual(cross_verified_plan(changed, [ad('260101_ROPOT_Spark_Josh_HP')], audit, baseline)[0]['decision'], 'skip_identity_changed')

    def test_binding_follows_row_moves(self):
        baseline, audit = self.fixture()
        moved = [[], [], [], baseline['values'][2]]
        result = cross_verified_plan(moved, [ad('260101_ROPOT_Spark_Josh_HP')], audit, baseline)
        self.assertEqual(result[0]['row'], 4)
        self.assertEqual(result[0]['decision'], 'proposed_update')
