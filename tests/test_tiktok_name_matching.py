import unittest

from update_tiktok_tracker import creator_segment, plan_rows


def ad(name, identity='1'):
    return {'advertiser_id': 'account', 'dimensions': {'ad_id': identity}, 'metrics': {
        'ad_name': name, 'spend': '10', 'complete_payment': '2',
        'complete_payment_roas': '3.25', 'reach': '80', 'impressions': '100',
        'video_play_actions': '90', 'video_watched_2s': '20'}}


class TikTokNameMatchingTests(unittest.TestCase):
    def test_content_suffix_is_not_substring(self):
        rows = [['header'], ['total'], ['Josh'], ['Josh3']]
        result = plan_rows(rows, [ad('260101_ROPOT_Spark_Josh3_HP')])
        self.assertEqual(result[0]['decision'], 'skip_no_name_match')
        self.assertEqual(result[1]['decision'], 'proposed_update')

    def test_ugc_prefix_and_suffix(self):
        self.assertEqual(creator_segment('260101_ROPOT_Spark_UGC_Amanda2_HP'), 'ugcamanda2')

    def test_repeated_creator_skipped(self):
        result = plan_rows([[], [], ['Josh'], ['Josh']], [ad('260101_ROPOT_Spark_Josh_HP')])
        self.assertTrue(all(r['decision'] == 'skip_repeated_creator' for r in result))

    def test_multiple_ads_not_summed(self):
        result = plan_rows([[], [], ['Josh']], [ad('260101_ROPOT_Spark_Josh_HP'),
                                               ad('260102_ROPOT_Spark_Josh_HP', '2')])
        self.assertEqual(result[0]['decision'], 'skip_multiple_ads')

    def test_metrics_and_not_exact_identity(self):
        result = plan_rows([[], [], ['Josh']], [ad('260101_ROPOT_Spark_Josh_HP')])[0]
        self.assertEqual(result['new_values'], [10, 2, 3.25, 80, 90, .2])
        self.assertFalse(result['identity_verified'])

    def test_missing_metrics_not_zero(self):
        record = ad('260101_ROPOT_Spark_Josh_HP')
        del record['metrics']['reach']
        self.assertEqual(plan_rows([[], [], ['Josh']], [record])[0]['decision'], 'skip_missing_metrics')

    def test_zero_impressions_undefined_rate(self):
        record = ad('260101_ROPOT_Spark_Josh_HP')
        record['metrics']['impressions'] = '0'
        self.assertIsNone(plan_rows([[], [], ['Josh']], [record])[0]['new_values'][-1])
