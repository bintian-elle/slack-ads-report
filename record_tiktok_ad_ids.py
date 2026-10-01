"""Reverse-match old TikTok metrics; only verified bindings may populate N."""
import argparse
import itertools
import json
import os
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from audit_tiktok_auxiliary_matches import compare
from add_tiktok_tracker_rows import code_key
from sync_creator_tracker import BASE, get_json
from update_meta_tracker import write_range
from update_tiktok_tracker import METRICS


def sufficiently_matches(comparison):
    checks = {c['metric']: c for c in comparison['checks']}
    return (checks.get('spend', {}).get('sheet', 0) > 0 and
            checks.get('views', {}).get('sheet', 0) > 0 and
            all(checks.get(k, {}).get('display_match') for k in ['spend', 'views']))


def reverse_matches(row, ads, allowed_ids):
    """Do not infer identity from numbers alone, or from all-zero matches."""
    eligible = [a for a in ads if a['dimensions']['ad_id'] in allowed_ids]
    matched = []
    for a in ads:
        comparison = compare(row, [a])
        if sufficiently_matches(comparison):
            matched.append({'ad_ids': [a['dimensions']['ad_id']], 'comparison': comparison,
                            'identity_supported': a['dimensions']['ad_id'] in allowed_ids})
    if 1 < len(eligible) <= 10:
        for size in range(2, min(4, len(eligible)) + 1):
            for subset in itertools.combinations(eligible, size):
                comparison = compare(row, subset)
                if sufficiently_matches(comparison):
                    matched.append({'ad_ids': sorted(a['dimensions']['ad_id'] for a in subset),
                                    'comparison': comparison, 'identity_supported': True})
    return matched


def read_native(session, endpoint):
    metadata = get_json(session, endpoint, {'fields': 'spreadsheetId,sheets(properties)'})
    prop = next(s['properties'] for s in metadata['sheets'] if s['properties']['title'] == 'TikTok')
    if prop['gridProperties']['columnCount'] < 14:
        raise RuntimeError('N column does not exist; no write')
    data = get_json(session, endpoint, {'ranges': "'TikTok'!A1:N%d" % prop['gridProperties']['rowCount'],
                'includeGridData': 'true', 'fields': 'sheets(properties,tables,merges,protectedRanges,data(rowData(values(userEnteredValue,userEnteredFormat,dataValidation,chipRuns,textFormatRuns,effectiveValue))))'})
    return prop, data


