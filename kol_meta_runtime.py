"""Process-local KOL transport guard; never imported by the Robot entry point.

Successful Graph GET pages are atomic checkpoints. Only the KOL CLI installs
this context, so Google/Slack/TikTok traffic and other processes are untouched.
"""
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

import requests


class MetaDeferred(RuntimeError):
    successful_keys = frozenset()  # Prevent live dispatch's per-row retry storm.


def reporting_window(now=None):
    current=datetime.fromtimestamp(time.time() if now is None else now,ZoneInfo('America/New_York'))
    if current.hour<2:current-=timedelta(days=1)
    return current.date().isoformat()


def next_boundary(now):
    current=datetime.fromtimestamp(now,ZoneInfo('America/New_York'))
    day=current if current.hour<2 else current+timedelta(days=1)
    return day.replace(hour=2,minute=0,second=0,microsecond=0).timestamp()


def clean_url(url):
    parts=urlsplit(url)
    return urlunsplit(parts._replace(query=urlencode(sorted((k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True)
                                                           if k not in ('access_token','appsecret_proof')))))


def clean_data(value):
    if isinstance(value,dict):return {k:clean_data(v) for k,v in value.items() if k not in ('access_token','appsecret_proof')}
    if isinstance(value,list):return [clean_data(v) for v in value]
    if isinstance(value,str) and value.startswith('https://graph.facebook.com/'):return clean_url(value)
    return value


class MetaRuntime:
    def __init__(self,root,token,budget=40,now=time.time,persist=True):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'meta-request-progress.json'
        self.now=now;self.budget=max(1,min(100,int(budget)));self.persist=persist;self.token_hash=hashlib.sha256(token.encode()).hexdigest()
        self.lock=threading.RLock();self.current='poll';self.resuming=False;self.sent=0;self.hits=0;self.deferred=set()
        self.current_lane=None;self.lane_sent={};self.lane_limits={}
        self.rotation_started=False
        try:self.state=json.loads(self.path.read_text())
        except (OSError,ValueError):self.state={}
        if self.state.get('token')!=self.token_hash:self.state={}
        self.state.update(token=self.token_hash)
        self.state.setdefault('cache',{});self.state.setdefault('pending',{})
        for item in self.state['cache'].values():
            # Migrate the initial short-lived inventory checkpoints while they
            # are still fresh; preserve them across an already queued cooldown.
            if 'data' in item and item.get('status',200)==200 and 'fresh_until' not in item and self.now()<item.get('until',0)<=self.now()+900:
                item['fresh_until']=item['until'];item['until']=next_boundary(self.now())
        self.state['cache']={k:v for k,v in self.state['cache'].items() if v.get('until',0)>self.now()}

    def save(self):
        if not self.persist:return
        from kol_tracker import save_json
        save_json(self.path,self.state)

    @contextmanager
    def stage(self,name,resume=False):
        previous=(self.current,self.resuming);self.current=name;self.resuming=resume
        try:yield
        finally:self.current,self.resuming=previous

    @contextmanager
    def lane(self,name,budget=5):
        previous=self.current_lane;self.current_lane=name
        self.lane_limits[name]=max(1,int(budget));self.lane_sent.setdefault(name,0)
        try:yield
        finally:self.current_lane=previous

    def metric_order(self):
        first=self.state.get('organic_meta_next_lane','engagement')
        if first not in ('views','engagement'):first='engagement'
        other='views' if first=='engagement' else 'engagement'
        return [first,other]

    def pending(self,due=False):
        return [k for k,v in self.state['pending'].items() if not due or v.get('retry_at',0)<=self.now()]

    def defer(self,reason,seconds=3600):
        self.deferred.add(self.current)
        self.state['pending'][self.current]={'retry_at':self.now()+seconds,'reason':reason}
        self.save()

    def complete(self,name):
        if name not in self.deferred:self.state['pending'].pop(name,None)
        self.save()

    def __enter__(self):
        self.original=requests.sessions.Session.request
        runtime=self
        def guarded(session,method,url,**kwargs):
            if urlsplit(url).hostname!='graph.facebook.com':return runtime.original(session,method,url,**kwargs)
            return runtime.request(session,method,url,**kwargs)
        requests.sessions.Session.request=guarded
        return self

    def __exit__(self,*exc):
        requests.sessions.Session.request=self.original
        self.save()
        print('KOL Meta requests: %d network; %d checkpoint hits; pending stages: %s'%
              (self.sent,self.hits,','.join(sorted(self.state['pending'])) or 'none'),flush=True)

    def request(self,session,method,url,**kwargs):
        # Serialize Graph reads so usage feedback applies before the next worker.
        with self.lock:
            if method.upper()!='GET':raise RuntimeError('KOL Meta guard permits GET only')
            params=kwargs.get('params') or {}
            if not isinstance(params,dict):raise RuntimeError('Unexpected Meta query parameters')
            query={k:v for k,v in parse_qsl(urlsplit(url).query) if k not in ('access_token','appsecret_proof')}
            query.update({k:str(v) for k,v in params.items() if k not in ('access_token','appsecret_proof')})
            path=urlsplit(url).path
            canonical=json.dumps([path,sorted(query.items())],sort_keys=True)
            key=hashlib.sha256(canonical.encode()).hexdigest()
            cached=self.state['cache'].get(key)
            if cached and cached['until']>self.now() and (self.resuming or cached.get('fresh_until',cached['until'])>self.now()):
                self.hits+=1
                if cached.get('failure'):
                    self.defer(cached['failure'],max(1,cached['until']-self.now()))
                    raise MetaDeferred(cached['failure'])
                result=requests.Response();result.status_code=cached.get('status',200)
                result._content=json.dumps(cached['data']).encode();result.headers['Content-Type']='application/json'
                return result
            if self.state.get('blocked_until',0)>self.now():
                reason=self.state.get('blocked_reason','Meta cooldown; remaining requests deferred')
                self.defer(reason,self.state['blocked_until']-self.now());raise MetaDeferred(reason)
            if self.sent>=self.budget:
                reason='Meta batch budget reached; remaining requests queued'
                self.defer(reason,300);raise MetaDeferred(reason)
            lane=self.current_lane
            if lane and self.lane_sent[lane]>=self.lane_limits[lane]:
                reason='Meta %s batch pending; remaining requests queued'%lane
                self.defer(reason,300);raise MetaDeferred(reason)
            self.sent+=1
            if lane:self.lane_sent[lane]+=1
            if lane in ('views','engagement') and not self.rotation_started:
                self.state['organic_meta_next_lane']='engagement' if lane=='views' else 'views'
                self.rotation_started=True
                self.save()
            try:result=self.original(session,method,url,**kwargs)
            except requests.RequestException:
                reason='Meta network request failed; queued for retry'
                self.state['cache'][key]={'failure':reason,'until':self.now()+1800}
                self.defer(reason,1800);raise MetaDeferred(reason) from None
            try:data=result.json()
            except ValueError:data={}
            code=data.get('error',{}).get('code') if isinstance(data,dict) else None
            usage={}
            for header in ('x-app-usage','x-ad-account-usage','x-business-use-case-usage'):
                try:usage[header]=json.loads(result.headers.get(header,'{}'))
                except (ValueError,TypeError):pass
            self.state['last_usage']={'at':self.now(),'stage':self.current,'lane':lane,'headers':usage}
            def percentages(value):
                if isinstance(value,dict):
                    for k,v in value.items():
                        if k in ('call_count','total_time','total_cputime','acc_id_util_pct') and isinstance(v,(int,float)):yield v
                        else:yield from percentages(v)
                elif isinstance(value,list):
                    for v in value:yield from percentages(v)
            limited=code in (4,17,613,80002) or result.status_code==429
            near=any(v>=80 for v in percentages(usage))
            if limited or near:
                reason=('Meta rate limit' if limited else 'Meta usage near limit')+'; remaining requests deferred'
                self.state.update(blocked_until=self.now()+3600,blocked_reason=reason)
                if lane in ('views','engagement'):
                    self.state['organic_meta_next_lane']='engagement' if lane=='views' else 'views'
                self.defer(reason)
            if limited:raise MetaDeferred(reason)
            if result.ok and isinstance(data,dict) and 'error' not in data:
                # Fresh inventory is short-lived; metric pages remain resumable
                # until the next Eastern daily reporting boundary.
                until=next_boundary(self.now())
                inventory=path.endswith('/ads') or ('fields' in query and 'business_discovery' not in query['fields'] and 'media' not in path and 'insights' not in path and 'organic_insights' not in query['fields'])
                self.state['cache'][key]={'data':clean_data(data),'until':until,'fresh_until':min(until,self.now()+900) if inventory else until}
            elif result.status_code>=500:
                reason='Meta temporary HTTP %d; queued for retry'%result.status_code
                self.state['cache'][key]={'failure':reason,'until':self.now()+1800}
                self.defer(reason,1800)
                raise MetaDeferred(reason)
            elif result.status_code in (400,401,403,404):
                # Stable authorization/identity failures must not consume every
                # continuation batch ahead of posts that can succeed.
                self.state['cache'][key]={'data':clean_data(data),'status':result.status_code,'until':self.now()+21600}
            self.save()
            if self.sent%10==0:print('KOL Meta %s: %d network; %d checkpoint hits'%(self.current,self.sent,self.hits),flush=True)
            return result


