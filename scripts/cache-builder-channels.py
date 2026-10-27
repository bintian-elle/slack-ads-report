"""Read Bluevua account reports into private local cache. No publishing or ad mutations."""
import sys, json, os, re, csv, io, zipfile, time
from pathlib import Path
from datetime import date, timedelta, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from builder_cache_policy import force_refresh_requested, period_closed, reusable_cache, closure_metadata
from dotenv import dotenv_values
from weekly_report.sources import request, pages
from bing_service import BingAdsService
from reddit_service import RedditAdsService
from chatgpt_ads_service import ChatGPTAdsService
BASE=Path(__file__).resolve().parents[1]
DASH=Path(os.environ.get('DASHBOARD_ROOT',BASE.parent/'dashboard/emw3-dashboard'))
ENV={**dotenv_values(BASE/'.env'),**dotenv_values(DASH/'.env.local')}
ROOT=DASH/'.state/ai-dashboard/overview';ROOT.mkdir(parents=True,exist_ok=True)
RANGES=json.loads(os.environ['AI_DASHBOARD_RANGES']) if os.environ.get('AI_DASHBOARD_RANGES') else [('2026-10-20','2026-10-26'),('2026-10-13','2026-10-19'),('2025-10-20','2025-10-26')]
if len(RANGES)>3 or any(len(r)!=2 or any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}',d) for d in r) for r in RANGES):raise ValueError('Invalid reporting ranges')
N=lambda v:float(v or 0)
def days(start,end):
 d=date.fromisoformat(start)
 while d.isoformat()<=end:
  yield d.isoformat();d+=timedelta(days=1)
def actions(row,key):
 values={v['action_type']:N(v.get('value')) for v in row.get(key,[])}
 return next((values[k] for k in ['omni_purchase','offsite_conversion.fb_pixel_purchase','purchase'] if k in values),0)
def fetch(source,start,end):
 if source=='meta_ads':
  from urllib.parse import urlencode
  account='act_'+ENV['META_AD_ACCOUNT_ID'].removeprefix('act_')
  def url(path,params):return 'https://graph.facebook.com/v25.0/'+path+'?'+urlencode({**params,'access_token':ENV['META_ACCESS_TOKEN']})
  meta=request(url(account,{'fields':'name,currency,timezone_name'}))
  if 'bluevua' not in str(meta.get('name','')).lower():raise ValueError('Bluevua account identity could not be verified')
  raw=pages(url(account+'/insights',{'level':'account','time_increment':1,'time_range':json.dumps({'since':start,'until':end}),'fields':'date_start,spend,impressions,clicks,actions,action_values','limit':500}))
  return [dict(date=r['date_start'],spend=N(r.get('spend')),revenue=actions(r,'action_values'),conversions=actions(r,'actions'),impressions=N(r.get('impressions')),clicks=N(r.get('clicks'))) for r in raw],meta['currency'],meta['timezone_name'],raw
 if source=='tiktok_ads':
  from builder_tiktok import fetch_daily_reporting
  return fetch_daily_reporting(ENV,start,end,ROOT/'tiktok-reporting-context.json')
 if source=='reddit_ads':
  service=RedditAdsService(BASE/ENV.get('REDDIT_CREDENTIALS_FILE','credentials/reddit.json'));token=service._refresh_access_token();zone=service._fetch_account_timezone(token)
  headers={'Authorization':'Bearer '+token,'User-Agent':'bluevua-dashboard-builder/1.0'};base='https://ads-api.reddit.com/api/v3/ad_accounts/'+service.ad_account_id
  meta=request(base,headers=headers)
  if 'bluevua' not in json.dumps(meta.get('data',{})).lower():raise ValueError('Reddit Bluevua account identity unverified')
  begin=datetime.combine(date.fromisoformat(start),dt_time.min,tzinfo=ZoneInfo(zone)).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
  finish=datetime.combine(date.fromisoformat(end)+timedelta(days=1),dt_time.min,tzinfo=ZoneInfo(zone)).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
  raw=pages(base+'/reports',headers=headers,kind='reddit',body={'data':{'starts_at':begin,'ends_at':finish,'time_zone_id':zone,'breakdowns':['DATE'],'fields':['SPEND','IMPRESSIONS','CLICKS','CONVERSION_PURCHASE_CLICKS','CONVERSION_PURCHASE_VIEWS','CONVERSION_PURCHASE_TOTAL_VALUE']}})
  return [dict(date=r['date'][:10],spend=N(r.get('spend'))/1e6,revenue=N(r.get('conversion_purchase_total_value'))/100,conversions=N(r.get('conversion_purchase_clicks'))+N(r.get('conversion_purchase_views')),impressions=N(r.get('impressions')),clicks=N(r.get('clicks'))) for r in raw],meta['data'].get('currency','USD'),zone,raw
 if source=='microsoft_ads':
  s=BingAdsService(ENV['GOOGLE_CLIENT_ID'],ENV['GOOGLE_CLIENT_SECRET'],ENV['GOOGLE_REFRESH_TOKEN'],ENV['BING_ADS_DEVELOPER_TOKEN']);token=s._refresh_access_token();accounts=s._find_accounts(token,s._get_user_id(token));accounts=[a for a in accounts if 'bluevua' in a.get('Name','').lower()]
  if not accounts:raise ValueError('No verified Bluevua Microsoft account')
  if len(accounts)!=1:raise ValueError('Multiple Microsoft accounts require explicit mapping')
  account=accounts[0];out=[]
  for day in days(start,end):
   m=s._download_report(token,account,date.fromisoformat(day));out.append(dict(date=day,spend=float(m.spend),revenue=float(m.revenue)))
  return out,account.get('CurrencyCode','USD'),account['TimeZone'],{'account':account,'rows':out}
 if source=='chatgpt_ads':
  from urllib.parse import urlencode
  import requests
  def get(url,headers):
   response=requests.get(url,headers=headers,timeout=30)
   if not response.ok:raise ValueError('ChatGPT Ads report HTTP '+str(response.status_code))
   return response.json()
  s=ChatGPTAdsService(ENV['CHATGPT_ADS_TOKEN']);meta=get('https://api.ads.openai.com/v1/ad_account',headers=s.headers)
  if 'bluevua' not in meta.get('name','').lower():raise ValueError('ChatGPT Ads Bluevua account identity unverified')
  params=[('aggregation_level','ad_account'),('time_granularity','daily'),('time_ranges[]',json.dumps({'type':'date_range','since':start,'until':end,'timezone':meta['timezone']}))]+[('fields[]',f) for f in ['metadata.readable_time','ad_account.spend','order_created_attributed_sales','order_created_attributed_sales_currency','order_created_roas']]
  raw=get('https://api.ads.openai.com/v1/ad_account/insights?'+urlencode(params),headers=s.headers)
  rows=raw.get('data')
  if not isinstance(rows,list) or not rows:raise ValueError('No ChatGPT Ads report rows for requested period')
  currencies={r.get('order_created_attributed_sales_currency') for r in rows}
  if currencies!={'USD'}:raise ValueError('ChatGPT Ads currency is missing or not USD')
  out=[dict(date=r['readable_time'][:10],spend=N(r['spend']),revenue=N(r['order_created_attributed_sales'])) for r in rows]
  return out,'USD',meta['timezone'],{'metadata':meta,'report':raw}
 raise ValueError('Unknown source')
