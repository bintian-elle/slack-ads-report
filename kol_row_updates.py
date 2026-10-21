"""KOL-only, post-identity checkpoints committed after native write verification."""
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from kol_meta_runtime import MetaDeferred, next_boundary, reporting_window


def root_path(env):
    from sync_creator_tracker import BASE
    return Path(env.get('KOL_TRACKER_STATE_DIR',str(BASE/'data/processed/kol_tracker_runtime')))


def metrics_allowed(env,now=None):
    path=root_path(env)/'metric-schedule-control.json'
    try:control=json.loads(path.read_text())
    except FileNotFoundError:return True
    # Invalid controls fail closed rather than accidentally issuing requests.
    return (time.time() if now is None else now)>=float(control['not_before'])


def hold_until_daily(env,now=None):
    from kol_tracker import save_json
    until=next_boundary(time.time() if now is None else now)
    save_json(root_path(env)/'metric-schedule-control.json',{'not_before':until})
    return until


class RowQueue:
    def __init__(self,path,scope,now=time.time,persist=True):
        self.path=Path(path);self.now=now;self.persist=persist;self.cycle=reporting_window(now())
        self.today=datetime.fromtimestamp(now(),ZoneInfo('America/New_York')).date()
        try:self.state=json.loads(self.path.read_text())
        except FileNotFoundError:self.state={}
        if self.state.get('scope')!=scope:self.state={'scope':scope,'rows':{}}
        self.state.setdefault('rows',{})
        self.remaining=0

    def save(self):
        if self.persist:
            from kol_tracker import save_json
            save_json(self.path,self.state)

    def prepare(self,item):
        old=self.state['rows'].setdefault(item['key'],{})
        if old.get('attempt_cycle')!=self.cycle:
            old.update(attempt_cycle=self.cycle,retry_at=0,attempts=0)
        status=item.get('status');paused=status in ('PAUSED','ADSET_PAUSED','CAMPAIGN_PAUSED')
        changed=old.get('observed_status')!=status
        if changed:old.pop('cycle',None)
        old['observed_status']=status
        if paused:
            old.setdefault('paused_since',self.today.isoformat())
        else:old.pop('paused_since',None)
        if not changed and old.get('cycle')==self.cycle:return None
        if old.get('retry_at',0)>self.now() and not changed:return None
        age=(self.today-datetime.fromisoformat(old['paused_since']).date()).days if paused else 0
        if paused and age>=7 and old.get('fetched_on'):
            last=datetime.fromisoformat(old['fetched_on']).date()
            if (self.today-last).days<7:return None
        priority=0 if status=='ACTIVE' else 2 if paused and age<7 else 3 if paused else 1
        return priority

    def run(self,items,writer,stop=None,limit=20):
        work=[]
        for item in items:
            rank=self.prepare(item)
            if rank is not None:work.append((rank,item))
        self.save();completed=set()
        for _,item in sorted(work,key=lambda pair:(pair[0],pair[1].get('row',0)))[:limit]:
            if stop and stop():break
            entry=self.state['rows'][item['key']]
            entry.update(inflight=True,row=item.get('row'),retry_at=self.now()+300)
            self.save()  # Crash before readback cannot advance completion.
            try:success=bool(writer(item))
            except MetaDeferred:
                entry.update(inflight=False,retry_at=self.now()+3600)
                self.save();break
            except (RuntimeError,requests.RequestException,ValueError):success=False
            entry['attempts']=0 if success else entry.get('attempts',0)+1
            entry.update(inflight=False,retry_at=0 if success else self.now()+(1800 if entry['attempts']<=6 else 21600))
            if success:
                entry.update(cycle=self.cycle,fetched_on=self.today.isoformat())
                completed.add(item['key'])
            self.save()
            print('KOL row %s: %s'%(item.get('row','?'),'verified' if success else 'pending retry'),flush=True)
        # Includes rows temporarily backed off, not merely this invocation's work.
        self.remaining=sum(1 for item in items if self.state['rows'][item['key']].get('cycle')!=self.cycle
            and not (item.get('status') in ('PAUSED','ADSET_PAUSED','CAMPAIGN_PAUSED')
                     and self.state['rows'][item['key']].get('fetched_on')
                     and (self.today-datetime.fromisoformat(self.state['rows'][item['key']]['paused_since']).date()).days>=7
                     and (self.today-datetime.fromisoformat(self.state['rows'][item['key']]['fetched_on']).date()).days<7))
        return completed