def run_stage(runtime,name,action,resume=False):
    with runtime.stage(name,resume=resume):
        # Persist before side effects: SIGTERM/power loss cannot silently lose
        # a stage that has already consumed quota and saved response pages.
        runtime.state['pending'][name]={'retry_at':runtime.now()+300,'reason':'Meta stage in progress; resume after interruption'}
        runtime.save()
        try:result=action()
        except (RuntimeError,requests.RequestException):
            if name not in runtime.deferred:runtime.defer('Meta stage incomplete; queued for retry',1800)
            raise
        runtime.complete(name)
        return result


def resume_pending(runtime,env,output,apply):
    """Resume only unfinished KOL rows, never Robot or the whole daily task."""
    from kol_row_updates import metrics_allowed,run_kind
    if not metrics_allowed(env,now=runtime.now()):return
    actions={name:(lambda env,out,apply,name=name:run_kind(env,out,apply,name))
             for name in ('ads-meta','organic-meta','ads-tiktok','organic-tiktok','organic-youtube')}
    # Legacy Views-only checkpoints migrate to the unified row queue.
    actions['organic-views']=lambda env,out,apply:run_kind(env,out,apply,'organic-meta')
    for name in runtime.pending(due=True):
        if name not in actions:continue
        target=output/('resume-'+name);target.mkdir(exist_ok=True)
        print('KOL Meta resuming '+name,flush=True)
        try:run_stage(runtime,name,lambda:actions[name](env,target,apply),resume=True)
        except (RuntimeError,requests.RequestException):
            print('KOL Meta '+name+' remains queued',flush=True)
