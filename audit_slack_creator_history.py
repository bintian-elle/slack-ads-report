"""Read-only July-October Slack/Tracker/Meta identity audit, no writes to services."""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from sync_creator_tracker import BASE, code_key, get_json, graph_pages, parse_announcements, post_key, read_sheet, read_slack


def main():
    load_dotenv(BASE / '.env')
    env = dict(os.environ)
    start = datetime(2026, 7, 1, tzinfo=ZoneInfo('America/Chicago'))
    end = datetime.now(timezone.utc)
    output = BASE / 'data/processed/kol_tracker_audit/slack_3months'
    output.mkdir(parents=True, exist_ok=True)
    cached = '--cached' in sys.argv
    if cached:
        source = json.loads((output / 'source.json').read_text())
        sheet, slack = source['sheet'], source['slack']
        end = datetime.fromisoformat(source['end'])
    else:
        sheet = read_sheet(env)
        print('Current Meta Tracker read', flush=True)
        slack = read_slack(env, start.timestamp(), end.timestamp())
    (output / 'source.json').write_text(json.dumps({'start': start.isoformat(), 'end': end.isoformat(), 'sheet': sheet, 'slack': slack}, ensure_ascii=False, indent=2))
    candidates, unresolved = parse_announcements(slack['messages'])
    print('Slack messages %d; parsed IG contents %d; unresolved %d' % (len(slack['messages']), len(candidates), len(unresolved)), flush=True)
    bypost, bycode = defaultdict(list), defaultdict(list)
    for i, r in enumerate(sheet['values']):
        if i < 2 or len(r) < 2 or not r[1]:
            continue
        r = r + [''] * max(0, 22 - len(r))
        record = {'row': i + 1, 'creator': r[1], 'post_link': r[6], 'ad_code': code_key(r[7])}
        if post_key(r[6]): bypost[post_key(r[6])].append(record)
        if code_key(r[7]): bycode[code_key(r[7])].append(record)
    s = requests.Session()
    s.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
    base = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION', 'v23.0')
    account = 'act_' + env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    ads = json.loads((output / 'ads.json').read_text()) if cached else graph_pages(s, base + '/' + account + '/ads', {
        'fields': 'id,name,creative{id,branded_content,source_instagram_media_id,effective_instagram_media_id,instagram_permalink_url}', 'limit': 100})
    (output / 'ads.json').write_text(json.dumps(ads, ensure_ascii=False, indent=2))
    adcodes = defaultdict(list)
    for ad in ads:
        value = ad.get('creative', {}).get('branded_content', {}).get('instagram_boost_post_access_token')
        if value: adcodes[code_key(value)].append(ad)
    for item in candidates:
        pr, cr = bypost[item['post_key']], bycode[item['ad_code']]
        # A same post with a different non-empty code is a conflict, not silently existing.
        if pr:
            compatible = all(not r['ad_code'] or r['ad_code'] == item['ad_code'] for r in pr)
            same_rows = not cr or {r['row'] for r in pr} == {r['row'] for r in cr}
            decision = 'existing' if compatible and same_rows and len(pr) == 1 else 'review_conflict'
        else:
            decision = ('existing_code_only' if len(cr) == 1 and not post_key(cr[0]['post_link'])
                        else 'review_conflict' if cr else 'proposed_new')
        item['decision'] = decision
        item['review_note'] = ('Same Ad Code already in one sheet row; sheet post link is blank/profile. Do not append or automatically replace link.'
                               if decision == 'existing_code_only' else '')
        item['sheet_rows'] = sorted({r['row'] for r in pr + cr})
        item['sheet_records'] = {str(r['row']): r for r in pr + cr}
        matched = adcodes[item['ad_code']]
        item['ad_ids'] = sorted({ad['id'] for ad in matched})
        item['ad_names'] = [ad['name'] for ad in matched]
        item['api_identity'] = 'creative_ad_code_exact' if matched else 'not_matched_do_not_fill_zero'
        item['announcement_date'] = datetime.fromtimestamp(float(item['ts']), ZoneInfo('America/Chicago')).isoformat()
    announcement_messages = [m for m in slack['messages'] if re.search(r'\[KOL Content is Live\]', m.get('text', ''), re.I)]
    report = {'start': start.isoformat(), 'end': end.isoformat(), 'messages': len(slack['messages']),
              'marked_announcements': len(announcement_messages), 'distinct_ig_contents': len(candidates),
              'decisions': dict(Counter(c['decision'] for c in candidates)),
              'api_matched_contents': sum(bool(c['ad_ids']) for c in candidates), 'account_ads_scanned': len(ads),
              'candidates': candidates, 'unresolved': unresolved, 'slack_issues': slack['issues'],
              'coverage': slack['coverage'],
              'scope': 'Meta IG contents only; no sheet write or deployment; Launch Date not inferred from announcement time.'}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    safe = {k: v for k, v in report.items() if k not in ['candidates', 'unresolved']}
    safe['non_existing'] = [{k: v for k, v in c.items() if k not in ['ad_code', 'sheet_records']} for c in candidates if c['decision'] != 'existing']
    safe['existing'] = [{'creator': c['creator'], 'rows': c['sheet_rows'], 'ads': len(c['ad_ids'])} for c in candidates if c['decision'] == 'existing']
    safe['unresolved'] = unresolved
    print(json.dumps(safe, ensure_ascii=False, indent=2), flush=True)
    print('PRIVATE_REPORT', output / 'report.json')


if __name__ == '__main__':
    main()
