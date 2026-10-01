"""Independent Slack-only TikTok row insertion. Default is read-only preview."""
import argparse
import html
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from sync_creator_tracker import BASE, get_json, read_slack
from update_meta_tracker import write_range

MARKER = re.compile(r'\[KOL Content is Live\]', re.I)
URL = re.compile(r'https?://[^\s<>]+')
SPARK = re.compile(r'(?<![A-Za-z0-9_/#?=])#?([A-Za-z0-9+/_-]{30,}={0,2})(?![A-Za-z0-9+/=_-])')


def code_key(value):
    return str(value or '').strip().lstrip('#')


def post_key(value):
    try:
        u = urlsplit(str(value or '').strip())
    except ValueError:
        return ''
    host = (u.hostname or '').lower()
    if host not in {'www.tiktok.com', 'tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'}:
        return ''
    match = re.search(r'/video/(\d+)', u.path)
    if match:
        return 'video:' + match.group(1)
    path = u.path.rstrip('/')
    if re.fullmatch(r'/t/[A-Za-z0-9]+', path) or (
            host in {'vm.tiktok.com', 'vt.tiktok.com'} and re.fullmatch(r'/[A-Za-z0-9]+', path)):
        return 'short:' + host.removeprefix('www.') + path
    return ''


def plain_text(text):
    text = html.unescape(text or '')
    text = re.sub(r'<https?://[^>|]+\|([^>]+)>(?=[‘’\']s)', r'\1', text)
    return re.sub(r'<(https?://[^>|]+)(?:\|[^>]+)?>', r'\1', text)


def creator_heading(section):
    heading = next((line.strip(' *•') for line in section.splitlines() if line.strip(' *•')), '')
    match = re.match(r"^(.+?)(?:[‘’']s|[‘’'])(?:\s|$)", heading)
    if match:
        return match.group(1).strip(" *‘’'")
    if not heading or re.search(r'https?://|<@|^#', heading):
        return ''
    return re.split(r'\s+-\s+|\s+content\s', heading, maxsplit=1, flags=re.I)[0].strip(' *')


def spark_codes(text):
    # URLs, Meta codes, and mentions must never become Spark authorization codes.
    text = URL.sub(' ', text)
    text = re.sub(r'(?:adcode-)?Q9jT[A-Za-z0-9_-]+', ' ', text)
    return list(dict.fromkeys(m.group(1) for m in SPARK.finditer(text)))


def parse_tiktok_announcements(messages):
    drafts, issues = [], []
    byts = {m['ts']: m for m in messages}
    for msg in sorted(messages, key=lambda m: float(m['ts'])):
        text = plain_text(msg.get('text', ''))
        if not MARKER.search(text):
            continue
        # Keep any content after the marker on its heading line.
        text = re.sub(r'^.*?\[KOL Content is Live\][ *]*(?:<@[^>]+>[ *]*)*', '', text, count=1, flags=re.I | re.S)
        for section in re.split(r'(?:^|\n)\s*\d+[.)]\s+', text):
            creator = creator_heading(section)
            urls = list(URL.finditer(section))
            for index, match in enumerate(urls):
                link = match.group().rstrip(').,*')
                key = post_key(link)
                if not key:
                    continue
                stop = urls[index + 1].start() if index + 1 < len(urls) else len(section)
                codes = spark_codes(section[match.end():stop])
                if not creator:
                    issues.append({'ts': msg['ts'], 'reason': 'TikTok creator heading missing'})
                    continue
                drafts.append({'creator': creator, 'post_link': link, 'post_key': key,
                               'ad_code': codes[0] if len(codes) == 1 else '',
                               'ts': msg['ts'], 'announcement_ts': msg['ts'],
                               'reason': 'Multiple Spark codes' if len(codes) > 1 else ''})
    # A code-only reply may complete exactly one pending TikTok post in its
    # marked parent. Multi-content/ambiguous threads are left for review.
    for msg in sorted(messages, key=lambda m: float(m['ts'])):
        parent_ts = msg.get('thread_ts')
        if not parent_ts or parent_ts == msg['ts']:
            continue
        parent = byts.get(parent_ts, {})
        if not MARKER.search(parent.get('text', '')):
            continue
        text = plain_text(msg.get('text', ''))
        if MARKER.search(text) or URL.search(text):
            continue
        codes = spark_codes(text)
        targets = [d for d in drafts if d['announcement_ts'] == parent_ts]
        if len(codes) == 1 and len(targets) == 1 and not targets[0]['ad_code'] and not targets[0]['reason']:
            targets[0].update(ad_code=codes[0], ts=msg['ts'])
    # Older announcements sometimes give a TT code without a TT post link.
    # Include these for existing-code reconciliation, not automatic insertion.
    for msg in sorted(messages, key=lambda m: float(m['ts'])):
        parent = byts.get(msg.get('thread_ts', ''), {})
        text = plain_text(msg.get('text', ''))
        marked = bool(MARKER.search(text))
        if not marked and not MARKER.search(parent.get('text', '')):
            continue
        heading_text = MARKER.sub('', text).lstrip(' *\n')
        creator = creator_heading(heading_text)
        if not creator:
            continue
        for line in text.splitlines():
            label = re.search(r'(?:^|[•*\s])(?:TT|TikTok)\s*:?[\s*-]+(.+)', line, re.I)
            if not label:
                continue
            for code in spark_codes(label.group(1)):
                if any(d['ad_code'] == code for d in drafts):
                    continue
                drafts.append({'creator': creator, 'post_link': '', 'post_key': '', 'ad_code': code,
                               'ts': msg['ts'], 'announcement_ts': msg['ts'] if marked else msg['thread_ts'],
                               'reason': ''})
    unique = {}
    for draft in drafts:
        if not draft['ad_code']:
            issues.append(dict(draft, reason=draft['reason'] or 'TikTok Spark Ad Code missing'))
            continue
        unique.setdefault((draft['post_key'], draft['ad_code']), draft)
    return list(unique.values()), issues


