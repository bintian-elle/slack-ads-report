"""Durable Slack insert-then-fetch jobs for every automated KOL tab."""
import copy
import html
import json
import re
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
from add_tiktok_tracker_rows import plain_text,spark_codes
from kol_organic_youtube import heading,MARKER,LIVE,URL
from kol_link_resolution import canonical_post,resolve_post_link,LinkUnavailable
from kol_targeting import row_keys

KINDS={'Meta':('ads-meta','organic-meta'),'TikTok':('ads-tiktok','organic-tiktok'),
       'YouTube':('organic-youtube',)}

def message_time(message):
    return max(float(message['ts']),float((message.get('edited') or {}).get('ts',0)))

def codes(line,platform):
    if platform=='Meta':return set(re.findall(r'(?:adcode-)?(Q9jT[A-Za-z0-9_-]+)',line))
    return set(spark_codes(line)) if platform=='TikTok' else set()

def creator_heading(line):
    if codes(line,'Meta') or codes(line,'TikTok'):return ''
    return heading(line)

def launch_events(messages,oldest):
    byts={m['ts']:m for m in messages};drafts=[]
    for message in sorted(messages,key=lambda x:float(x['ts'])):
        text=plain_text(message.get('text',''));parent=byts.get(message.get('thread_ts'),{})
        parent_text=plain_text(parent.get('text',''))
        if not (MARKER.search(text+parent_text) or LIVE.search(text+'\n'+parent_text)):continue
        names={creator_heading(line) for line in MARKER.sub('',parent_text).splitlines()}-{''}
        creator=next(iter(names)) if len(names)==1 else '';current=None
        for line in MARKER.sub('',text).splitlines():
            name=creator_heading(line)
            if name:creator=name;current=None
            for link in URL.findall(line):
                current=None;link=html.unescape(link).rstrip(').,*');canonical=canonical_post(link)
                if canonical:
                    host=urlsplit(canonical).hostname
                    platform='Meta' if host=='www.instagram.com' else 'TikTok' if host=='www.tiktok.com' else 'YouTube'
                elif urlsplit(link).hostname=='wearerow.mxficus.com':
                    platform=('Meta' if re.search(r'\b(?:IG|Instagram)\b',line,re.I) else
                              'TikTok' if re.search(r'\b(?:TT|TikTok)\b',line,re.I) else
                              'YouTube' if re.search(r'\b(?:YT|YTB|YouTube)\b',line,re.I) else '')
                else:continue
                if not creator or not platform:continue
                current={'creator':creator,'platform':platform,'post_link':canonical or link,
                         'ts':message['ts'],'root_ts':message.get('thread_ts',message['ts']),
                         '_latest':message_time(message),'_codes':set()}
                drafts.append(current)
            if current:current['_codes'].update(codes(line,current['platform']))
        if not URL.search(text) and message.get('thread_ts'):
            for platform in ('Meta','TikTok'):
                found=codes(text,platform)
                targets=[d for d in drafts if d['root_ts']==message['thread_ts'] and d['platform']==platform and not d['_codes']]
                if len(found)==1 and len(targets)==1:
                    targets[0]['_codes']=found;targets[0]['_latest']=message_time(message)
    result={}
    for draft in drafts:
        latest=draft.pop('_latest');found=draft.pop('_codes')
        if latest<oldest:continue
        draft['ad_code']=next(iter(found)) if len(found)==1 else ''
        draft['conflict']=len(found)>1
        key=(draft['platform'],draft['post_link'])
        old=result.get(key)
        if old and old.get('ad_code') and draft.get('ad_code') and old['ad_code']!=draft['ad_code']:
            old.update(ad_code='',conflict=True)
        elif not old or (not old.get('ad_code') and draft.get('ad_code')):result[key]=draft
    return list(result.values())

