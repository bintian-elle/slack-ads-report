"""Cache approved ANC aggregate reports for local Dashboard Builder projects.

Runs only fixed read-only CLI operations over SSH; credentials remain on ANC.
Raw aggregate results and normalized caches are private and project-separated.
"""
from __future__ import annotations
import argparse, base64, json, os, re, shlex, subprocess, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
from zoneinfo import ZoneInfo

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from builder_cache_policy import force_refresh_requested, period_closed, captured_closed_period, closure_metadata
DASH=Path(os.environ.get('DASHBOARD_ROOT',BASE.parent/'dashboard/emw3-dashboard'))
HOST='EMW3_ANC_AI'
SKILLS='/home/ubuntu/.agents/skills'
ACCOUNTS={
 'biceek':{'ga4':('ga4.biceek','497540678'),'google_ads':('google-ads.biceek','3508380986'),'meta_ads':('meta-ads.biceek','4415468505361063'),'store':('shopify.biceek','81924522263'),'edm':('klaviyo.biceek','THABgs')},
 'ringconn':{'ga4':('ga4.ringconn','377025799')},
}
PLATFORMS={'ga4':'ga4','google_ads':'google-ads','meta_ads':'meta-ads','store':'shopify','edm':'klaviyo'}
# This wrapper only executes the locally selected fixed CLI; it expands only
# exports referenced by that trusted result, never paths from marketing text.
REMOTE=r'''
import sys,json,base64,subprocess,pathlib
spec=json.loads(base64.b64decode(sys.argv[1]));p=spec['platform']
allowed={'ga4':[['reports','query']],'google-ads':[['reports','query']],'meta-ads':[['insights','query']],'shopify':[['reports','query']],'klaviyo':[['reports','summary']]}
if spec['command'] not in allowed.get(p,[]):raise ValueError('Unapproved operation')
cli='/home/ubuntu/.agents/skills/anc-'+p+'-data-readonly/scripts/anc-'+p
r=subprocess.run([cli]+spec['command']+['--account',spec['account'],'--input-file','-'],input=json.dumps(spec['input']),text=True,capture_output=True)
try:out=json.loads(r.stdout)
except Exception:print(json.dumps({'status':'adapter_failed','message':'CLI did not return JSON','exitCode':r.returncode}));sys.exit(0)
seen=set();root=pathlib.Path('/home/ubuntu/work/anc-data-tools/.local/exports').resolve()
def expand(value,depth=0):
 if depth>12:raise ValueError('Export nesting exceeded')
 if isinstance(value,list):return[expand(x,depth+1) for x in value]
 if not isinstance(value,dict):return value
 path=value.get('export_file')
 if path:
  f=pathlib.Path(path).resolve(strict=True)
  if root not in f.parents or f in seen or f.stat().st_size>64*1024*1024:raise ValueError('Invalid export reference')
  seen.add(f)
  if f.suffix=='.json':loaded=json.loads(f.read_text())
  elif f.suffix=='.jsonl':
   lines=f.read_text().splitlines();head=json.loads(lines.pop(0))
   if set(head)!={'metadata'}:raise ValueError('Missing export metadata')
   loaded=[json.loads(x) for x in lines if x.strip()]
   if 'count' in value and len(loaded)!=value['count']:raise ValueError('Export count mismatch')
  else:raise ValueError('Unsupported export format')
  if isinstance(loaded,dict):value={**value,**loaded};value.pop('export_file',None)
  else:value={**value,'data':loaded};value.pop('export_file',None)
  value['_expanded_export']=str(f)
 return{k:expand(v,depth+1) for k,v in value.items()}
try:out=expand(out)
except Exception:out={'status':'export_invalid','message':'Full report export could not be verified','account_ref':spec['account']}
print(json.dumps(out,separators=(',',':')))
'''

def num(v):
 if v is None:return None
 try:
  n=Decimal(str(v));return float(n) if n.is_finite() else None
 except Exception:return None

def total(rows,key):
 values=[num(r.get(key)) for r in rows]
 return float(sum(Decimal(str(v)) for v in values)) if values and all(v is not None for v in values) else None

def iso(v):
 text=str(v)
 if re.fullmatch(r'\d{8}',text):return text[:4]+'-'+text[4:6]+'-'+text[6:8]
 return text[:10]

def last_week(today):
 current=today-timedelta(days=(today.weekday()-1)%7)
 return (current-timedelta(days=7)).isoformat(),(current-timedelta(days=1)).isoformat()

