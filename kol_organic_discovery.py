"""Discover approved-account Reels; unknown authors remain private candidates."""
import json
import re
from datetime import datetime
import requests


def fetch_discovery(env):
    from sync_creator_tracker import get_json
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
    ig = env.get('KOL_ORGANIC_META_IG_USER_ID','17841448894150543')
    url = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION','v23.0') + '/' + env.get(
        'KOL_ORGANIC_META_BUSINESS_ID','997763325183322') + '/partnership-ads-advertisable-content'
    params = {'ig_user_id':ig,'fields':'content_id,permalink,creation_time,author','limit':25}
    found, cursors = {}, set()
    try:
        for page in range(100):
            data = get_json(session,url,params)
            if not isinstance(data.get('data'),list):
                raise RuntimeError('Invalid discovery response')
            batch = data['data']
            for item in batch:
                cid = str(item['content_id'])
                if cid in found and found[cid] != item:
                    raise RuntimeError('Conflicting discovery content ID')
                found[cid] = item
            print('Organic discovery page %d: %d unique posts' % (page+1,len(found)),flush=True)
            cursor = data.get('paging',{}).get('cursors',{}).get('after')
            if not batch or not cursor:
                return list(found.values())
            if cursor in cursors:
                raise RuntimeError('Repeated discovery cursor; no insert')
            cursors.add(cursor)
            params['after'] = cursor
        raise RuntimeError('Discovery page limit reached; no insert')
    finally:
        session.close()


def discovery_plan(values, content, year=2026, approved_ids=()):
    from kol_organic_meta import post_key
    norm = lambda name: re.sub(r'[^a-z0-9]','',str(name).lower())
    rows = [row for row in values[2:] if row and row[0] and str(row[0]).lower()!='summary']
    existing = {post_key(row[3]) for row in rows if len(row)>3 and post_key(row[3])}
    names = {norm(row[0]) for row in rows}
    approved = set(str(x) for x in approved_ids)
    # Exact existing post links establish author identity despite creator aliases.
    for item in content:
        if post_key(item.get('permalink','')) in existing:
            author = item.get('author') or {}
            if author.get('ig_user_id'):
                approved.add(str(author['ig_user_id']))
    for item in content:
        author = item.get('author') or {}
        if author.get('display_name') and norm(author['display_name']) in names and author.get('ig_user_id'):
            approved.add(str(author['ig_user_id']))
    additions, pending, seen_ids = [], [], set()
    for item in sorted(content,key=lambda x:(str(x.get('creation_time','')),str(x.get('content_id','')))):
        cid = str(item['content_id'])
        link = item.get('permalink','')
        key = post_key(link)
        if not key or '/reel/' not in link or key in existing or cid in seen_ids:
            continue
        try:
            day = datetime.strptime(str(item.get('creation_time',''))[:10],'%Y-%m-%d').date()
        except ValueError:
            continue
        if day.year != year:
            continue
        author = item.get('author') or {}
        candidate = {'content_id':cid,'creator':author.get('display_name'),
            'author_id':str(author.get('ig_user_id') or ''),'post_link':link,'date':day.isoformat()}
        seen_ids.add(cid)
        existing.add(key)
        if candidate['author_id'] in approved and candidate['creator']:
            additions.append(candidate)
        else:
            pending.append(candidate)
    return additions,pending


def accepted_candidates(env, candidates):
    """Eligibility is per post, never inherited from another post by its author."""
    from sync_creator_tracker import get_json
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
    base = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION','v23.0')
    ig = env.get('KOL_ORGANIC_META_IG_USER_ID','17841448894150543')
    accepted, rejected = [], []
    try:
        for index,item in enumerate(candidates,1):
            try:
                data = get_json(session,base+'/'+item['content_id']+'/collaborators',{})
                if not isinstance(data.get('data'),list) or data.get('paging',{}).get('next'):
                    raise RuntimeError('Incomplete collaborator response')
                ok = any(str(c.get('id'))==ig and c.get('invite_status')=='Accepted' for c in data['data'])
                if ok:
                    accepted.append(item)
                else:
                    rejected.append(dict(item,reason='No Accepted Bluevua collaborator'))
            except (requests.RequestException,RuntimeError,ValueError):
                rejected.append(dict(item,reason='Collaborator query failed; not inserted'))
            print('Organic collaborator verification: %d/%d' % (index,len(candidates)),flush=True)
        return accepted,rejected
    finally:
        session.close()


def insertion_positions(values, cells, additions, summary):
    """Use native numeric dates; preserve the order and contents of existing rows."""
    dated = []
    for index in range(2,summary):
        row = cells[index].get('values',[])
        value = row[1].get('userEnteredValue',{}) if len(row)>1 else {}
        if values[index] and values[index][0]:
            if 'numberValue' not in value:
                raise RuntimeError('Existing Organic Launch Date is not a native date')
            dated.append((index,value['numberValue']))
    if any(a[1]>b[1] for a,b in zip(dated,dated[1:])):
        raise RuntimeError('Existing Organic dates are not ascending; no insert')
    positions=[]
    for item in sorted(additions,key=lambda x:(x['date'],x['content_id'])):
        day=datetime.strptime(item['date'],'%Y-%m-%d').date()
        serial=(day-datetime(1899,12,30).date()).days
        at=next((index for index,date in dated if date>serial),summary)
        positions.append((at,item))
    return positions