def enrich_messages(messages,state,now,oldest,resolver=resolve_post_link):
    aliases=state.setdefault('link_aliases',{});errors=state.setdefault('link_errors',{});result=[]
    active_roots={message.get('thread_ts') for message in messages if message_time(message)>=oldest}
    for message in messages:
        fresh=dict(message);text=html.unescape(message.get('text',''))
        if message_time(message)>=oldest or message['ts'] in active_roots:
            for link in URL.findall(text):
                link=link.rstrip(').,*');canonical=canonical_post(link)
                host=urlsplit(link).hostname
                eligible=(host in ('wearerow.mxficus.com','vm.tiktok.com','vt.tiktok.com') or
                    (host in ('www.tiktok.com','tiktok.com') and '/t/' in urlsplit(link).path) or
                    (host in ('google.com','www.google.com') and urlsplit(link).path=='/url'))
                if not canonical and eligible:
                    canonical=aliases.get(link,'')
                    if not canonical and errors.get(link,{}).get('retry_at',0)<=now:
                        try:
                            canonical=resolver(link)
                            line=next((line for line in text.splitlines() if link in line),'')
                            expected=('www.tiktok.com' if re.search(r'\b(?:TT|TikTok)\b',line,re.I) else
                                      'www.youtube.com' if re.search(r'\b(?:YT|YTB|YouTube)\b',line,re.I) else
                                      'www.instagram.com' if re.search(r'\b(?:IG|Instagram)\b',line,re.I) else '')
                            if expected and urlsplit(canonical).hostname!=expected:
                                errors[link]={'reason':'Slack platform and resolved post conflict','retry_at':now+86400}
                                text=text.replace(link,'[Conflicting platform link omitted]');canonical=''
                            else:aliases[link]=canonical;errors.pop(link,None)
                        except LinkUnavailable as error:
                            errors[link]={'reason':str(error),'retry_at':now+(86400 if error.blocked else 1800)}
                if errors.get(link,{}).get('reason')=='Slack platform and resolved post conflict':
                    text=text.replace(link,'[Conflicting platform link omitted]')
                elif canonical:text=text.replace(link,canonical)
        fresh['text']=text;result.append(fresh)
    return result

def job_keys(kind,job,aliases=None):
    row=['']*8
    col={'ads-meta':6,'ads-tiktok':3,'organic-meta':3,'organic-tiktok':2,'organic-youtube':2}[kind]
    row[col]=job['post_link']
    if kind=='ads-meta':row[7]=job.get('ad_code','')
    if kind=='ads-tiktok':row[2]=job.get('ad_code','')
    return row_keys(kind,row,aliases)

def enqueue_jobs(state,events,now):
    jobs=state.setdefault('jobs',{});done=state.setdefault('completed',{})
    for event in events:
        for kind in KINDS[event['platform']]:
            if kind.startswith('ads-') and (not event.get('ad_code') or event.get('conflict')):continue
            keys=job_keys(kind,event,state.get('link_aliases'))
            if not keys:continue
            primary=next(iter(sorted(keys)))
            job_id=kind+'|'+primary
            if job_id in done:continue
            if job_id not in jobs:
                jobs[job_id]=dict(event,kind=kind,retry_at=now,attempts=0)
            elif not jobs[job_id].get('ad_code') and event.get('ad_code'):
                jobs[job_id]['ad_code']=event['ad_code'];jobs[job_id]['retry_at']=now

def dispatch_jobs(state,handlers,insert,save,now,on_failure=None):
    save()  # Persist jobs before any insertion/metric side effects.
    jobs=state.setdefault('jobs',{});completed=state.setdefault('completed',{})
    for kind in ('organic-youtube','organic-tiktok','organic-meta','ads-meta','ads-tiktok'):
        pending={k:v for k,v in jobs.items() if v['kind']==kind and v.get('retry_at',0)<=now}
        if not pending:continue
        reason='Metrics not available yet; existing values retained';row_errors={}
        try:
            insert(kind,list(pending.values()))
            success=handlers[kind](list(pending.values())) or set()
        except Exception as error:
            success=getattr(error,'successful_keys',set())
            reason='Immediate update failed ('+type(error).__name__+'); existing values retained'
            # Isolate a rejected/protected row; a platform backoff must never
            # trigger immediate per-row network retries.
            if not hasattr(error,'successful_keys') and len(pending)>1:
                for key,job in pending.items():
                    try:
                        insert(kind,[job]);success.update(handlers[kind]([job]) or set())
                    except Exception as individual:
                        success.update(getattr(individual,'successful_keys',set()))
                        row_errors[key]='Immediate update failed ('+type(individual).__name__+'); existing values retained'
        failed=[]
        for key,job in pending.items():
            if job_keys(kind,job,state.get('link_aliases'))&success:
                completed[key]=now;jobs.pop(key,None)
            else:
                job['attempts']=job.get('attempts',0)+1
                job['retry_at']=now+(300 if job['attempts']<=6 else 3600)
                job['reason']=row_errors.get(key,reason);failed.append(job)
        if failed and on_failure:
            try:on_failure(kind,failed,reason)
            except Exception:print('Immediate '+kind+' reason target unavailable; queued for retry',flush=True)
        print('Immediate %s: %d complete; %d pending'%(kind,len(pending)-len(failed),len(failed)),flush=True)
        save()

