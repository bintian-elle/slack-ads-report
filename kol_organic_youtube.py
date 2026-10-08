"""Slack-sourced YouTube rows and public lifetime counters for Organic YTB."""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
from zoneinfo import ZoneInfo

import requests

MARKER = re.compile(r'\[KOL Content is Live\]', re.I)
LIVE = re.compile(r'\b(?:is|are)\s+live\b|\b(?:have|has)\s+been\s+published\b|\b(?:was|were)\s+published\b', re.I)
URL = re.compile(r'https?://[^\s<>|]+')
NOTE = 'Reason for data update failure'
MANAGED = ('Shares and saves are not available via public YouTube Data API',
           'YouTube API ', 'Video unavailable in YouTube API', 'Missing YouTube ',
           'Missing numeric KOL fee', 'Missing or unsupported YouTube link')


def video_id(link):
    try:
        u = urlsplit(html.unescape(str(link)))
        if u.hostname in ('google.com', 'www.google.com') and u.path == '/url':
            return video_id(parse_qs(u.query).get('q', [''])[0])
        if u.hostname in ('youtu.be', 'www.youtu.be'):
            vid = u.path.strip('/')
        elif u.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            vid = (parse_qs(u.query).get('v', [''])[0] if u.path == '/watch'
                   else u.path.split('/')[2] if u.path.startswith(('/shorts/', '/embed/')) else '')
        else:
            return ''
        return vid if re.fullmatch(r'[A-Za-z0-9_-]{11}', vid) else ''
    except (ValueError, IndexError):
        return ''


def heading(line):
    line = re.sub(r'<@[^>]+>', '', line).strip(' *•\t')
    line = re.sub(r'^\d+[.)]\s*', '', line)
    m = re.match(r"^([^\n<>:]+)[‘’']s\s", line)
    if m:
        name = m.group(1).strip(' *')
        return name if not name.lower().startswith(('here ', 'this ')) else ''
    if not line or URL.search(line) or MARKER.search(line) or ':' in line:
        return ''
    if re.search(r'^(?:long form|short|youtube|ytb|YT:|instagram|tiktok|blog|adcode|IG|TT|FB|Pinterest|ROPOT|\$|\(?[:$]|#)', line, re.I):
        return ''
    if line.lower().startswith(('for this', 'please ', 'thanks', 'already ', 'need ', 'content ', 'here ')):
        return ''
    name = re.split(r'\s+--?\s+|\s+content\s', line, 1, flags=re.I)[0].strip(' *')
    return name if len(name) <= 70 and not re.search(r'[.!?。]', name.replace('.', '')) else ''


def parse_announcements(messages):
    byts = {m['ts']: m for m in messages}
    found, issues = {}, []
    for msg in sorted(messages, key=lambda x: float(x['ts'])):
        text = html.unescape(msg.get('text', ''))
        parent = html.unescape(byts.get(msg.get('thread_ts'), {}).get('text', ''))
        marked = bool(MARKER.search(text) or MARKER.search(parent))
        if not marked and not LIVE.search(text):
            continue
        # A reply without its own creator may inherit only a single unambiguous
        # parent creator, never the first name from a multi-creator announcement.
        parent_names = {heading(l) for l in MARKER.sub('', parent).splitlines()} - {''}
        creator = next(iter(parent_names)) if len(parent_names) == 1 else ''
        for line in MARKER.sub('', text).splitlines():
            name = heading(line)
            if name:
                creator = name
            for link in URL.findall(line):
                vid = video_id(link)
                if not vid:
                    continue
                if not creator:
                    issues.append({'ts': msg['ts'], 'video_id': vid, 'reason': 'Missing or ambiguous Slack Creator'})
                    continue
                found.setdefault(vid, {'video_id': vid, 'creator': creator,
                    'post_link': 'https://www.youtube.com/watch?v=' + vid,
                    'ts': msg['ts'], 'kind': 'marked' if marked else 'ordinary'})
    return list(found.values()), issues


