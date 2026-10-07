"""Standalone READ-ONLY Meta KOL audit. No remote write or deployment commands.

Uses the existing .env and dependencies, but imports none of the deployed app.
Run --help. Snapshots contain private business data; keep outputs private.
"""
import argparse
import csv
import html
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs, urlencode, urlunparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

BASE = Path(__file__).resolve().parent
METRICS = ['spend', 'roas', 'purchase', 'aov', 'atc', 'impressions', 'clicks', 'cpc', 'ctr', 'cpm']


def ad_location(ad):
    """Destination category; call only for exact-matched effectively ACTIVE ads."""
    name = ad.get('adset', {}).get('name', '')
    for pattern in [r'Partnership[-_ ]?Scale', r'Partnership\d+', r'KOL\d+', r'EVG\d+']:
        found = re.search(pattern, name, re.I)
        if found:
            value = found.group()
            return 'Partnership-Scale' if 'scale' in value.lower() else value
    text = name + ' ' + ad.get('campaign', {}).get('name', '')
    for token, label in [('awareness', 'Awareness'), ('retargeting', 'Retargeting'),
                         ('bidcap', 'BidCap'), ('highestvalue', 'HighestValue'),
                         ('traffic', 'Traffic'), ('atc', 'ATC')]:
        if token in text.lower(): return label
    return name or ad.get('campaign', {}).get('name', 'Unknown destination')


def get_json(session, url, params=None):
    """GET only; sanitize network exceptions (URLs may contain credentials)."""
    for attempt in range(4):
        try:
            response = session.get(url, params=params, timeout=60)
        except requests.RequestException:
            raise RuntimeError('Read request failed: ' + urlparse(url).netloc) from None
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 3:
                time.sleep(min(float(response.headers.get('Retry-After', 2 ** attempt)), 30))
                continue
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError('Non-JSON response from ' + urlparse(url).netloc) from None
        if not response.ok or data.get('error') or data.get('ok') is False:
            error = data.get('error', 'HTTP ' + str(response.status_code))
            if isinstance(error, dict):
                error = error.get('message', 'API error')
            raise RuntimeError(str(error))
        return data
    raise RuntimeError('Read retries exhausted')


def graph_pages(session, url, params):
    rows = []
    while url:
        data = get_json(session, url, params)
        rows.extend(data.get('data', []))
        url = data.get('paging', {}).get('next')
        if url:
            parsed = urlparse(url)
            if parsed.hostname != 'graph.facebook.com':
                raise RuntimeError('Unexpected pagination host')
            query = parse_qs(parsed.query)
            query.pop('access_token', None)
            url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        params = None
    return rows


def norm(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower())


def post_key(url):
    found = re.search(r'instagram\.com/(?:p|reels?)/([^/?#<>\s]+)', url or '', re.I)
    return found.group(1) if found else ''


def code_key(code):
    return re.sub(r'^adcode-', '', (code or '').strip(), flags=re.I)


def creator_from_ad(name):
    # Keep underscores inside handles (andreww_mckenna, sofie_langberg).
    found = re.search(r'Partnership_(.+?)_ROPO', name, re.I)
    return found.group(1).strip() if found else ''


def ad_name_matches(name, creator):
    if norm(creator_from_ad(name)) == norm(creator):
        return True
    # Awareness/ThruPlay ads also belong in this Ads Tracker. A whole name
    # segment must match; Josh must not accidentally match Josh4.
    parts = name.split('_')
    return any(norm('_'.join(parts[i:j])) == norm(creator)
               for i in range(len(parts)) for j in range(i+1, len(parts)+1))


