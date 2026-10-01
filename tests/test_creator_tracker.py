"""Regression checks for read-only KOL identity, parsing and aggregation."""
import unittest
from sync_creator_tracker import ad_name_matches, aggregate, creator_from_ad, parse_announcements, reconcile


class CreatorTrackerTests(unittest.TestCase):
    def test_creator_profile_link_uses_slack_display_name(self):
        text = "[KOL Content is Live]\n<https://www.tiktok.com/@zaaachydub|zaaachydub>’s content is live on IG\nhttps://www.instagram.com/p/AAA/ - Q9jTabc"
        result, unresolved = parse_announcements([{'ts': '1', 'text': text}])
        self.assertFalse(unresolved)
        self.assertEqual(result[0]['creator'], 'zaaachydub')

    def test_older_marked_announcement_headings(self):
        for heading, expected in [("Nathan Kehn's GIVEAWAY posts are here:", 'Nathan Kehn'),
                                  ("*doctor Myro's videos are out:*", 'doctor Myro'),
                                  ('barefoot.mimosas - ROPOT(UV) -- 2500', 'barefoot.mimosas'),
                                  ('Lorenzo Love', 'Lorenzo Love'),
                                  ('Melanie’s July 1st video is', 'Melanie')]:
            result, unresolved = parse_announcements([{'ts': '1', 'text': '[KOL Content is Live]\n' + heading + '\nhttps://www.instagram.com/reel/AAA/ - adcode-Q9jTabc'}])
            self.assertFalse(unresolved)
            self.assertEqual(result[0]['creator'], expected)

    def test_handles_keep_underscores(self):
        self.assertEqual(creator_from_ad('Sep25_Partnership_andreww_mckenna_ROPOT-Lite_ShopNow'), 'andreww_mckenna')

    def test_awareness_included_without_matching_another_content_suffix(self):
        self.assertTrue(ad_name_matches('2026Sep25_ThruPlays_bylyssaj__HP','bylyssaj'))
        self.assertFalse(ad_name_matches('Jun1_Partnership_Josh4_ROPOT_ShopNow','Josh'))

    def test_original_date_disambiguates_repeated_creator_as_candidate(self):
        snapshot = {'sheet':{'values':[['Location','Creator','','','','','Post Link','Ad Code'],[],['KOL','Selcen',46195,'','','','https://www.instagram.com/p/SOURCE/','Q9jT1']]},'meta':{'ads':[{'id':'1','name':'Mar24_Partnership_Selcen_ROPOT','creative':{'effective_instagram_media_id':'10'}},{'id':'2','name':'Jun22_Partnership_Selcen_ROPOT','creative':{'effective_instagram_media_id':'20'}}], 'media':{},'insights':[],'start_date':'2025-01-01','end_date':'2026-09-28','attribution':'test','account':{}}}
        report = reconcile(snapshot,'website','all')
        self.assertEqual(report['mapping'][0]['ad_ids'],['2'])
        self.assertEqual(report['mapping'][0]['method'],'ad_name_candidate')
        self.assertFalse(report['verified_equal'])

    def test_multi_creator_slack_message(self):
        messages = [{'ts':'1', 'text': "[KOL Content is Live]\n1. madison noelle hall's content live on\n- <https://www.instagram.com/reel/AAA/?x=y|IG> - adcode-Q9jTabc\n- <https://youtube.com/shorts/test|Youtube>\n2. Life in Jeneral‘s content is live on <https://www.instagram.com/p/BBB/|IG> - adcode-Q9jTdef\n3. bylyssaj's content is live on <https://www.instagram.com/reel/CCC/|IG> - Q9jTghi"}]
        result, unresolved = parse_announcements(messages)
        self.assertEqual([r['creator'] for r in result], ['madison noelle hall','Life in Jeneral','bylyssaj'])
        self.assertEqual([r['post_key'] for r in result], ['AAA','BBB','CCC'])
        self.assertFalse(unresolved)

    def test_missing_code_is_reviewed(self):
        result, unresolved = parse_announcements([{'ts':'1','text':"[KOL Content is Live]\nJoe's content is live on https://www.instagram.com/reel/AAA/"}])
        self.assertFalse(result)
        self.assertEqual(len(unresolved),1)

    def test_unmarked_conversation_never_proposes_new_rows(self):
        result, unresolved = parse_announcements([{'ts':'1','text':"Joe's content is live on https://www.instagram.com/reel/AAA/ - adcode-Q9jTabc"}])
        self.assertFalse(result)
        self.assertFalse(unresolved)

    def test_single_creator_heading_and_multiline_code(self):
        text = "*[KOL Content is Live]*\nandreww_mckenna's content is live on\n• Instagram: <https://www.instagram.com/reel/AAA/|IG>\n    ◦ Ad code: adcode-Q9jTabc\n• Youtube: https://youtube.com/shorts/test"
        result, unresolved = parse_announcements([{'ts':'1','text':text}])
        self.assertFalse(unresolved)
        self.assertEqual(result[0]['creator'],'andreww_mckenna')
        self.assertEqual(result[0]['ad_code'],'Q9jTabc')

    def test_does_not_pair_code_across_another_platform_url(self):
        text = "*[KOL Content is Live]*\nJoe's content is live on https://www.instagram.com/reel/AAA/\nhttps://youtube.com/shorts/test\nQ9jTbad"
        result, unresolved = parse_announcements([{'ts':'1','text':text}])
        self.assertFalse(result)
        self.assertTrue(unresolved)

    def test_ratios_use_aggregate_and_do_not_double_count_actions(self):
        row = {'spend':'100','impressions':'1000','clicks':'20','actions':[{'action_type':'omni_purchase','value':'3'},{'action_type':'offsite_conversion.fb_pixel_purchase','value':'2'}], 'action_values':[{'action_type':'offsite_conversion.fb_pixel_purchase','value':'400'}]}
        metrics = aggregate([row,row])
        self.assertEqual(metrics['purchase'],4)
        self.assertEqual(metrics['roas'],4)
        self.assertEqual(metrics['ctr'],0.02)
        self.assertIsNone(aggregate([])['roas'])

    def test_name_only_match_cannot_pass_and_code_conflict_reviewed(self):
        values = [['Location','Creator','','','','','Post Link','Ad Code'], [], ['KOL','Joe','','','','','https://www.instagram.com/p/AAA/','Q9jT1']]
        snapshot = {'sheet':{'values':values},'meta':{'ads':[{'id':'1','name':'Sep1_Partnership_Joe_ROPOT_ShopNow','creative':{'effective_instagram_media_id':'2'}}], 'media':{}, 'insights':[], 'start_date':'2025-01-01','end_date':'2026-09-29','attribution':'test','account':{}}, 'slack':{'messages':[{'ts':'1','text':"[KOL Content is Live]\nJoe's content is live on https://www.instagram.com/p/BBB/ - Q9jT1"}]}}
        report = reconcile(snapshot,'website','all')
        self.assertFalse(report['verified_equal'])
        self.assertEqual(report['mapping'][0]['method'],'ad_name_candidate')
        self.assertEqual(report['slack_candidates'][0]['decision'],'review_conflict')


if __name__ == '__main__':
    unittest.main()
