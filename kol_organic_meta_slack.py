"""Organic Meta Slack-only fallback. Metrics are never a prerequisite to insert."""
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from add_tiktok_tracker_rows import plain_text
from kol_organic_youtube import heading, MARKER, LIVE, URL
from kol_organic_meta import post_key


def parse_launches(messages, oldest=0):
    byts={m['ts']:m for m in messages};drafts=[];issues=[]
    for message in sorted(messages,key=lambda m:float(m['ts'])):
        text=plain_text(message.get('text',''));parent=byts.get(message.get('thread_ts'),{})
        marked=bool(MARKER.search(text) or MARKER.search(parent.get('text','')))
        if not marked and not LIVE.search(text+'\n'+plain_text(parent.get('text',''))):continue
        names={heading(line) for line in MARKER.sub('',plain_text(parent.get('text',''))).splitlines()
               if not re.search(r'(?:adcode-)?Q9jT[A-Za-z0-9_-]+',line)}-{''}
        creator=next(iter(names)) if len(names)==1 else '';current=None
        for line in MARKER.sub('',text).splitlines():
            line_codes=set(re.findall(r'(?:adcode-)?(Q9jT[A-Za-z0-9_-]+)',line))
            name='' if line_codes and not URL.search(line) else heading(line)
            if name:creator=name;current=None
            for match in URL.finditer(line):
                link=html.unescape(match.group()).rstrip(').,*');key=post_key(link)
                current=None
                if not key:continue
                if not creator:
                    if float(message['ts'])>=oldest:
                        issues.append({'ts':message['ts'],'reason':'Slack Creator missing or ambiguous'})
                    continue
                current={'creator':creator,'post_key':key,'post_link':'https://www.instagram.com/reel/'+key+'/',
                         'ts':message['ts'],'_latest':message['ts'],'ad_code':'','codes':set()}
                drafts.append(current)
            if current:
                current['codes'].update(re.findall(r'(?:adcode-)?(Q9jT[A-Za-z0-9_-]+)',line))
        if not URL.search(text) and message.get('thread_ts'):
            targets=[d for d in drafts if d.get('root_ts',d['ts'])==message['thread_ts'] and not d['codes']]
            codes=set(re.findall(r'(?:adcode-)?(Q9jT[A-Za-z0-9_-]+)',text))
            if len(targets)==1 and len(codes)==1:
                targets[0]['codes']=codes;targets[0]['_latest']=message['ts']
        for draft in drafts:
            if draft['ts']==message['ts']:draft['root_ts']=message.get('thread_ts',message['ts'])
    result=[]
    for item in drafts:
        if float(item.pop('_latest'))<oldest:continue
        codes=item.pop('codes')
        if len(codes)>1:
            issues.append({'ts':item['ts'],'post_key':item['post_key'],'reason':'Conflicting Slack Ad Codes'});continue
        item['ad_code']=next(iter(codes)) if codes else '';result.append(item)
    return result,issues


def pending_rows(values, candidates):
    existing={post_key(row[3]) for row in values[2:] if len(row)>3 and post_key(row[3])}
    groups={};issues=[];new=[]
    for item in candidates:groups.setdefault(item['post_key'],[]).append(item)
    for key,items in groups.items():
        if key in existing:continue
        codes={x['ad_code'] for x in items if x.get('ad_code')}
        if len(codes)>1:
            issues.append({'post_key':key,'reason':'Conflicting Slack Ad Codes'});continue
        item=dict(min(items,key=lambda x:float(x['ts'])))
        item['ad_code']=next(iter(codes)) if codes else ''
        new.append(item)
    return new,issues


def code_backfills(values, candidates):
    groups={}
    for item in candidates:
        if item.get('ad_code'):groups.setdefault(item['post_key'],set()).add(item['ad_code'])
    result=[]
    for row,values_row in enumerate(values[2:],3):
        if len(values_row)<=3:continue
        codes=groups.get(post_key(values_row[3]),set())
        if len(codes)==1 and (len(values_row)<5 or not str(values_row[4]).strip()):
            result.append({'row':row,'code':next(iter(codes))})
    return result


