"""Organic Meta public totals. Never substitute organic counters for totals."""
import copy
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from zoneinfo import ZoneInfo

import requests

from kol_organic_meta import post_key, plan as validate_schema

COUNTERS = {7:('total_views_count','Views'), 9:('total_like_count','Likes'),
            10:('total_comments_count','Comments'), 11:('shares_count','Shares'),
            12:('saved_count','Saves')}
MEDIA_FIELDS = ('id,permalink,username,timestamp,media_type,media_product_type,'
                'total_views_count,total_like_count,total_comments_count,shares_count,saved_count')
MANAGED = ('自动：','API did not return ', 'Missing or unsupported post link',
           'KOL fee requires manual entry','CPM ','CPE ','Total ','Public ', 'Slack ')


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0


def verified_media(media, key):
    return bool(media.get('id') and post_key(media.get('permalink',''))==key)


def metric_plan(values, content, method_a=None):
    validate_schema(values,{})
    planned=[]
    for row,original in enumerate(values[2:],3):
        if not original or not original[0] or str(original[0]).lower()=='summary':
            continue
        v=list(original)+['']*17;key=post_key(v[3]);media=content.get(key,{})
        changes={};reasons=[];missing=[]
        if not key:
            reasons.append('Missing or unsupported post link')
        elif not verified_media(media,key):
            reasons.append('Total metrics unavailable: '+media.get('_error','exact media unresolved'))
        else:
            for col,(field,label) in COUNTERS.items():
                if number(media.get(field)):changes[col]=media[field]
                else:missing.append(label)
            if method_a is None and all(c in changes for c in (9,10,11,12)):
                changes[8]=sum(changes[c] for c in (9,10,11,12))
            elif method_a is None or not number(method_a.get(row)):
                missing.insert(0,'Interaction')
            if missing:reasons.append('Total '+ '/'.join(missing)+' unavailable')
        if key and method_a is not None and number(method_a.get(row)):
            changes[8]=method_a[row]
        if reasons:
            reasons.append('existing values retained')
        if number(v[6]):
            if changes.get(7,0)>0:changes[13]=v[6]/changes[7]*1000
            else:reasons.append('CPM not refreshed: total Views unavailable or zero')
            if changes.get(8,0)>0:changes[14]=v[6]/changes[8]
            else:reasons.append('CPE not refreshed: total Interaction unavailable or zero')
        else:
            reasons.append('KOL fee requires manual entry; CPM/CPE unchanged')
        planned.append({'row':row,'content_id':str(media.get('id','')),'changes':changes,
                        'missing':missing,'reason':'; '.join(reasons)})
    return planned


def note_changes(values, planned):
    header=values[1]
    if len(header)<=16 or header[16]!='Reason for data update failure':
        raise RuntimeError('Organic Meta failure reason header changed')
    result=[]
    for item in planned:
        row=values[item['row']-1];old=str(row[16]) if len(row)>16 else ''
        if old and not old.startswith(MANAGED):continue
        if old!=item['reason']:result.append({'row':item['row'],'changes':{16:item['reason']}})
    return result