def plan_new_rows(values, candidates):
    existing = [(i + 1, r + [''] * max(0, 13 - len(r))) for i, r in enumerate(values)
                if i >= 2 and r and r[0]]
    proposed, decisions = [], []
    post_codes = {}
    code_posts = {}
    for item in candidates:
        if item['post_key']:
            post_codes.setdefault(item['post_key'], set()).add(item['ad_code'])
            code_posts.setdefault(item['ad_code'], set()).add(item['post_key'])
    for item in candidates:
        codes = [(n, r) for n, r in existing if code_key(r[2]) == item['ad_code']]
        posts = [(n, r) for n, r in existing if post_key(r[3]) and post_key(r[3]) == item['post_key']]
        if len(post_codes.get(item['post_key'], set())) > 1 or len(code_posts.get(item['ad_code'], set())) > 1:
            reason = 'review_conflict'
        elif codes:
            n, row = codes[0]
            incompatible = post_key(row[3]) and post_key(row[3]) != item['post_key']
            # Different short URLs may resolve to the same post. Do not append
            # or replace user data; flag this rather than declaring it verified.
            reason = 'review_link_difference' if incompatible else 'existing_code'
            if len(codes) > 1 or any(k != n for k, _ in posts):
                reason = 'review_conflict'
        elif posts:
            reason = 'review_conflict'
        elif not item['post_key']:
            reason = 'review_missing_post_link'
        else:
            reason = 'proposed_new'
            proposed.append(item)
            existing.append((-len(proposed), [item['creator'], '', item['ad_code'], item['post_link']]))
        decisions.append({'creator': item['creator'], 'decision': reason,
                          'sheet_rows': [n for n, _ in codes + posts if n > 0]})
    return proposed, decisions