def slack_snapshot(env,state,now):
    from kol_tracker import slack_pages
    oldest=max(now-7*86400,float(state.get('cursor',now-7*86400))-86400)
    session=requests.Session();session.headers['Authorization']='Bearer '+(
        env.get('KOL_TRACKER_SLACK_BOT_TOKEN') or env.get('KOL_TRACKER_SLACK_TOKEN') or env['SLACK_BOT_TOKEN'])
    history=slack_pages(session,'conversations.history',{'channel':env['KOL_TRACKER_SLACK_CHANNEL_ID'],'latest':now})
    roots=dict(state.get('threads',{}));messages=list(history);changed=[]
    for message in history:
        if not message.get('reply_count'):continue
        old=roots.get(message['ts'],{})
        recent=float(message.get('latest_reply',message['ts']))>=oldest
        if recent and (old.get('latest_reply')!=message.get('latest_reply') or old.get('reply_count')!=message.get('reply_count')):
            changed.append(message['ts'])
        roots[message['ts']]=message
    for count,ts in enumerate(changed,1):
        messages.extend(slack_pages(session,'conversations.replies',{'channel':env['KOL_TRACKER_SLACK_CHANNEL_ID'],'ts':ts,'latest':now}))
        print('Live Slack changed thread: %d/%d'%(count,len(changed)),flush=True)
    messages.extend(roots.values());state['threads']=roots;state['cursor']=now
    return list({m['ts']:m for m in messages}.values()),oldest

def poll(env,output,apply):
    from kol_tracker import BASE,save_json,tiktok_daily
    from kol_live_insert import insert_jobs,write_failure
    from update_meta_tracker import run as ads_meta
    from kol_organic_meta_totals import run as organic_meta
    from kol_organic_tiktok import run as organic_tiktok
    from kol_organic_youtube import run as organic_youtube
    root=Path(env.get('KOL_TRACKER_STATE_DIR',str(BASE/'data/processed/kol_tracker_runtime')))
    path=root/'live-metric-jobs.json';state=json.loads(path.read_text()) if path.exists() else {}
    if state and state.get('channel')!=env['KOL_TRACKER_SLACK_CHANNEL_ID']:raise RuntimeError('Live Slack channel changed')
    state['channel']=env['KOL_TRACKER_SLACK_CHANNEL_ID'];now=datetime.now(timezone.utc).timestamp()
    messages,oldest=slack_snapshot(env,state,now)
    messages=enrich_messages(messages,state,now,oldest)
    events=launch_events(messages,oldest);enqueue_jobs(state,events,now)
    save_json(output/'live-launch-plan.json',{'events':events,'pending':state['jobs'],'link_errors':state.get('link_errors',{})})
    writers={'ads-meta':ads_meta,'ads-tiktok':tiktok_daily,'organic-meta':organic_meta,
             'organic-tiktok':organic_tiktok,'organic-youtube':organic_youtube}
    handlers={}
    for kind,writer in writers.items():
        def invoke(jobs,kind=kind,writer=writer):
            out=output/kind;out.mkdir(exist_ok=True)
            keys=set().union(*(job_keys(kind,job,state.get('link_aliases')) for job in jobs))
            return writer(env,out,apply,target_keys=keys,link_aliases=state.get('link_aliases'))
        handlers[kind]=invoke
    def save():
        if apply:save_json(path,state)
    dispatch_jobs(state,handlers,
        lambda kind,jobs:insert_jobs(env,kind,jobs,output,apply,state.get('link_aliases',{})),save,now,
        lambda kind,jobs,reason:write_failure(env,kind,jobs,reason,apply,state.get('link_aliases',{})))
    for job in state['jobs'].values():
        for raw,error in state.get('link_errors',{}).items():
            if job['post_link']==raw:job['retry_at']=max(job['retry_at'],error['retry_at'])
        if job['kind']=='organic-tiktok':
            blocked=root/'organic-tiktok-state.json'
            if blocked.exists():
                until=json.loads(blocked.read_text()).get('blocked_until')
                if until:job['retry_at']=max(job['retry_at'],datetime.fromisoformat(until).timestamp())
    save()
    print('Live KOL poll: %d launch posts; %d pending metric jobs'%(len(events),len(state['jobs'])),flush=True)
