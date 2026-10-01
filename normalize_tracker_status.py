"""Display two-decimal ROAS and sort by Launch Date; preserve all existing Status."""
import argparse
import json
import os
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from sync_creator_tracker import BASE, get_json, graph_pages, read_sheet
from update_meta_tracker import exact_mapping, new_row_status


tracker_status = new_row_status


def identity(row):
    padded=row+['']*max(0,22-len(row))
    return tuple(padded[i] for i in [1,6,7])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');args=p.parse_args()
    load_dotenv(BASE/'.env');env=dict(os.environ)
    before=read_sheet(env);raw=before['values'];sid=before['metadata']['spreadsheetId']
    prop=next(s['properties'] for s in before['metadata']['sheets'] if s['properties']['title']=='Meta');sheet_id=prop['sheetId']
    if sheet_id!=661635149 or raw[0][8]!='Status' or raw[0][2]!='Launch Date':raise RuntimeError('Unexpected schema')
    rows=[(i+1,r+['']*max(0,22-len(r))) for i,r in enumerate(raw) if i>1 and len(r)>1 and r[1]]
    last=max(n for n,_ in rows)
    if len(rows)!=last-2:raise RuntimeError('Non-contiguous rows; inspect sort scope first')
    if any(not isinstance(r[2],(int,float)) for _,r in rows):raise RuntimeError('Unknown/non-numeric dates; explicit blank-date policy required')
    if len({identity(r) for _,r in rows})!=len(rows):raise RuntimeError('Non-unique row identity; cannot verify safe sort')
    gs=AuthorizedSession(Credentials.from_service_account_file(str(BASE/env.get('GOOGLE_SERVICE_ACCOUNT_FILE','credentials/google-service-account.json')),scopes=['https://www.googleapis.com/auth/spreadsheets']))
    endpoint='https://sheets.googleapis.com/v4/spreadsheets/'+sid
    grid_params={'ranges':"'Meta'!A1:V%d"%last,'includeGridData':'true',
        'fields':'sheets(properties,merges,protectedRanges,tables,data(startRow,rowData(values(userEnteredValue,formattedValue,userEnteredFormat,dataValidation))))'}
    grid=get_json(gs,endpoint,grid_params);tab=grid['sheets'][0]
    if tab.get('merges') or tab.get('tables') or tab.get('protectedRanges'):raise RuntimeError('Structured/protected range requires separate inspection')
    block=tab['data'][0]['rowData']
    s=requests.Session();s.headers['Authorization']='Bearer '+env['META_ACCESS_TOKEN']
    base='https://graph.facebook.com/'+env.get('KOL_TRACKER_META_API_VERSION','v23.0');account='act_'+env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    ads=graph_pages(s,base+'/'+account+'/ads',{'fields':'id,name,effective_status,creative{id,branded_content}','limit':100})
    mapping,skips,conflicts=exact_mapping(rows,ads);byid={a['id']:a for a in ads}
    expected={identity(r):list(r) for _,r in rows};updates=[];requests_body=[]
    # No existing row Status writes, including blanks. Only the insertion writer
    # initializes Status on a newly added row.
    for col in [10,20]:
        requests_body.append({'repeatCell':{'range':{'sheetId':sheet_id,'startRowIndex':2,'endRowIndex':last,'startColumnIndex':col,'endColumnIndex':col+1},
            'cell':{'userEnteredFormat':{'numberFormat':{'type':'NUMBER','pattern':'0.00'}}},'fields':'userEnteredFormat.numberFormat'}})
    requests_body.append({'sortRange':{'range':{'sheetId':sheet_id,'startRowIndex':2,'endRowIndex':last,'startColumnIndex':0,'endColumnIndex':prop['gridProperties']['columnCount']},
        'sortSpecs':[{'dimensionIndex':2,'sortOrder':'ASCENDING'}]}})
    out=BASE/'data/processed/kol_tracker_audit/status_sort';out.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(ZoneInfo('America/Chicago')).strftime('%Y%m%dT%H%M%S')
    (out/(stamp+'-before.json')).write_text(json.dumps({'sheet':before,'grid':grid},ensure_ascii=False,indent=2))
    plan={'updates':updates,'skips':skips,'conflicts':conflicts,'requests':requests_body,'rows':len(rows),'status_totals':dict(Counter(r[8] for r in expected.values()))}
    (out/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    print(json.dumps({k:plan[k] for k in ['updates','skips','rows','status_totals']},ensure_ascii=False,indent=2),flush=True)
    if not args.apply:return
    if read_sheet(env)['values']!=raw:raise RuntimeError('Concurrent edit; not written')
    response=gs.post(endpoint+':batchUpdate',json={'requests':requests_body},timeout=90)
    if not response.ok:raise RuntimeError('Sheets batch rejected: '+response.text)
    after=read_sheet(env);after_grid=get_json(gs,endpoint,grid_params);newblock=after_grid['sheets'][0]['data'][0]['rowData']
    actual=[r+['']*max(0,22-len(r)) for r in after['values'][2:last]]
    if len(actual)!=len(rows) or {identity(r) for r in actual}!=set(expected):raise RuntimeError('Sort changed row membership')
    dates=[r[2] for r in actual]
    if dates!=sorted(dates):raise RuntimeError('Date sort verification failed')
    old_by_identity={identity(row):block[number-1].get('values',[]) for number,row in rows}
    for index,row in enumerate(actual,start=2):
        if row[:22]!=expected[identity(row)][:22]:raise RuntimeError('Row data changed beyond authorized Status')
        cells=newblock[index].get('values',[]);old=old_by_identity[identity(row)]
        for col in range(22):
            a=old[col] if len(old)>col else {};b=cells[col] if len(cells)>col else {}
            for key in ['dataValidation']:
                if a.get(key)!=b.get(key):raise RuntimeError('Validation changed during sort')
            fmt_old=dict(a.get('userEnteredFormat',{}));fmt_new=dict(b.get('userEnteredFormat',{}))
            if col in [10,20]:
                if fmt_new.get('numberFormat')!={'type':'NUMBER','pattern':'0.00'}:raise RuntimeError('ROAS format failed')
                fmt_old.pop('numberFormat',None);fmt_new.pop('numberFormat',None)
            if fmt_old!=fmt_new:raise RuntimeError('Other formatting changed during sort')
    (out/(stamp+'-after.json')).write_text(json.dumps({'sheet':after,'grid':after_grid},ensure_ascii=False,indent=2))
    print('APPLIED AND VERIFIED:',len(updates),'Status changes;',len(rows),'rows sorted; ROAS 0.00; Followers unchanged',flush=True)


if __name__=='__main__':main()
