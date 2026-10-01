"""Update authorized locations/new launch date; audit followers/status without writing them."""
import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from sync_creator_tracker import BASE, get_json, graph_pages, read_sheet, ad_location as location
from update_meta_tracker import exact_mapping, write_range


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    load_dotenv(BASE/'.env');env=dict(os.environ)
    before=read_sheet(env);raw=before['values'];sid=before['metadata']['spreadsheetId']
    prop=next(s['properties'] for s in before['metadata']['sheets'] if s['properties']['title']=='Meta');sheet_id=prop['sheetId']
    if sheet_id!=661635149 or raw[0][0]!='Location in Meta (now)' or raw[0][2]!='Launch Date':raise RuntimeError('Schema changed')
    rows=[(i+1,r+['']*max(0,22-len(r))) for i,r in enumerate(raw) if i>1 and len(r)>1 and r[1]]
    s=requests.Session();s.headers['Authorization']='Bearer '+env['META_ACCESS_TOKEN']
    base='https://graph.facebook.com/'+env.get('KOL_TRACKER_META_API_VERSION','v23.0');account='act_'+env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    info=get_json(s,base+'/'+account,{'fields':'id,name,timezone_name'})
    ads=graph_pages(s,base+'/'+account+'/ads',{'fields':'id,name,status,effective_status,created_time,campaign{id,name,effective_status},adset{id,name,effective_status},creative{id,branded_content,instagram_user_id}','limit':100})
    mapping,skips,conflicts=exact_mapping(rows,ads);byid={a['id']:a for a in ads}
    credentials=Credentials.from_service_account_file(str(BASE/env.get('GOOGLE_SERVICE_ACCOUNT_FILE','credentials/google-service-account.json')),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    gs=AuthorizedSession(credentials);endpoint='https://sheets.googleapis.com/v4/spreadsheets/'+sid
    fields='sheets(properties,data(startRow,rowData(values(userEnteredValue,formattedValue,userEnteredFormat,dataValidation))))'
    params={'ranges':"'Meta'!A1:V%d"%max(n for n,_ in rows),'includeGridData':'true','fields':fields}
    grid=get_json(gs,endpoint,params);block=grid['sheets'][0]['data'][0]['rowData']
    updates=[];requests_body=[];status_rows=[]
    for number,row in rows:
        matched=[byid[aid] for aid in mapping.get(number,[])]
        active=[a for a in matched if a['effective_status']=='ACTIVE']
        status_rows.append({'row':number,'creator':row[1],'followers':row[4],'sheet_status':row[8],
                            'ad_status_counts':dict(Counter(a['status'] for a in matched)),
                            'effective_status_counts':dict(Counter(a['effective_status'] for a in matched)),
                            'has_active':bool(active),'matched':bool(matched)})
        if active:
            labels=sorted({location(a) for a in active});value=' & '.join(labels)
            if value!=row[0]:
                if block[number-1].get('values',[{}])[0].get('dataValidation'):raise RuntimeError('Constrained location cell')
                requests_body.append(write_range(sheet_id,number,0,[value]))
                updates.append({'row':number,'creator':row[1],'column':'A','before':row[0],'after':value})
        if row[1]=='Lorenzo Love' and not row[2] and matched:
            date=min(datetime.strptime(a['created_time'],'%Y-%m-%dT%H:%M:%S%z').astimezone(ZoneInfo(info['timezone_name'])).date() for a in matched)
            serial=(date-datetime(1899,12,30).date()).days
            requests_body.append(write_range(sheet_id,number,2,[serial]))
            updates.append({'row':number,'creator':row[1],'column':'C','before':'','after':date.isoformat()})
    follower_tests=[]
    for name in ['madisonnoelle','zaaachydub','Lorenzo Love']:
        number,row=next((n,r) for n,r in rows if r[1]==name)
        matched=[byid[aid] for aid in mapping.get(number,[])]
        if not matched:continue
        ad=matched[0];uid=ad.get('creative',{}).get('instagram_user_id')
        if not uid:continue
        for fields_value, scoped in [('id,username,followers_count',False),('id,username,followed_by_count',False),('id,username,followed_by_count',True)]:
            params_user={'fields':fields_value}
            if scoped:params_user['adgroup_id']=ad['id']
            response=s.get(base+'/'+uid,params=params_user,timeout=40)
            result=response.json()
            follower_tests.append({'creator':name,'row':number,'sheet_followers':row[4],'ig_user_id':uid,
                                   'fields':fields_value,'adgroup_scoped':scoped,'http_status':response.status_code,'response':result})
    out=BASE/'data/processed/kol_tracker_audit/followers_status';out.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(ZoneInfo('America/Chicago')).strftime('%Y%m%dT%H%M%S')
    report={'updates':updates,'requests':requests_body,'rows':status_rows,'follower_tests':follower_tests,'skips':skips,'shared_ads':conflicts,
            'no_active_policy':'Preserve existing location; do not interpret it as currently active',
            'location_policy':'Distinct categories from ACTIVE exact-code ads; deterministic Ad Set first, then Campaign name tokens.'}
    (out/(stamp+'-before.json')).write_text(json.dumps({'sheet':before,'grid':grid},ensure_ascii=False,indent=2))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'updates':updates,'follower_tests':follower_tests,'status_distribution':dict(Counter(r['sheet_status'] for r in status_rows)),
                      'paused_with_active':[r['creator'] for r in status_rows if r['sheet_status']=='paused' and r['has_active']],
                      'testing_without_active':[r['creator'] for r in status_rows if r['sheet_status']=='testing' and r['matched'] and not r['has_active']]},ensure_ascii=False,indent=2),flush=True)
    if not args.apply:return
    if read_sheet(env)['values']!=raw:raise RuntimeError('Concurrent sheet change; not written')
    response=gs.post(endpoint+':batchUpdate',json={'requests':requests_body},timeout=90)
    if not response.ok:raise RuntimeError('Sheets rejected batch: '+response.text)
    after=read_sheet(env);after_grid=get_json(gs,endpoint,params);fresh=after_grid['sheets'][0]['data'][0]['rowData']
    allowed={(u['row'],0 if u['column']=='A' else 2) for u in updates}
    for number,row in rows:
        current=after['values'][number-1]+['']*22
        for col in range(22):
            if (number,col) not in allowed and current[col]!=row[col]:raise RuntimeError('Unrequested field changed')
        old=block[number-1].get('values',[]);new=fresh[number-1].get('values',[])
        for col in [0,2]:
            if (number,col) not in allowed:continue
            for key in ['userEnteredFormat','dataValidation']:
                if (old[col].get(key) if len(old)>col else None)!=(new[col].get(key) if len(new)>col else None):raise RuntimeError('Formatting changed')
    for u in updates:
        col=0 if u['column']=='A' else 2
        actual=after['values'][u['row']-1][col]
        expected=u['after'] if col==0 else (datetime.fromisoformat(u['after']).date()-datetime(1899,12,30).date()).days
        if actual!=expected:raise RuntimeError('Changed value failed verification')
    (out/(stamp+'-after.json')).write_text(json.dumps(after,ensure_ascii=False,indent=2))
    print('APPLIED AND VERIFIED',len(updates),'cells',flush=True)


if __name__=='__main__':main()
