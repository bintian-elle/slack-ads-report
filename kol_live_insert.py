"""Native, idempotent live-row insertion and row-scoped failure notes."""
import copy
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from kol_targeting import row_keys,scope_values,resolve_values
from kol_live_updates import job_keys

TABS={'ads-meta':'Meta','ads-tiktok':'TikTok','organic-meta':'Meta-IG',
      'organic-tiktok':'TikTok','organic-youtube':'YTB'}

def open_tab(env,kind):
    from kol_tracker import sheet_session,read_tab
    scoped=dict(env)
    if kind.startswith('organic-'):scoped['KOL_TRACKER_GOOGLE_SHEETS_LINK']=env['KOL_ORGANIC_SHEETS_LINK']
    session,endpoint=sheet_session(scoped)
    prop,native,values=read_tab(session,endpoint,TABS[kind],not kind.startswith('organic-'))
    return session,endpoint,prop,native,values

def insert_jobs(env,kind,jobs,output,apply,aliases):
    from kol_tracker import dedup_new,new_row,insert_rows,meta_inventory,save_json
    from kol_organic_discovery import insert_additions
    from sync_creator_tracker import post_key as ig_post
    from add_tiktok_tracker_rows import post_key as tt_post
    session,endpoint,prop,native,values=open_tab(env,kind)
    if kind in ('ads-meta','ads-tiktok','organic-meta'):
        values,native=backfill_codes(session,endpoint,prop,native,values,kind,jobs,apply,aliases)
    existing=set().union(*(row_keys(kind,row,aliases) for row in values[2 if kind in ('ads-meta','ads-tiktok','organic-meta') else 1:]))
    pending=[];seen=set(existing)
    for job in jobs:
        keys=job_keys(kind,job,aliases)
        if not keys&seen:pending.append(job);seen.update(keys)
    if not pending:return
    if kind.startswith('ads-'):
        platform='Meta' if kind=='ads-meta' else 'TikTok';items=[]
        for job in pending:
            link=aliases.get(job['post_link'],job['post_link'])
            key=ig_post(link) if platform=='Meta' else tt_post(link)
            if not key:continue
            items.append(dict(job,post_link=link,post_key=key,announcement_ts=job['ts']))
        items,issues=dedup_new(platform,values,items)
        info=ads=None
        if platform=='Meta' and items:
            try:info,ads=meta_inventory(env)
            except Exception:pass  # Identity/date first; metric stage records failure.
        proposed=[new_row(platform,item,info,ads) for item in items]
        insert_rows(session,endpoint,platform,prop,native,values,proposed,output,apply)
        return
    additions=[]
    for job in pending:
        day=datetime.fromtimestamp(float(job['ts']),ZoneInfo('America/New_York')).date()
        if day.year!=int(env.get('KOL_ORGANIC_META_YEAR','2026')):continue
        link=aliases.get(job['post_link'],job['post_link'])
        additions.append(dict(job,post_link=link,date=day.isoformat(),content_id='',
            note='Metrics pending immediate API update; KOL fee requires manual entry'))
    if kind=='organic-meta':
        from kol_organic_meta_totals import fetch_totals,verified_media
        from kol_organic_meta import post_key
        proposed=[[],values[1]]+[[x['creator'],'','',x['post_link'],x.get('ad_code','')] for x in additions]
        if additions:
            content,_=fetch_totals(env,proposed)
            for item in additions:
                key=post_key(item['post_link']);media=content.get(key,{})
                if verified_media(media,key):
                    item['content_id']=str(media['id'])
                    if media.get('timestamp'):
                        item['date']=datetime.fromisoformat(media['timestamp'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
                else:item['note']='Total metrics unavailable: exact media unresolved; launch date from Slack; KOL fee requires manual entry'
        insert_additions(env,output,apply,prop,native,values,additions)
    else:
        if kind=='organic-youtube' and additions:
            from kol_organic_youtube import fetch_videos,video_id
            try:api=fetch_videos(env,[video_id(x['post_link']) for x in additions if video_id(x['post_link'])])
            except Exception:api={}
            for item in additions:
                video=api.get(video_id(item['post_link']),{})
                if video.get('snippet',{}).get('publishedAt'):
                    item['date']=datetime.fromisoformat(video['snippet']['publishedAt'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        additions=[x for x in additions if x['date'].startswith('2026-')]
        simple_organic_insert(session,endpoint,prop,native,values,kind,additions,output,apply)
    save_json(output/(kind+'-live-insert-plan.json'),{'additions':additions})

def backfill_codes(session,endpoint,prop,native,values,kind,jobs,apply,aliases):
    from kol_tracker import read_tab,verify_literal_target
    from update_meta_tracker import write_range
    col={'ads-meta':7,'ads-tiktok':2,'organic-meta':4}[kind];body=[]
    cells=native['sheets'][0]['data'][0]['rowData']
    for number,row in enumerate(values[2:],3):
        if not row:continue
        keys=row_keys(kind,row,aliases)
        matches=[job for job in jobs if job.get('ad_code') and
                 {k for k in job_keys(kind,job,aliases) if not k.startswith(('igcode:','ttcode:'))}&keys]
        found={job['ad_code'] for job in matches}
        if len(found)!=1:continue
        code=next(iter(found));old=str(row[col]).strip() if len(row)>col else ''
        if old:
            if kind.startswith('ads-') and old.lstrip('#').removeprefix('adcode-')!=code:
                raise RuntimeError('Conflicting existing post and Slack Ad Code')
            continue
        code=('#'+code) if kind=='ads-tiktok' else code
        raw=cells[number-1].get('values',[])
        try:verify_literal_target(raw[col] if len(raw)>col else {},code)
        except RuntimeError:continue
        body.append(write_range(prop['sheetId'],number,col,[code]))
    if not apply or not body:return values,native
    if read_tab(session,endpoint,TABS[kind],not kind.startswith('organic-'))[1]!=native:raise RuntimeError('Code target changed')
    session.post(endpoint+':batchUpdate',json={'requests':body},timeout=90).raise_for_status()
    _,fresh,actual=read_tab(session,endpoint,TABS[kind],not kind.startswith('organic-'))
    for request in body:
        at=request['updateCells']['range']['startRowIndex'];expected=request['updateCells']['rows'][0]['values'][0]['userEnteredValue']['stringValue']
        if actual[at][col]!=expected:raise RuntimeError('Code readback failed')
    return actual,fresh

def simple_organic_insert(session,endpoint,prop,native,values,kind,additions,output,apply):
    from kol_tracker import read_tab,save_json
    from update_meta_tracker import write_range
    if not additions:return
    tab=native['sheets'][0]
    if tab.get('tables') or tab.get('merges') or tab.get('protectedRanges'):raise RuntimeError('Structured Organic target')
    summaries=[i for i,row in enumerate(values) if row and str(row[0]).strip().lower()=='summary']
    if len(summaries)!=1 or summaries[0]<2:raise RuntimeError('Organic summary/exemplar unavailable')
    index=summaries[0];sid=prop['sheetId'];note=values[0].index('Reason for data update failure')
    rows=native['sheets'][0]['data'][0]['rowData'];body=[];expected={}
    count=len(additions)
    body.append({'insertDimension':{'range':{'sheetId':sid,'dimension':'ROWS','startIndex':index,'endIndex':index+count},'inheritFromBefore':True}})
    for offset,item in enumerate(sorted(additions,key=lambda x:(x['date'],x['post_link']))):
        row=index+offset+1
        for paste in ('PASTE_FORMAT','PASTE_DATA_VALIDATION'):
            body.append({'copyPaste':{'source':{'sheetId':sid,'startRowIndex':index-1,'endRowIndex':index,'startColumnIndex':0,'endColumnIndex':note+1},
                'destination':{'sheetId':sid,'startRowIndex':row-1,'endRowIndex':row,'startColumnIndex':0,'endColumnIndex':note+1},'pasteType':paste}})
        day=datetime.fromisoformat(item['date']).date();serial=(day-datetime(1899,12,30).date()).days
        for col,value in ((0,item['creator']),(1,serial),(2,item['post_link']),(note,item['note'])):
            body.append(write_range(sid,row,col,[value]));expected[(row,col)]=value
        formula='=SUM(G%d:J%d)'%(row,row) if kind=='organic-tiktok' else '=G%d+H%d'%(row,row)
        for col,value in ((5,formula),(10,'=IFERROR(IF(AND(ISNUMBER(D%d),E%d>0),D%d/E%d*1000,""),"")'%((row,)*4)),
                          (11,'=IFERROR(IF(AND(ISNUMBER(D%d),F%d>0),D%d/F%d,""),"")'%((row,)*4))):
            body.append({'updateCells':{'range':{'sheetId':sid,'startRowIndex':row-1,'endRowIndex':row,'startColumnIndex':col,'endColumnIndex':col+1},
                'rows':[{'values':[{'userEnteredValue':{'formulaValue':value}}]}],'fields':'userEnteredValue'}})
    # Extend only recognized simple summary sums; custom formulas are preserved.
    for col,cell in enumerate(rows[index].get('values',[])):
        formula=cell.get('userEnteredValue',{}).get('formulaValue','')
        match=re.fullmatch(r'=SUM\((?P<col>[A-Z]+)2:(?P=col)%d\)'%index,formula,re.I)
        if match:
            value='=SUM(%s2:%s%d)'%(match.group(1),match.group(1),index+count)
            body.append({'updateCells':{'range':{'sheetId':sid,'startRowIndex':index+count,'endRowIndex':index+count+1,'startColumnIndex':col,'endColumnIndex':col+1},
                'rows':[{'values':[{'userEnteredValue':{'formulaValue':value}}]}],'fields':'userEnteredValue'}})
    save_json(output/(kind+'-insert-before.json'),native)
    if not apply:return body
    if read_tab(session,endpoint,TABS[kind],False)[1]!=native:raise RuntimeError('Organic changed; no insert')
    session.post(endpoint+':batchUpdate',json={'requests':body},timeout=90).raise_for_status()
    _,after,actual=read_tab(session,endpoint,TABS[kind],False)
    save_json(output/(kind+'-insert-after.json'),after)
    for (row,col),value in expected.items():
        if actual[row-1][col]!=value:raise RuntimeError('Organic inserted identity verification failed')
    fresh_rows=after['sheets'][0]['data'][0]['rowData']
    for i,old_row in enumerate(rows):
        fresh=fresh_rows[i if i<index else i+count].get('values',[])
        for col,old in enumerate(old_row.get('values',[])):
            new=fresh[col] if col<len(fresh) else {}
            for key in ('userEnteredFormat','dataValidation','chipRuns','textFormatRuns'):
                if old.get(key)!=new.get(key):raise RuntimeError('Organic existing structure changed')
            if 'formulaValue' not in old.get('userEnteredValue',{}) and old.get('userEnteredValue')!=new.get('userEnteredValue'):
                raise RuntimeError('Organic existing value changed')
    print(kind+': native new rows verified '+str(count),flush=True)

def write_failure(env,kind,jobs,reason,apply,aliases):
    from kol_tracker import read_tab,verify_literal_target
    from update_meta_tracker import write_range
    if not apply:return
    if kind.startswith('ads-'):reason='Update failed: '+reason
    session,endpoint,prop,native,values=open_tab(env,kind)
    header=values[1] if kind=='organic-meta' else values[0]
    if 'Reason for data update failure' not in header:return
    col=header.index('Reason for data update failure');body=[]
    targets=set().union(*(job_keys(kind,job,aliases) for job in jobs))
    scoped=scope_values(values,kind,targets,aliases);cells=native['sheets'][0]['data'][0]['rowData']
    for number,row in enumerate(scoped,1):
        if number<=(2 if kind in ('ads-meta','ads-tiktok','organic-meta') else 1) or not row:continue
        old=str(row[col]) if len(row)>col else ''
        # Keep specific API errors and manual reasons; replace only our placeholder.
        if old and not old.startswith(('Metrics pending immediate','Immediate update failed','Metrics not available yet','Update failed: Immediate')):continue
        raw=cells[number-1].get('values',[])
        try:verify_literal_target(raw[col] if len(raw)>col else {},reason)
        except RuntimeError:continue
        body.append(write_range(prop['sheetId'],number,col,[reason]))
    if body:
        if read_tab(session,endpoint,TABS[kind],not kind.startswith('organic-'))[1]!=native:raise RuntimeError('Reason target changed')
        session.post(endpoint+':batchUpdate',json={'requests':body},timeout=90).raise_for_status()
        after=read_tab(session,endpoint,TABS[kind],not kind.startswith('organic-'))[2]
        for request in body:
            at=request['updateCells']['range']['startRowIndex']
            if after[at][col]!=reason:raise RuntimeError('Live failure note readback failed')