def insertion_requests(sheet_id, edge, proposed, template):
    """Copy only native structure; every new literal is Slack-sourced or blank."""
    source = {'sheetId': sheet_id, 'startRowIndex': edge, 'endRowIndex': edge + 1,
              'startColumnIndex': 0, 'endColumnIndex': 13}
    requests_body = [{'insertDimension': {'range': {'sheetId': sheet_id, 'dimension': 'ROWS',
                     'startIndex': edge + 1, 'endIndex': edge + 1 + len(proposed)}, 'inheritFromBefore': True}}]
    new_values = []
    for index, item in enumerate(proposed, edge + 1):
        row = [item['creator'], '', '#' + item['ad_code'], item['post_link']] + [''] * 9
        # A code-only reply completing a known post retains that post's
        # announcement timestamp. A new marked announcement uses its own ts.
        launch = item.get('api_launch_date')
        if launch:
            day = datetime.strptime(launch, '%Y-%m-%d').date()
        else:
            timestamp = item.get('announcement_ts') or item.get('ts')
            if not timestamp:
                raise RuntimeError('Missing Slack date for new content')
            day = datetime.fromtimestamp(float(timestamp), ZoneInfo('America/Chicago')).date()
        row[4] = (day - datetime(1899, 12, 30).date()).days
        for col, value in enumerate(row):
            rule = template[col].get('dataValidation', {}) if col < len(template) else {}
            if value and rule:
                allowed = [v.get('userEnteredValue') for v in rule.get('condition', {}).get('values', [])]
                kind = rule.get('condition', {}).get('type')
                if col == 4 and kind == 'DATE_IS_VALID' and isinstance(value, int):
                    continue
                if kind != 'ONE_OF_LIST' or value not in allowed:
                    raise RuntimeError('New TikTok value fails exemplar validation')
        dest = dict(source, startRowIndex=index, endRowIndex=index + 1)
        for kind in ['PASTE_FORMAT', 'PASTE_DATA_VALIDATION']:
            requests_body.append({'copyPaste': {'source': source, 'destination': dest, 'pasteType': kind}})
        requests_body.append(write_range(sheet_id, index + 1, 0, row))
        new_values.append(row)
    return requests_body, new_values


