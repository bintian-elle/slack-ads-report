"""Read-only comparison of Tracker launch/location to exact-code matched ads."""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv
from sync_creator_tracker import BASE, get_json, graph_pages, read_sheet
from update_meta_tracker import exact_mapping


def main():
    load_dotenv(BASE/'.env'); env=dict(os.environ)
    out=BASE/'data/processed/kol_tracker_audit/dimensions';out.mkdir(parents=True,exist_ok=True)
    sheet=read_sheet(env)
    rows=[(i+1,r+['']*max(0,22-len(r))) for i,r in enumerate(sheet['values']) if i>1 and len(r)>1 and r[1]]
    s=requests.Session(); s.headers['Authorization']='Bearer '+env['META_ACCESS_TOKEN']
    base='https://graph.facebook.com/'+env.get('KOL_TRACKER_META_API_VERSION','v23.0')
    account='act_'+env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    info=get_json(s,base+'/'+account,{'fields':'id,name,timezone_name'})
    ads=json.loads((out/'ads.json').read_text()) if '--cached-ads' in sys.argv else graph_pages(s,base+'/'+account+'/ads',{'fields':'id,name,created_time,effective_status,campaign{id,name,effective_status},adset{id,name,start_time,effective_status},creative{id,branded_content}','limit':100})
    (out/'ads.json').write_text(json.dumps(ads,ensure_ascii=False,indent=2))
    print('Ads retrieved',len(ads),flush=True)
    mapping,skips,conflicts=exact_mapping(rows,ads)
    byid={a['id']:a for a in ads}; report=[]
    for number,row in rows:
        matched=[byid[aid] for aid in mapping.get(number,[])]
        date=((datetime(1899,12,30)+timedelta(days=row[2])).date().isoformat() if isinstance(row[2],(int,float)) else str(row[2]))
        created={tz: sorted({datetime.strptime(a['created_time'],'%Y-%m-%dT%H:%M:%S%z').astimezone(ZoneInfo(tz)).date().isoformat() for a in matched if a.get('created_time')}) for tz in [info['timezone_name'],'America/Chicago','UTC']}
        active=[a for a in matched if a.get('effective_status')=='ACTIVE']
        report.append({'row':number,'creator':row[1],'sheet_location':row[0],'sheet_launch':date,'matched_ads':matched,
                       'creation_dates':created,'active_ad_ids':[a['id'] for a in active],
                       'campaign_names':sorted({a.get('campaign',{}).get('name','') for a in matched}),
                       'adset_names':sorted({a.get('adset',{}).get('name','') for a in matched}),
                       'active_adset_names':sorted({a.get('adset',{}).get('name','') for a in active})})
    # Representative rows only: daily Insights are evidence of first OBSERVED
    # delivery within the API reporting window, not a universal launch field.
    samples={3,87,93,108,114,123,127,128}
    jobs=[a['id'] for r in report if r['row'] in samples for a in r['matched_ads']]
    def first_delivery(aid):
        try:
            values=graph_pages(s,base+'/'+aid+'/insights',{'fields':'ad_id,date_start,impressions,spend','time_range':json.dumps({'since':'2025-01-01','until':'2026-09-30'}),'time_increment':1,'limit':100})
            dates=[r['date_start'] for r in values if float(r.get('impressions',0))>0 or float(r.get('spend',0))>0]
            return aid,{'first_observed_delivery':min(dates) if dates else None}
        except RuntimeError as error: return aid,{'error':str(error)}
    with ThreadPoolExecutor(max_workers=4) as pool: deliveries=dict(pool.map(first_delivery,jobs))
    for item in report:
        if item['row'] in samples:
            item['delivery_evidence']={a['id']:deliveries[a['id']] for a in item['matched_ads']}
            dates=[v['first_observed_delivery'] for v in item['delivery_evidence'].values() if v.get('first_observed_delivery')]
            item['earliest_observed_delivery']=min(dates) if dates else None
    stats={tz: {'dated_matched_rows':sum(bool(r['sheet_launch']) and bool(r['matched_ads']) for r in report),
                'equals_earliest_creation':sum(bool(r['sheet_launch']) and bool(r['creation_dates'][tz]) and r['sheet_launch']==r['creation_dates'][tz][0] for r in report),
                'equals_any_creation':sum(bool(r['sheet_launch']) and r['sheet_launch'] in r['creation_dates'][tz] for r in report)} for tz in [info['timezone_name'],'America/Chicago','UTC']}
    out=BASE/'data/processed/kol_tracker_audit/dimensions';out.mkdir(parents=True,exist_ok=True)
    data={'account':info,'date_stats':stats,'rows':report,'skips':skips,'conflicts':conflicts,'location_counts':dict(Counter(r['sheet_location'] for r in report))}
    (out/'report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print(json.dumps({'date_stats':stats,'samples':[{k:v for k,v in r.items() if k not in ['matched_ads']} for r in report if r['row'] in samples]},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__': main()
