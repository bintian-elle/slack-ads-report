import unittest
from update_meta_tracker import cell, exact_mapping, write_range, new_row_status


class WriterTests(unittest.TestCase):
    def test_new_row_initial_status(self):
        self.assertEqual(new_row_status([{'effective_status':'ACTIVE'}, {'effective_status':'PAUSED'}]), 'testing')
        self.assertEqual(new_row_status([{'effective_status':'ADSET_PAUSED'}]), 'paused')
        self.assertIsNone(new_row_status([]))
        self.assertIsNone(new_row_status([{'effective_status':'DISAPPROVED'}]))

    def test_exact_code_only_and_ad_dedup(self):
        row = ['', 'Joe', '', '', '', '', '', 'adcode-ABC']
        ad = {'id': '1', 'name': 'another alias', 'creative': {'branded_content': {'instagram_boost_post_access_token': 'ABC'}}}
        matches, skips, conflicts = exact_mapping([(3,row)], [ad,ad])
        self.assertEqual(matches, {3:['1']})
        self.assertFalse(skips)
        self.assertFalse(conflicts)

    def test_shared_ad_skips_both_rows(self):
        row = ['', 'Joe', '', '', '', '', '', 'ABC']
        ad = {'id':'1','creative':{'branded_content':{'instagram_boost_post_access_token':'ABC'}}}
        matches, skips, conflicts = exact_mapping([(3,row),(4,row)], [ad])
        self.assertFalse(matches)
        self.assertEqual(conflicts, {'1':[3,4]})

    def test_missing_code_does_not_match_name(self):
        row = ['', 'Joe', '', '', '', '', '', '']
        matches, skips, _ = exact_mapping([(3,row)], [{'id':'1','name':'Joe','creative':{}}])
        self.assertFalse(matches)
        self.assertEqual(len(skips), 1)

    def test_zero_is_numeric_none_is_blank(self):
        self.assertEqual(cell(0), {'userEnteredValue':{'numberValue':0}})
        self.assertEqual(cell(None), {})
        request=write_range(123,3,9,[0,None])['updateCells']
        self.assertEqual(request['fields'],'userEnteredValue')
        self.assertEqual(request['range']['startColumnIndex'],9)
        self.assertEqual(request['range']['endColumnIndex'],11)


if __name__ == '__main__':
    unittest.main()
