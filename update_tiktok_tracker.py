"""Standalone provisional Ad Name matching. Preview by default; no ad mutations."""
import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from add_tiktok_tracker_rows import read_tiktok
from sync_creator_tracker import BASE, get_json
from update_meta_tracker import write_range

METRICS = ['ad_name', 'spend', 'complete_payment', 'complete_payment_roas',
           'reach', 'impressions', 'video_play_actions', 'video_watched_2s']


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def creator_segment(name):
    # Capture the entire identity segment, including content suffixes, not a
    # substring: Josh must not match Josh3, UGC_Amanda must not match Amanda.
    match = re.search(r'_(?:Spark|Partnership)_(.+?)_(?:HP|PDP|LP|Collection[^_]*)$', name, re.I)
    return normalized(match.group(1)) if match else ''


def fetch_report(env):
    token = env.get('KOL_TRACKER_TIKTOK_ACCESS_TOKEN') or env['TIKTOK_ACCESS_TOKEN']
    accounts = env['TIKTOK_ADVERTISER_IDS'].strip()
    accounts = json.loads(accounts) if accounts.startswith('[') else accounts.split(',')
    session = requests.Session()
    session.headers['Access-Token'] = token
    records = []
    for account in accounts:
        page = 1
        while True:
            result = get_json(session, 'https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/', {
                'advertiser_id': str(account).strip(), 'report_type': 'BASIC',
                'data_level': 'AUCTION_AD', 'dimensions': json.dumps(['ad_id']),
                'metrics': json.dumps(METRICS), 'query_lifetime': 'true',
                'page': page, 'page_size': 1000})
            if result.get('code') != 0:
                raise RuntimeError('TikTok reporting failed; code=%s' % result.get('code'))
            data = result['data']
            records.extend(dict(row, advertiser_id=str(account).strip()) for row in data['list'])
            if page >= int(data['page_info']['total_page']):
                break
            page += 1
    return records


def plan_rows(values, records):
    counts = Counter(normalized(row[0]) for row in values[2:] if row and row[0])
    decisions = []
    for index, row in enumerate(values[2:], 3):
        if not row or not row[0]:
            continue
        key = normalized(row[0])
        matches = [ad for ad in records if creator_segment(ad['metrics']['ad_name']) == key]
        item = {'row': index, 'creator': row[0], 'match_method': 'ad_name_creator_segment',
                'identity_verified': False, 'candidates': [
                    {'ad_id': ad['dimensions']['ad_id'], 'ad_name': ad['metrics']['ad_name'],
                     'advertiser_id': ad['advertiser_id']} for ad in matches]}
        if counts[key] > 1:
            item['decision'] = 'skip_repeated_creator'
        elif not matches:
            item['decision'] = 'skip_no_name_match'
        elif len(matches) != 1:
            # Reach cannot be summed across ads. Multiple ads/content dates
            # need explicit resolution rather than a silently inflated total.
            item['decision'] = 'skip_multiple_ads'
        else:
            m = matches[0]['metrics']
            try:
                metric = {k: float(m[k]) for k in METRICS if k != 'ad_name'}
                impressions = metric['impressions']
                import math
                if any(not math.isfinite(v) or v < 0 for v in metric.values()):
                    raise ValueError('Invalid metric')
                item['new_values'] = [metric['spend'], metric['complete_payment'],
                                      metric['complete_payment_roas'], metric['reach'],
                                      metric['video_play_actions'],
                                      metric['video_watched_2s'] / impressions if impressions else None]
                item['old_values'] = (row + [''] * 13)[7:13]
                item['decision'] = 'proposed_update'
            except (KeyError, ValueError, TypeError):
                item['decision'] = 'skip_missing_metrics'
        decisions.append(item)
    claims = Counter((ad['advertiser_id'], ad['ad_id']) for item in decisions
                     if item['decision'] == 'proposed_update' for ad in item['candidates'])
    for item in decisions:
        if item['decision'] == 'proposed_update' and any(
                claims[(ad['advertiser_id'], ad['ad_id'])] > 1 for ad in item['candidates']):
            item['decision'] = 'skip_shared_ad'
    return decisions