def run(env,output,apply):
    from kol_tracker import sheet_session,read_tab,save_json
    from update_meta_tracker import write_range
    session,endpoint = sheet_session(dict(env,KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK']))
    prop,native,values = read_tab(session,endpoint,'Meta-IG')
    from kol_organic_meta import plan
    plan(values,{})  # Fail schema validation before fetching or inserting.
    content = fetch_discovery(env)
    ig = env.get('KOL_ORGANIC_META_IG_USER_ID','17841448894150543')
    content = [item for item in content if str((item.get('author') or {}).get('ig_user_id'))!=ig]
    known,unknown = discovery_plan(values,content,int(env.get('KOL_ORGANIC_META_YEAR','2026')))
    additions,pending = accepted_candidates(env,known+unknown)
    save_json(output/'Organic-Meta-discovery.json',{'additions':additions,'pending':pending})
    print('Organic discovery: %d Accepted Bluevua Reels; %d excluded/unverified' % (len(additions),len(pending)),flush=True)
    if not apply or not additions:
        return
    tab = native['sheets'][0]
    if tab.get('tables') or tab.get('merges') or tab.get('protectedRanges'):
        raise RuntimeError('Structured Organic sheet requires explicit insert review')
    summaries = [i for i,row in enumerate(values) if row and str(row[0]).lower()=='summary']
    if len(summaries)!=1:
        raise RuntimeError('Expected one Organic Summary row')
    start = summaries[0]
    cells = tab['data'][0]['rowData']
    exemplar = start-1
    if exemplar<2 or not values[exemplar][0]:
        raise RuntimeError('Missing Organic row exemplar')
    width = prop['gridProperties']['columnCount']
    source = cells[exemplar].get('values',[])
    positions = insertion_positions(values,cells,additions,start)
    body = []
    source_shift = 0
    # Descending inserts keep all remaining original row coordinates stable.
    for index,item in reversed(positions):
        body.append({'insertDimension':{'range':{'sheetId':prop['sheetId'],'dimension':'ROWS',
            'startIndex':index,'endIndex':index+1},'inheritFromBefore':True}})
        # Copy only safe formatting/validation; no fees, formulas, codes or links.
        new_cells = []
        for col in range(width):
            old = source[col] if col<len(source) else {}
            cell = {key:json.loads(json.dumps(old[key])) for key in ('userEnteredFormat','dataValidation') if key in old}
            cell.get('userEnteredFormat',{}).get('textFormat',{}).pop('link',None)
            new_cells.append(cell)
        body.append({'updateCells':{'range':{'sheetId':prop['sheetId'],'startRowIndex':index,
            'endRowIndex':index+1,'startColumnIndex':0,'endColumnIndex':width},
            'rows':[{'values':new_cells}],'fields':'userEnteredFormat,dataValidation'}})
        # Native copy preserves dropdown presentation that JSON validation omits.
        source_shift += int(exemplar>=index)
        source_index=exemplar+source_shift
        for col in (2,5):
            body.append({'copyPaste':{'source':{'sheetId':prop['sheetId'],'startRowIndex':source_index,
                'endRowIndex':source_index+1,'startColumnIndex':col,'endColumnIndex':col+1},
                'destination':{'sheetId':prop['sheetId'],'startRowIndex':index,'endRowIndex':index+1,
                    'startColumnIndex':col,'endColumnIndex':col+1},'pasteType':'PASTE_NORMAL'}})
            body.append(write_range(prop['sheetId'],index+1,col,['']))
        day = datetime.strptime(item['date'],'%Y-%m-%d').date()
        for col,value in [(0,item['creator']),(1,(day-datetime(1899,12,30).date()).days),(3,item['post_link'])]:
            if new_cells[col].get('dataValidation'):
                raise RuntimeError('Constrained new identity cell')
            body.append(write_range(prop['sheetId'],index+1,col,[value]))
    save_json(output/'Organic-Meta-discovery-before.json',native)
    if read_tab(session,endpoint,'Meta-IG')[1]!=native:
        raise RuntimeError('Organic changed during discovery; no insert')
    session.post(endpoint+':batchUpdate',json={'requests':body},timeout=90).raise_for_status()
    _,after,actual = read_tab(session,endpoint,'Meta-IG')
    save_json(output/'Organic-Meta-discovery-after.json',after)
    for offset,(original_index,item) in enumerate(positions):
        index=original_index+offset
        item['row']=index+1
        row = actual[index]
        if row[0]!=item['creator'] or row[3]!=item['post_link'] or row[6]!='':
            raise RuntimeError('Organic new identity/blank fee verification failed')
        fresh = after['sheets'][0]['data'][0]['rowData'][index].get('values',[])
        for col in (2,5):
            if fresh[col].get('dataValidation')!=source[col].get('dataValidation'):
                raise RuntimeError('Organic new dropdown verification failed')
    # Existing rows and summary retain native values/styles; formulas may shift.
    for index,row in enumerate(cells):
        shifted = index+sum(1 for at,_ in positions if at<=index)
        fresh = after['sheets'][0]['data'][0]['rowData'][shifted].get('values',[])
        for col,old in enumerate(row.get('values',[])):
            new = fresh[col] if col<len(fresh) else {}
            for key in ('userEnteredFormat','dataValidation','chipRuns','textFormatRuns'):
                if old.get(key)!=new.get(key):
                    raise RuntimeError('Existing Organic native structure changed')
            if 'formulaValue' not in old.get('userEnteredValue',{}) and old.get('userEnteredValue')!=new.get('userEnteredValue'):
                raise RuntimeError('Existing Organic value changed')
    print('Organic new rows inserted and verified: %d' % len(additions),flush=True)
    save_json(output/'Organic-Meta-discovery.json',{'additions':additions,'pending':pending})