def fetch_videos(env, ids):
    result = {}
    ids = sorted(set(ids))
    for start in range(0, len(ids), 50):
        try:
            r = requests.get('https://www.googleapis.com/youtube/v3/videos', params={
                'key': env['YOUTUBE_API_KEY'], 'part': 'snippet,statistics',
                'id': ','.join(ids[start:start+50])}, timeout=45)
        except requests.RequestException:
            raise RuntimeError('YouTube API request failed') from None
        if not r.ok:
            raise RuntimeError('YouTube API HTTP ' + str(r.status_code))
        for item in r.json().get('items', []):
            result[item['id']] = item
    return result


def schema(values):
    h = [str(x).split('\n')[0].strip() for x in values[0]]
    if h[:8] != ['Creator', 'Organic Launch Date', 'Post Link', 'KOL Fee', 'Views', 'Interaction', 'Likes', 'Comments'] or h[10:12] != ['CPM','CPE'] or NOTE not in h:
        raise RuntimeError('Organic YTB schema changed')
    return h.index(NOTE)


def commit(session, endpoint, prop, native, body, expected, output, label):
    from kol_tracker import read_tab, save_json
    if not body:
        return
    save_json(output / (label+'-before.json'), native)
    if read_tab(session, endpoint, 'YTB', False)[1] != native:
        raise RuntimeError('Organic YTB changed before write')
    session.post(endpoint+':batchUpdate', json={'requests':body}, timeout=90).raise_for_status()
    after = read_tab(session, endpoint, 'YTB', False)[1]
    save_json(output / (label+'-after.json'), after)
    rows = after['sheets'][0]['data'][0]['rowData']
    for (row,col), value in expected.items():
        got = rows[row-1].get('values', [])
        got = next(iter(got[col].get('effectiveValue',{}).values()), '') if len(got)>col else ''
        if got != value:
            raise RuntimeError('Organic YTB write verification failed')
    if not any('insertDimension' in req for req in body):
        old_rows=native['sheets'][0]['data'][0]['rowData']
        for number,old_row in enumerate(old_rows,1):
            new_row=rows[number-1] if len(rows)>=number else {}
            old_cells=old_row.get('values',[]);new_cells=new_row.get('values',[])
            for col in range(max(len(old_cells),len(new_cells))):
                old=old_cells[col] if len(old_cells)>col else {}
                new=new_cells[col] if len(new_cells)>col else {}
                for field in ('userEnteredFormat','dataValidation','chipRuns','textFormatRuns'):
                    if old.get(field)!=new.get(field):
                        raise RuntimeError('Organic YTB native format changed')
                if (number,col) not in expected and old.get('userEnteredValue')!=new.get('userEnteredValue'):
                    raise RuntimeError('Organic YTB unrelated value changed')