def method_a_values(env,values,output,cache_path):
    """Keep the approved existing Interaction calculation; isolate failed batches."""
    from kol_organic_meta import fetch_content,fetch_ad_interactions
    links=sorted({'https://www.instagram.com/reel/'+post_key(row[3])+'/'
                  for row in values[2:] if len(row)>3 and row[0] and post_key(row[3])})
    batches=[links[i:i+5] for i in range(0,len(links),5)]
    def fetch(batch):
        try:return fetch_content(env,batch)
        except (RuntimeError,requests.RequestException):return {}
    natural={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for count,batch in enumerate(pool.map(fetch,batches),1):
            natural.update(batch)
            print('Organic Meta Interaction content: %d/%d'%(count,len(batches)),flush=True)
    if not natural:return {}
    try:_,paid=fetch_ad_interactions(env,natural,cache_path)
    except (RuntimeError,requests.RequestException):
        print('Organic Meta paid Interaction unavailable; existing Interaction/CPE retained',flush=True)
        return {}
    old_plan,_=validate_schema(values,natural,paid)
    result={item['row']:item['changes'][8] for item in old_plan if 8 in item['changes']}
    from kol_tracker import save_json
    save_json(output/'Organic-Meta-Method-A-source.json',{'natural':natural,'paid':paid,'rows':result})
    return result


class APIReadError(RuntimeError):
    pass


def api_read(env, path, params=None):
    """A sanitized GET; never include request URLs or Meta messages in logs."""
    base='https://graph.facebook.com/'+env.get('KOL_TRACKER_META_API_VERSION','v23.0')
    url=path if path.startswith('https://') else base+'/'+path
    if urlsplit(url).hostname!='graph.facebook.com':raise APIReadError('Invalid pagination host')
    u=urlsplit(url);q=parse_qs(u.query);q.pop('access_token',None)
    url=urlunsplit(u._replace(query=urlencode(q,doseq=True)))
    for attempt in range(3):
        try:
            r=requests.get(url,params=params,headers={'Authorization':'Bearer '+env['META_ACCESS_TOKEN']},timeout=45)
        except requests.RequestException:
            raise APIReadError('request failed') from None
        if r.status_code in (500,502,503,504) and attempt<2:continue
        try:data=r.json()
        except ValueError:raise APIReadError('invalid JSON') from None
        if not r.ok or 'error' in data:
            raise APIReadError('HTTP %s / Meta code %s'%(r.status_code,data.get('error',{}).get('code','unknown')))
        return data


def ad_inventory(env):
    from sync_creator_tracker import graph_pages
    session=requests.Session();session.headers['Authorization']='Bearer '+env['META_ACCESS_TOKEN']
    base='https://graph.facebook.com/'+env.get('KOL_TRACKER_META_API_VERSION','v23.0')
    try:
        return graph_pages(session,base+'/act_'+env['META_AD_ACCOUNT_ID'].removeprefix('act_')+'/ads',{
            'fields':'id,name,effective_status,creative{id,branded_content,source_instagram_media_id,instagram_permalink_url}', 'limit':100})
    finally:session.close()


def creator_media(env, username, wanted):
    """Resolve only exact permalinks within a named professional account."""
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,30}',username):return {}
    fields='id,permalink,username,timestamp,media_type,media_product_type'
    ig=env.get('KOL_ORGANIC_META_IG_USER_ID','17841448894150543')
    data=api_read(env,ig,{'fields':'business_discovery.username(%s){id,username,media.limit(100){%s}}'%(username,fields)})
    media=data.get('business_discovery',{}).get('media',{})
    found={};seen=set()
    for _ in range(10):
        for item in media.get('data',[]):
            key=post_key(item.get('permalink',''))
            if key in wanted:found[key]=item
        if set(found)>=wanted:return found
        nxt=media.get('paging',{}).get('next')
        if not nxt or nxt in seen:return found
        seen.add(nxt);data=api_read(env,nxt)
        media=data.get('business_discovery',{}).get('media',data)
    return found