def poll(env, output, apply, messages=None):
    from kol_tracker import BASE,save_json,sheet_session,read_tab,slack_pages,verify_literal_target
    from update_meta_tracker import write_range
    from kol_organic_discovery import insert_additions
    from kol_organic_meta_totals import fetch_totals,verified_media
    root=Path(env.get('KOL_TRACKER_STATE_DIR',str(BASE/'data/processed/kol_tracker_runtime')))
    path=root/'organic-meta-slack-state.json'
    state=json.loads(path.read_text()) if path.exists() else {}
    channel=env['KOL_TRACKER_SLACK_CHANNEL_ID'];now=datetime.now(timezone.utc).timestamp()
    if state and state.get('channel')!=channel:raise RuntimeError('Organic Meta Slack channel changed')
    oldest=max(0,float(state.get('cursor',now-7*86400))-86400)
    # On rollout retain a seven-day window; old thread parents provide context only.
    if not state:oldest=now-7*86400
    roots=dict(state.get('threads',{}))
    if messages is None:
        session=requests.Session()
        session.headers['Authorization']='Bearer '+(env.get('KOL_TRACKER_SLACK_BOT_TOKEN') or env.get('KOL_TRACKER_SLACK_TOKEN') or env['SLACK_BOT_TOKEN'])
        history=slack_pages(session,'conversations.history',{'channel':channel,'latest':now})
        messages=list(history);changed=[]
        for message in history:
            if message.get('reply_count'):
                old=roots.get(message['ts'],{})
                if message.get('latest_reply')!=old.get('latest_reply') or message.get('reply_count')!=old.get('reply_count'):
                    changed.append(message['ts'])
                roots[message['ts']]=message
        for index,ts in enumerate(changed,1):
            messages.extend(slack_pages(session,'conversations.replies',{'channel':channel,'ts':ts,'latest':now}))
            if index%25==0 or index==len(changed):
                print('Organic Meta Slack threads: %d/%d'%(index,len(changed)),flush=True)
        messages.extend(roots.values())
        messages=list({m['ts']:m for m in messages}.values())
    parsed,issues=parse_launches(messages,oldest)
    candidates=dict(state.get('candidates',{}))
    for item in parsed:
        old=candidates.get(item['post_key'])
        if old and old.get('ad_code') and item.get('ad_code') and old['ad_code']!=item['ad_code']:
            issues.append({'post_key':item['post_key'],'reason':'Conflicting saved Slack code'})
            old['conflict']=True
        elif not old or (not old.get('ad_code') and item.get('ad_code')):candidates[item['post_key']]=item
    session,endpoint=sheet_session(dict(env,KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK']))
    prop,native,values=read_tab(session,endpoint,'Meta-IG')
    pending,conflicts=pending_rows(values,[x for x in candidates.values() if not x.get('conflict')]);issues.extend(conflicts)
    additions=[]
    # Only new rows need network resolution. Existing manual identities are untouched.
    if pending:
        rows=[[],values[1]]
        for item in pending:rows.append([item['creator'],'','',item['post_link'],item.get('ad_code','')])
        content,_=fetch_totals(env,rows)
        for item in pending:
            media=content.get(item['post_key'],{})
            if verified_media(media,item['post_key']) and media.get('timestamp'):
                day=datetime.fromisoformat(media['timestamp'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date()
                mid=str(media['id'])
                note='Public total metrics pending first daily update; KOL fee requires manual entry'
            else:
                day=datetime.fromtimestamp(float(item['ts']),ZoneInfo('America/New_York')).date();mid=''
                note='Total metrics unavailable: exact media unresolved; launch date from Slack; KOL fee requires manual entry'
            if day.year!=int(env.get('KOL_ORGANIC_META_YEAR','2026')):continue
            if not item.get('ad_code'):note+='; Slack Ad Code missing'
            additions.append(dict(item,content_id=mid or item['post_key'],date=day.isoformat(),note=note))
    save_json(output/'Organic-Meta-Slack-plan.json',{'additions':additions,'issues':issues,'messages':len(messages)})
    print('Organic Meta Slack fallback: %d new rows; %d review issues'%(len(additions),len(issues)),flush=True)
    insert_additions(env,output,apply,prop,native,values,additions,issues)
    # A later reply may provide the code after the identity-only row was added.
    # Resolve rows again after insertions; never overwrite a nonblank/manual code.
    if apply:
        prop,native,values=read_tab(session,endpoint,'Meta-IG')
        backfills=code_backfills(values,[x for x in candidates.values() if not x.get('conflict')])
        body=[]
        cells=native['sheets'][0]['data'][0]['rowData']
        for item in backfills:
            raw=cells[item['row']-1].get('values',[])
            target=raw[4] if len(raw)>4 else {}
            try:verify_literal_target(target,item['code'])
            except RuntimeError:continue
            body.append(write_range(prop['sheetId'],item['row'],4,[item['code']]))
        if body:
            if read_tab(session,endpoint,'Meta-IG')[1]!=native:raise RuntimeError('Organic sheet changed; no code backfill')
            session.post(endpoint+':batchUpdate',json={'requests':body},timeout=90).raise_for_status()
            after=read_tab(session,endpoint,'Meta-IG')[2]
            for request in body:
                row=request['updateCells']['range']['startRowIndex']
                expected=request['updateCells']['rows'][0]['values'][0]['userEnteredValue']['stringValue']
                if after[row][4]!=expected:raise RuntimeError('Slack code backfill readback failed')
            print('Organic Meta Slack exact code backfills verified: %d'%len(body),flush=True)
    if apply:save_json(path,{'channel':channel,'cursor':now,'threads':roots,'candidates':candidates})
    return additions
