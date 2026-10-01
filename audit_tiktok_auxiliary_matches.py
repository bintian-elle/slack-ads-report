"""Read-only reconciliation of saved Slack, Sheet and dated TikTok API evidence."""
import itertools
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from add_tiktok_tracker_rows import code_key, parse_tiktok_announcements, post_key
from sync_creator_tracker import BASE
from update_tiktok_tracker import creator_segment, normalized


def username(link):
    match = re.search(r'/@([^/]+)', urlsplit(link or '').path)
    return normalized(match.group(1)) if match else ''


def date_serial(value):
    if isinstance(value, (int, float)):
        return (datetime(1899, 12, 30) + timedelta(days=value)).date().isoformat()
    return str(value or '')


def name_date(value):
    match = re.match(r'^(\d{6})_', value)
    if match:
        try:
            return datetime.strptime(match.group(1), '%y%m%d').date().isoformat()
        except ValueError:
            pass
    return ''


def compare(row, ads):
    metrics = [a['metrics'] for a in ads]
    spend = sum(float(m['spend']) for m in metrics)
    impressions = sum(float(m['impressions']) for m in metrics)
    proposed = [spend, sum(float(m['complete_payment']) for m in metrics),
                sum(float(m['spend']) * float(m['complete_payment_roas']) for m in metrics) / spend if spend else 0,
                float(metrics[0]['reach']) if len(metrics) == 1 else None,
                sum(float(m['video_play_actions']) for m in metrics),
                sum(float(m['video_watched_2s']) for m in metrics) / impressions if impressions else None]
    old = (row + [''] * 13)[7:13]
    checks = []
    for i, label in enumerate(['spend', 'purchase', 'roas', 'reach', 'views', 'hook_rate']):
        a, b = old[i], proposed[i]
        if isinstance(a, (int, float)) and b is not None:
            tolerance = [.005, 0, .005, 0, 0, .00005][i]
            checks.append({'metric': label, 'sheet': a, 'api': b, 'difference': b-a,
                           'display_match': abs(b-a) <= tolerance})
    return {'checks': checks, 'match_count': sum(c['display_match'] for c in checks),
            'compared_count': len(checks), 'reach_not_additive': len(ads) > 1,
            'roas_aggregation_approximate': len(ads) > 1}


def main():
    root = BASE / 'data/processed/kol_tracker_audit'
    source = json.loads((root / 'slack_3months/source.json').read_text())
    sheet = json.loads((root / 'tiktok/sheet.json').read_text())
    api = json.loads((root / 'tiktok/candidate_comparison.json').read_text())
    announcements, issues = parse_tiktok_announcements(source['slack']['messages'])
    ads = api['payload']['data']['list']
    rows = []
    for n, original in enumerate(sheet['values'][2:], 3):
        if not original or not original[0]:
            continue
        row = original + [''] * max(0, 13-len(original))
        linked = [a for a in announcements if code_key(row[2]) == a['ad_code']]
        aliases = {normalized(row[0]), username(row[3])} - {''}
        for a in linked:
            aliases.update({normalized(a['creator']), username(a['post_link'])} - {''})
        dates = {datetime.fromtimestamp(float(a['announcement_ts']), ZoneInfo('America/Chicago')).date().isoformat()
                 for a in linked}
        code_dates = {datetime.fromtimestamp(float(a['ts']), ZoneInfo('America/Chicago')).date().isoformat()
                      for a in linked}
        postid = post_key(row[3]).removeprefix('video:') if post_key(row[3]).startswith('video:') else ''
        candidates = []
        for ad in ads:
            m = ad['metrics']
            segment = creator_segment(m['ad_name'])
            if not segment:
                continue
            reasons = []
            if segment in aliases:
                reasons.append('exact_normalized_alias')
            # Content-number removal is a candidate generator, never proof.
            if re.sub(r'\d+$', '', segment) in {re.sub(r'\d+$', '', a) for a in aliases}:
                reasons.append('same_creator_base_content_number_ambiguous')
            if not reasons:
                continue
            date = name_date(m['ad_name'])
            text = m.get('ad_text', '')
            c = {'ad_id': ad['dimensions']['ad_id'], 'ad_name': m['ad_name'],
                 'campaign': m.get('campaign_name'), 'adgroup': m.get('adgroup_name'),
                 'reasons': reasons, 'name_date': date, 'launch_date_equal': date == date_serial(row[4]),
                 'slack_announcement_date_equal': date in dates,
                 'slack_code_message_date_equal': date in code_dates,
                 'post_id_in_name_or_text': bool(postid and postid in m['ad_name'] + ' ' + text),
                 'ad_text': text, 'comparison': compare(row, [ad])}
            candidates.append(c)
        # Explore group totals for diagnosis only, not an approved mapping.
        subsets = []
        if 1 < len(candidates) <= 8:
            eligible = {c['ad_id'] for c in candidates}
            group = [a for a in ads if a['dimensions']['ad_id'] in eligible]
            for size in range(2, min(4, len(group)) + 1):
                for subset in itertools.combinations(group, size):
                    comparison = compare(row, subset)
                    if comparison['match_count'] >= 2:
                        subsets.append({'ad_ids': [a['dimensions']['ad_id'] for a in subset], **comparison})
        rows.append({'row': n, 'creator': row[0], 'aliases': sorted(aliases),
                     'launch_date': date_serial(row[4]), 'slack_dates': sorted(dates),
                     'slack_code_message_dates': sorted(code_dates),
                     'slack_code_matches': len(linked), 'slack_post_matches': sum(
                         bool(post_key(row[3])) and post_key(row[3]) == a['post_key'] for a in linked),
                     'candidates': candidates, 'group_comparisons': subsets})
    result = {'scope': 'Saved Sheet snapshot; Slack July 1–October 1; API December 1–September 29',
              'api_ads': len(ads), 'slack_announcements': len(announcements), 'slack_issues': len(issues),
              'rows': rows}
    output = root / 'tiktok_auxiliary_matching'
    output.mkdir(exist_ok=True)
    (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    summary = {'rows': len(rows), 'slack_code_linked': sum(bool(r['slack_code_matches']) for r in rows),
               'slack_same_post': sum(bool(r['slack_post_matches']) for r in rows),
               'candidate_counts': dict(Counter(len(r['candidates']) for r in rows)),
               'exact_post_id_in_ad_text_or_name': sum(c['post_id_in_name_or_text'] for r in rows for c in r['candidates']),
               'name_date_equals_launch': sum(any(c['launch_date_equal'] for c in r['candidates']) for r in rows),
               'name_date_equals_slack': sum(any(c['slack_announcement_date_equal'] for c in r['candidates']) for r in rows)}
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
