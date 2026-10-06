"""Natural-only partnership content refresh; no Slack inserts or ad mutations."""
import json
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


def post_key(value):
    match = re.search(r'instagram\.com/(?:p|reel|reels)/([^/?#]+)', str(value))
    return match.group(1) if match else None


def fetch_content(env, links):
    token = env['META_ACCESS_TOKEN']
    base = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION', 'v23.0')
    business = env.get('KOL_ORGANIC_META_BUSINESS_ID', '997763325183322')
    account = env.get('KOL_ORGANIC_META_IG_USER_ID', '17841448894150543')
    found = {}
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + token
    unique = sorted(set(links))
    for offset in range(0, len(unique), 5):
        print('Organic Meta content batch %d/%d' % (offset // 5 + 1, (len(unique)+4)//5), flush=True)
        response = session.get(base + '/' + business + '/partnership-ads-advertisable-content',
            params={'ig_user_id': account, 'permalinks': json.dumps(unique[offset:offset+5]),
                    'fields': 'content_id,permalink,organic_insights{views,interaction,likes,comments,shares,saves}'},
            timeout=45)
        # Never log raw responses: Meta errors can echo credentials.
        if not response.ok:
            raise RuntimeError('Organic content request failed; HTTP %s' % response.status_code)
        data = response.json()
        if 'error' in data or not isinstance(data.get('data'), list):
            raise RuntimeError('Invalid Organic API response')
        for item in data['data']:
            key = post_key(item.get('permalink', ''))
            if not key:
                raise RuntimeError('Organic response missing permalink')
            if key in found and found[key] != item:
                raise RuntimeError('Conflicting Organic content response')
            found[key] = item
    return found


def plan(values, content):
    headers = [str(x).strip() for x in values[1]] if len(values) > 1 else []
    expected = ['Creator', 'Organic Launch Date', 'Content Brief', 'Post Link', 'Ad Code',
                'Status', 'KOL Fee', 'Views', 'Interaction', 'Likes', 'Comments', 'Shares', 'Saves', 'CPM', 'CPE']
    if headers[:15] != expected:
        raise RuntimeError('Organic Meta schema changed')
    planned, skipped = [], []
    for number, row in enumerate(values[2:], 3):
        if not row or not row[0]:
            continue
        key = post_key(row[3] if len(row) > 3 else '')
        item = content.get(key)
        if not item:
            skipped.append({'row': number, 'reason': 'No exact API post match'})
            continue
        metrics = item.get('organic_insights') or {}
        changes, missing = {}, []
        for col, field in enumerate(['views', 'interaction', 'likes', 'comments', 'shares', 'saves'], 7):
            value = metrics.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                missing.append(field)
            else:
                changes[col] = value
        # Never combine a fresh numerator with a stale denominator.
        fee = row[6] if len(row) > 6 else None
        if isinstance(fee, (int, float)) and not isinstance(fee, bool) and math.isfinite(fee) and fee >= 0:
            if changes.get(7, 0) > 0:
                changes[13] = fee / changes[7] * 1000
            if changes.get(8, 0) > 0:
                changes[14] = fee / changes[8]
        planned.append({'row': number, 'content_id': item['content_id'], 'changes': changes, 'missing': missing})
    return planned, skipped


def status_plan(values, ads, cells=None):
    """Only exact, unshared Ad Code bindings can change automatic statuses."""
    from update_meta_tracker import exact_mapping
    rows = []
    for number, row in enumerate(values[2:], 3):
        if row and row[0] and len(row) > 4 and row[4]:
            mapped = [''] * 8
            mapped[1], mapped[7] = row[0], row[4]
            rows.append((number, mapped))
    matches, _, _ = exact_mapping(rows, ads)
    by_id = {ad['id']: ad for ad in ads}
    result = []
    for number, ids in matches.items():
        row = values[number-1]
        current = row[5] if len(row) > 5 else ''
        if str(current).strip().lower() not in ['', 'testing', 'pause', 'paused']:
            continue
        statuses = {by_id[aid].get('effective_status') for aid in ids}
        value = ('testing' if 'ACTIVE' in statuses else 'pause'
                 if statuses and statuses.issubset({'PAUSED', 'ADSET_PAUSED', 'CAMPAIGN_PAUSED'}) else None)
        if value is not None and cells is not None:
            row_cells = cells[number-1].get('values', [])
            target = row_cells[5] if len(row_cells) > 5 else {}
            rule = target.get('dataValidation', {})
            condition = rule.get('condition', {})
            if condition.get('type') == 'ONE_OF_LIST':
                allowed = [v.get('userEnteredValue') for v in condition.get('values', [])]
                if value == 'pause' and value not in allowed and 'paused' in allowed:
                    value = 'paused'
                if value not in allowed:
                    continue
        if value is not None and value != current:
            result.append({'row': number, 'ad_ids': ids, 'changes': {5: value}})
    return result


def code_plan(values, tracker_values, ads):
    """Bridge exact post links only, with live Creative code validation."""
    from sync_creator_tracker import code_key
    from update_meta_tracker import exact_mapping
    live = {code_key(ad.get('creative', {}).get('branded_content', {}).get(
        'instagram_boost_post_access_token')) for ad in ads}
    live.discard('')
    by_post = {}
    for row in tracker_values[2:]:
        if len(row) > 7 and post_key(row[6]) and code_key(row[7]):
            by_post.setdefault(post_key(row[6]), {})[code_key(row[7])] = row[7]
    resolved = [list(row) for row in values]
    updates, issues, blocked = [], [], set()
    for number, row in enumerate(resolved[2:], 3):
        if not row or not row[0]:
            continue
        row.extend([''] * max(0, 6-len(row)))
        candidates = by_post.get(post_key(row[3]), {})
        existing = code_key(row[4])
        if len(candidates) > 1 or (existing and candidates and existing not in candidates):
            blocked.add(number)
            issues.append({'row': number, 'reason': 'Conflicting post/code binding'})
        elif not existing and len(candidates) == 1:
            key = next(iter(candidates))
            if key in live:
                row[4] = candidates[key]
                updates.append({'row': number, 'changes': {4: row[4]},
                                'source': 'Exact Ads Tracker Post Link + live Creative Ad Code'})
    bindings = []
    for number, row in enumerate(resolved[2:], 3):
        if row and row[0] and len(row) > 4 and row[4]:
            bindings.append((number, ['', row[0], '', '', '', '', '', row[4]]))
    _, _, conflicts = exact_mapping(bindings, ads)
    blocked.update(number for numbers in conflicts.values() for number in numbers)
    for number in sorted({n for ns in conflicts.values() for n in ns}):
        issues.append({'row': number, 'reason': 'Ad ID claimed by multiple Organic rows'})
    updates = [item for item in updates if item['row'] not in blocked]
    # Remove ambiguous bindings from status calculation, without changing sheet values.
    for number in blocked:
        resolved[number-1][4] = ''
    return resolved, updates, issues


def run(env, output, apply):
    from kol_tracker import sheet_session, read_tab, save_json, verify_literal_target, meta_inventory
    from update_meta_tracker import write_range
    scoped = dict(env, KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK'])
    session, endpoint = sheet_session(scoped)
    prop, native, values = read_tab(session, endpoint, 'Meta-IG')
    # Validate schema before making API calls.
    plan(values, {})
    print('Organic Meta sheet read; fetching natural metrics', flush=True)
    links = ['https://www.instagram.com/reel/' + post_key(row[3]) + '/'
             for row in values[2:] if len(row) > 3 and row[0] and post_key(row[3])]
    planned, skipped = plan(values, fetch_content(env, links))
    print('Organic Meta natural metrics fetched; fetching ad statuses', flush=True)
    _, ads = meta_inventory(env)
    tracker_values = []
    if env.get('KOL_TRACKER_GOOGLE_SHEETS_LINK'):
        tracker_session, tracker_endpoint = sheet_session(env)
        _, _, tracker_values = read_tab(tracker_session, tracker_endpoint, 'Meta')
    resolved, codes, issues = code_plan(values, tracker_values, ads)
    statuses = status_plan(resolved, ads, native['sheets'][0]['data'][0]['rowData'])
    save_json(output / 'Organic-Meta-plan.json', {'basis': 'API organic_insights; no paid totals',
                                               'planned': planned, 'skipped': skipped, 'status_updates': statuses,
                                               'code_updates': codes, 'identity_issues': issues})
    print('Organic Meta planned: %d; skipped: %d' % (len(planned), len(skipped)), flush=True)
    print('Organic Meta automatic status updates: %d' % len(statuses), flush=True)
    print('Organic Meta exact Post Link code backfills: %d; identity conflicts: %d' %
          (len(codes), len(issues)), flush=True)
    if not apply:
        return
    cells = native['sheets'][0]['data'][0]['rowData']
    body, expected = [], json.loads(json.dumps(native))
    expected_rows = expected['sheets'][0]['data'][0]['rowData']
    for item in planned + codes + statuses:
        for col, value in item['changes'].items():
            row = cells[item['row']-1].get('values', [])
            target = row[col] if col < len(row) else {}
            if col in [13, 14] and 'formulaValue' in target.get('userEnteredValue', {}):
                continue  # Existing CPM/CPE formulas recalculate; never replace them.
            verify_literal_target(target, value)
            request = write_range(prop['sheetId'], item['row'], col, [value])
            body.append(request)
            expected_row = expected_rows[item['row']-1].setdefault('values', [])
            expected_row.extend({} for _ in range(max(0, col+1-len(expected_row))))
            expected_row[col]['userEnteredValue'] = request['updateCells']['rows'][0]['values'][0]['userEnteredValue']
    if not body:
        return
    # One atomic batch: the date cannot advance if the metrics write fails.
    stamp = datetime.now(ZoneInfo('America/Chicago')).strftime('[%m/%d update]')
    header = cells[0].get('values', [])
    verify_literal_target(header[5] if len(header) > 5 else {}, stamp)
    request = write_range(prop['sheetId'], 1, 5, [stamp])
    body.append(request)
    expected_header = expected_rows[0].setdefault('values', [])
    expected_header.extend({} for _ in range(max(0, 6-len(expected_header))))
    expected_header[5]['userEnteredValue'] = request['updateCells']['rows'][0]['values'][0]['userEnteredValue']
    save_json(output / 'Organic-Meta-before.json', native)
    if read_tab(session, endpoint, 'Meta-IG')[1] != native:
        raise RuntimeError('Organic sheet changed; no write')
    response = session.post(endpoint + ':batchUpdate', json={'requests': body}, timeout=90)
    response.raise_for_status()
    after = read_tab(session, endpoint, 'Meta-IG')[1]
    save_json(output / 'Organic-Meta-after.json', after)
    for obj in [expected, after]:
        for row in obj['sheets'][0]['data'][0]['rowData']:
            for cell in row.get('values', []):
                cell.pop('effectiveValue', None)
            while row.get('values') and not row['values'][-1]:
                row['values'].pop()
    if expected != after:
        raise RuntimeError('Organic native readback failed; inspect backup')
    print('Organic Meta natural metrics verified', flush=True)
    print('Organic Meta F1 verified: ' + stamp, flush=True)
