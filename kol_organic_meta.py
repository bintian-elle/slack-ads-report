"""Partnership content refresh using Method A; no Slack inserts or ad mutations."""
import json
import math
import re
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

import requests


def post_key(value):
    match = re.search(r'instagram\.com/(?:[^/?#]+/)?(?:p|reel|reels)/([^/?#]+)', str(value))
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
        for attempt in range(3):
            response = session.get(base + '/' + business + '/partnership-ads-advertisable-content',
                params={'ig_user_id': account, 'permalinks': json.dumps(unique[offset:offset+5]),
                        'fields': 'content_id,permalink,organic_insights{views,interaction,likes,comments,shares,saves}'},
                timeout=45)
            if response.status_code not in (500, 502, 503, 504) or attempt == 2:
                break
            print('Organic Meta temporary HTTP %s; retrying batch' % response.status_code, flush=True)
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


AD_INTERACTION_FIELDS = ('onsite_conversion.post_net_like',
                         'onsite_conversion.post_net_comment', 'post')
PAUSED_STATUSES = {'PAUSED', 'ADSET_PAUSED', 'CAMPAIGN_PAUSED'}


def paid_cache_decision(entry, status, today, grace_days=30, refresh_days=7):
    """Observation-based grace period, never infer pause time from creation."""
    if status not in PAUSED_STATUSES:
        return True, None
    try:
        first = datetime.strptime(entry['paused_since'], '%Y-%m-%d').date()
        fetched = datetime.strptime(entry['fetched_on'], '%Y-%m-%d').date()
        valid = valid_number(entry['value']) and first <= today and fetched <= today
        if not valid:
            raise ValueError('Invalid cache')
    except (KeyError, ValueError, TypeError):
        return True, today.isoformat()
    refresh = ((today - first).days < grace_days or
               (today - fetched).days >= refresh_days)
    return refresh, first.isoformat()


def valid_metric(value):
    return valid_number(value) and value >= 0


def valid_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def fetch_ad_interactions(env, content, cache_path=None):
    """Complete inventory, exact source-media binding, Instagram-only Method A."""
    from sync_creator_tracker import get_json, graph_pages
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
    base = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION', 'v23.0')
    account = 'act_' + env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    info = get_json(session, base + '/' + account, {'fields': 'timezone_name'})
    ads = graph_pages(session, base + '/' + account + '/ads', {'limit': 100,
        'fields': 'id,name,created_time,effective_status,creative{id,branded_content,source_instagram_media_id}'})
    by_media = {str(item['content_id']): set() for item in content.values()}
    for ad in ads:
        source = str(ad.get('creative', {}).get('source_instagram_media_id', ''))
        if source in by_media:
            by_media[source].add(ad['id'])
    ids = sorted({aid for group in by_media.values() for aid in group})
    starts = {ad['id']: datetime.strptime(ad['created_time'], '%Y-%m-%dT%H:%M:%S%z')
              .astimezone(ZoneInfo(info['timezone_name'])).date().isoformat()
              for ad in ads if ad['id'] in ids}
    cutoff = datetime.now(ZoneInfo(info['timezone_name'])).date().isoformat()
    today = datetime.strptime(cutoff, '%Y-%m-%d').date()
    scope = {'account': account, 'base': base, 'timezone': info['timezone_name'],
             'fields': list(AD_INTERACTION_FIELDS), 'schema': 1}
    cached = {}
    if cache_path and Path(cache_path).exists():
        try:
            saved = json.loads(Path(cache_path).read_text())
            if saved.get('scope') == scope and isinstance(saved.get('ads'), dict):
                cached = saved['ads']
        except (ValueError, OSError):
            pass  # A corrupt cache triggers a full fresh read, never zero metrics.
    grace = max(30, int(env.get('KOL_ORGANIC_PAUSED_GRACE_DAYS', '30')))
    interval = max(1, int(env.get('KOL_ORGANIC_PAUSED_REFRESH_DAYS', '7')))
    by_id = {ad['id']: ad for ad in ads}
    # Disappeared/unbound ads must establish a new pause observation if they return.
    cached = {aid: entry for aid, entry in cached.items() if aid in ids}
    totals, refresh_ids, pause_dates = {}, [], {}
    for aid in ids:
        entry = cached.get(aid, {})
        if not isinstance(entry, dict) or entry.get('since') != starts[aid]:
            entry = {}
        refresh, pause_dates[aid] = paid_cache_decision(
            entry, by_id[aid].get('effective_status'), today, grace, interval)
        if refresh:
            refresh_ids.append(aid)
        else:
            totals[aid] = entry['value']
    print('Organic Method A: %d exact ads; querying %d; cached paused %d' %
          (len(ids), len(refresh_ids), len(totals)), flush=True)

    def read_ad(aid):
        # Separate sessions avoid shared mutable connection state between workers.
        worker = requests.Session()
        worker.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
        try:
            data = graph_pages(worker, base + '/' + aid + '/insights', {
                'fields': 'ad_id,actions', 'breakdowns': 'publisher_platform', 'limit': 100,
                'time_range': json.dumps({'since': starts[aid], 'until': cutoff})})
            total = 0
            for item in data:
                if item.get('publisher_platform') != 'instagram':
                    continue
                for action in item.get('actions', []):
                    if action.get('action_type') in AD_INTERACTION_FIELDS:
                        value = float(action['value'])
                        if not valid_number(value):
                            raise RuntimeError('Invalid paid interaction metric')
                        total += value
            return aid, total
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=5) as pool:
        for index, (aid, total) in enumerate(pool.map(read_ad, refresh_ids), 1):
            totals[aid] = total
            cached[aid] = {'value': total, 'since': starts[aid], 'fetched_on': cutoff,
                           'paused_since': pause_dates[aid]}
            if index % 25 == 0 or index == len(refresh_ids):
                print('Organic Method A ad progress: %d/%d' % (index, len(refresh_ids)), flush=True)
    if cache_path:
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'scope': scope, 'ads': cached}))
        os.replace(temporary, path)
    paid = {cid: {'value': sum(totals[aid] for aid in group),
                  'ad_ids': sorted(group), 'cutoff': cutoff,
                  'ad_fetched_on': {aid: cached[aid]['fetched_on'] for aid in group}}
            for cid, group in by_media.items()}
    return ads, paid