def fetch_totals(env, values, discovery=(), ads=(), identities=None):
    from sync_creator_tracker import code_key
    ids=dict(identities or {});errors={};bycreator={};wanted={}
    known={post_key(c.get('permalink','')):str(c['content_id']) for c in discovery if c.get('content_id')}
    code_ids={};link_ids={}
    for ad in ads:
        c=ad.get('creative',{});mid=c.get('source_instagram_media_id')
        if not mid:continue
        code=code_key(c.get('branded_content',{}).get('instagram_boost_post_access_token'))
        if code:code_ids.setdefault(code,set()).add(str(mid))
        key=post_key(c.get('instagram_permalink_url',''))
        if key:link_ids.setdefault(key,set()).add(str(mid))
    for row in values[2:]:
        if len(row)<=3 or not row[0] or not post_key(row[3]):continue
        key=post_key(row[3]);wanted[key]=row
        candidates=set()
        if key in known:candidates.add(known[key])
        if len(row)>17 and re.fullmatch(r'\d{10,}',str(row[17])):candidates.add(str(row[17]))
        candidates.update(link_ids.get(key,set()))
        if len(row)>4:candidates.update(code_ids.get(code_key(row[4]),set()))
        if not candidates and key in ids:candidates.add(str(ids[key]))
        if len(candidates)==1:ids[key]=next(iter(candidates))
        elif len(candidates)>1:ids.pop(key,None);errors[key]='conflicting exact media bindings'
        else:bycreator.setdefault(str(row[0]).strip().lstrip('@'),set()).add(key)
    for count,(creator,keys) in enumerate(bycreator.items(),1):
        try:
            for key,item in creator_media(env,creator,keys).items():ids[key]=str(item['id'])
        except APIReadError as exc:
            for key in keys:errors[key]='Creator lookup '+str(exc)
        print('Organic Meta exact Creator lookup: %d/%d'%(count,len(bycreator)),flush=True)
    content={key:{'_error':errors.get(key,'exact media unresolved')} for key in wanted}
    grouped={}
    for key in wanted:
        if key in ids and key not in errors:grouped.setdefault(ids[key],[]).append(key)
    def fetch(pair):
        mid,keys=pair
        try:
            data=api_read(env,mid,{'fields':MEDIA_FIELDS})
            return mid,keys,data
        except APIReadError as exc:return mid,keys,{'_error':str(exc)}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for count,(mid,keys,data) in enumerate(pool.map(fetch,grouped.items()),1):
            for key in keys:
                if verified_media(data,key) and str(data['id'])==mid:content[key]=data;ids[key]=mid
                else:content[key]={'_error':data.get('_error','returned permalink does not match')}
            if count%10==0 or count==len(grouped):
                print('Organic Meta public totals: %d/%d'%(count,len(grouped)),flush=True)
    return content,ids


def write_updates(session, endpoint, prop, native, values, planned, other, output, apply):
    from kol_tracker import read_tab, save_json, verify_literal_target
    from update_meta_tracker import write_range
    cells=native['sheets'][0]['data'][0]['rowData'];body=[];expected=copy.deepcopy(native)
    successful=0
    def build(row,col,value):
        source=cells[row-1].get('values',[]);target=source[col] if len(source)>col else {}
        formula=target.get('userEnteredValue',{}).get('formulaValue')
        if col in (13,14) and formula:return None
        if col==8 and formula in ('=J%d+K%d+L%d+M%d'%((row,)*4),'=SUM(J%d:M%d)'%(row,row)):
            target=dict(target,userEnteredValue={})
        verify_literal_target(target,value)
        return write_range(prop['sheetId'],row,col,[value])
    def record(request):
        body.append(request);u=request['updateCells'];row=u['range']['startRowIndex'];col=u['range']['startColumnIndex']
        dest=expected['sheets'][0]['data'][0]['rowData'][row].setdefault('values',[])
        dest.extend({} for _ in range(max(0,col+1-len(dest))))
        entered=u['rows'][0]['values'][0].get('userEnteredValue')
        if entered:dest[col]['userEnteredValue']=entered
        else:dest[col].pop('userEnteredValue',None)
    for item in planned:
        row=item['row'];changes=dict(item['changes']);raw=cells[row-1].get('values',[])
        formula=raw[8].get('userEnteredValue',{}).get('formulaValue') if len(raw)>8 else None
        if formula and 8 not in changes and any(c in changes for c in (9,10,11,12)):
            if formula in ('=J%d+K%d+L%d+M%d'%((row,)*4),'=SUM(J%d:M%d)'%(row,row)):
                changes[8]=values[row-1][8]  # Freeze the old basis, do not recompute a mixed sum.
            else:
                changes={c:v for c,v in changes.items() if c not in (9,10,11,12)}
                item['reason']+='; Total J:M retained: custom Interaction formula depends on incomplete totals'
        try:requests_body=[build(row,c,v) for c,v in changes.items()]
        except RuntimeError:
            item['reason']='Total metrics target protected; existing values retained'
            continue
        for request in requests_body:
            if request:record(request)
        if any(c in changes for c in (7,8,9,10,11,12)):successful+=1
    for item in other+note_changes(values,planned):
        try:requests_body=[build(item['row'],c,v) for c,v in item['changes'].items()]
        except RuntimeError:
            print('Organic Meta protected auxiliary cell skipped: row %d'%item['row'],flush=True);continue
        for request in requests_body:
            if request:record(request)
    if successful:
        stamp=datetime.now(ZoneInfo('America/New_York')).strftime('[%m/%d update]')
        record(build(1,5,stamp+'\nUpdated: %d/%d'%(successful,len(planned))))
    save_json(output/'Organic-Meta-Totals-plan.json',{'basis':'Public lifetime counters; Method A Interaction/CPE',
              'successful_rows':successful,'planned':planned,'auxiliary_updates':other})
    if not apply or not body:return
    save_json(output/'Organic-Meta-Totals-before.json',native)
    if read_tab(session,endpoint,'Meta-IG')[1]!=native:raise RuntimeError('Organic sheet changed; no write')
    session.post(endpoint+':batchUpdate',json={'requests':body},timeout=90).raise_for_status()
    after=read_tab(session,endpoint,'Meta-IG')[1];save_json(output/'Organic-Meta-Totals-after.json',after)
    for obj in (expected,after):
        for row in obj['sheets'][0]['data'][0]['rowData']:
            for cell in row.get('values',[]):cell.pop('effectiveValue',None)
            while row.get('values') and not row['values'][-1]:row['values'].pop()
    if expected!=after:raise RuntimeError('Organic Meta totals native readback failed')
    print('Organic Meta total counters and notes verified: %d/%d rows'%(successful,len(planned)),flush=True)