def cross_verified_plan(values, records, audit, baseline):
    """Bind approved audit evidence by content code, never mutable row index."""
    from add_tiktok_tracker_rows import code_key
    decisions = plan_rows(values, records)
    by_code = {code_key(row[2]): i for i, row in enumerate(baseline['values'])
               if i > 1 and len(row) > 2 and row[0] and row[2]}
    evidence = {r['row'] - 1: r for r in audit['rows']}
    by_id = {r['dimensions']['ad_id']: r for r in records}
    for item in decisions:
        row = values[item['row'] - 1] + [''] * 13
        index = by_code.get(code_key(row[2]))
        if index is None:
            item['decision'] = 'skip_not_cross_verified'
            continue
        old = baseline['values'][index] + [''] * 13
        if row[:5] != old[:5]:
            item['decision'] = 'skip_identity_changed'
            continue
        e = evidence[index]
        def supported(comparison):
            checks = {c['metric']: c for c in comparison['checks']}
            return all(checks.get(k, {}).get('display_match') for k in ['spend', 'views'])
        strong = [c for c in e['candidates'] if c['comparison']['match_count'] >= 2 and
                  any(check['metric'] == 'spend' and check['display_match'] for check in c['comparison']['checks'])]
        groups = {tuple(sorted(g['ad_ids'])) for g in e['group_comparisons'] if supported(g)}
        if groups:
            minimum = min(map(len, groups))
            groups = {g for g in groups if len(g) == minimum}
        chosen = None
        if len(strong) == 1:
            chosen = [strong[0]['ad_id']]
        elif not strong and len(groups) == 1:
            chosen = list(next(iter(groups)))
        # A unique date with contradictory historical metrics is not enough.
        if not chosen or any(aid not in by_id for aid in chosen):
            item['decision'] = 'skip_cross_verification_ambiguous'
            continue
        ads = [by_id[aid] for aid in chosen]
        ms = [a['metrics'] for a in ads]
        if len({a['advertiser_id'] for a in ads}) != 1:
            item['decision'] = 'skip_cross_account'
            continue
        spend = sum(float(m['spend']) for m in ms)
        impressions = sum(float(m['impressions']) for m in ms)
        # Rounded single-ad ROAS is not a precise revenue source. Preserve
        # multi-ad ROAS and Reach until a filtered aggregate is verified.
        item.update(decision='proposed_update', match_method='approved_cross_verification',
                    candidates=[{'ad_id': a['dimensions']['ad_id'], 'ad_name': a['metrics']['ad_name'],
                                 'advertiser_id': a['advertiser_id']} for a in ads],
                    new_values=[spend, sum(float(m['complete_payment']) for m in ms),
                                float(ms[0]['complete_payment_roas']) if len(ms) == 1 else row[9],
                                float(ms[0]['reach']) if len(ms) == 1 else row[10],
                                sum(float(m['video_play_actions']) for m in ms),
                                sum(float(m['video_watched_2s']) for m in ms) / impressions if impressions else None],
                    old_values=row[7:13], preserve_columns=[9, 10] if len(ms) > 1 else [])
    claims = Counter((c['advertiser_id'], c['ad_id']) for d in decisions
                     if d['decision'] == 'proposed_update' for c in d['candidates'])
    for d in decisions:
        if d['decision'] == 'proposed_update' and any(claims[(c['advertiser_id'], c['ad_id'])] > 1 for c in d['candidates']):
            d['decision'] = 'skip_shared_ad'
    return decisions