def plan(values, content, paid=None):
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
        basis = 'API organic interaction'
        natural = changes.get(8)
        if natural is None and all(col in changes for col in [9, 10, 11, 12]):
            natural = sum(changes[col] for col in [9, 10, 11, 12])
            basis = 'Organic likes + comments + shares + saves'
        if natural is not None:
            binding = (paid or {}).get(str(item['content_id']))
            if paid is None:
                changes[8] = natural
            elif (binding is not None and valid_number(binding.get('value'))
                  and valid_metric(natural + binding['value'])):
                changes[8] = natural + binding['value']
            else:
                changes.pop(8, None)
                basis = 'Paid interaction lookup unavailable'
            missing = [field for field in missing if field != 'interaction']
            if 8 not in changes:
                missing.append('interaction')
        # Never combine a fresh numerator with a stale denominator.
        fee = row[6] if len(row) > 6 else None
        if isinstance(fee, (int, float)) and not isinstance(fee, bool) and math.isfinite(fee) and fee >= 0:
            if changes.get(7, 0) > 0:
                changes[13] = fee / changes[7] * 1000
            if changes.get(8, 0) > 0:
                changes[14] = fee / changes[8]
        planned.append({'row': number, 'content_id': item['content_id'], 'changes': changes,
                        'missing': missing, 'interaction_basis': basis})
    return planned, skipped


def status_plan(values, ads, cells=None, ad_matches=None):
    """Only exact, unshared Ad Code bindings can change automatic statuses."""
    from update_meta_tracker import exact_mapping
    rows = []
    for number, row in enumerate(values[2:], 3):
        if row and row[0] and len(row) > 4 and row[4]:
            mapped = [''] * 8
            mapped[1], mapped[7] = row[0], row[4]
            rows.append((number, mapped))
    matches, _, _ = exact_mapping(rows, ads)
    if ad_matches is not None:
        matches = ad_matches
    by_id = {ad['id']: ad for ad in ads}
    result = []
    for number, ids in matches.items():
        row = values[number-1]
        current = row[5] if len(row) > 5 else ''
        if str(current).strip().lower() not in ['', 'testing', 'pause', 'paused']:
            continue
        statuses = {by_id[aid].get('effective_status') for aid in ids}
        value = ('testing' if 'ACTIVE' in statuses else 'paused'
                 if statuses and statuses.issubset(PAUSED_STATUSES) else None)
        if value is not None and cells is not None:
            row_cells = cells[number-1].get('values', [])
            target = row_cells[5] if len(row_cells) > 5 else {}
            rule = target.get('dataValidation', {})
            condition = rule.get('condition', {})
            if condition.get('type') == 'ONE_OF_LIST':
                allowed = [v.get('userEnteredValue') for v in condition.get('values', [])]
                if value == 'paused' and value not in allowed and 'pause' in allowed:
                    value = 'pause'
                if value not in allowed:
                    continue
        if value is not None and value != current:
            result.append({'row': number, 'ad_ids': ids, 'changes': {5: value}})
    return result