def read_tiktok(env):
    sid = re.search(r'/spreadsheets/d/([^/]+)', env['KOL_TRACKER_GOOGLE_SHEETS_LINK']).group(1)
    session = AuthorizedSession(Credentials.from_service_account_file(
        str(BASE / env.get('GOOGLE_SERVICE_ACCOUNT_FILE', 'credentials/google-service-account.json')),
        scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']))
    endpoint = 'https://sheets.googleapis.com/v4/spreadsheets/' + sid
    meta = get_json(session, endpoint, {'fields': 'spreadsheetId,sheets(properties)'})
    prop = next(s['properties'] for s in meta['sheets'] if s['properties']['title'] == 'TikTok')
    values = []
    for start in range(1, prop['gridProperties']['rowCount'] + 1, 400):
        stop = min(start + 399, prop['gridProperties']['rowCount'])
        chunk = get_json(session, endpoint + '/values/' + quote("'TikTok'!A%d:M%d" % (start, stop), safe=''),
                         {'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values', [])
        values.extend(chunk + [[]] * (stop - start + 1 - len(chunk)))
    if values[0][:7] != ['Creator', 'Content Brief', 'Ad Code', 'Post Link', 'Launch Date', 'Status', 'Note']:
        raise RuntimeError('TikTok schema changed')
    return {'metadata': meta, 'tab': prop, 'values': values}


def apply_new_rows(env, before, proposed, output):
    """Insert Slack identity + announcement date; no guessed status/metrics."""
    if not proposed:
        return
    import fcntl
    with (output / 'insert.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if read_tiktok(env)['values'] != before['values']:
            raise RuntimeError('Concurrent sheet edit; not written')
        prop, values = before['tab'], before['values']
        sid = before['metadata']['spreadsheetId']
        endpoint = 'https://sheets.googleapis.com/v4/spreadsheets/' + sid
        session = AuthorizedSession(Credentials.from_service_account_file(
            str(BASE / env.get('GOOGLE_SERVICE_ACCOUNT_FILE', 'credentials/google-service-account.json')),
            scopes=['https://www.googleapis.com/auth/spreadsheets']))
        # Insert immediately after the creator table, retaining pre-seeded
        # briefs/values below the table rather than overwriting blank-name rows.
        edge = max(i for i, row in enumerate(values) if i >= 2 and row and row[0])
        # Check all native columns, including N's recorded Ad IDs, so an
        # insertion never loses existing identity bindings to another row.
        params = {'ranges': "'TikTok'!A1:AC%d" % prop['gridProperties']['rowCount'], 'includeGridData': 'true',
                  'fields': 'sheets(properties,tables,merges,protectedRanges,data(rowData(values(userEnteredValue,userEnteredFormat,dataValidation,chipRuns,textFormatRuns))))'}
        grid = get_json(session, endpoint, params)
        tab = grid['sheets'][0]
        if tab.get('tables') or tab.get('merges') or tab.get('protectedRanges'):
            raise RuntimeError('Structured/protected TikTok range requires review')
        cells = tab['data'][0]['rowData']
        template = cells[edge].get('values', [])
        if len(template) < 6:
            raise RuntimeError('Missing complete insertion exemplar')
        requests_body, new_values = insertion_requests(prop['sheetId'], edge, proposed, template)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        (output / (stamp + '-before.json')).write_text(json.dumps({'sheet': before, 'grid': grid}, ensure_ascii=False, indent=2))
        if read_tiktok(env)['values'] != values:
            raise RuntimeError('Concurrent sheet edit; not written')
        response = session.post(endpoint + ':batchUpdate', json={'requests': requests_body}, timeout=90)
        response.raise_for_status()  # Never replay an unknown write outcome.
        after = read_tiktok(env)
        expected = values[:edge + 1] + new_values + values[edge + 1:]
        pad = lambda r: r + [''] * max(0, 13 - len(r))
        if list(map(pad, expected)) != list(map(pad, after['values'])):
            raise RuntimeError('Insertion verification failed; do not replay')
        params['ranges'] = "'TikTok'!A1:AC%d" % after['tab']['gridProperties']['rowCount']
        fresh_grid = get_json(session, endpoint, params)
        fresh = fresh_grid['sheets'][0]['data'][0]['rowData']
        for index in range(edge + 1, edge + 1 + len(proposed)):
            for col in range(13):
                old = template[col] if len(template) > col else {}
                new = fresh[index]['values'][col] if len(fresh[index].get('values', [])) > col else {}
                for key in ['userEnteredFormat', 'dataValidation']:
                    if old.get(key) != new.get(key):
                        raise RuntimeError('New row native structure verification failed')
                if new.get('chipRuns') or new.get('textFormatRuns'):
                    raise RuntimeError('Unexpected copied rich content')
        for index, old in enumerate(cells):
            target = index if index <= edge else index + len(proposed)
            if old != (fresh[target] if len(fresh) > target else {}):
                raise RuntimeError('Existing native cells changed; inspect before retrying')
        (output / (stamp + '-after.json')).write_text(json.dumps({'sheet': after, 'grid': fresh_grid}, ensure_ascii=False, indent=2))
        print('APPLIED AND VERIFIED:', len(proposed), 'TikTok rows; existing Status untouched')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached', action='store_true', help='Preview saved three-month Slack/Sheet snapshots')
    parser.add_argument('--slack-days', type=int, default=7)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.cached and args.apply:
        parser.error('--cached cannot write')
    if not 1 <= args.slack_days <= 100:
        parser.error('--slack-days must be between 1 and 100')
    load_dotenv(BASE / '.env')
    env = dict(os.environ)
    output = BASE / 'data/processed/kol_tracker_audit/tiktok_new_rows'
    output.mkdir(parents=True, exist_ok=True)
    if args.cached:
        source = json.loads((BASE / 'data/processed/kol_tracker_audit/slack_3months/source.json').read_text())
        slack = source['slack']
        sheet = json.loads((BASE / 'data/processed/kol_tracker_audit/tiktok/sheet.json').read_text())
    else:
        sheet = read_tiktok(env)
        now = datetime.now(timezone.utc)
        slack = read_slack(env, (now - timedelta(days=args.slack_days)).timestamp(), now.timestamp())
    candidates, issues = parse_tiktok_announcements(slack['messages'])
    proposed, decisions = plan_new_rows(sheet['values'], candidates)
    report = {'candidates': candidates, 'proposed': proposed, 'decisions': decisions,
              'unresolved': issues, 'slack_issues': slack.get('issues', []),
              'coverage': slack.get('coverage'), 'cached': args.cached,
              'pending_api': 'New rows use Slack announcement date when API date is unavailable; Status, metrics and Ad IDs stay blank until verified'}
    (output / 'plan.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'candidates': len(candidates), 'decisions': dict(Counter(d['decision'] for d in decisions)),
                      'new_creators': [p['creator'] for p in proposed], 'unresolved': len(issues),
                      'slack_issues': slack.get('issues', []), 'pending_api': report['pending_api']}, ensure_ascii=False, indent=2))
    if args.apply:
        if slack.get('issues'):
            raise RuntimeError('Slack read incomplete; preview only')
        apply_new_rows(env, sheet, proposed, output)


if __name__ == '__main__':
    main()
