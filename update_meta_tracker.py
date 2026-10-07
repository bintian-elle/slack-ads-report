"""Independent Meta Tracker writer. Defaults to plan-only; --apply explicitly writes."""
import argparse
import fcntl
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from sync_creator_tracker import BASE, METRICS, ad_location, aggregate, code_key, fetch_insights, get_json, graph_pages, post_key, read_sheet


def exact_mapping(rows, ads):
    codes, claims = defaultdict(list), defaultdict(list)
    for ad in ads:
        code = code_key(ad.get('creative', {}).get('branded_content', {}).get('instagram_boost_post_access_token'))
        if code: codes[code].append(ad['id'])
    matches, skips = {}, []
    for n, row in rows:
        code = code_key(row[7])
        ids = sorted(set(codes.get(code, []))) if code else []
        if not ids:
            skips.append({'row': n, 'creator': row[1], 'reason': 'No exact Creative Ad Code match'})
        else:
            matches[n] = ids
            for aid in ids: claims[aid].append(n)
    conflicts = {aid: ns for aid, ns in claims.items() if len(ns) > 1}
    rejected = {n for ns in conflicts.values() for n in ns}
    for n in rejected:
        matches.pop(n, None)
        skips.append({'row': n, 'reason': 'Ad ID claimed by multiple Tracker rows'})
    return matches, skips, conflicts


def cell(value):
    if value is None or value == '': return {}
    return {'userEnteredValue': {'numberValue' if isinstance(value, (int, float)) else 'stringValue': value}}


def new_row_status(ads):
    """Initialize newly inserted rows only; existing Status cells are user-owned."""
    if not ads:
        return None
    statuses = {a.get('effective_status') for a in ads}
    if 'ACTIVE' in statuses:
        return 'testing'
    if statuses.issubset({'PAUSED', 'ADSET_PAUSED', 'CAMPAIGN_PAUSED'}):
        return 'paused'
    return None