NOTE_HEADER = 'Reason for data update failure'
NOTE_PREFIX = '自动：'  # Legacy notes are migrated, never written with this prefix.
MANAGED_NOTE_STARTS = ('API did not return ', 'Missing or unsupported post link',
                       'KOL fee requires manual entry', 'CPM ', 'CPE ')


def note_plan(values, planned):
    """Explain metric gaps, never infer authorization failure from an absent post."""
    header = values[1][16] if len(values[1]) > 16 else ''
    if header not in ['', NOTE_HEADER, '数据更新说明（自动）', '数据更新失败原因']:
        raise RuntimeError('Organic Q column is occupied')
    updates = [{'row': 2, 'changes': {16: NOTE_HEADER}}] if header != NOTE_HEADER else []
    by_row = {item['row']: item for item in planned}
    labels = dict(zip(['views', 'interaction', 'likes', 'comments', 'shares', 'saves'],
                      ['Views', 'Interaction', 'Likes', 'Comments', 'Shares', 'Saves']))
    for number, original in enumerate(values[2:], 3):
        row = list(original) + [''] * 17
        if not row[0] or str(row[0]).strip().lower() == 'summary':
            continue
        old = row[16]
        if old and not str(old).startswith((NOTE_PREFIX,) + MANAGED_NOTE_STARTS):
            continue  # User-authored notes are not ours to replace.
        reasons = []
        if not post_key(row[3]):
            reasons.append('Missing or unsupported post link; H:O unchanged')
        elif number not in by_row:
            reasons.append('API did not return the matching post; H:O unchanged')
        else:
            item = by_row[number]
            if item['missing']:
                reasons.append('API did not return ' + '/'.join(labels[x] for x in item['missing']))
            fee = row[6]
            valid_fee = (not isinstance(fee, bool) and isinstance(fee, (int, float))
                         and math.isfinite(fee) and fee >= 0)
            if not valid_fee:
                reasons.append('KOL fee requires manual entry; CPM/CPE unchanged')
            else:
                for col, label in [(7, 'CPM'), (8, 'CPE')]:
                    if col not in item['changes']:
                        reasons.append(label + ' denominator unavailable')
                    elif item['changes'][col] == 0:
                        reasons.append(label + ' denominator is zero; unchanged')
        value = '; '.join(reasons)
        if value != old:
            updates.append({'row': number, 'changes': {16: value}})
    return updates


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


def media_identity_plan(values, ads, content, blocked_rows=()):
    """Exact post -> returned content ID -> Creative source media; never names."""
    from sync_creator_tracker import code_key
    by_media, by_code = {}, {}
    for ad in ads:
        creative = ad.get('creative', {})
        source = str(creative.get('source_instagram_media_id') or '')
        if source:
            by_media.setdefault(source, set()).add(ad['id'])
        code = code_key(creative.get('branded_content', {}).get('instagram_boost_post_access_token'))
        if code:
            by_code.setdefault(code, set()).add(ad['id'])
    resolved = [list(row) for row in values]
    matches, updates, issues, claims = {}, [], [], {}
    blocked = set(blocked_rows)
    for number, row in enumerate(resolved[2:], 3):
        if not row or not row[0] or number in blocked:
            continue
        row.extend([''] * max(0, 6-len(row)))
        item = content.get(post_key(row[3]))
        media_ids = by_media.get(str(item['content_id']), set()) if item else set()
        existing = code_key(row[4])
        code_ids = by_code.get(existing, set()) if existing else set()
        if media_ids and code_ids and not code_ids.issubset(media_ids):
            issues.append({'row': number, 'reason': 'Conflicting Media ID/Ad Code binding'})
            blocked.add(number)
            continue
        ids = media_ids or code_ids
        if not ids:
            continue
        matches[number] = sorted(ids)
        for aid in ids:
            claims.setdefault(aid, set()).add(number)
        if not existing and media_ids:
            candidates = {}
            for ad in ads:
                if ad['id'] not in media_ids:
                    continue
                raw = ad.get('creative', {}).get('branded_content', {}).get('instagram_boost_post_access_token')
                if code_key(raw):
                    candidates[code_key(raw)] = raw
            if len(candidates) == 1:
                key, raw = next(iter(candidates.items()))
                # A code shared with other media cannot be safely assigned.
                if by_code[key].issubset(media_ids):
                    updates.append({'row': number, 'changes': {4: raw},
                                    'source': 'Exact source_instagram_media_id + unique Creative Ad Code'})
    for numbers in claims.values():
        if len(numbers) > 1:
            blocked.update(numbers)
    for number in sorted(blocked - set(blocked_rows)):
        if not any(item['row'] == number for item in issues):
            issues.append({'row': number, 'reason': 'Ad ID claimed by multiple Organic rows'})
    matches = {number: ids for number, ids in matches.items() if number not in blocked}
    updates = [item for item in updates if item['row'] not in blocked]
    for item in updates:
        resolved[item['row']-1][4] = item['changes'][4]
    return resolved, matches, updates, issues