def read_sheet(env, all_columns=False):
    match = re.search(r'/spreadsheets/d/([^/]+)', env['KOL_TRACKER_GOOGLE_SHEETS_LINK'])
    if not match:
        raise RuntimeError('Invalid KOL_TRACKER_GOOGLE_SHEETS_LINK')
    sid = match.group(1)
    credential_path = BASE / env.get('GOOGLE_SERVICE_ACCOUNT_FILE', 'credentials/google-service-account.json')
    credentials = Credentials.from_service_account_file(str(credential_path), scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
    session = AuthorizedSession(credentials)
    base = 'https://sheets.googleapis.com/v4/spreadsheets/' + sid
    metadata = get_json(session, base, {'fields': 'spreadsheetId,properties,sheets.properties'})
    prop = next((s['properties'] for s in metadata['sheets'] if s['properties']['title'] == 'Meta'), None)
    if not prop:
        raise RuntimeError('Meta tab not found')
    values = []
    label = 'V'
    if all_columns:
        label, count = '', prop['gridProperties']['columnCount']
        while count:
            count, remainder = divmod(count - 1, 26)
            label = chr(65 + remainder) + label
    for start in range(1, prop['gridProperties']['rowCount'] + 1, 400):
        end = min(start + 399, prop['gridProperties']['rowCount'])
        part = get_json(session, base + '/values/' + quote("'Meta'!A%d:%s%d" % (start, label, end), safe=''), {'valueRenderOption': 'UNFORMATTED_VALUE'})
        chunk = part.get('values', [])
        values.extend(chunk + [[]] * (end - start + 1 - len(chunk)))
    return {'metadata': metadata, 'values': values}


def read_slack(env, oldest, latest):
    session = requests.Session()
    token = env.get('KOL_TRACKER_SLACK_BOT_TOKEN') or env.get('KOL_TRACKER_SLACK_TOKEN') or env.get('SLACK_BOT_TOKEN')
    if not token:
        raise RuntimeError('Missing Slack history token')
    session.headers['Authorization'] = 'Bearer ' + token
    channel = env['KOL_TRACKER_SLACK_CHANNEL_ID']
    messages, cursor, issues = [], '', []
    while True:
        data = get_json(session, 'https://slack.com/api/conversations.history', {'channel': channel, 'oldest': oldest, 'latest': latest, 'limit': 100, 'cursor': cursor})
        messages.extend(data.get('messages', []))
        cursor = data.get('response_metadata', {}).get('next_cursor', '')
        if not cursor:
            break
    # Replies to recent roots; old-root threads are explicitly a coverage limit.
    roots = list(messages)
    for root in roots:
        if not root.get('reply_count'):
            continue
        cursor = ''
        try:
            while True:
                data = get_json(session, 'https://slack.com/api/conversations.replies', {'channel': channel, 'ts': root['ts'], 'oldest': oldest, 'latest': latest, 'limit': 100, 'cursor': cursor})
                messages.extend(data.get('messages', []))
                cursor = data.get('response_metadata', {}).get('next_cursor', '')
                if not cursor:
                    break
        except RuntimeError as error:
            issues.append('Thread %s: %s' % (root['ts'], error))
    unique = {m['ts']: m for m in messages}
    return {'messages': list(unique.values()), 'issues': issues, 'oldest':oldest, 'latest':latest, 'coverage': 'Channel roots in the requested interval and their replies; replies to older roots are not covered.'}


def parse_announcements(messages):
    candidates, unresolved = [], []
    for msg in messages:
        text = html.unescape(msg.get('text', ''))
        # New rows originate exclusively from explicit KOL live announcements
        # in the configured Slack channel, never from the Meta ad inventory.
        if not re.search(r'\[KOL Content is Live\]', text, re.I):
            continue
        # A creator heading can be a Slack profile link: keep its supplied
        # display label, while content/post links below retain their URLs.
        text = re.sub(r'<https?://[^>|]+\|([^>]+)>(?=[‘’\']s)', r'\1', text)
        # Unwrap Slack <URL|label> without losing the URL.
        text = re.sub(r'<(https?://[^>|]+)(?:\|[^>]+)?>', r'\1', text)
        # Remove the announcement heading before extracting a creator. The
        # heading itself contains "Content is Live" but is not a person's name.
        text = re.sub(r'^.*?\[KOL Content is Live\][^\n]*\n?', '', text, flags=re.I | re.M)
        if 'instagram.com/' not in text:
            continue
        sections = re.split(r'(?:^|\n)\s*\d+[.)]\s+', text)
        for section in sections:
            # Allow a code on the next line, but do not cross another platform's
            # URL (which could pair the wrong authorization with the IG post).
            pairs = re.findall(r'(https?://(?:www\.)?instagram\.com/[^\s<>]+)(?:(?!https?://).)*?((?:adcode-)?Q9jT[A-Za-z0-9_-]+)', section, re.S)
            title = re.search(r"^\s*\*?([^\n]+?)(?:[‘’']s|[‘’']|\s+content)\s*[^\n]*?\blive\b", section, re.I | re.M)
            creator = title.group(1).strip(' *') if title else ''
            if not creator:
                # Older marked announcements use "posts are here", "videos
                # are out", bare creator headings, or "Creator - product".
                # Only inspect the heading, never infer identity from a URL.
                heading = next((line.strip(' *') for line in section.splitlines() if line.strip(' *')), '')
                possessive = re.match(r"^(.+?)(?:[‘’']s|[‘’'])(?:\s|$)", heading)
                if possessive:
                    creator = possessive.group(1).strip()
                elif heading and not re.search(r'https?://|<@|^[•#]', heading):
                    creator = re.split(r'\s+-\s+', heading, maxsplit=1)[0].strip()
            if not pairs and 'instagram.com/' in section:
                unresolved.append({'ts': msg['ts'], 'reason': 'IG announcement could not be paired with an Ad Code', 'text': section})
            for url, code in pairs:
                if not creator or not post_key(url):
                    unresolved.append({'ts': msg['ts'], 'reason': 'Creator or single-post URL missing', 'text': section})
                    continue
                candidates.append({'creator': creator, 'post_link': url.rstrip(').,*'), 'post_key': post_key(url), 'ad_code': code_key(code), 'ts': msg['ts']})
    unique = {}
    for item in sorted(candidates, key=lambda x: float(x['ts'])):
        unique[(item['post_key'], item['ad_code'])] = item
    return list(unique.values()), unresolved


def fetch_meta(env, end_date):
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
    base = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION', 'v23.0')
    account = 'act_' + env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    info = get_json(session, base + '/' + account, {'fields': 'id,name,currency,timezone_name'})
    if not end_date:
        end_date = (datetime.now(ZoneInfo(info['timezone_name'])).date() - timedelta(days=1)).isoformat()
    ads = graph_pages(session, base + '/' + account + '/ads', {'fields': 'id,name,created_time,effective_status,adset{id,name},creative{id,effective_instagram_media_id}', 'limit': 100})
    media_ids = sorted({a.get('creative', {}).get('effective_instagram_media_id') for a in ads if a.get('creative', {}).get('effective_instagram_media_id')})
    def media_read(mid):
        try:
            return mid, get_json(session, base + '/' + mid, {'fields': 'id,permalink,username'})
        except RuntimeError as error:
            return mid, {'error': str(error)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        media = dict(pool.map(media_read, media_ids))
    insights = fetch_insights(session, base, account, env.get('KOL_TRACKER_START_DATE', '2025-01-01'), end_date)
    return {'account': info, 'ads': ads, 'media': media, 'insights': insights, 'end_date': end_date, 'start_date': env.get('KOL_TRACKER_START_DATE', '2025-01-01'), 'attribution': 'use_unified_attribution_setting=true'}


def fetch_insights(session, base, account, start, end):
    return graph_pages(session, base + '/' + account + '/insights', {'level': 'ad', 'fields': 'ad_id,ad_name,spend,impressions,clicks,inline_link_clicks,actions,action_values,date_start,date_stop', 'time_range': json.dumps({'since': start, 'until': end}), 'use_unified_attribution_setting': 'true', 'limit': 100})


def action(row, field, names):
    items = {x['action_type']: float(x['value']) for x in row.get(field, [])}
    return next((items[name] for name in names if name in items), 0.0)


def aggregate(rows, purchase_type='website', clicks_type='all'):
    purchase_keys = ['offsite_conversion.fb_pixel_purchase'] if purchase_type == 'website' else ['omni_purchase', 'purchase', 'offsite_conversion.fb_pixel_purchase']
    atc_keys = ['offsite_conversion.fb_pixel_add_to_cart'] if purchase_type == 'website' else ['omni_add_to_cart', 'add_to_cart', 'offsite_conversion.fb_pixel_add_to_cart']
    spend = sum(float(r.get('spend', 0)) for r in rows)
    purchase = sum(action(r, 'actions', purchase_keys) for r in rows)
    revenue = sum(action(r, 'action_values', purchase_keys) for r in rows)
    atc = sum(action(r, 'actions', atc_keys) for r in rows)
    impressions = sum(float(r.get('impressions', 0)) for r in rows)
    clicks = sum(float(r.get('clicks' if clicks_type == 'all' else 'inline_link_clicks', 0)) for r in rows)
    def ratio(n, d):
        return n / d if d else None
    return dict(zip(METRICS, [spend, ratio(revenue, spend), purchase, ratio(revenue, purchase), atc, impressions, clicks, ratio(spend, clicks), ratio(clicks, impressions), ratio(spend * 1000, impressions)]))


def reconcile(snapshot, purchase_type, clicks_type, reviewed_mapping=None):
    sheet, meta = snapshot['sheet'], snapshot['meta']
    raw = sheet['values']
    expected = ['Creator', 'Post Link', 'Ad Code']
    if [raw[0][i] for i in [1, 6, 7]] != expected:
        raise RuntimeError('Meta identification headers changed; refusing to infer columns')
    rows = [(i+1, v + [''] * (22-len(v))) for i, v in enumerate(raw) if i >= 2 and len(v) > 1 and v[1]]
    bypost, bycode = defaultdict(list), defaultdict(list)
    for number, row in rows:
        if post_key(row[6]): bypost[post_key(row[6])].append(number)
        if code_key(row[7]): bycode[code_key(row[7])].append(number)
    insights = defaultdict(list)
    for insight in meta['insights']: insights[insight['ad_id']].append(insight)
    comparison, mapping = [], []
    claimed = defaultdict(list)
    for number, row in rows:
        key = post_key(row[6])
        reviewed = (reviewed_mapping or {}).get('by_post', {}).get(key, {}) if key else {}
        exact = [a for a in meta['ads'] if key and post_key(meta['media'].get(a.get('creative', {}).get('effective_instagram_media_id'), {}).get('permalink', '')) == key]
        named = [a for a in meta['ads'] if ad_name_matches(a['name'], row[1])]
        name_scope = 'whole Creator label'
        if not exact and len({a.get('creative',{}).get('effective_instagram_media_id') for a in named}) > 1 and isinstance(row[2],(int,float)):
            launch = datetime(1899,12,30) + timedelta(days=row[2])
            stamp = launch.strftime('%b') + str(launch.day)
            dated = [a for a in named if re.search(r'(?<![a-z])'+stamp+r'(?!\d)',a['name'],re.I)]
            selected_media = {a.get('creative',{}).get('effective_instagram_media_id') for a in dated}
            if len(selected_media)==1 and None not in selected_media:
                named = [a for a in named if a.get('creative',{}).get('effective_instagram_media_id') in selected_media]
                name_scope = 'Creator label + original launch month/day in Ad Name (requires review)'
        method = 'post_id' if exact else 'ad_name_candidate'
        matches = exact or named
        if reviewed.get('approved') is True:
            requested = set(reviewed.get('ad_ids', []))
            known = {a['id'] for a in meta['ads']}
            if not requested or not requested.issubset(known) or not reviewed.get('note'):
                raise RuntimeError('Invalid reviewed identity mapping for '+key)
            matches = [a for a in meta['ads'] if a['id'] in requested]
            method = 'reviewed_ad_ids'
        mids = {a.get('creative', {}).get('effective_instagram_media_id') for a in matches}
        issues = []
        if method=='ad_name_candidate': issues.append('Identity requires review: Ad Name match is a candidate, not a verified post')
        if len(mids) > 1 and method=='ad_name_candidate': issues.append('Multiple posts under this Creator name')
        if key and len(bypost[key]) > 1: issues.append('Post repeated in sheet')
        if code_key(row[7]) and len(bycode[code_key(row[7])]) > 1: issues.append('Ad Code repeated in sheet')
        if not matches: issues.append('No matching ad')
        api_rows = [r for a in matches for r in insights[a['id']]]
        metrics = aggregate(api_rows, purchase_type, clicks_type) if api_rows else {}
        for a in matches: claimed[a['id']].append(number)
        mapping.append({'row': number, 'creator': row[1], 'method': method, 'name_scope':name_scope, 'ad_ids': [a['id'] for a in matches], 'ad_names': [a['name'] for a in matches], 'media_ids': sorted(str(x) for x in mids), 'issues': issues, 'insights_rows': len(api_rows), 'advertised_posts':[meta['media'].get(mid,{}) for mid in mids if mid]})
        for offset, metric in enumerate(METRICS):
            existing = row[9+offset]
            fetched = metrics.get(metric)
            tolerance = 0.00005 if metric == 'ctr' else 0.005
            same = isinstance(existing, (int, float)) and fetched is not None and abs(existing-fetched) <= tolerance + 1e-9
            state = 'match' if same else 'blank' if existing == '' else 'no_api_data' if fetched is None else 'different'
            comparison.append({'row': number, 'creator': row[1], 'metric': metric, 'sheet': existing, 'api': fetched, 'delta': fetched-existing if fetched is not None and isinstance(existing, (int,float)) else None, 'result': state, 'identity': method})
    ratio_diagnostics = []
    for number, row in rows:
        vals = dict(zip(METRICS, row[9:19]))
        for metric, numerator, denominator, multiplier in [('cpc','spend','clicks',1),('ctr','clicks','impressions',1),('cpm','spend','impressions',1000)]:
            v, n, d = vals[metric], vals[numerator], vals[denominator]
            if all(isinstance(x,(int,float)) for x in [v,n,d]) and d:
                expected = n/d*multiplier
                tolerance = 0.00005 if metric=='ctr' else 0.005
                if abs(v-expected)>tolerance+1e-9:
                    ratio_diagnostics.append({'row':number,'creator':row[1],'metric':metric,'stored':v,'ratio_of_sheet_totals':expected,'note':'Check mixed definitions or manual copying; CTR may use link clicks while Clicks uses all clicks.'})
    candidates, unresolved = parse_announcements(snapshot.get('slack', {}).get('messages', []))
    for item in candidates:
        post_rows = bypost.get(item['post_key'], [])
        code_rows = bycode.get(item['ad_code'], [])
        item['sheet_rows'] = sorted(set(post_rows + code_rows))
        item['decision'] = 'existing' if post_rows and (not code_rows or post_rows == code_rows) else 'review_conflict' if code_rows else 'proposed_new'
        item['matching_ad_ids'] = [a['id'] for a in meta['ads'] if post_key(meta['media'].get(a.get('creative',{}).get('effective_instagram_media_id'),{}).get('permalink','')) == item['post_key']]
        item['launch_date'] = None  # Announcement time is not first paid delivery.
        matched_insights = [r for aid in item['matching_ad_ids'] for r in insights[aid]]
        item['metrics_preview'] = aggregate(matched_insights, purchase_type, clicks_type) if matched_insights else {}
        item['manual_fields'] = ['Content Brief', 'Followers', 'KOL content note', 'Status', 'KOL fee', 'Note']
    conflicts = {k:v for k,v in claimed.items() if len(v)>1}
    passed = bool(rows) and all(m['method'] in ['post_id','reviewed_ad_ids'] and not m['issues'] and m['insights_rows'] for m in mapping) and all(c['result']=='match' for c in comparison) and not conflicts and not snapshot.get('errors') and not unresolved and not snapshot.get('slack',{}).get('issues')
    return {'verified_equal': passed, 'sheet_rows':len(rows), 'comparison':comparison, 'mapping':mapping, 'shared_ads':conflicts, 'sheet_ratio_diagnostics':ratio_diagnostics, 'slack_candidates':candidates, 'slack_unresolved':unresolved, 'errors':snapshot.get('errors',[]), 'slack_issues':snapshot.get('slack',{}).get('issues',[]), 'reporting':{k:meta[k] for k in ['start_date','end_date','attribution','account']}, 'purchase_type':purchase_type, 'clicks_type':clicks_type}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end-date', help='Explicit account-local YYYY-MM-DD reporting cutoff; default yesterday')
    parser.add_argument('--snapshot', type=Path, help='Reprocess a local snapshot without network access')
    parser.add_argument('--refresh-insights', action='store_true', help='With --snapshot and --end-date, refetch only Meta insights using cached identity data')
    parser.add_argument('--mapping-file', type=Path, help='Optional locally reviewed by_post -> approved ad_ids mapping; never modifies the sheet')
    parser.add_argument('--slack-days', type=int, default=7, help='Rolling Slack history window in days (use 30 for a month)')
    parser.add_argument('--output-dir', type=Path, default=BASE / 'data/processed/kol_tracker_audit')
    parser.add_argument('--purchase-type', choices=['website','omni'], default='website')
    parser.add_argument('--clicks-type', choices=['all','link'], default='all')
    args = parser.parse_args()
    if args.slack_days < 1:
        parser.error('--slack-days must be positive')
    if args.refresh_insights and (not args.snapshot or not args.end_date):
        parser.error('--refresh-insights requires --snapshot and --end-date')
    if args.end_date:
        datetime.strptime(args.end_date, '%Y-%m-%d')
    if args.snapshot and args.end_date and not args.refresh_insights:
        parser.error('--end-date with a snapshot requires --refresh-insights; cached values cannot be relabeled')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text())
        if args.refresh_insights:
            load_dotenv(BASE / '.env')
            env = dict(os.environ)
            session = requests.Session()
            session.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
            account = 'act_' + env['META_AD_ACCOUNT_ID'].removeprefix('act_')
            if account != snapshot['meta']['account']['id']:
                raise RuntimeError('Configured Meta account differs from snapshot')
            snapshot['meta']['insights'] = fetch_insights(session, 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION','v23.0'), account, snapshot['meta']['start_date'], args.end_date)
            snapshot['meta']['end_date'] = args.end_date
            snapshot['insights_refreshed_at'] = datetime.now(timezone.utc).isoformat()
            (args.output_dir/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
    else:
        load_dotenv(BASE / '.env')
        env = dict(os.environ)
        for key in ['KOL_TRACKER_GOOGLE_SHEETS_LINK','KOL_TRACKER_SLACK_CHANNEL_ID','META_ACCESS_TOKEN','META_AD_ACCOUNT_ID']:
            if not env.get(key): raise RuntimeError('Missing configuration: '+key)
        now = datetime.now(timezone.utc)
        end = args.end_date
        print('Reading Tracker, Meta ads/insights and %d days of Slack (GET only)' % args.slack_days, flush=True)
        snapshot = {'fetched_at':now.isoformat(),'errors':[]}
        snapshot['sheet'] = read_sheet(env)
        print('Tracker read; collecting Meta', flush=True)
        snapshot['meta'] = fetch_meta(env,end)
        print('Meta read: %d ads, %d insights; collecting Slack' % (len(snapshot['meta']['ads']),len(snapshot['meta']['insights'])),flush=True)
        try:
            snapshot['slack'] = read_slack(env,(now-timedelta(days=args.slack_days)).timestamp(),now.timestamp())
        except RuntimeError as error:
            snapshot['errors'].append('Slack: '+str(error))
            snapshot['slack'] = {'messages':[], 'issues':[str(error)]}
        (args.output_dir/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
    reviewed_mapping = json.loads(args.mapping_file.read_text()) if args.mapping_file else None
    report = reconcile(snapshot,args.purchase_type,args.clicks_type,reviewed_mapping)
    (args.output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    with (args.output_dir/'comparison.csv').open('w',newline='') as file:
        writer = csv.DictWriter(file,fieldnames=['row','creator','metric','sheet','api','delta','result','identity'])
        writer.writeheader(); writer.writerows(report['comparison'])
    summary = {'verified_equal':report['verified_equal'],'sheet_rows':report['sheet_rows'],'matched_cells':sum(x['result']=='match' for x in report['comparison']),'total_cells':len(report['comparison']),'verified_post_rows':sum(x['method']=='post_id' for x in report['mapping']),'slack_candidates':len(report['slack_candidates']),'proposed_new':sum(x['decision']=='proposed_new' for x in report['slack_candidates']),'errors':report['errors'],'output':str(args.output_dir)}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if report['verified_equal'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print('Audit failed: '+str(error))
        raise SystemExit(1)
