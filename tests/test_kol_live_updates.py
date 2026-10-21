import json
import unittest
from kol_live_updates import launch_events,enqueue_jobs,dispatch_jobs,enrich_messages

class LiveTests(unittest.TestCase):
    def test_platforms_do_not_cross_creators(self):
        text="[KOL Content is Live]\n1. hannnahmitchelll's content is live\nTikTok: https://www.tiktok.com/@hannahhlyyn/video/7698432185451302175\n#"+'a'*40+"\n2. nutritionbyanastasiia's content is live\nIG: https://instagram.com/p/DeregCnx1U8/\nQ9jTNutrition\nYT: https://youtube.com/shorts/m3voSsAfZQw"
        events=launch_events([{'ts':'100','text':text}],0)
        yt=[x for x in events if x['platform']=='YouTube']
        self.assertEqual([x['creator'] for x in yt],['nutritionbyanastasiia'])
        self.assertEqual(next(x for x in events if x['platform']=='TikTok')['ad_code'],'a'*40)

    def test_ordinary_parent_url_reply(self):
        events=launch_events([{'ts':'1','text':"Eva's content is live"},
            {'ts':'100','thread_ts':'1','text':'https://instagram.com/reel/ABC/\nQ9jTCode'}],10)
        self.assertEqual(events[0]['creator'],'Eva')
        self.assertEqual(events[0]['ad_code'],'Q9jTCode')

    def test_edited_root_and_recent_reply_resolve_old_tracking_context(self):
        messages=[{'ts':'1','edited':{'ts':'100'},'text':"[KOL Content is Live]\nEva's content is live\nIG: https://wearerow.mxficus.com/l/abc"},
                  {'ts':'101','thread_ts':'1','text':'Q9jTCode'}]
        state={}
        resolved=enrich_messages(messages,state,101,50,lambda url:'https://www.instagram.com/reel/ABC/')
        events=launch_events(resolved,50)
        self.assertEqual(events[0]['post_link'],'https://www.instagram.com/reel/ABC/')
        self.assertEqual(events[0]['ad_code'],'Q9jTCode')

    def test_insert_then_fetch_same_poll_and_success_not_requeued(self):
        event={'platform':'YouTube','creator':'Eva','post_link':'https://youtu.be/m3voSsAfZQw','ts':'100','ad_code':''}
        state={};enqueue_jobs(state,[event],100)
        calls=[]
        dispatch_jobs(state,{'organic-youtube':lambda jobs:(calls.append('metrics') or {'yt:m3voSsAfZQw'})},
                      lambda kind,jobs:calls.append('insert'),lambda:None,100)
        self.assertEqual(calls,['insert','metrics'])
        self.assertFalse(state['jobs'])
        enqueue_jobs(state,[event],101)
        self.assertFalse(state['jobs'])

    def test_failure_survives_restart_and_other_tab_continues(self):
        state={};events=[{'platform':p,'creator':'Eva','post_link':link,'ts':'100','ad_code':''}
            for p,link in [('Meta','https://instagram.com/reel/ABC/'),('YouTube','https://youtu.be/m3voSsAfZQw')]]
        enqueue_jobs(state,events,100);saved=[];inserted=set();inserts=[]
        def insert(kind,jobs):
            if kind not in inserted:inserted.add(kind);inserts.append(kind)
        def fail(jobs):raise RuntimeError('private credentials must not be logged')
        dispatch_jobs(state,{'organic-meta':fail,'organic-youtube':lambda jobs:{'yt:m3voSsAfZQw'}},insert,
                      lambda:saved.append(json.loads(json.dumps(state))),100)
        self.assertEqual(len(state['jobs']),1)
        self.assertNotIn('private credentials',json.dumps(state))
        recovered=json.loads(json.dumps(state))
        dispatch_jobs(recovered,{'organic-meta':lambda jobs:{'ig:ABC'}},insert,lambda:None,401)
        self.assertFalse(recovered['jobs'])
        self.assertEqual(inserts.count('organic-meta'),1)
        self.assertTrue(saved)

    def test_tracking_resolution_cached_and_platform_verified_by_destination(self):
        messages=[{'ts':'100','text':"[KOL Content is Live]\nHannah's content is live\nTikTok: https://wearerow.mxficus.com/l/abc"}]
        state={};calls=[]
        def resolver(url):calls.append(url);return 'https://www.tiktok.com/@a/video/123'
        resolved=enrich_messages(messages,state,100,0,resolver)
        enrich_messages(messages,state,101,0,resolver)
        self.assertEqual(len(calls),1)
        self.assertEqual(launch_events(resolved,0)[0]['platform'],'TikTok')

    def test_mismatched_platform_redirect_never_creates_youtube_row(self):
        messages=[{'ts':'100','text':"[KOL Content is Live]\nHannah's content is live\nTikTok: https://wearerow.mxficus.com/l/abc"}]
        state={}
        resolved=enrich_messages(messages,state,100,0,lambda url:'https://www.youtube.com/watch?v=m3voSsAfZQw')
        self.assertFalse(launch_events(resolved,0))
        self.assertTrue(state['link_errors'])
        repeated=enrich_messages(messages,state,101,0,lambda url:self.fail('must back off'))
        self.assertFalse(launch_events(repeated,0))

    def test_one_rejected_row_does_not_block_same_tab(self):
        events=[{'platform':'YouTube','creator':name,'post_link':'https://youtu.be/'+vid,'ts':'100','ad_code':''}
                for name,vid in [('bad','ETVZaHw6gpc'),('good','m3voSsAfZQw')]]
        state={};enqueue_jobs(state,events,100)
        def insert(kind,jobs):
            if any(x['creator']=='bad' for x in jobs):raise RuntimeError('protected row')
        dispatch_jobs(state,{'organic-youtube':lambda jobs:{'yt:m3voSsAfZQw'}},insert,lambda:None,100)
        self.assertEqual([x['creator'] for x in state['jobs'].values()],['bad'])