def run(env, output, apply):
    from kol_organic_discovery import run as discover
    discover(env,output,apply)
    from kol_tracker import sheet_session, read_tab, save_json, verify_literal_target
    from update_meta_tracker import write_range
    scoped = dict(env, KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK'])
    session, endpoint = sheet_session(scoped)
    prop, native, values = read_tab(session, endpoint, 'Meta-IG')
    # Validate schema before making API calls.
    plan(values, {})
    print('Organic Meta sheet read; fetching natural metrics', flush=True)
    links = ['https://www.instagram.com/reel/' + post_key(row[3]) + '/'
             for row in values[2:] if len(row) > 3 and row[0] and post_key(row[3])]
    content = fetch_content(env, links)
    print('Organic content fetched; fetching Method A paid interactions', flush=True)
    cache_path = Path(env.get('KOL_TRACKER_STATE_DIR',
        str(Path(__file__).parent / 'data/processed/kol_tracker_runtime'))) / 'organic-paid-cache.json'
    ads, paid = fetch_ad_interactions(env, content, cache_path)
    planned, skipped = plan(values, content, paid)
    notes = note_plan(values, planned)
    print('Organic Meta natural metrics fetched; fetching ad statuses', flush=True)
    tracker_values = []
    if env.get('KOL_TRACKER_GOOGLE_SHEETS_LINK'):
        tracker_session, tracker_endpoint = sheet_session(env)
        _, _, tracker_values = read_tab(tracker_session, tracker_endpoint, 'Meta')
    resolved, codes, issues = code_plan(values, tracker_values, ads)
    resolved, matches, media_codes, media_issues = media_identity_plan(
        resolved, ads, content, {item['row'] for item in issues})
    issues.extend(media_issues)
    rejected = {item['row'] for item in issues}
    codes = [item for item in codes if item['row'] not in rejected] + media_codes
    statuses = status_plan(resolved, ads, native['sheets'][0]['data'][0]['rowData'], matches)
    save_json(output / 'Organic-Meta-plan.json', {'basis': 'Method A: organic interaction (four-part fallback) + Instagram paid net likes/net comments/shares',
                                               'paid_interactions': paid,
                                               'planned': planned, 'skipped': skipped, 'status_updates': statuses,
                                               'code_updates': codes, 'identity_issues': issues,
                                               'note_updates': notes})
    print('Organic Meta planned: %d; skipped: %d' % (len(planned), len(skipped)), flush=True)
    print('Organic Meta automatic status updates: %d' % len(statuses), flush=True)
    print('Organic Meta exact Post Link code backfills: %d; identity conflicts: %d' %
          (len(codes), len(issues)), flush=True)
    if not apply:
        return
    cells = native['sheets'][0]['data'][0]['rowData']
    body, expected = [], json.loads(json.dumps(native))
    expected_rows = expected['sheets'][0]['data'][0]['rowData']
    for item in planned + codes + statuses + notes:
        for col, value in item['changes'].items():
            row = cells[item['row']-1].get('values', [])
            target = row[col] if col < len(row) else {}
            if col in [13, 14] and 'formulaValue' in target.get('userEnteredValue', {}):
                continue  # Existing CPM/CPE formulas recalculate; never replace them.
            if col == 8 and target.get('userEnteredValue', {}).get('formulaValue') in [
                    '=J%d+K%d+L%d+M%d' % ((item['row'],) * 4),
                    '=SUM(J%d:M%d)' % (item['row'], item['row'])]:
                # User explicitly selected Method A for every I cell: migrate
                # the old four-part-only I formula to the API-backed result.
                literal_target = dict(target)
                literal_target['userEnteredValue'] = {}
                verify_literal_target(literal_target, value)
            else:
                verify_literal_target(target, value)
            request = write_range(prop['sheetId'], item['row'], col, [value])
            body.append(request)
            expected_row = expected_rows[item['row']-1].setdefault('values', [])
            expected_row.extend({} for _ in range(max(0, col+1-len(expected_row))))
            entered = request['updateCells']['rows'][0]['values'][0].get('userEnteredValue')
            if entered:
                expected_row[col]['userEnteredValue'] = entered
            else:
                expected_row[col].pop('userEnteredValue', None)
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
