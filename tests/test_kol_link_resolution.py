import unittest
from kol_link_resolution import resolve_post_link, canonical_post, LinkUnavailable

class Response:
    def __init__(self,status,location=''):
        self.status_code=status;self.headers={'Location':location}
    def close(self):pass

class Session:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs));return next(self.responses)

class LinkTests(unittest.TestCase):
    def test_hannah_tracking_redirect(self):
        session=Session([Response(302,'https://www.tiktok.com/@hannahhlyyn/video/7698432185451302175?foo=bar')])
        self.assertEqual(resolve_post_link('https://wearerow.mxficus.com/l/tracking',session),
                         'https://www.tiktok.com/@hannahhlyyn/video/7698432185451302175')
        self.assertFalse(session.calls[0][1]['allow_redirects'])

    def test_unknown_private_destination_not_fetched(self):
        session=Session([Response(302,'http://127.0.0.1/secrets')])
        with self.assertRaises(LinkUnavailable):resolve_post_link('https://wearerow.mxficus.com/l/x',session)
        self.assertEqual(len(session.calls),1)

    def test_blocked_stops_and_marks_backoff(self):
        for status in (403,429):
            session=Session([Response(status)])
            with self.assertRaises(LinkUnavailable) as error:
                resolve_post_link('https://www.tiktok.com/t/SHORT/',session)
            self.assertTrue(error.exception.blocked)
            self.assertEqual(len(session.calls),1)

    def test_official_posts_no_requests_and_google_wrapper(self):
        session=Session([])
        self.assertEqual(resolve_post_link('https://instagram.com/p/ABC/?tracking=yes',session),
                         'https://www.instagram.com/reel/ABC/')
        self.assertEqual(resolve_post_link('https://www.google.com/url?q=https%3A%2F%2Fyoutu.be%2Fm3voSsAfZQw',session),
                         'https://www.youtube.com/watch?v=m3voSsAfZQw')
        self.assertFalse(session.calls)

    def test_loop_and_credentials_rejected(self):
        session=Session([Response(302,'https://wearerow.mxficus.com/l/x')])
        with self.assertRaises(LinkUnavailable):resolve_post_link('https://wearerow.mxficus.com/l/x',session)
        self.assertFalse(canonical_post('https://user:password@www.tiktok.com/@a/video/123'))