def run(source,start,end):
 target=ROOT/f'{source}-{start}-{end}.json'
 if target.exists():
  old=json.loads(target.read_text())
  age=(datetime.now(timezone.utc)-datetime.fromisoformat(old['fetchedAt'])).total_seconds()
  complete=len({r['date'] for r in old.get('rows',[]) if start<=r['date']<=end})==len(list(days(start,end)))
  if reusable_cache(old,end,old.get('timezone'),complete):return source,start,'cached',len(old['rows'])
 payload=dict(source=source,start=start,end=end,fetchedAt=datetime.now(timezone.utc).isoformat(),origin='Official account reporting API')
 try:
  rows,currency,zone,raw=fetch(source,start,end)
  # Missing dates are kept missing, never converted into zero activity.
  payload.update(status='ready',rows=rows,currency=currency,timezone=zone,evidence=raw);payload.update(closure_metadata(payload,end,zone))
 except Exception as e:
  # Error messages may contain request URLs/tokens; only retain safe error class/status.
  reason='Account report unavailable; check local credentials, permissions or API coverage.'
  if isinstance(e,ValueError):reason=str(e)[:180]
  payload.update(status='unavailable',rows=[],error=type(e).__name__,reason=reason)
 if payload['status']=='unavailable' and target.exists():
  previous=json.loads(target.read_text())
  if previous.get('status')=='ready':
   errors=ROOT/'.refresh-errors';errors.mkdir(exist_ok=True,mode=0o700);error=errors/target.name;error.write_text(json.dumps(payload));error.chmod(0o600)
   return source,start,'stale-preserved',len(previous.get('rows',[]))
 tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(payload));tmp.chmod(0o600);tmp.replace(target)
 return source,start,payload['status'],len(payload['rows'])
if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=5) as pool:
  futures=[pool.submit(run,s,a,b) for s in ['meta_ads','tiktok_ads','reddit_ads','microsoft_ads','chatgpt_ads'] for a,b in RANGES]
  for f in as_completed(futures):print(*f.result(),flush=True)
