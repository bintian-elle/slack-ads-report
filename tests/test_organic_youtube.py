import unittest
from unittest.mock import patch, Mock
from pathlib import Path
from kol_organic_youtube import video_id, parse_announcements, schema


class YouTubeTests(unittest.TestCase):
    def test_links(self):
        for link in ['https://youtu.be/ETVZaHw6gpc?si=x', 'https://www.youtube.com/watch?v=ETVZaHw6gpc', 'https://youtube.com/shorts/ETVZaHw6gpc', 'https://www.google.com/url?q=https%3A%2F%2Fyoutu.be%2FETVZaHw6gpc&source=gmail']:
            self.assertEqual(video_id(link),'ETVZaHw6gpc')
        self.assertEqual(video_id('https://evil.com/shorts/ETVZaHw6gpc'),'')

    def test_ordinary_and_dedup(self):
        text="<@U123> Victoria’s contents have been published last Friday as scheduled. Here are the codes if needed.\nLong form — <https://youtu.be/ETVZaHw6gpc>\nShort — <https://youtube.com/shorts/7utXab2yHyQ>"
        result,issues=parse_announcements([{'ts':'1','text':text},{'ts':'2','text':text}])
        self.assertEqual(len(result),2)
        self.assertEqual({x['creator'] for x in result},{'Victoria'})
        self.assertFalse(issues)

    def test_not_live(self):
        self.assertFalse(parse_announcements([{'ts':'1','text':'Victoria please review https://youtu.be/ETVZaHw6gpc'}])[0])

    def test_multi_creator(self):
        result,_=parse_announcements([{'ts':'1','text':"[KOL Content is Live]\nAubree Jones's -- product\nhttps://instagram.com/reel/x\nEmily Spicer's <https://youtube.com/shorts/zwnTOpP20Bs|ytb shorts> is live"}])
        self.assertEqual(result[0]['creator'],'Emily Spicer')

    def test_parent_reply(self):
        result,_=parse_announcements([{'ts':'1','text':'[KOL Content is Live]\nLorenzo Love'}, {'ts':'2','thread_ts':'1','text':'https://youtu.be/ETVZaHw6gpc'}])
        self.assertEqual(result[0]['creator'],'Lorenzo Love')

    def test_schema_ignores_shares_saves(self):
        h=['Creator','Organic Launch Date','Post Link','KOL Fee','Views\n[10/08 update]','Interaction','Likes','Comments','unused','unused','CPM','CPE','Note: Lifetime','Reason for data update failure']
        self.assertEqual(schema([h]),13)

    def test_product_and_code_not_names(self):
        r,_=parse_announcements([{'ts':'1','text':"[KOL Content is Live]\ndoctor Myro‘s content is live\n(:moneybag:15,000)\nROPOT (UV) in white\nAd code:\nYT: https://youtu.be/ETVZaHw6gpc"}])
        self.assertEqual(r[0]['creator'],'doctor Myro')

    def test_ambiguous_parent_skipped(self):
        r,issues= parse_announcements([{'ts':'1','text':'[KOL Content is Live]\nAlice\nBob'}, {'ts':'2','thread_ts':'1','text':'https://youtu.be/ETVZaHw6gpc'}])
        self.assertFalse(r)
        self.assertTrue(issues)

    def test_brand_review_without_launch_not_added(self):
        r,_=parse_announcements([{'ts':'1','text':'Please review this draft from Victoria https://youtu.be/ETVZaHw6gpc'}])
        self.assertFalse(r)

    def test_daily_no_shares_saves_and_clear_old_reason(self):
        from kol_organic_youtube import run
        headers=['Creator','Organic Launch Date','Post Link','KOL Fee','Views','Interaction','Likes','Comments','Saves','Shares','CPM','CPE','Note: Lifetime','Reason for data update failure']
        row=['Victoria','Aug-21','https://youtu.be/ETVZaHw6gpc',100,10,3,2,1,'','',10,30,'','Shares and saves are not available via public YouTube Data API']
        native={'sheets':[{'data':[{'rowData':[{'values':[{} for _ in headers]},{'values':[{} for _ in headers]}]}]}]}
        native['sheets'][0]['data'][0]['rowData'][1]['values'][5]={'userEnteredValue':{'formulaValue':'=G2+H2+I2+J2'}}
        with patch('kol_tracker.sheet_session',return_value=(Mock(),'endpoint')),patch('kol_tracker.read_tab',return_value=({'sheetId':1},native,[headers,row])),patch('kol_organic_youtube.fetch_videos',return_value={'ETVZaHw6gpc':{'statistics':{'viewCount':'20','likeCount':'4','commentCount':'2'}}}),patch('kol_tracker.save_json') as save:
            run({'KOL_ORGANIC_SHEETS_LINK':'link'},Path('/tmp'),False)
        plan=save.call_args.args[1]
        writes={(x['row'],x['col']):x['value'] for x in plan['writes']}
        self.assertEqual(writes[(2,13)],'')
        self.assertEqual(writes[(2,5)],6)
        self.assertNotIn((2,8),writes)
        self.assertNotIn((2,9),writes)

    def test_error_sanitized(self):
        from kol_organic_youtube import fetch_videos
        with patch('kol_organic_youtube.requests.get',return_value=Mock(ok=False,status_code=403)):
            with self.assertRaisesRegex(RuntimeError,'^YouTube API HTTP 403$'):
                fetch_videos({'YOUTUBE_API_KEY':'private'},['ETVZaHw6gpc'])