def api_statuses(env,kind,values,ads):
    """Manual T0 etc never determine API priority and are never overwritten here."""
    from update_meta_tracker import exact_mapping
    if kind=='ads-meta':
        matches,_,_=exact_mapping([(i,row+['']*max(0,8-len(row))) for i,row in enumerate(values,1)
                                  if i>2 and len(row)>1 and row[1]],ads)
    elif kind=='organic-meta':
        from kol_organic_meta import code_plan,media_identity_plan,post_key
        resolved,_,issues=code_plan(values,[],ads)
        identities={post_key(row[3]):{'content_id':str(row[17])} for row in values[2:]
                    if len(row)>17 and str(row[17]).isdigit()}
        _,matches,_,_=media_identity_plan(resolved,ads,identities,{x['row'] for x in issues})
    else:return {}
    byid={ad['id']:ad for ad in ads};result={}
    paused={'PAUSED','ADSET_PAUSED','CAMPAIGN_PAUSED'}
    for row,ids in matches.items():
        statuses={byid[aid].get('effective_status') for aid in ids}
        result[row]='ACTIVE' if 'ACTIVE' in statuses else 'PAUSED' if statuses and statuses<=paused else None
    return result


def automatic_status(current,ads,target):
    if str(current).strip().lower() not in ('','testing','pause','paused'):return None
    statuses={ad.get('effective_status') for ad in ads}
    value='testing' if 'ACTIVE' in statuses else 'paused' if statuses and statuses<={'PAUSED','ADSET_PAUSED','CAMPAIGN_PAUSED'} else None
    rule=target.get('dataValidation',{}).get('condition',{})
    if value and rule:
        if rule.get('type')!='ONE_OF_LIST':return None
        allowed=[x.get('userEnteredValue') for x in rule.get('values',[])]
        if value=='paused' and value not in allowed and 'pause' in allowed:value='pause'
        if value not in allowed:return None
    return value if value!=current else None