def run(env, output, apply, creators=None):
    from kol_tracker import BASE, sheet_session,read_tab,save_json
    from kol_organic_discovery import run as discover
    from kol_organic_meta import code_plan,media_identity_plan,status_plan
    discovery=[]
    if not creators:
        try:discovery=discover(env,output,apply) or []
        except (RuntimeError,requests.RequestException):
            print('Organic Meta API discovery unavailable; existing rows still queried',flush=True)
    session,endpoint=sheet_session(dict(env,KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK']))
    prop,native,values=read_tab(session,endpoint,'Meta-IG');validate_schema(values,{})
    selected=values
    if creators:
        selected=[row if i<2 or (row and row[0] in creators) else [] for i,row in enumerate(values)]
    try:ads=ad_inventory(env)
    except (RuntimeError,requests.RequestException):
        ads=[];print('Organic Meta ad statuses unavailable; existing statuses retained',flush=True)
    state_path=Path(env.get('KOL_TRACKER_STATE_DIR',str(BASE/'data/processed/kol_tracker_runtime')))/'organic-meta-identities.json'
    try:registry=json.loads(state_path.read_text()).get('posts',{})
    except (ValueError,OSError):registry={}
    content,registry=fetch_totals(env,selected,discovery,ads,registry)
    save_json(output/'Organic-Meta-Totals-source.json',content)
    interaction=method_a_values(env,selected,output,state_path.parent/'organic-paid-cache.json')
    planned=metric_plan(selected,content,interaction)
    identity_content={key:{'content_id':x['id']} for key,x in content.items() if verified_media(x,key)}
    resolved,codes,issues=code_plan(selected,[],ads)
    resolved,matches,media_codes,media_issues=media_identity_plan(resolved,ads,identity_content,{x['row'] for x in issues})
    statuses=status_plan(resolved,ads,native['sheets'][0]['data'][0]['rowData'],matches)
    print('Organic Meta totals planned: %d rows; %d status changes'%(len(planned),len(statuses)),flush=True)
    write_updates(session,endpoint,prop,native,values,planned,codes+media_codes+statuses,output,apply)
    if apply:save_json(state_path,{'posts':registry})
    return planned
