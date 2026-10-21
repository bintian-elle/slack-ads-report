import unittest
from kol_organic_meta_slack import parse_launches, pending_rows


class OrganicSlackTests(unittest.TestCase):
    def test_marked_multiple_creators_and_platform_fenced_codes(self):
        messages=[{'ts':'1792538197','text':"[KOL Content is Live]\n1. Hannah's content is live\nhttps://instagram.com/reel/HAN/\nAd Code: Q9jTHannah\n2. nutritionbyanastasiia's content is live on:\nIG: https://instagram.com/p/DeregCnx1U8/\nAd Code: Q9jTNutrition\nYT: https://youtube.com/shorts/m3voSsAfZQw\nTikTok: https://tiktok.com/t/ABC"}]
        rows,issues=parse_launches(messages)
        self.assertEqual([(x['creator'],x['post_key'],x['ad_code']) for x in rows],
                         [('Hannah','HAN','Q9jTHannah'),('nutritionbyanastasiia','DeregCnx1U8','Q9jTNutrition')])
        self.assertFalse(issues)

    def test_ordinary_launch_and_missing_code_still_added(self):
        rows,_=parse_launches([{'ts':'10','text':"Eva’s contents have been published.\nhttps://instagram.com/reel/ABC/"}])
        self.assertEqual(rows[0]['creator'],'Eva')
        self.assertEqual(rows[0]['ad_code'],'')

    def test_ordinary_parent_reply_keeps_launch_context(self):
        rows,_=parse_launches([{'ts':'1','text':"Eva's content is live"},
            {'ts':'20','thread_ts':'1','text':'https://instagram.com/reel/ABC/\nQ9jTCode'}],oldest=10)
        self.assertEqual(rows[0]['creator'],'Eva')
        self.assertEqual(rows[0]['ad_code'],'Q9jTCode')

    def test_drafts_not_launch(self):
        self.assertFalse(parse_launches([{'ts':'1','text':'Eva draft https://instagram.com/reel/ABC/'}])[0])

    def test_manual_nutrition_p_reel_dedup_even_blank_creator(self):
        values=[[],[],['manual name','','','https://instagram.com/p/DeregCnx1U8/']]
        item={'post_key':'DeregCnx1U8','post_link':'https://instagram.com/reel/DeregCnx1U8/','creator':'nutrition','ad_code':'code','ts':'10'}
        self.assertFalse(pending_rows(values,[item])[0])
        values[2][0]=''
        self.assertFalse(pending_rows(values,[item])[0])

    def test_conflicting_codes_rejected_not_duplicated(self):
        a={'creator':'Eva','post_key':'ABC','post_link':'https://instagram.com/reel/ABC/','ad_code':'one','ts':'10'}
        rows,issues=pending_rows([[],[]],[a,dict(a,ad_code='two')])
        self.assertFalse(rows)
        self.assertTrue(issues)

    def test_old_parent_context_with_new_reply(self):
        rows,_=parse_launches([{'ts':'1','text':'[KOL Content is Live]\nEva'},
            {'ts':'20','thread_ts':'1','text':'https://instagram.com/reel/ABC/\nQ9jTCode'}],oldest=10)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['creator'],'Eva')
        self.assertEqual(rows[0]['ad_code'],'Q9jTCode')

    def test_late_code_only_fills_blank_exact_post(self):
        from kol_organic_meta_slack import code_backfills
        values=[[],[],['Eva','','','https://instagram.com/p/ABC/',''],
                ['Manual','','','https://instagram.com/reel/DEF/','manual-code']]
        candidates=[{'post_key':'ABC','ad_code':'Q9jTLate'}, {'post_key':'DEF','ad_code':'Q9jTOther'}]
        self.assertEqual(code_backfills(values,candidates),[{'row':3,'code':'Q9jTLate'}])

    def test_old_post_with_recent_code_reply(self):
        rows,_=parse_launches([{'ts':'1','text':"[KOL Content is Live]\nEva\nhttps://instagram.com/reel/ABC/"},
            {'ts':'20','thread_ts':'1','text':'Q9jTLate'}],oldest=10)
        self.assertEqual(rows[0]['ad_code'],'Q9jTLate')
        self.assertFalse(parse_launches([{'ts':'1','text':"[KOL Content is Live]\nEva\nhttps://instagram.com/reel/ABC/"}],oldest=10)[0])

    def test_parent_raw_code_is_not_creator_heading(self):
        rows,_=parse_launches([{'ts':'1','text':'[KOL Content is Live]\nEva\nhttps://instagram.com/reel/ABC/\nQ9jTFirst'},
            {'ts':'20','thread_ts':'1','text':'https://instagram.com/reel/DEF/\nAd Code: Q9jTSecond'}],oldest=10)
        self.assertEqual([(x['creator'],x['post_key'],x['ad_code']) for x in rows],[('Eva','DEF','Q9jTSecond')])