def apply_plan(env, before, decisions, output):
    chosen = [d for d in decisions if d['decision'] == 'proposed_update']
    if not chosen:
        return
    import fcntl
    with (output / 'write.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if read_tiktok(env)['values'] != before['values']:
            raise RuntimeError('Sheet changed; not written')
        (output / 'before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2))
        session = AuthorizedSession(Credentials.from_service_account_file(
            str(BASE / env.get('GOOGLE_SERVICE_ACCOUNT_FILE', 'credentials/google-service-account.json')),
            scopes=['https://www.googleapis.com/auth/spreadsheets']))
        tab_id = before['tab']['sheetId']
        endpoint = 'https://sheets.googleapis.com/v4/spreadsheets/' + before['metadata']['spreadsheetId']
        params = {'ranges': "'TikTok'!A1:M%d" % before['tab']['gridProperties']['rowCount'],
                  'includeGridData': 'true', 'fields': 'sheets(properties,data(rowData(values(userEnteredValue,userEnteredFormat,dataValidation,chipRuns,textFormatRuns))))'}
        native_before = get_json(session, endpoint, params)
        (output / 'native-before.json').write_text(json.dumps(native_before, ensure_ascii=False))
        body = []
        cells = native_before['sheets'][0]['data'][0].get('rowData', [])
        for item in chosen:
            for offset, value in enumerate(item['new_values']):
                column = 7 + offset
                if column in item.get('preserve_columns', []):
                    continue
                native = cells[item['row'] - 1].get('values', [])
                target = native[column] if len(native) > column else {}
                if target.get('dataValidation') or target.get('chipRuns') or 'formulaValue' in target.get('userEnteredValue', {}):
                    raise RuntimeError('Constrained/formula target; no write')
                body.append(write_range(tab_id, item['row'], column, [value]))
        stamp = datetime.now(ZoneInfo('America/Chicago')).strftime('%m/%d')
        header = 'Lifetime Spend\n[%s update]' % stamp
        body.append(write_range(tab_id, 1, 7, [header]))
        if read_tiktok(env)['values'] != before['values']:
            raise RuntimeError('Sheet changed; not written')
        response = session.post('https://sheets.googleapis.com/v4/spreadsheets/' +
                                before['metadata']['spreadsheetId'] + ':batchUpdate',
                                json={'requests': body}, timeout=60)
        response.raise_for_status()
        after = read_tiktok(env)
        expected = [list(row) for row in before['values']]
        for item in chosen:
            row = expected[item['row'] - 1]
            row.extend([''] * max(0, 13 - len(row)))
            row[7:13] = ['' if v is None else v for v in item['new_values']]
        expected[0][7] = header
        # Untouched formulas (e.g. row 2 totals) legitimately recalculate.
        for index, data in enumerate(cells):
            for column, native in enumerate(data.get('values', [])):
                if 'formulaValue' in native.get('userEnteredValue', {}):
                    padded = after['values'][index] + [''] * 13
                    expected[index].extend([''] * max(0, column + 1 - len(expected[index])))
                    expected[index][column] = padded[column]
        clean = lambda rows: [list(reversed(list(__import__('itertools').dropwhile(
            lambda v: v == '', reversed(row))))) for row in rows]
        if clean(after['values']) != clean(expected):
            raise RuntimeError('Readback differs; inspect backup, do not retry blindly')
        (output / 'after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2))
        native_after = get_json(session, endpoint, params)
        changed = {(d['row'] - 1, col) for d in chosen for col in range(7, 13)
                   if col not in d.get('preserve_columns', [])} | {(0, 7)}
        previous = native_before['sheets'][0]['data'][0].get('rowData', [])
        current = native_after['sheets'][0]['data'][0].get('rowData', [])
        for index in range(max(len(previous), len(current))):
            a = previous[index].get('values', []) if index < len(previous) else []
            b = current[index].get('values', []) if index < len(current) else []
            for col in range(max(len(a), len(b))):
                left, right = dict(a[col]) if col < len(a) else {}, dict(b[col]) if col < len(b) else {}
                if (index, col) in changed:
                    left.pop('userEnteredValue', None); right.pop('userEnteredValue', None)
                if left != right:
                    raise RuntimeError('Native cell verification failed; inspect backup')
        print('Native formats, validations and unmodified values verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--cross-verified', action='store_true')
    args = parser.parse_args()
    load_dotenv(BASE / '.env')
    env = dict(os.environ)
    before = read_tiktok(env)
    records = fetch_report(env)
    decisions = plan_rows(before['values'], records)
    if args.cross_verified:
        root = BASE / 'data/processed/kol_tracker_audit'
        decisions = cross_verified_plan(before['values'], records,
            json.loads((root / 'tiktok_auxiliary_matching/report.json').read_text()),
            json.loads((root / 'tiktok/sheet.json').read_text()))
    output = BASE / 'data/processed/kol_tracker_audit/tiktok_name_matching'
    output.mkdir(parents=True, exist_ok=True)
    plan = {'report_window': 'query_lifetime=true', 'ads': len(records), 'rows': decisions}
    (output / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(json.dumps({'ads': len(records), 'decisions': dict(Counter(d['decision'] for d in decisions))}))
    if args.apply:
        apply_plan(env, before, decisions, output)
        print('TikTok metric writes verified; existing A:G and unmatched rows preserved')


if __name__ == '__main__':
    main()