def run_rows(env,output,apply,kind,writer,target_keys=None,link_aliases=None):
    """The writer receives exactly one post; it verifies all writes before return."""
    from kol_live_insert import open_tab
    from kol_targeting import START,row_keys,load_link_aliases,matches_keys
    runtime=env.get('_KOL_META_RUNTIME')
    if not metrics_allowed(env):
        print('KOL metrics held until next daily boundary',flush=True);return set()
    if runtime and kind in ('ads-meta','organic-meta') and runtime.state.get('blocked_until',0)>runtime.now():
        runtime.defer(runtime.state.get('blocked_reason','Meta cooldown'),runtime.state['blocked_until']-runtime.now())
        return set()
    if kind=='organic-meta' and target_keys is None:
        from kol_organic_discovery import run as discover
        try:discover(env,output,apply)
        except (RuntimeError,requests.RequestException):
            if runtime:runtime.defer('Organic discovery pending; queued for retry',1800)
    _,endpoint,_,_,values=open_tab(env,kind)
    aliases=load_link_aliases(env) if link_aliases is None else link_aliases
    statuses={}
    if kind in ('ads-meta','organic-meta'):
        from kol_organic_meta_totals import ad_inventory
        try:statuses=api_statuses(env,kind,values,ad_inventory(env))
        except (RuntimeError,requests.RequestException):
            if runtime and (runtime.sent>=runtime.budget or runtime.state.get('blocked_until',0)>runtime.now()):return set()
    if kind=='ads-tiktok':
        from kol_tracker import fetch_ad_details,fetch_report,tiktok_bindings
        # Shared account reports are fetched once, never once per row.
        if '_KOL_TIKTOK_DETAILS' not in env:env['_KOL_TIKTOK_DETAILS']=fetch_ad_details(env)
        if '_KOL_TIKTOK_REPORT' not in env:env['_KOL_TIKTOK_REPORT']=fetch_report(env)
        bound,_=tiktok_bindings(values,env['_KOL_TIKTOK_REPORT'],env['_KOL_TIKTOK_DETAILS'])
        byid={str(ad['ad_id']):ad for ad in env['_KOL_TIKTOK_DETAILS']}
        for item in bound:
            states={byid.get(str(aid),{}).get('operation_status') for aid in item['ad_ids']}
            statuses[item['row']]='ACTIVE' if 'ENABLE' in states else 'PAUSED' if states=={'DISABLE'} else None
    items=[]
    creator_col=1 if kind=='ads-meta' else 0
    for n,row in enumerate(values,1):
        if n<=START[kind] or len(row)<=creator_col or not row[creator_col] or str(row[creator_col]).lower()=='summary':continue
        keys=row_keys(kind,row,aliases)
        if target_keys is not None and not matches_keys(keys,target_keys):continue
        # No usable identity must never silently expand to the whole sheet.
        if not keys:continue
        primary=sorted(k for k in keys if not k.startswith(('igcode:','ttcode:'))) or sorted(keys)
        items.append({'key':primary[0],'row':n,'keys':keys,'status':statuses.get(n)})
    scope=endpoint+'|'+kind
    digest=hashlib.sha256(scope.encode()).hexdigest()[:16]
    queue=RowQueue(root_path(env)/('row-progress-'+digest+'.json'),scope,persist=apply)
    success=set()
    def write(item):
        target=output/('row-'+str(item['row']));target.mkdir(exist_ok=True)
        # A row involves multiple Sheets reads/write/readback. Pace real runs
        # to stay below per-user Sheets quotas and TikTok's public-read cadence.
        if apply:time.sleep(7)
        # Same request pages can be reused within the current reporting window.
        if runtime:
            with runtime.stage(runtime.current,resume=True):
                result=writer(dict(env,_KOL_ROW_MODE=True,_KOL_LIVE_METRICS=target_keys is not None),target,apply,target_keys=item['keys'],link_aliases=aliases) or set()
        else:result=writer(dict(env,_KOL_ROW_MODE=True,_KOL_LIVE_METRICS=target_keys is not None),target,apply,target_keys=item['keys'],link_aliases=aliases) or set()
        done=matches_keys(item['keys'],result)
        if done:success.update(item['keys'])
        return done
    def stopped():
        return bool(runtime and kind in ('ads-meta','organic-meta') and
                    (runtime.sent>=runtime.budget or runtime.state.get('blocked_until',0)>runtime.now()
                     or (runtime.current_lane and runtime.lane_sent[runtime.current_lane]>=runtime.lane_limits[runtime.current_lane])))
    queue.run(items,write,stopped)
    if queue.remaining and runtime:
        pending=runtime.state['pending'].get(runtime.current,{})
        if runtime.state.get('blocked_until',0)<=runtime.now():
            runtime.defer('KOL row queue incomplete; continue unverified rows',300)
        elif runtime.current not in runtime.deferred:
            runtime.defer(pending.get('reason','Meta cooldown'),runtime.state['blocked_until']-runtime.now())
    for item in items:
        if queue.state['rows'][item['key']].get('cycle')==queue.cycle:success.update(item['keys'])
    return success


def run_kind(env,output,apply,kind):
    from kol_tracker import tiktok_daily
    from update_meta_tracker import run as ads_meta
    from kol_organic_meta_totals import run as organic_meta
    from kol_organic_tiktok import run as organic_tiktok
    from kol_organic_youtube import run as organic_youtube
    from contextlib import nullcontext
    writers={'ads-meta':ads_meta,'ads-tiktok':tiktok_daily,'organic-meta':organic_meta,
             'organic-tiktok':organic_tiktok,'organic-youtube':organic_youtube}
    runtime=env.get('_KOL_META_RUNTIME')
    # One Meta tab cannot spend the whole invocation ahead of the other tab.
    with runtime.lane(kind,20) if runtime and kind in ('ads-meta','organic-meta') else nullcontext():
        return run_rows(env,output,apply,kind,writers[kind])