def default_ranges():
 a,b=last_week(datetime.now(ZoneInfo('America/Chicago')).date());s,e=date.fromisoformat(a),date.fromisoformat(b)
 def year(d):
  try:return d.replace(year=d.year-1)
  except ValueError:return d.replace(year=d.year-1,day=28)
 return[(a,b),((s-timedelta(days=7)).isoformat(),(e-timedelta(days=7)).isoformat()),(year(s).isoformat(),year(e).isoformat())]

def validate_ranges(ranges):
 if not isinstance(ranges,list) or not 1<=len(ranges)<=3:raise ValueError('Choose one to three periods')
 for r in ranges:
  if not isinstance(r,(list,tuple)) or len(r)!=2:raise ValueError('Invalid period')
  a,b=map(date.fromisoformat,r)
  if a>b or (b-a).days>92:raise ValueError('Period outside local report budget')
 return ranges

def call(project,source,start,end,kind='daily',force=False):
 force=force_refresh_requested(force)
 account,identity=ACCOUNTS[project][source];root=DASH/'.state/ai-dashboard/projects'/project
 raw=root/'raw'/f'{source}-{start}-{end}-{kind}.json'
 if raw.exists() and not force:
  cached=json.loads(raw.read_text())
  # Retry transient busy/transport/budget errors on a later explicit refresh;
  # stable successful or denied reads are reused rather than hammering APIs.
  if cached.get('status') in ('access_denied','auth_failed','auth_expired','scope_missing'):return cached
  captured={'fetchedAt':cached.get('executed_at')}
  if cached.get('status') in ('ok','empty','partial') and captured_closed_period(captured,end,cached.get('timezone')):return cached
 end_exclusive=(date.fromisoformat(end)+timedelta(days=1)).isoformat();period={'start':start,'end':end_exclusive}
 if source=='ga4':
  params={'period':period,'dimensions':[] if kind=='totals' else ['sessionDefaultChannelGroup'] if kind=='channel' else ['date'],'metrics':['sessions','ecommercePurchases','purchaseRevenue'],'page_size':100,'max_rows':1000}
  if project=='ringconn':params['dimensionFilter']={'filter':{'fieldName':'hostName','inListFilter':{'values':['ringconn.com','www.ringconn.com']}}}
  command=['reports','query']
 elif source=='google_ads':params={'resource':'customer','period':period,'fields':['segments.date','metrics.impressions','metrics.clicks','metrics.cost_micros','metrics.conversions','metrics.conversions_value'],'order_by':[{'field':'segments.date'}],'max_rows':100};command=['reports','query']
 elif source=='meta_ads':params={'period':period,'level':'account','fields':['spend','impressions','clicks','actions','action_values'],'time_increment':1,'max_rows':100,'attribution_windows':['7d_click','1d_view'],'action_report_time':'impression'};command=['insights','query']
 elif source=='store':params={'start':start,'end':end_exclusive,'metrics':['orders','total_sales'],'timeseries':'day'};command=['reports','query']
 elif source=='edm':params={'period':period};command=['reports','summary']
 else:raise ValueError('Source not approved')
 spec={'platform':PLATFORMS[source],'account':account,'command':command,'input':params}
 encoded=base64.b64encode(json.dumps(spec).encode()).decode()
 command='python3 -c '+shlex.quote(REMOTE)+' '+shlex.quote(encoded)
 transport=os.environ.get('AI_DASHBOARD_ANC_TRANSPORT','ssh')
 if transport not in ('ssh','local'):raise ValueError('Choose the fixed ssh or local ANC transport')
 argv=[sys.executable,'-c',REMOTE,encoded] if transport=='local' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',HOST,command]
 result=subprocess.run(argv,text=True,capture_output=True)
 if result.returncode:out={'status':'transport_failed','account_ref':account,'message':'Approved ANC reader was unreachable'}
 else:
  try:out=json.loads(result.stdout)
  except Exception:out={'status':'adapter_failed','account_ref':account,'message':'Approved ANC reader returned invalid JSON'}
 # No auth metadata is needed in a presentation data fixture.
 for key in ['auth_evidence','auth_events','permissions']:out.pop(key,None)
 out['_query']={**spec,'expectedIdentity':identity};private_write(raw,out)
 return out

def private_write(path,payload):
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')));temporary.chmod(0o600);temporary.replace(path)

def valid(result,account,identity):
 if result.get('identity_verified') is not True or result.get('account_ref')!=account:return False
 actual=result.get('resource_id') or result.get('identity',{}).get('id')
 return str(actual).split('/')[-1]==identity and result.get('status') in ('ok','empty','partial')

def complete(result):
 return result.get('status') in ('ok','empty') and result.get('coverage',{}).get('complete') is True