def poll(env, output, apply):
    from kol_tracker import BASE, save_json, sheet_session, read_tab, slack_pages
    from update_meta_tracker import write_range
    path = Path(env.get('KOL_TRACKER_STATE_DIR', str(BASE/'data/processed/kol_tracker_runtime'))) / 'organic-youtube-slack-state.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    channel = env['KOL_TRACKER_SLACK_CHANNEL_ID']
    if state and state.get('channel') != channel:
        raise RuntimeError('YouTube Slack state channel changed')
    now = datetime.now(timezone.utc).timestamp()
    slack = requests.Session()
    slack.headers['Authorization'] = 'Bearer '+(env.get('KOL_TRACKER_SLACK_BOT_TOKEN') or env.get('KOL_TRACKER_SLACK_TOKEN') or env['SLACK_BOT_TOKEN'])
    history = slack_pages(slack,'conversations.history',{'channel':channel,'latest':now})
    roots = dict(state.get('threads', {}))
    messages = list(history)
    changed = set()
    for m in history:
        # Retain every thread root: ordinary launch updates can be posted later
        # inside a contract thread that originally had no YouTube link.
        if m.get('reply_count'):
            if m['ts'] not in roots or m.get('latest_reply') != roots[m['ts']].get('latest_reply') or m.get('reply_count') != roots[m['ts']].get('reply_count'):
                changed.add(m['ts'])
            roots[m['ts']] = m
    next_roots = {}
    for count,(ts, old) in enumerate(roots.items(),1):
        # Bootstrap all replies; thereafter query only replies since overlap.
        thread = slack_pages(slack,'conversations.replies',{'channel':channel,'ts':ts,'latest':now}) if ts in changed else []
        messages.append(old)
        messages.extend(thread)
        next_roots[ts] = old
        if count % 25 == 0 or count == len(roots):
            print('Organic YTB Slack threads: %d/%d (%d changed)'%(count,len(roots),len(changed)),flush=True)
    messages = list({m['ts']:m for m in messages}.values())
    parsed, issues = parse_announcements(messages)
    discovered = dict(state.get('candidates', {}))
    for candidate in parsed:
        discovered.setdefault(candidate['video_id'],candidate)
    candidates = list(discovered.values())
    session, endpoint = sheet_session(dict(env,KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK']))
    prop,native,values = read_tab(session,endpoint,'YTB',False)
    note_col = schema(values)
    existing = {video_id(str(v[2])) for v in values[1:] if len(v)>2}
    candidates = [c for c in candidates if c['video_id'] not in existing]
    api = fetch_videos(env,[c['video_id'] for c in candidates]) if candidates else {}
    proposed = []
    for c in candidates:
        item = api.get(c['video_id'])
        if not item:
            issues.append(dict(c,reason='Video unavailable in YouTube API'));continue
        published = datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York'))
        if published.year != 2026:
            continue
        proposed.append(dict(c,launch=published.date().isoformat()))
    proposed.sort(key=lambda c:(c['launch'],c['video_id']))
    save_json(output/'Organic-YTB-new-rows.json',{'proposed':proposed,'issues':issues,'messages':len(messages)})
    print('Organic YTB Slack: %d messages; %d new rows; %d review issues'%(len(messages),len(proposed),len(issues)),flush=True)
    if apply and proposed:
        summary = [i for i,v in enumerate(values) if v and str(v[0]).strip().lower()=='summary']
        if len(summary)!=1:
            raise RuntimeError('Organic YTB requires one Summary row')
        index=summary[0];sid=prop['sheetId'];body=[];expected={}
        if index<2 or any(not v or not v[0] for v in values[1:index]):
            raise RuntimeError('Organic YTB has blank data rows; review insertion')
        body.append({'insertDimension':{'range':{'sheetId':sid,'dimension':'ROWS','startIndex':index,'endIndex':index+len(proposed)},'inheritFromBefore':True}})
        for offset,c in enumerate(proposed):
            row=index+offset+1
            for kind in ['PASTE_FORMAT','PASTE_DATA_VALIDATION']:
                body.append({'copyPaste':{'source':{'sheetId':sid,'startRowIndex':index-1,'endRowIndex':index,'startColumnIndex':0,'endColumnIndex':note_col+1},'destination':{'sheetId':sid,'startRowIndex':row-1,'endRowIndex':row,'startColumnIndex':0,'endColumnIndex':note_col+1},'pasteType':kind}})
            if len(c['creator'])>26:
                body.append({'repeatCell':{'range':{'sheetId':sid,'startRowIndex':row-1,'endRowIndex':row,'startColumnIndex':0,'endColumnIndex':1},'cell':{'userEnteredFormat':{'wrapStrategy':'WRAP'}},'fields':'userEnteredFormat.wrapStrategy'}})
                body.append({'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'ROWS','startIndex':row-1,'endIndex':row},'properties':{'pixelSize':42},'fields':'pixelSize'}})
            date=(datetime.fromisoformat(c['launch'])-datetime(1899,12,30)).days
            for col,val in {0:c['creator'],1:date,2:c['post_link'],note_col:'Missing numeric KOL fee for CPM/CPE'}.items():
                body.append(write_range(sid,row,col,[val]));expected[(row,col)]=val
            for col,formula in {5:'=G%d+H%d'%(row,row),10:'=IF(AND(ISNUMBER(D%d),E%d>0),D%d/E%d*1000,"")'%(row,row,row,row),11:'=IF(AND(ISNUMBER(D%d),F%d>0),D%d/F%d,"")'%(row,row,row,row)}.items():
                body.append({'updateCells':{'range':{'sheetId':sid,'startRowIndex':row-1,'endRowIndex':row,'startColumnIndex':col,'endColumnIndex':col+1},'rows':[{'values':[{'userEnteredValue':{'formulaValue':formula}}]}],'fields':'userEnteredValue'}})
        # Inserting immediately before Summary need not expand SUM's end bound.
        for col in (3,4,5):
            target=native['sheets'][0]['data'][0]['rowData'][index]['values'][col]
            letter=chr(65+col);formula=target.get('userEnteredValue',{}).get('formulaValue','')
            if formula.upper() != '=SUM(%s2:%s%d)'%(letter,letter,index):
                raise RuntimeError('Organic YTB Summary formula changed; review insertion')
            body.append({'updateCells':{'range':{'sheetId':sid,'startRowIndex':index+len(proposed),'endRowIndex':index+len(proposed)+1,'startColumnIndex':col,'endColumnIndex':col+1},'rows':[{'values':[{'userEnteredValue':{'formulaValue':'=SUM(%s2:%s%d)'%(letter,letter,index+len(proposed))}}]}],'fields':'userEnteredValue'}})
        commit(session,endpoint,prop,native,body,expected,output,'Organic-YTB-insert')
        # Verify exact video identities and uniqueness, including inserted rows.
        fresh=read_tab(session,endpoint,'YTB',False)[2]
        for c in proposed:
            if sum(len(v)>2 and video_id(str(v[2]))==c['video_id'] for v in fresh)!=1:
                raise RuntimeError('Organic YTB inserted identity not unique')
        after_native=read_tab(session,endpoint,'YTB',False)[1]
        old_rows=native['sheets'][0]['data'][0]['rowData']
        fresh_rows=after_native['sheets'][0]['data'][0]['rowData']
        for old_index,old_row in enumerate(old_rows):
            new_index=old_index if old_index<index else old_index+len(proposed)
            new_cells=fresh_rows[new_index].get('values',[])
            for col,cell in enumerate(old_row.get('values',[])):
                new_cell=new_cells[col] if len(new_cells)>col else {}
                for field in ('userEnteredFormat','dataValidation','chipRuns','textFormatRuns'):
                    if cell.get(field)!=new_cell.get(field):raise RuntimeError('Organic YTB existing row format changed')
                entered=cell.get('userEnteredValue',{})
                if 'formulaValue' not in entered and entered!=new_cell.get('userEnteredValue',{}):
                    raise RuntimeError('Organic YTB existing row literal changed')
        print('Organic YTB inserted rows verified',flush=True)
    if apply:
        save_json(path,{'cursor':now,'channel':channel,'threads':next_roots,'candidates':discovered})


def run(env, output, apply):
    from kol_tracker import sheet_session, read_tab, verify_literal_target, save_json
    from update_meta_tracker import write_range
    session,endpoint=sheet_session(dict(env,KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK']))
    prop,native,values=read_tab(session,endpoint,'YTB',False);note_col=schema(values)
    data_rows=[(i,v) for i,v in enumerate(values[1:],2) if v and v[0] and str(v[0]).strip().lower()!='summary']
    try:
        api=fetch_videos(env,[video_id(str(v[2])) for _,v in data_rows if len(v)>2 and video_id(str(v[2]))])
        error=''
    except RuntimeError as exc:
        api={};error=str(exc)
    cells=native['sheets'][0]['data'][0]['rowData'];body=[];expected={};success=0;issues=[]
    def add(row,col,val):
        vs=cells[row-1].get('values',[]);target=vs[col] if len(vs)>col else {}
        if col in (5,10,11) and 'formulaValue' in target.get('userEnteredValue',{}):
            formula=target['userEnteredValue']['formulaValue']
            # Migrate only the known old four-counter template; retain custom
            # formulas. Shares/Saves are not part of the YouTube definition.
            if col==5 and formula.upper().replace(' ','')=='=G%d+H%d+I%d+J%d'%(row,row,row,row):
                body.append({'updateCells':{'range':{'sheetId':prop['sheetId'],'startRowIndex':row-1,'endRowIndex':row,'startColumnIndex':col,'endColumnIndex':col+1},'rows':[{'values':[{'userEnteredValue':{'formulaValue':'=G%d+H%d'%(row,row)}}]}],'fields':'userEnteredValue'}})
                expected[(row,col)]=val
            return
        verify_literal_target(target,val)
        body.append(write_range(prop['sheetId'],row,col,[val]));expected[(row,col)]=val
    for row,v in data_rows:
        vid=video_id(str(v[2])) if len(v)>2 else '';item=api.get(vid);changes={};reason=''
        if not vid:reason='Missing or unsupported YouTube link; existing metrics retained'
        elif error:reason=error+'; existing metrics retained'
        elif not item:reason='Video unavailable in YouTube API; existing metrics retained'
        else:
            stats=item.get('statistics',{});missing=[]
            for key,col in [('viewCount',4),('likeCount',6),('commentCount',7)]:
                if key in stats and str(stats[key]).isdigit():changes[col]=int(stats[key])
                else:missing.append(key)
            if 6 in changes and 7 in changes:changes[5]=changes[6]+changes[7]
            fee=v[3] if len(v)>3 else None
            if isinstance(fee,(int,float)) and not isinstance(fee,bool) and fee>=0:
                if changes.get(4):changes[10]=fee/changes[4]*1000
                if changes.get(5):changes[11]=fee/changes[5]
            else:reason='Missing numeric KOL fee for CPM/CPE'
            if missing:reason='Missing YouTube '+', '.join(missing)+'; existing missing metrics retained'+('; '+reason if reason else '')
        try:
            # Preflight all metric targets so a protected row cannot abort peers.
            for col,val in changes.items():
                target=cells[row-1].get('values',[]);target=target[col] if len(target)>col else {}
                if col in (5,10,11) and 'formulaValue' in target.get('userEnteredValue',{}):continue
                verify_literal_target(target,val)
            for col,val in changes.items():add(row,col,val)
            if changes:success+=1
        except RuntimeError:
            reason='YouTube API metrics target protected; existing metrics retained'
        old=str(v[note_col]) if len(v)>note_col else ''
        if not old or old.startswith(MANAGED):
            try:add(row,note_col,reason)
            except RuntimeError:issues.append({'row':row,'reason':'Failure reason target protected'})
        print('Organic YTB row %d: %s'%(row,reason or 'metrics ready'),flush=True)
    if success:add(1,4,'Views\n[%s update]'%datetime.now(ZoneInfo('America/New_York')).strftime('%m/%d'))
    save_json(output/'Organic-YTB-plan.json',{'successful_rows':success,'issues':issues,'writes':[{'row':r,'col':c,'value':v} for (r,c),v in expected.items()]})
    if apply:commit(session,endpoint,prop,native,body,expected,output,'Organic-YTB-metrics')
    print('Organic YTB: %d rows%s'%(success,' verified' if apply else ' planned'),flush=True)
    if error:raise RuntimeError(error)
