import unittest
from kol_targeting import scope_values,row_keys

class TargetTests(unittest.TestCase):
    def test_code_never_selects_a_different_explicit_post(self):
        rows=[[],[],['Eva','','','','','','https://instagram.com/reel/OLD/','Q9jTShared']]
        self.assertEqual(scope_values(rows,'ads-meta',{'ig:NEW','igcode:Q9jTShared'})[2],[])

    def test_daily_aliases_load_from_persisted_live_registry(self):
        import tempfile,json
        from pathlib import Path
        from kol_targeting import load_link_aliases
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'live-metric-jobs.json').write_text(json.dumps({'link_aliases':{'raw':'canonical'}}))
            self.assertEqual(load_link_aliases({'KOL_TRACKER_STATE_DIR':directory}),{'raw':'canonical'})

    def test_two_posts_same_creator_only_exact_target(self):
        rows=[[],[],['Eva','','','https://instagram.com/reel/ONE/'],
              ['Eva','','','https://instagram.com/reel/TWO/']]
        scoped=scope_values(rows,'organic-meta',{'ig:TWO'})
        self.assertEqual(scoped[2],[])
        self.assertEqual(scoped[3],rows[3])
        self.assertEqual(rows[2][0],'Eva')

    def test_default_does_not_change_daily_rows(self):
        rows=[['a']]
        self.assertIs(scope_values(rows,'organic-youtube',None),rows)

    def test_manual_tracking_alias_selects_same_video(self):
        row=['Hannah','','https://wearerow.mxficus.com/l/hannah']
        aliases={row[2]:'https://www.tiktok.com/@hannahhlyyn/video/7698432185451302175'}
        self.assertIn('tt:7698432185451302175',row_keys('organic-tiktok',row,aliases))

    def test_ads_code_and_post_keys(self):
        row=['Hannah','','#spark-code','https://www.tiktok.com/@a/video/123']
        self.assertEqual(row_keys('ads-tiktok',row),{'tt:123','ttcode:spark-code'})