def write_range(sheet_id, row, start, values):
    return {'updateCells': {'range': {'sheetId': sheet_id, 'startRowIndex': row-1,
        'endRowIndex': row, 'startColumnIndex': start, 'endColumnIndex': start+len(values)},
        'rows': [{'values': [cell(v) for v in values]}], 'fields': 'userEnteredValue'}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    load_dotenv(BASE / '.env')
    env = dict(os.environ)
    output = BASE / 'data/processed/kol_tracker_writer'
    output.mkdir(parents=True, exist_ok=True)
    # A process-wide lock protects concurrent polling/daily runs on this host.
    with (output / 'writer.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(env, output, args.apply)


def run(env, output, apply):
    from tracker_failure_notes import plan_notes
    from kol_tracker import verify_literal_target
    before = read_sheet(env, all_columns=True)
    print('Tracker read; checking native cell metadata',flush=True)
    sid = before['metadata']['spreadsheetId']
    prop = next(s['properties'] for s in before['metadata']['sheets'] if s['properties']['title'] == 'Meta')
    sheet_id = prop['sheetId']
    if sheet_id != 661635149: raise RuntimeError('Unexpected Meta sheet ID')
    raw = before['values']
    if [raw[0][i] for i in [1,6,7]] != ['Creator','Post Link','Ad Code']: raise RuntimeError('Identity headers changed')
    if len(raw[0]) < 19 or 'Lifetime Spend' not in raw[0][9]: raise RuntimeError('Metric header changed')
    rows = [(i+1, r+['']*max(0,22-len(r))) for i,r in enumerate(raw) if i>1 and len(r)>1 and r[1]]
    # New rows are inserted only by the production Slack task. Daily metrics
    # must not depend on private local audit snapshots or hardcoded creators.
    last = max(i+1 for i,r in enumerate(raw) if any(v != '' for v in r))
    new_number = None
    credentials = Credentials.from_service_account_file(str(BASE / env.get('GOOGLE_SERVICE_ACCOUNT_FILE','credentials/google-service-account.json')), scopes=['https://www.googleapis.com/auth/spreadsheets'])
    gs = AuthorizedSession(credentials)
    endpoint = 'https://sheets.googleapis.com/v4/spreadsheets/'+sid
    fields = 'spreadsheetId,sheets(properties,tables,merges,protectedRanges,data(startRow,startColumn,rowData(values(userEnteredValue,effectiveValue,formattedValue,userEnteredFormat,dataValidation,chipRuns,textFormatRuns))))'
    column_label, count = '', prop['gridProperties']['columnCount']
    while count:
        count, remainder = divmod(count - 1, 26)
        column_label = chr(65 + remainder) + column_label
    native_range = "'Meta'!A1:%s%d" % (column_label,last+1)
    grid = get_json(gs, endpoint, {'ranges': native_range, 'includeGridData': 'true', 'fields': fields})
    tab = next(s for s in grid['sheets'] if s['properties']['sheetId']==sheet_id)
    if tab.get('tables') or tab.get('merges') or tab.get('protectedRanges'):
        raise RuntimeError('Native tables/merges/protection require explicit inspection before writing')
    block = tab['data'][0].get('rowData',[])
    print('Cell metadata read; fetching complete ad inventory',flush=True)
    s = requests.Session(); s.headers['Authorization']='Bearer '+env['META_ACCESS_TOKEN']
    base='https://graph.facebook.com/'+env.get('KOL_TRACKER_META_API_VERSION','v23.0')
    account='act_'+env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    info=get_json(s,base+'/'+account,{'fields':'id,name,currency,timezone_name'})
    end=(datetime.now(ZoneInfo(info['timezone_name'])).date()-timedelta(days=1)).isoformat()
    ads=graph_pages(s,base+'/'+account+'/ads',{'fields':'id,name,created_time,effective_status,campaign{id,name},adset{id,name},creative{id,branded_content}','limit':100})
    matches, skips, conflicts=exact_mapping(rows,ads)
    byid={a['id']:a for a in ads}
    locations={}
    for number,row in rows:
        matched=[byid[aid] for aid in matches.get(number,[])]
        active=[a for a in matched if a.get('effective_status')=='ACTIVE']
        if active: locations[number]=' & '.join(sorted({ad_location(a) for a in active}))
        if number==new_number and matched:
            row[0]=locations.get(number,'')
            row[8]=new_row_status(matched) or ''
            date=min(datetime.strptime(a['created_time'],'%Y-%m-%dT%H:%M:%S%z').astimezone(ZoneInfo(info['timezone_name'])).date() for a in matched)
            row[2]=(date-datetime(1899,12,30).date()).days
    print('Exact identity mapping complete:',len(matches),'rows; fetching cumulative Insights',flush=True)
    insights=fetch_insights(s,base,account,env.get('KOL_TRACKER_START_DATE','2025-01-01'),end)
    byad=defaultdict(list)
    for r in insights: byad[r['ad_id']].append(r)
    requests_body=[]; updated=[]
    location_changes={}
    for number,row in rows:
        if number!=new_number and number in locations and row[0]!=locations[number]:
            if block[number-1].get('values',[{}])[0].get('dataValidation'): raise RuntimeError('Constrained Location cell')
            requests_body.append(write_range(sheet_id,number,0,[locations[number]]))
            location_changes[number]=locations[number]
    if new_number:
        if new_number not in matches: raise RuntimeError('New Lorenzo content does not have exact ad match')
        exemplar=max(n for n,r in rows if n<=last and isinstance(r[9],(int,float)))
        source={'sheetId':sheet_id,'startRowIndex':exemplar-1,'endRowIndex':exemplar,'startColumnIndex':0,'endColumnIndex':22}
        dest=dict(source,startRowIndex=new_number-1,endRowIndex=new_number)
        for kind in ['PASTE_FORMAT','PASTE_DATA_VALIDATION']:
            requests_body.append({'copyPaste':{'source':source,'destination':dest,'pasteType':kind}})
        requests_body.append(write_range(sheet_id,new_number,0,dict(rows)[new_number]))
        exemplar_cells=block[exemplar-1].get('values',[])
        status_rule=exemplar_cells[8].get('dataValidation',{}) if len(exemplar_cells)>8 else {}
        new_status=dict(rows)[new_number][8]
        if new_status and status_rule:
            allowed=[v.get('userEnteredValue') for v in status_rule.get('condition',{}).get('values',[])]
            if status_rule.get('condition',{}).get('type')!='ONE_OF_LIST' or new_status not in allowed:
                raise RuntimeError('New row Status fails live validation')
        if len(exemplar_cells)>20 and exemplar_cells[20].get('userEnteredValue',{}).get('formulaValue'):
            formula_source=dict(source,startColumnIndex=20,endColumnIndex=21)
            formula_dest=dict(dest,startColumnIndex=20,endColumnIndex=21)
            requests_body.append({'copyPaste':{'source':formula_source,'destination':formula_dest,'pasteType':'PASTE_FORMULA'}})
    for number,row in rows:
        if number not in matches: continue
        observed=[r for aid in matches[number] for r in byad[aid]]
        if not observed:
            skips.append({'row':number,'creator':row[1],'reason':'Matched ads have no returned Insights; preserve existing metrics'})
            continue
        if number<=len(block):
            cells=block[number-1].get('values',[])
            if any(c.get('dataValidation') or c.get('chipRuns') for c in cells[9:19]):
                raise RuntimeError('Constrained metrics at row '+str(number))
        totals=aggregate(observed)
        requests_body.append(write_range(sheet_id,number,9,[totals[k] for k in METRICS]))
        updated.append({'row':number,'creator':row[1],'ad_ids':matches[number],'metrics':totals})
    partial = [{'row':item['row'],'reason':'Metric unavailable: '+', '.join(
        key for key,value in item['metrics'].items() if value is None)}
        for item in updated if any(value is None for value in item['metrics'].values())]
    notes = plan_notes(raw,{item['row'] for item in updated},skips+partial,1,3)
    for item in notes:
        for col,value in item['changes'].items():
            target = block[item['row']-1].get('values',[])
            verify_literal_target(target[col] if col<len(target) else {},value)
            requests_body.append(write_range(sheet_id,item['row'],col,[value]))
    now=datetime.now(ZoneInfo('America/Chicago'))
    header=re.sub(r'\[[^\]]*update[^\]]*\]', '['+now.strftime('%Y-%m-%d')+' update]',raw[0][9],flags=re.I)
    if header==raw[0][9] and 'update' not in header.lower(): header+='\n['+now.strftime('%Y-%m-%d')+' update]'
    requests_body.append(write_range(sheet_id,1,9,[header]))
    plan={'fetched_at':now.isoformat(),'cutoff':end,'account':info,'new_row':new_number,'updated_rows':updated,'location_changes':location_changes,'skipped':skips,'note_updates':notes,'shared_ads':conflicts,'j1':header,'requests':requests_body}
    stamp=now.strftime('%Y%m%dT%H%M%S')
    (output/(stamp+'-before.json')).write_text(json.dumps({'sheet':before,'grid':grid},ensure_ascii=False,indent=2))
    (output/(stamp+'-plan.json')).write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    print(json.dumps({k:plan[k] for k in ['cutoff','new_row','skipped','shared_ads','j1']},ensure_ascii=False),flush=True)
    print('Planned metric rows:',len(updated),flush=True)
    if not apply: return
    if read_sheet(env,all_columns=True)['values'] != raw: raise RuntimeError('Sheet changed during fetch; no write performed')
    response=gs.post(endpoint+':batchUpdate',json={'requests':requests_body},timeout=90)
    if not response.ok: raise RuntimeError('Sheets batch rejected: '+response.text)
    after=read_sheet(env,all_columns=True)
    after_grid=get_json(gs,endpoint,{'ranges':native_range,'includeGridData':'true','fields':fields})
    after_tab=next(s for s in after_grid['sheets'] if s['properties']['sheetId']==sheet_id)
    after_block=after_tab['data'][0].get('rowData',[])
    for number,row in rows:
        if number==new_number:
            current=after['values'][number-1]+['']*22
            if current[:9]!=row[:9]: raise RuntimeError('New row identity/Status verification failed')
        if number<=last:
            current=after['values'][number-1]+['']*22
            if current[1:9]!=row[1:9] or current[0]!=location_changes.get(number,row[0]): raise RuntimeError('Manual fields verification failed')
            for col in [19,20,21]:
                if any(item['row']==number and col in item['changes'] for item in notes): continue
                old=block[number-1].get('values',[])
                fresh=after_block[number-1].get('values',[])
                original=old[col].get('userEnteredValue',{}) if len(old)>col else {}
                actual=fresh[col].get('userEnteredValue',{}) if len(fresh)>col else {}
                if original!=actual: raise RuntimeError('Manual value/formula changed')
    for request in requests_body:
        update=request.get('updateCells')
        if not update: continue
        region=update['range']; n=region['startRowIndex']; start=region['startColumnIndex']
        old=block[n].get('values',[]) if n<len(block) else []
        fresh=after_block[n].get('values',[])
        for col in range(start,region['endColumnIndex']):
            if new_number is not None and n==new_number-1: continue
            for key in ['userEnteredFormat','dataValidation']:
                if (old[col].get(key) if len(old)>col else None)!=(fresh[col].get(key) if len(fresh)>col else None):
                    raise RuntimeError('Format/validation changed outside planned new row')
    for item in updated:
        values=after['values'][item['row']-1]+['']*22
        for i,key in enumerate(METRICS):
            expected=item['metrics'][key]; actual=values[9+i]
            if expected is None:
                if actual!='': raise RuntimeError('Blank ratio verification failed')
            elif not isinstance(actual,(int,float)) or abs(actual-expected)>1e-8: raise RuntimeError('Metric verification failed')
    if after['values'][0][9]!=header: raise RuntimeError('Header verification failed')
    for item in notes:
        for col,value in item['changes'].items():
            row=after['values'][item['row']-1]
            if (row[col] if col<len(row) else '')!=value: raise RuntimeError('Failure reason verification failed')
    (output/(stamp+'-after.json')).write_text(json.dumps(after,ensure_ascii=False,indent=2))
    print('APPLIED AND VERIFIED',len(updated),'metric rows; new row',new_number,flush=True)


if __name__=='__main__':
    main()