def rows_of(result):
 rows=result.get('data',[])
 if not isinstance(rows,list):raise ValueError('Expected report rows')
 count=result.get('count',len(rows))
 if count!=len(rows):raise ValueError('Incomplete report preview')
 return rows

def ga4_row(r,label=None):
 return{'date':iso(r.get('date','')),'label':label or str(r.get('sessionDefaultChannelGroup','')),'sessions':num(r.get('sessions')),'conversions':num(r.get('ecommercePurchases')),'revenue':num(r.get('purchaseRevenue'))}

def normalize(project,source,start,end,results):
 account,identity=ACCOUNTS[project][source];r=results['daily'];meta=r.get('identity') or {};currency=r.get('currency') or meta.get('currency');zone=r.get('timezone') or meta.get('timezone')
 payload={'schemaVersion':1,'project':project,'source':source,'start':start,'end':end,'accountRef':account,'accountId':identity,'currency':currency,'timezone':zone,'fetchedAt':r.get('executed_at') or datetime.now(timezone.utc).isoformat(),'origin':'ANC fixed read-only reporting CLI','status':'unavailable','complete':False,'rows':[],'reason':r.get('message') or 'Account reporting data unavailable','query':r.get('_query',{}).get('input',{}),'sourceStatus':r.get('status'),'identityVerified':r.get('identity_verified') is True,'coverage':r.get('coverage',{})}
 if not valid(r,account,identity):return payload
 payload['status']='ready';payload['complete']=complete(r);payload.pop('reason',None)
 if source=='ga4':
  payload['rows']=[ga4_row(x) for x in rows_of(r)];t=results['totals'];c=results['channel']
  payload['supportedGroups']=['day','overview','channel']
  if valid(t,account,identity) and complete(t) and rows_of(t):payload['totals']={k:v for k,v in ga4_row(rows_of(t)[0]).items() if k not in ('date','label')}
  if valid(c,account,identity) and complete(c):payload['breakdowns']={'channel':[{k:v for k,v in ga4_row(x,str(x.get('sessionDefaultChannelGroup','Unknown'))).items() if k!='date'} for x in rows_of(c)]}
  payload['complete']=payload['complete'] and valid(t,account,identity) and complete(t)
  payload['metadata']=r.get('response_metadata',{});payload['hostFilter']=['ringconn.com','www.ringconn.com'] if project=='ringconn' else None
 elif source=='google_ads':
  raw=rows_of(r)
  if not raw:
   payload.update(status='unavailable',complete=False,reason='Successful account query returned no reporting rows; spend is unavailable, not zero.');return payload
  for row in raw:
   m=row.get('metrics',{});payload['rows'].append({'date':iso(row.get('segments',{}).get('date')),'spend':num(m.get('costMicros'))/1e6 if num(m.get('costMicros')) is not None else None,'impressions':num(m.get('impressions')),'clicks':num(m.get('clicks')),'conversions':num(m.get('conversions')),'revenue':num(m.get('conversionsValue'))})
  if complete(r):
   fields=['spend','impressions','clicks','conversions','revenue']
   payload['totals']={key:total(payload['rows'],key) for key in fields}
   # Exhausted, identity-verified Google customer queries omit zero-activity days.
   seen={row['date'] for row in payload['rows']}
   for offset in range((date.fromisoformat(end)-date.fromisoformat(start)).days+1):
    day=(date.fromisoformat(start)+timedelta(days=offset)).isoformat()
    if day not in seen:payload['rows'].append({'date':day,**{key:0.0 for key in fields}})
   payload['rows'].sort(key=lambda row:row['date'])
 elif source=='meta_ads':
  def action(row,key):return next((num(x.get('value')) for x in row.get(key,[]) if x.get('action_type')=='offsite_conversion.fb_pixel_purchase'),None)
  payload['rows']=[{'date':iso(x.get('date_start')),'spend':num(x.get('spend')),'impressions':num(x.get('impressions')),'clicks':num(x.get('clicks')),'revenue':action(x,'action_values'),'conversions':action(x,'actions')} for x in rows_of(r)]
  payload['attribution']={'windows':['7d_click','1d_view'],'actionReportTime':'impression','purchaseActionType':'offsite_conversion.fb_pixel_purchase'}
 elif source=='store':
  data=r.get('data',{});raw=data.get('rows',[])
  if not isinstance(raw,list) or len(raw)!=r.get('count'):raise ValueError('Shopify daily rows incomplete')
  payload['rows']=[{'date':iso(x.get('day')),'revenue':num(x.get('total_sales')),'conversions':num(x.get('orders'))} for x in raw]
  payload['metricBasis']=r.get('metric_basis');payload['totals']={key:total(payload['rows'],key) for key in ['revenue','conversions']}
 elif source=='edm':
  totals={'revenue':0.0,'conversions':0.0,'delivered':0.0,'clicks':0.0,'opens':0.0,'campaignRevenue':0.0,'flowRevenue':0.0};channels={};campaigns=[];payload['supportedGroups']=['overview','channel','campaign'];payload['metricBasis']=r.get('metric_basis');payload['uniquesBasis']=r.get('uniques_basis')
  for kind in ['campaign','flow']:
   part=r.get('data',{}).get(kind,{})
   if not complete({**part,'status':'ok'}) or part.get('count')!=len(part.get('data',[])):payload.update(complete=False,status='unavailable',reason='Klaviyo component report was incomplete');return payload
   for row in part.get('data',[]):
    stats=row.get('statistics',{});g=row.get('groupings',{});channel='SMS' if str(g.get('send_channel','')).lower()=='sms' else 'Email';values={'revenue':num(stats.get('conversion_value')),'conversions':num(stats.get('conversions')),'delivered':num(stats.get('delivered')),'clicks':num(stats.get('clicks_unique')),'opens':num(stats.get('opens_unique')),'campaignRevenue':num(stats.get('conversion_value')) if kind=='campaign' else 0.0,'flowRevenue':num(stats.get('conversion_value')) if kind=='flow' else 0.0}
    if any(v is None for v in values.values()):payload.update(complete=False,status='unavailable',reason='Klaviyo summary metric missing');return payload
    for k,v in values.items():totals[k]+=v
    bucket=channels.setdefault(channel,{'label':channel,**{k:0.0 for k in totals}})
    for k,v in values.items():bucket[k]+=v
    campaigns.append({'label':kind.title()+' · '+str(g.get(kind+'_message_id') or g.get(kind+'_id')),**values})
  if not campaigns:payload.update(status='unavailable',complete=False,reason='Successful Klaviyo query returned no message reporting rows; attribution is unavailable, not zero.');return payload
  payload['totals']=totals;payload['breakdowns']={'channel':list(channels.values()),'campaign':campaigns}
 return payload

