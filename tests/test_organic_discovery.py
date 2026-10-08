import unittest
from unittest.mock import patch
from kol_organic_discovery import discovery_plan,fetch_discovery,accepted_candidates,insertion_positions


class DiscoveryTests(unittest.TestCase):
    def test_accepted_is_per_post(self):
        candidates=[{'content_id':'1'},{'content_id':'2'},{'content_id':'3'}]
        responses=[{'data':[{'id':'brand','invite_status':'Accepted'}]},
                   {'data':[{'id':'brand','invite_status':'Pending'}]},{'data':[]}]
        with patch('sync_creator_tracker.get_json',side_effect=responses):
            yes,no=accepted_candidates({'META_ACCESS_TOKEN':'test','KOL_ORGANIC_META_IG_USER_ID':'brand'},candidates)
        self.assertEqual([x['content_id'] for x in yes],['1'])
        self.assertEqual(len(no),2)

    def test_insert_dates_and_same_date(self):
        from datetime import date
        serial=lambda d:(date.fromisoformat(d)-date(1899,12,30)).days
        values=[[],[],['a'],['b'],['Summary']]
        cells=[{},{}]+[{'values':[{}, {'userEnteredValue':{'numberValue':serial(d)}}]} for d in ['2026-01-01','2026-03-01']]
        items=[{'date':'2026-03-01','content_id':'2'},{'date':'2026-02-01','content_id':'1'}]
        self.assertEqual([i for i,x in insertion_positions(values,cells,items,4)],[3,4])

    def test_known_alias_new_post_unknown_year_and_dedupe(self):
        values=[[],[],['Alias','','','https://www.instagram.com/reel/OLD/']]
        def item(cid,key,author,day='2026-10-07',kind='reel'):
            return dict(content_id=cid,permalink='https://www.instagram.com/%s/%s/'%(kind,key),
                creation_time=day,author={'ig_user_id':author,'display_name':'handle'+author})
        data=[item('1','OLD','a'),item('2','NEW','a'),item('2','NEW','a'),
              item('3','UNKNOWN','b'),item('4','PAST','a','2025-01-01'),item('5','POST','a',kind='p')]
        additions,pending=discovery_plan(values,data)
        self.assertEqual([x['content_id'] for x in additions],['2'])
        self.assertEqual([x['content_id'] for x in pending],['3'])
        self.assertEqual(len(discovery_plan(values,data,approved_ids=['b'])[0]),2)

    def test_cursor_only_pagination_and_repeated_cursor(self):
        responses=[{'data':[{'content_id':'1'}],'paging':{'cursors':{'after':'a'}}},
                   {'data':[{'content_id':'2'}]}]
        with patch('sync_creator_tracker.get_json',side_effect=responses) as get:
            self.assertEqual(len(fetch_discovery({'META_ACCESS_TOKEN':'test'})),2)
            self.assertEqual(get.call_count,2)
        with patch('sync_creator_tracker.get_json',return_value=responses[0]):
            with self.assertRaises(RuntimeError):
                fetch_discovery({'META_ACCESS_TOKEN':'test'})