def fetch_dated(env, end):
    session = requests.Session()
    session.headers['Access-Token'] = env.get('KOL_TRACKER_TIKTOK_ACCESS_TOKEN') or env['TIKTOK_ACCESS_TOKEN']
    accounts = env['TIKTOK_ADVERTISER_IDS'].strip()
    accounts = json.loads(accounts) if accounts.startswith('[') else accounts.split(',')
    if len(accounts) != 1:
        raise RuntimeError('Historical audit only covered one account; inspect before matching')
    records, page = [], 1
    while True:
        payload = get_json(session, 'https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/', {
            'advertiser_id': str(accounts[0]).strip(), 'report_type': 'BASIC', 'data_level': 'AUCTION_AD',
            'dimensions': json.dumps(['ad_id']), 'metrics': json.dumps(METRICS),
            'start_date': '2025-12-01', 'end_date': end, 'page': page, 'page_size': 1000})
        if payload.get('code') != 0:
            raise RuntimeError('Reporting failed: %s' % payload.get('code'))
        records.extend(payload['data']['list'])
        if page >= int(payload['data']['page_info']['total_page']):
            return records
        page += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--scan-days', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.scan_days <= 60:
        parser.error('--scan-days must be 1..60')
    load_dotenv(BASE / '.env')
    env = dict(os.environ)
    root = BASE / 'data/processed/kol_tracker_audit'
    output = root / 'tiktok_reverse_match' / datetime.now().strftime('%Y%m%dT%H%M%S')
    output.mkdir(parents=True)
    old = json.loads((root / 'tiktok/sheet.json').read_text())
    audit = json.loads((root / 'tiktok_auxiliary_matching/report.json').read_text())
    previous = json.loads((root / 'tiktok_name_matching/plan.json').read_text())
    by_row = {r['row']: r for r in audit['rows']}
    baseline = {code_key(r[2]): (i + 1, r + [''] * 14) for i, r in enumerate(old['values'])
                if i > 1 and len(r) > 2 and r[0] and r[2]}
    code_counts = Counter(code_key(r[2]) for r in old['values'][2:] if len(r) > 2 and r[0] and r[2])
    baseline = {key: item for key, item in baseline.items() if code_counts[key] == 1}
    sid = old['metadata']['spreadsheetId']
    gs = AuthorizedSession(Credentials.from_service_account_file(
        str(BASE / env.get('GOOGLE_SERVICE_ACCOUNT_FILE', 'credentials/google-service-account.json')),
        scopes=['https://www.googleapis.com/auth/spreadsheets']))
    endpoint = 'https://sheets.googleapis.com/v4/spreadsheets/' + sid
    prop, native = read_native(gs, endpoint)
    values = get_json(gs, endpoint + '/values/' + quote("'TikTok'!A1:N%d" % prop['gridProperties']['rowCount'], safe=''),
                      {'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values', [])
    (output / 'native-before.json').write_text(json.dumps(native, ensure_ascii=False))
    confirmed = {}
    for p in previous['rows']:
        if p['decision'] != 'proposed_update':
            continue
        row = values[p['row'] - 1] + [''] * 14
        key = code_key(row[2])
        if key not in baseline or row[:5] != baseline[key][1][:5]:
            continue
        confirmed[key] = {'ad_ids': sorted(c['ad_id'] for c in p['candidates']), 'method': 'prior_cross_verified'}
    unresolved = [key for key in baseline if key not in confirmed]
    observations = {key: [] for key in unresolved}
    # Table's prior update was Sep 30; scan exact historical cutoff dates,
    # never select the closest monetary value as a fallback.
    for offset in range(args.scan_days):
        end = (datetime(2026, 9, 30) - timedelta(days=offset)).date().isoformat()
        cache = output.parent / ('report-' + end + '.json')
        if cache.exists():
            ads = json.loads(cache.read_text())
        else:
            ads = fetch_dated(env, end)
            cache.write_text(json.dumps(ads, ensure_ascii=False))
        for key in unresolved:
            number, row = baseline[key]
            allowed = {c['ad_id'] for c in by_row[number]['candidates']}
            for match in reverse_matches(row, ads, allowed):
                observations[key].append(dict(match, end_date=end))
        print('Checked ' + end, flush=True)
    for key, hits in observations.items():
        supported = [h for h in hits if h['identity_supported']]
        choices = {tuple(h['ad_ids']) for h in supported}
        # A strict subset plus zero-delivery ads is still ambiguous: don't
        # pretend reverse metrics prove the unobserved extra ads belong.
        if len(choices) == 1:
            confirmed[key] = {'ad_ids': list(next(iter(choices))), 'method': 'historical_reverse_match',
                              'evidence': supported}
    claims = Counter(a for item in confirmed.values() for a in item['ad_ids'])
    confirmed = {key: item for key, item in confirmed.items() if all(claims[a] == 1 for a in item['ad_ids'])}
    planned = []
    for number, current in enumerate(values, 1):
        if number < 3 or len(current) < 3 or not current[0]:
            continue
        row = current + [''] * 14
        key = code_key(row[2])
        if key not in confirmed or row[:5] != baseline[key][1][:5]:
            continue
        value = '\n'.join(confirmed[key]['ad_ids'])
        if row[13] and row[13] != value:
            continue
        planned.append({'row': number, 'creator': row[0], 'value': value, **confirmed[key]})
    report = {'planned': planned, 'observations': observations, 'scan_days': args.scan_days}
    (output / 'plan.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'planned': len(planned), 'new_reverse_matches': sum(p['method'] == 'historical_reverse_match' for p in planned),
                      'unresolved': sum(bool(r and r[0]) for r in values[2:]) - len(planned)}), flush=True)
    if not args.apply:
        return
    tab = native['sheets'][0]
    if tab.get('tables') or tab.get('protectedRanges') or tab.get('merges'):
        raise RuntimeError('Inspect native structure before writing N')
    cells = tab['data'][0]['rowData']
    header = cells[0].get('values', [])
    if len(header) > 13 and header[13].get('userEnteredValue', {}).get('stringValue', '') not in ['', 'Ad IDs']:
        raise RuntimeError('N1 already used; not overwritten')
    body = [write_range(prop['sheetId'], 1, 13, ['Ad IDs'])]
    for item in planned:
        peer = cells[item['row'] - 1].get('values', [])
        target = peer[13] if len(peer) > 13 else {}
        if target.get('dataValidation') or target.get('chipRuns') or 'formulaValue' in target.get('userEnteredValue', {}):
            raise RuntimeError('Constrained N target; no write')
        body.append(write_range(prop['sheetId'], item['row'], 13, [item['value']]))
    if read_native(gs, endpoint)[1] != native:
        raise RuntimeError('Concurrent sheet edit; no write')
    response = gs.post(endpoint + ':batchUpdate', json={'requests': body}, timeout=60)
    response.raise_for_status()
    after = read_native(gs, endpoint)[1]
    expected = json.loads(json.dumps(native))
    ecells = expected['sheets'][0]['data'][0]['rowData']
    for item in [{'row': 1, 'value': 'Ad IDs'}] + planned:
        row = ecells[item['row'] - 1].setdefault('values', [])
        row.extend({} for _ in range(max(0, 14 - len(row))))
        row[13]['userEnteredValue'] = {'stringValue': item['value']}
        row[13]['effectiveValue'] = {'stringValue': item['value']}
    # Strip trailing empty cells from both shapes before exact comparison.
    for obj in [expected, after]:
        for row in obj['sheets'][0]['data'][0]['rowData']:
            peers = row.get('values', [])
            while peers and not peers[-1]:
                peers.pop()
    (output / 'native-after.json').write_text(json.dumps(after, ensure_ascii=False))
    if expected != after:
        raise RuntimeError('N write readback differs; inspect backups, do not repeat blindly')
    print('N-column IDs and untouched A:M native cells verified')


if __name__ == '__main__':
    main()