def collect(project,ranges,force=False):
 force=force_refresh_requested(force)
 summary=[]
 for source in ACCOUNTS[project]:
  auth_failure=None
  for start,end in ranges:
   target=DASH/'.state/ai-dashboard/projects'/project/'overview'/f'{source}-{start}-{end}.json'
   if target.exists() and not force:
    old=json.loads(target.read_text())
    if old.get('complete') and old.get('status')=='ready' and captured_closed_period(old,end,old.get('timezone')):summary.append((project,source,start,'cached',len(old.get('rows',[]))));continue
   try:
    if auth_failure:r=auth_failure
    else:r=call(project,source,start,end,force=force)
    if r.get('status') in ('access_denied','auth_failed','auth_expired','scope_missing'):auth_failure=r
    results={'daily':r}
    if source=='ga4' and valid(r,*ACCOUNTS[project][source]):
     results['totals']=call(project,source,start,end,'totals',force);results['channel']=call(project,source,start,end,'channel',force)
    data=normalize(project,source,start,end,results)
   except Exception as error:
    data={'project':project,'source':source,'start':start,'end':end,'accountRef':ACCOUNTS[project][source][0],'accountId':ACCOUNTS[project][source][1],'status':'unavailable','complete':False,'rows':[],'fetchedAt':datetime.now(timezone.utc).isoformat(),'origin':'ANC fixed read-only reporting CLI','reason':'Aggregate adapter could not verify report ('+type(error).__name__+').'}
   data.update(closure_metadata(data,end,data.get('timezone')))
   if data.get('status')!='ready' and target.exists():
    previous=json.loads(target.read_text())
    if previous.get('status')=='ready' and previous.get('complete'):
     private_write(target.parent/'.refresh-errors'/target.name,data);summary.append((project,source,start,'stale-preserved',len(previous.get('rows',[]))));print(*summary[-1],flush=True);continue
   private_write(target,data);summary.append((project,source,start,data['status'],len(data.get('rows',[]))));print(*summary[-1],flush=True)
 return summary

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--project',choices=['biceek','ringconn','all'],default='all');parser.add_argument('--force',action='store_true');args=parser.parse_args()
 ranges=validate_ranges(json.loads(os.environ['AI_DASHBOARD_RANGES']) if os.environ.get('AI_DASHBOARD_RANGES') else default_ranges())
 for project in (ACCOUNTS if args.project=='all' else [args.project]):collect(project,ranges,args.force)
