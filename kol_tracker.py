"""Independent production tracker. poll/daily/check default to read-only."""
import argparse
import fcntl
import json
import math
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from add_tiktok_tracker_rows import MARKER, URL, creator_heading, plain_text, post_key as tt_post, spark_codes
from sync_creator_tracker import BASE, code_key as meta_code, get_json, graph_pages, ad_location, post_key as ig_post
from update_meta_tracker import exact_mapping, new_row_status, run as meta_daily, write_range
from update_tiktok_tracker import creator_segment, fetch_report, fetch_ad_details, normalized
from kol_organic_meta import run as organic_meta_daily

CHICAGO = ZoneInfo('America/Chicago')


def save_json(path, value):
    """Private atomic state writes; a failed run never advances its cursor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.tracker-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def slack_pages(session, method, params):
    messages, cursor = [], ''
    while True:
        data = get_json(session, 'https://slack.com/api/' + method, dict(params, cursor=cursor, limit=100))
        messages.extend(data.get('messages', []))
        cursor = data.get('response_metadata', {}).get('next_cursor', '')
        if not cursor:
            return messages


def read_incremental_slack(env, state, now):
    session = requests.Session()
    token = env.get('KOL_TRACKER_SLACK_BOT_TOKEN') or env.get('KOL_TRACKER_SLACK_TOKEN') or env.get('SLACK_BOT_TOKEN')
    if not token:
        raise RuntimeError('Missing Slack bot token')
    session.headers['Authorization'] = 'Bearer ' + token
    channel = env['KOL_TRACKER_SLACK_CHANNEL_ID']
    retention = int(env.get('KOL_TRACKER_SLACK_RETENTION_DAYS', '90'))
    oldest = max(now - retention * 86400, float(state.get('cursor', now - retention * 86400)) - 86400)
    history = slack_pages(session, 'conversations.history', {'channel': channel, 'oldest': oldest, 'latest': now})
    roots = {k: v for k, v in state.get('threads', {}).items() if float(k) >= now - retention * 86400}
    messages = []
    for message in history:
        if MARKER.search(message.get('text', '')):
            roots[message.get('thread_ts', message['ts'])] = message
            messages.append(message)
    # Re-read tracked threads, including old roots with newly supplied codes.
    # Keep the original parent context; strict parsers fence creator sections.
    for ts in list(roots):
        thread = slack_pages(session, 'conversations.replies', {'channel': channel, 'ts': ts, 'latest': now})
        if thread:
            roots[ts] = thread[0]
            messages.extend(thread)
    unique = {m['ts']: m for m in messages}
    return list(unique.values()), {'cursor': now, 'threads': roots, 'channel': channel}


def parse_messages(messages):
    """Platform-fenced link/code pairs; only marked content or its replies."""
    byts = {m['ts']: m for m in messages}
    drafts, issues = [], []
    for message in sorted(messages, key=lambda m: float(m['ts'])):
        text = plain_text(message.get('text', ''))
        parent = byts.get(message.get('thread_ts', ''), {})
        marked = bool(MARKER.search(text))
        if not marked and not MARKER.search(parent.get('text', '')):
            continue
        text = MARKER.sub('', text).lstrip(' *\n')
        for section in re.split(r'(?:^|\n)\s*\d+[.)]\s+', text):
            creator = creator_heading(section)
            links = list(URL.finditer(section))
            for i, found in enumerate(links):
                link = found.group().rstrip(').,*')
                platform = 'Meta' if ig_post(link) else 'TikTok' if tt_post(link) else ''
                if not platform:
                    continue
                stop = links[i + 1].start() if i + 1 < len(links) else len(section)
                tail = section[found.end():stop]
                codes = list(dict.fromkeys(meta_code(c) for c in re.findall(r'(?:adcode-)?Q9jT[A-Za-z0-9_-]+', tail))) if platform == 'Meta' else spark_codes(tail)
                if not creator:
                    issues.append({'ts': message['ts'], 'reason': 'Creator missing', 'platform': platform})
                    continue
                drafts.append({'platform': platform, 'creator': creator, 'post_link': link,
                               'post_key': ig_post(link) if platform == 'Meta' else tt_post(link),
                               'ad_code': codes[0] if len(codes) == 1 else '', 'ts': message['ts'],
                               'announcement_ts': message['ts'],
                               'root_ts': message.get('thread_ts', message['ts']),
                               'conflict': len(codes) > 1})
    # Code-only replies complete one unambiguous pending post per platform.
    for message in messages:
        parent_ts = message.get('thread_ts')
        if not parent_ts or parent_ts == message['ts'] or not MARKER.search(byts.get(parent_ts, {}).get('text', '')):
            continue
        text = plain_text(message.get('text', ''))
        if URL.search(text) or MARKER.search(text):
            continue
        for platform, codes in [('Meta', list(dict.fromkeys(meta_code(c) for c in re.findall(r'(?:adcode-)?Q9jT[A-Za-z0-9_-]+', text)))),
                                ('TikTok', spark_codes(text))]:
            targets = [d for d in drafts if d['root_ts'] == parent_ts and d['platform'] == platform and not d['ad_code'] and not d['conflict']]
            if len(codes) == 1 and len(targets) == 1:
                targets[0]['ad_code'] = codes[0]
    complete = {}
    for draft in drafts:
        if draft['ad_code'] and not draft['conflict']:
            complete.setdefault((draft['platform'], draft['post_key'], draft['ad_code']), draft)
        else:
            issues.append({'ts': draft['ts'], 'creator': draft['creator'], 'platform': draft['platform'], 'reason': 'Missing or ambiguous code'})
    return list(complete.values()), issues


def sheet_session(env):
    sid = re.search(r'/spreadsheets/d/([^/]+)', env['KOL_TRACKER_GOOGLE_SHEETS_LINK']).group(1)
    credentials = Credentials.from_service_account_file(
        str(BASE / env.get('GOOGLE_SERVICE_ACCOUNT_FILE', 'credentials/google-service-account.json')),
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return AuthorizedSession(credentials), 'https://sheets.googleapis.com/v4/spreadsheets/' + sid


def read_tab(session, endpoint, title):
    metadata = get_json(session, endpoint, {'fields': 'sheets(properties)'})
    prop = next(s['properties'] for s in metadata['sheets'] if s['properties']['title'] == title)
    # Metadata-grounded, bounded native reads; preserve every existing column.
    count = prop['gridProperties']['columnCount']
    label, n = '', count
    while n:
        n, r = divmod(n - 1, 26); label = chr(65 + r) + label
    data = get_json(session, endpoint, {'ranges': "'%s'!A1:%s%d" % (title, label, prop['gridProperties']['rowCount']),
        'includeGridData': 'true', 'fields': 'sheets(properties,tables,merges,protectedRanges,data(rowData(values(userEnteredValue,userEnteredFormat,dataValidation,chipRuns,textFormatRuns,effectiveValue))))'})
    tab = data['sheets'][0]
    rows = tab.get('data', [{}])[0].get('rowData', [])
    values = [[next(iter(c.get('effectiveValue', {}).values()), '') for c in row.get('values', [])] for row in rows]
    if not values:
        raise RuntimeError('Missing Tracker headers')
    headers = values[0]
    if title == 'Meta':
        if len(headers) < 22 or [headers[i] for i in [1, 6, 7, 8]] != ['Creator', 'Post Link', 'Ad Code', 'Status']:
            raise RuntimeError('Meta schema changed')
    elif title == 'TikTok':
        if headers[:7] != ['Creator', 'Content Brief', 'Ad Code', 'Post Link', 'Launch Date', 'Status', 'Note'] or len(headers) < 14 or headers[13] != 'Ad IDs':
            raise RuntimeError('TikTok schema changed; expected N1 Ad IDs')
    return prop, data, values


def columns(platform):
    return (1, 7, 6, 2, 8, 22) if platform == 'Meta' else (0, 2, 3, 4, 5, 14)


def dedup_new(platform, values, candidates):
    creator_col, code_col, post_col, _, _, _ = columns(platform)
    normal_code = meta_code if platform == 'Meta' else lambda v: str(v or '').lstrip('#').strip()
    post = ig_post if platform == 'Meta' else tt_post
    existing = [r + [''] * 22 for r in values[2:] if len(r) > creator_col and r[creator_col]]
    source_codes, source_posts = {}, {}
    for item in candidates:
        source_codes.setdefault(item['ad_code'], set()).add(item['post_key'])
        source_posts.setdefault(item['post_key'], set()).add(item['ad_code'])
    new, skipped = [], []
    for item in candidates:
        if len(source_codes[item['ad_code']]) > 1 or len(source_posts[item['post_key']]) > 1:
            skipped.append({'creator': item['creator'], 'reason': 'Conflicting Slack identity'}); continue
        if any(normal_code(r[code_col]) == item['ad_code'] or post(r[post_col]) == item['post_key'] for r in existing):
            continue
        new.append(item)
        stub = [''] * 22
        stub[creator_col], stub[code_col], stub[post_col] = item['creator'], item['ad_code'], item['post_link']
        existing.append(stub)
    return new, skipped


def meta_inventory(env):
    s = requests.Session(); s.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
    base = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION', 'v23.0')
    account = 'act_' + env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    info = get_json(s, base + '/' + account, {'fields': 'timezone_name'})
    ads = graph_pages(s, base + '/' + account + '/ads', {'fields': 'id,name,created_time,effective_status,campaign{id,name},adset{id,name},creative{id,branded_content}', 'limit': 100})
    return info, ads


def new_row(platform, item, info=None, ads=None):
    creator_col, code_col, post_col, date_col, status_col, width = columns(platform)
    row = [''] * width
    row[creator_col], row[code_col], row[post_col] = item['creator'], item['ad_code'], item['post_link']
    if platform == 'TikTok':
        row[code_col] = '#' + row[code_col]
    day = datetime.fromtimestamp(float(item['announcement_ts']), CHICAGO).date()
    if platform == 'Meta' and ads is not None:
        matches = [a for a in ads if meta_code(a.get('creative', {}).get('branded_content', {}).get('instagram_boost_post_access_token')) == item['ad_code']]
        if matches:
            day = min(datetime.strptime(a['created_time'], '%Y-%m-%dT%H:%M:%S%z').astimezone(ZoneInfo(info['timezone_name'])).date() for a in matches)
            row[status_col] = new_row_status(matches) or ''
            row[0] = ' & '.join(sorted({ad_location(a) for a in matches if a.get('effective_status') == 'ACTIVE'}))
    row[date_col] = (day - datetime(1899, 12, 30).date()).days
    return row


def verify_literal_target(target, value):
    if target.get('chipRuns') or target.get('textFormatRuns') or 'formulaValue' in target.get('userEnteredValue', {}):
        raise RuntimeError('Refusing to overwrite formula or rich target')
    rule = target.get('dataValidation')
    if not rule or value in ['', None]:
        return
    kind = rule.get('condition', {}).get('type')
    allowed = [x.get('userEnteredValue') for x in rule.get('condition', {}).get('values', [])]
    if (kind == 'DATE_IS_VALID' and isinstance(value, (int, float))) or (kind == 'ONE_OF_LIST' and value in allowed):
        return
    raise RuntimeError('Value does not satisfy native validation')


def insert_rows(session, endpoint, platform, prop, native, values, proposed, output, apply):
    if not proposed:
        return 0
    tab = native['sheets'][0]
    if tab.get('tables') or tab.get('merges') or tab.get('protectedRanges'):
        raise RuntimeError('Structured target needs explicit inspection')
    ci, _, _, di, _, width = columns(platform)
    creator_indexes = [i for i, r in enumerate(values) if i >= 2 and len(r) > ci and r[ci]]
    if not creator_indexes:
        raise RuntimeError('Missing row exemplar')
    cells = tab['data'][0]['rowData']
    # Bottom-up native insertions keep dates ascending and later briefs intact.
    placements = []
    for row in proposed:
        edge = next((i for i in creator_indexes if len(values[i]) > di and isinstance(values[i][di], (int, float)) and values[i][di] > row[di]), creator_indexes[-1] + 1)
        placements.append((edge, row))
    requests_body, inserted = [], []
    for edge, row in sorted(placements, key=lambda p: (p[0], p[1][di]), reverse=True):
        exemplar = max(2, edge - 1)
        template = cells[exemplar].get('values', [])
        for col, value in enumerate(row):
            # Only constraints/formats are copied; another Creator's value is not.
            verify_literal_target({'dataValidation': template[col].get('dataValidation')} if col < len(template) else {}, value)
        requests_body.append({'insertDimension': {'range': {'sheetId': prop['sheetId'], 'dimension': 'ROWS', 'startIndex': edge, 'endIndex': edge + 1}, 'inheritFromBefore': True}})
        source_index = exemplar + (1 if exemplar >= edge else 0)
        source = {'sheetId': prop['sheetId'], 'startRowIndex': source_index, 'endRowIndex': source_index + 1, 'startColumnIndex': 0, 'endColumnIndex': width}
        dest = dict(source, startRowIndex=edge, endRowIndex=edge + 1)
        for kind in ['PASTE_FORMAT', 'PASTE_DATA_VALIDATION']:
            requests_body.append({'copyPaste': {'source': source, 'destination': dest, 'pasteType': kind}})
        requests_body.append(write_range(prop['sheetId'], edge + 1, 0, row))
        inserted.append((edge, row))
    save_json(output / (platform + '-insert-before.json'), native)
    if not apply:
        print(platform + ': proposed new rows ' + str(len(proposed)), flush=True); return len(proposed)
    if read_tab(session, endpoint, platform)[1] != native:
        raise RuntimeError('Concurrent sheet edit; no insertion')
    response = session.post(endpoint + ':batchUpdate', json={'requests': requests_body}, timeout=90)
    response.raise_for_status()
    _, after, _ = read_tab(session, endpoint, platform)
    after_cells = after['sheets'][0]['data'][0]['rowData']
    expected = list(cells)
    for edge, row in inserted:
        expected.insert(edge, {'new_row_values': row})
    for index, old in enumerate(expected):
        fresh = after_cells[index].get('values', []) if index < len(after_cells) else []
        if 'new_row_values' in old:
            for col, value in enumerate(old['new_row_values']):
                observed = next(iter(fresh[col].get('userEnteredValue', {}).values()), '') if col < len(fresh) else ''
                if observed != value:
                    raise RuntimeError('Inserted row verification failed; do not replay')
        else:
            previous = old.get('values', [])
            for col in range(max(len(previous), len(fresh))):
                a = dict(previous[col]) if col < len(previous) else {}
                b = dict(fresh[col]) if col < len(fresh) else {}
                a.pop('effectiveValue', None); b.pop('effectiveValue', None)
                # Sheets adjusts formula references when native rows move.
                if 'formulaValue' in a.get('userEnteredValue', {}):
                    if 'formulaValue' not in b.get('userEnteredValue', {}):
                        raise RuntimeError('Existing formula lost')
                    a.pop('userEnteredValue'); b.pop('userEnteredValue')
                if a != b:
                    raise RuntimeError('Existing native row changed; do not replay')
    save_json(output / (platform + '-insert-after.json'), after)
    print(platform + ': inserted and verified ' + str(len(proposed)), flush=True)
    return len(proposed)


def poll(env, state_path, output, apply):
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state and state.get('channel') != env['KOL_TRACKER_SLACK_CHANNEL_ID']:
        raise RuntimeError('State belongs to another Slack channel')
    messages, next_state = read_incremental_slack(env, state, datetime.now(timezone.utc).timestamp())
    candidates, issues = parse_messages(messages)
    session, endpoint = sheet_session(env)
    report = {'messages': len(messages), 'issues': issues, 'platforms': {}}
    for platform in ['Meta', 'TikTok']:
        prop, native, values = read_tab(session, endpoint, platform)
        items, skips = dedup_new(platform, values, [i for i in candidates if i['platform'] == platform])
        info = ads = None
        if items and platform == 'Meta':
            try:
                info, ads = meta_inventory(env)
            except RuntimeError:
                report['platforms'][platform] = {'api_fallback': 'Slack date; no inferred status'}
        proposed = [new_row(platform, item, info, ads) for item in items]
        inserted = insert_rows(session, endpoint, platform, prop, native, values, proposed, output, apply)
        report['platforms'].setdefault(platform, {}).update(new_rows=inserted, conflicts=skips)
    save_json(output / 'poll-report.json', report)
    if apply:
        save_json(state_path, next_state)
    print(json.dumps(report, ensure_ascii=False), flush=True)


def tiktok_bindings(values, ads, details=None):
    """Exact post IDs precede provisional names; never silently replace N."""
    byid = {a['dimensions']['ad_id']: a for a in ads}
    detail_byid = {str(a['ad_id']): a for a in (details or [])}
    planned, skipped = [], []
    for index, original in enumerate(values[2:], 3):
        if not original or not original[0]:
            continue
        row = original + [''] * 14
        binding = str(row[13]).strip()
        if binding and not re.fullmatch(r'\d{10,}(?:[\s,;]+\d{10,})*', binding):
            skipped.append({'row': index, 'reason': 'Invalid persisted Ad IDs'}); continue
        ids = re.findall(r'\d{10,}', binding)
        method = 'persisted_ad_ids' if ids else 'name_date_provisional'
        video = re.search(r'/video/(\d+)', str(row[3]))
        post_id = video.group(1) if video else None
        exact = sorted({str(a['ad_id']) for a in (details or [])
                        if post_id and str(a.get('tiktok_item_id', '')) == post_id})
        if ids and post_id and details is not None:
            conflicts = [aid for aid in ids if aid in detail_byid
                         and detail_byid[aid].get('tiktok_item_id')
                         and str(detail_byid[aid]['tiktok_item_id']) != post_id]
            if conflicts or (exact and set(exact) != set(ids)):
                skipped.append({'row': index, 'reason': 'Post ID conflicts with persisted binding; preserve row'}); continue
            if exact:
                method = 'post_id_exact_verified_binding'
        if not ids and str(row[5]).lower() in ['pause', 'paused']:
            skipped.append({'row': index, 'reason': 'Historical paused row without Ad IDs',
                            'exact_candidate_ids': exact}); continue
        if not ids and exact:
            ids = exact
            method = 'post_id_exact'
        if not ids and post_id and details is not None:
            skipped.append({'row': index, 'reason': 'No exact Post ID match; no name-only fallback'}); continue
        if not ids:
            aliases = {normalized(row[0])}
            handle = re.search(r'/@([^/]+)', str(row[3]))
            if handle:
                aliases.add(normalized(handle.group(1)))
            day = datetime(1899, 12, 30).date() + timedelta(days=row[4]) if isinstance(row[4], (int, float)) else None
            matches = []
            for ad in ads:
                name = ad['metrics']['ad_name']
                if creator_segment(name) not in aliases:
                    continue
                try:
                    date = datetime.strptime(name[:6], '%y%m%d').date()
                except ValueError:
                    continue
                if day and -1 <= (date - day).days <= 14:
                    matches.append(ad)
            # One identity/date/name group can have multiple targeting ads.
            groups = {a['metrics']['ad_name'] for a in matches}
            if len(groups) == 1:
                ids = [a['dimensions']['ad_id'] for a in matches]
        ids = sorted(set(ids))
        if not ids or any(aid not in byid for aid in ids):
            skipped.append({'row': index, 'reason': 'Missing or ambiguous Ad ID binding'}); continue
        ms = [byid[aid]['metrics'] for aid in ids]
        required = ['spend', 'complete_payment', 'complete_payment_roas', 'reach', 'impressions', 'video_play_actions', 'video_watched_2s']
        if any(k not in m or not math.isfinite(float(m[k])) or float(m[k]) < 0 for m in ms for k in required):
            skipped.append({'row': index, 'reason': 'Invalid API metrics'}); continue
        spend = sum(float(m['spend']) for m in ms)
        impressions = sum(float(m['impressions']) for m in ms)
        metric = [spend, sum(float(m['complete_payment']) for m in ms),
                  float(ms[0]['complete_payment_roas']) if len(ms) == 1 else row[9],
                  float(ms[0]['reach']) if len(ms) == 1 else row[10],
                  sum(float(m['video_play_actions']) for m in ms),
                  sum(float(m['video_watched_2s']) for m in ms) / impressions if impressions else None]
        planned.append({'row': index, 'ad_ids': ids, 'metrics': metric, 'preserve': [9, 10] if len(ms) > 1 else [], 'new_binding': not bool(binding), 'match_method': method})
    claims = Counter(aid for p in planned for aid in p['ad_ids'])
    accepted = [p for p in planned if all(claims[aid] == 1 for aid in p['ad_ids'])]
    skipped.extend({'row': p['row'], 'reason': 'Ad claimed by multiple rows'} for p in planned if p not in accepted)
    return accepted, skipped


def tiktok_daily(env, output, apply):
    session, endpoint = sheet_session(env)
    prop, native, values = read_tab(session, endpoint, 'TikTok')
    details = fetch_ad_details(env)
    planned, skipped = tiktok_bindings(values, fetch_report(env), details)
    save_json(output / 'TikTok-daily-plan.json', {'planned': planned, 'skipped': skipped, 'window': 'API lifetime through request time'})
    print('TikTok metrics planned: %d; skipped: %d' % (len(planned), len(skipped)), flush=True)
    if not apply or not planned:
        return
    cells = native['sheets'][0]['data'][0]['rowData']
    body, expected = [], json.loads(json.dumps(native))
    expected_cells = expected['sheets'][0]['data'][0]['rowData']
    for p in planned:
        changes = [(7+i, v) for i, v in enumerate(p['metrics']) if 7+i not in p['preserve']]
        if p['new_binding']:
            changes.append((13, '\n'.join(p['ad_ids'])))
        for col, value in changes:
            row = cells[p['row']-1].get('values', [])
            verify_literal_target(row[col] if col < len(row) else {}, value)
            body.append(write_range(prop['sheetId'], p['row'], col, [value]))
    header = 'Lifetime Spend\n[%s update]' % datetime.now(CHICAGO).strftime('%m/%d')
    body.append(write_range(prop['sheetId'], 1, 7, [header]))
    for request in body:
        update = request['updateCells']; region = update['range']
        row = expected_cells[region['startRowIndex']].setdefault('values', [])
        col = region['startColumnIndex']; row.extend({} for _ in range(max(0, col+1-len(row))))
        row[col]['userEnteredValue'] = update['rows'][0]['values'][0].get('userEnteredValue', {})
        if not row[col]['userEnteredValue']:
            row[col].pop('userEnteredValue')
    save_json(output / 'TikTok-daily-before.json', native)
    if read_tab(session, endpoint, 'TikTok')[1] != native:
        raise RuntimeError('Sheet changed; no daily write')
    response = session.post(endpoint + ':batchUpdate', json={'requests': body}, timeout=90)
    response.raise_for_status()
    after = read_tab(session, endpoint, 'TikTok')[1]
    for obj in [expected, after]:
        for row in obj['sheets'][0]['data'][0]['rowData']:
            for cell in row.get('values', []):
                cell.pop('effectiveValue', None)
            while row.get('values') and not row['values'][-1]:
                row['values'].pop()
    save_json(output / 'TikTok-daily-after.json', after)
    if expected != after:
        raise RuntimeError('Daily native readback failed; inspect backup')
    print('TikTok daily metrics and bindings verified', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', choices=['poll', 'daily', 'organic', 'check'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv(BASE / '.env')
    env = dict(os.environ)
    state_dir = Path(env.get('KOL_TRACKER_STATE_DIR', str(BASE / 'data/processed/kol_tracker_runtime')))
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'task.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if args.task not in ['daily', 'organic']:
                print('Another tracker task is running; skipped', flush=True); return
            print('Another tracker task is running; daily update queued', flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        output = state_dir / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        output.mkdir(parents=True)
        if args.task == 'check':
            session, endpoint = sheet_session(env)
            for title in ['Meta', 'TikTok']:
                prop, _, _ = read_tab(session, endpoint, title)
                print(title + ' native sheet accessible: ' + str(prop['sheetId']))
            print('Tracker configuration check passed; no writes'); return
        if args.task == 'poll':
            poll(env, state_dir / 'slack-state.json', output, args.apply)
        elif args.task == 'organic':
            organic_meta_daily(env, output, args.apply)
        else:
            failures = []
            for name, action in [('Meta', lambda: meta_daily(env, output, args.apply)),
                                 ('TikTok', lambda: tiktok_daily(env, output, args.apply))] + (
                                 [('Organic Meta', lambda: organic_meta_daily(env, output, args.apply))]
                                 if env.get('KOL_ORGANIC_SHEETS_LINK') else []):
                try:
                    action()
                except Exception as error:
                    # Do not expose API URLs, request headers or codes in logs.
                    failures.append(name)
                    print(name + ' failed (' + type(error).__name__ + '); inspect private run backup', flush=True)
            if failures:
                raise SystemExit('Daily tasks failed: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
