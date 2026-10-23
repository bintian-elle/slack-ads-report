"""KOL-only, post-identity checkpoints committed after native write verification."""
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from kol_meta_runtime import MetaDeferred, next_boundary, reporting_window
from kol_failures import Outcome,RowReport,RowResult,classify_exception


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
        fingerprint=item.get('fingerprint')
        input_changed=bool(fingerprint and old.get('fingerprint') and old['fingerprint']!=fingerprint)
        if fingerprint:old['fingerprint']=fingerprint
        if input_changed:
            old.pop('unavailable_cycle',None);old.pop('cycle',None);old['retry_at']=0
        old['observed_status']=status
        if paused:
            old.setdefault('paused_since',self.today.isoformat())
        else:old.pop('paused_since',None)
        if old.get('unavailable_cycle')==self.cycle:return None
        if changed:old.pop('cycle',None)
        if not changed and old.get('cycle')==self.cycle:return None
        if old.get('retry_at',0)>self.now() and not changed:return None
        age=(self.today-datetime.fromisoformat(old['paused_since']).date()).days if paused else 0
        if paused and age>=7 and old.get('fetched_on'):
            last=datetime.fromisoformat(old['fetched_on']).date()
            if (self.today-last).days<7:return None
        priority=0 if status=='ACTIVE' else 2 if paused and age<7 else 3 if paused else 1
        return priority

    def run(self,items,writer,stop=None,limit=20):
        # One post can appear on more than one physical sheet row. Merge before
        # preparing state so conflicting row statuses cannot reset its backoff.
        groups={}
        for item in items:groups.setdefault(item['key'],[]).append(item)
        merged=[]
        for group in groups.values():
            item=dict(min(group,key=lambda x:x.get('row',0)))
            statuses={x.get('status') for x in group}
            item['status']='ACTIVE' if 'ACTIVE' in statuses else 'PAUSED' if statuses and statuses<={'PAUSED','ADSET_PAUSED','CAMPAIGN_PAUSED'} else None
            if any(x.get('fingerprint') for x in group):
                item['fingerprint']=hashlib.sha256(json.dumps(sorted(x.get('fingerprint','') for x in group)).encode()).hexdigest() if len(group)>1 else group[0].get('fingerprint')
            merged.append(item)
        items=merged
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
            stop_after=False
            try:
                result=writer(item)
                outcome=result if isinstance(result,Outcome) else Outcome(success=bool(result))
            except Exception as error:
                retryable,reason=classify_exception(error)
                outcome=Outcome(reason=reason,retryable=retryable,retry_at=getattr(error,'retry_at',0))
                stop_after=isinstance(error,MetaDeferred)
            entry['attempts']=0 if outcome.success else entry.get('attempts',0)+1
            entry.update(inflight=False,retryable=outcome.retryable,reason='' if outcome.success else outcome.reason)
            entry['retry_at']=(outcome.retry_at if outcome.retry_at>self.now() else self.now()+1800) if outcome.retryable and not outcome.success else 0
            if outcome.success:
                entry.update(cycle=self.cycle,fetched_on=self.today.isoformat())
                entry.pop('unavailable_cycle',None)
                completed.add(item['key'])
            elif not outcome.retryable:entry['unavailable_cycle']=self.cycle
            self.save()
            print('KOL row %s: %s'%(item.get('row','?'),'verified' if outcome.success else 'pending retry' if outcome.retryable else 'unavailable this cycle: '+outcome.reason),flush=True)
            if stop_after:break
        # Includes rows temporarily backed off, not merely this invocation's work.
        self.remaining=sum(1 for item in items if self.state['rows'][item['key']].get('cycle')!=self.cycle
            and self.state['rows'][item['key']].get('unavailable_cycle')!=self.cycle
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
    from kol_organic_meta import observed_meta_status
    for row,ids in matches.items():
        statuses={observed_meta_status(byid[aid]) for aid in ids}
        result[row]='ACTIVE' if 'ACTIVE' in statuses else 'PAUSED' if statuses=={'PAUSED'} else None
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
        print('KOL metrics held until next daily boundary',flush=True);return RowResult()
    if runtime and kind in ('ads-meta','organic-meta') and runtime.state.get('blocked_until',0)>runtime.now():
        runtime.defer(runtime.state.get('blocked_reason','Meta cooldown'),runtime.state['blocked_until']-runtime.now())
        return RowResult()
    if kind in ('ads-meta','organic-meta') and env.get('KOL_ORGANIC_SHEETS_LINK'):
        from kol_business_discovery_phase import prepare_views_phase
        prepare_views_phase(env,output,apply)
    discovery_retry=False
    if kind=='organic-meta' and target_keys is None:
        from kol_organic_discovery import run as discover
        try:discover(env,output,apply)
        except (RuntimeError,requests.RequestException) as error:
            retryable,reason=classify_exception(error)
            discovery_retry=retryable
            if runtime and retryable:runtime.defer(reason,1800)
    _,endpoint,_,_,values=open_tab(env,kind)
    aliases=load_link_aliases(env) if link_aliases is None else link_aliases
    statuses={}
    if kind in ('ads-meta','organic-meta'):
        from kol_organic_meta_totals import ad_inventory
        try:statuses=api_statuses(env,kind,values,ad_inventory(env))
        except (RuntimeError,requests.RequestException) as error:
            retryable,reason=classify_exception(error)
            if runtime and retryable:runtime.defer(reason,1800)
            if runtime and (runtime.sent>=runtime.budget or runtime.state.get('blocked_until',0)>runtime.now()):return RowResult()
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
        columns={'ads-meta':(1,6,7),'ads-tiktok':(0,2,3,13),'organic-meta':(0,3,4,17),'organic-tiktok':(0,2),'organic-youtube':(0,2)}[kind]
        fingerprint=hashlib.sha256(json.dumps([row[c] if len(row)>c else '' for c in columns],sort_keys=True).encode()).hexdigest()
        items.append({'key':primary[0],'row':n,'keys':keys,'status':statuses.get(n),'fingerprint':fingerprint})
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
        report=RowReport()
        row_env=dict(env,_KOL_ROW_MODE=True,_KOL_LIVE_METRICS=target_keys is not None,_KOL_ROW_REPORT=report)
        try:
            if runtime:
                with runtime.stage(runtime.current,resume=True),runtime.row_context(report):
                    result=writer(row_env,target,apply,target_keys=item['keys'],link_aliases=aliases) or set()
            else:result=writer(row_env,target,apply,target_keys=item['keys'],link_aliases=aliases) or set()
        except MetaDeferred as error:
            error.retry_at=report.retry_at
            raise
        except Exception as error:
            retryable,reason=classify_exception(error);report.record(reason,retryable)
            return Outcome(False,'; '.join(report.reasons),report.retryable,report.retry_at)
        done=matches_keys(item['keys'],result) and not report.incomplete
        if done:success.update(item['keys'])
        return Outcome(done,'; '.join(report.reasons) or 'No usable metrics returned; existing values retained',report.retryable,report.retry_at)
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
    elif runtime and not discovery_retry:
        runtime.deferred.discard(runtime.current)
        runtime.state['pending'].pop(runtime.current,None)
        runtime.save()
    unavailable={}
    for item in items:
        if queue.state['rows'][item['key']].get('cycle')==queue.cycle:success.update(item['keys'])
        if queue.state['rows'][item['key']].get('unavailable_cycle')==queue.cycle:
            unavailable.update({key:queue.state['rows'][item['key']]['reason'] for key in item['keys']})
    return RowResult(success,unavailable)


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
