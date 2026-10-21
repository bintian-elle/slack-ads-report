"""Bounded, allowlisted HTTP redirect resolution for Slack post links."""
import html
import re
from urllib.parse import urlsplit,parse_qs,urljoin
import requests

HOSTS={'wearerow.mxficus.com','www.tiktok.com','tiktok.com','vm.tiktok.com','vt.tiktok.com',
       'www.instagram.com','instagram.com','youtube.com','www.youtube.com','m.youtube.com','youtu.be'}

class LinkUnavailable(RuntimeError):
    def __init__(self,message,blocked=False):super().__init__(message);self.blocked=blocked

def safe_url(url):
    try:
        u=urlsplit(html.unescape(str(url)))
        if u.scheme!='https' or u.username or u.password or u.port not in (None,443):return None
        return u
    except ValueError:return None

def canonical_post(url):
    u=safe_url(url)
    if not u:return ''
    if u.hostname in ('instagram.com','www.instagram.com'):
        match=re.fullmatch(r'/(?:p|reel|reels)/([A-Za-z0-9_-]+)/?',u.path)
        if match:return 'https://www.instagram.com/reel/'+match.group(1)+'/'
    if u.hostname in ('tiktok.com','www.tiktok.com'):
        match=re.fullmatch(r'/(@[^/]+)/video/(\d+)/?',u.path)
        if match:return 'https://www.tiktok.com/'+match.group(1)+'/video/'+match.group(2)
    from kol_organic_youtube import video_id
    vid=video_id(url)
    return 'https://www.youtube.com/watch?v='+vid if vid else ''

def resolve_post_link(url,session=None):
    url=html.unescape(str(url));seen=set();session=session or requests.Session()
    u=safe_url(url)
    if u and u.hostname in ('google.com','www.google.com') and u.path=='/url':
        url=parse_qs(u.query).get('q',[''])[0]
    for _ in range(5):
        canonical=canonical_post(url)
        if canonical:return canonical
        u=safe_url(url)
        if not u or u.hostname not in HOSTS:raise LinkUnavailable('Unsupported or unsafe post-link redirect')
        if url in seen:raise LinkUnavailable('Repeated post-link redirect')
        seen.add(url)
        try:r=session.get(url,allow_redirects=False,timeout=20,stream=True)
        except requests.RequestException:raise LinkUnavailable('Post-link resolution request failed') from None
        try:
            if r.status_code in (403,429):raise LinkUnavailable('Post-link resolution HTTP '+str(r.status_code),blocked=True)
            if r.status_code not in (301,302,303,307,308) or not r.headers.get('Location'):
                raise LinkUnavailable('Post-link did not resolve to an official video')
            url=urljoin(url,r.headers['Location'])
        finally:r.close()
    canonical=canonical_post(url)
    if canonical:return canonical
    raise LinkUnavailable('Post-link redirect limit reached')
