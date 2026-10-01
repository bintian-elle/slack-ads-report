"""Independent read-only ad/creative identity diagnostic; no IG account calls."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from sync_creator_tracker import BASE, METRICS, aggregate, code_key, graph_pages, get_json, post_key, read_sheet


def main():
    load_dotenv(BASE / '.env')
    env = dict(os.environ)
    sheet = read_sheet(env)
    targets = [(i + 1, r) for i, r in enumerate(sheet['values'])
               if i > 1 and len(r) > 7 and 'madison' in str(r[1]).lower()]
    if len(targets) != 1:
        raise RuntimeError('Expected exactly one Madison Tracker row')
    number, row = targets[0]
    launch = ((datetime(1899, 12, 30) + timedelta(days=row[2])).date().isoformat()
              if isinstance(row[2], (int, float)) else str(row[2]))
    target = {'row': number, 'creator': row[1], 'launch_date': launch,
              'post_link': row[6], 'ad_code': code_key(row[7]), 'values': row,
              'headers': sheet['values'][0]}
    s = requests.Session()
    s.headers['Authorization'] = 'Bearer ' + env['META_ACCESS_TOKEN']
    base = 'https://graph.facebook.com/' + env.get('KOL_TRACKER_META_API_VERSION', 'v23.0')
    account = 'act_' + env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    info = get_json(s, base + '/' + account, {'fields': 'id,name,currency,timezone_name'})
    end = (datetime.now(ZoneInfo(info['timezone_name'])).date() - timedelta(days=1)).isoformat()
    ads = graph_pages(s, base + '/' + account + '/ads', {
        'fields': 'id,name,created_time,effective_status,campaign{id,name},adset{id,name},creative{id}', 'limit': 100})
    recent = [a for a in ads if a.get('created_time', '')[:10] >= '2026-09-01']
    partnership = [a for a in recent if 'partnership' in a['name'].lower()]
    fields = ['instagram_permalink_url', 'source_instagram_media_id', 'effective_instagram_media_id',
              'instagram_user_id', 'instagram_branded_content', 'facebook_branded_content',
              'branded_content', 'branded_content_sponsor_page_id', 'object_story_spec',
              'object_story_id', 'effective_object_story_id', 'source_facebook_post_id', 'body', 'name']
    creatives, errors = {}, []
    for a in partnership:
        cid = a.get('creative', {}).get('id')
        if not cid or cid in creatives:
            continue
        try:
            creatives[cid] = get_json(s, base + '/' + cid, {'fields': 'id,' + ','.join(fields)})
        except RuntimeError:
            creatives[cid] = {'id': cid}
            for field in fields:
                try:
                    creatives[cid].update(get_json(s, base + '/' + cid, {'fields': 'id,' + field}))
                except RuntimeError as exc:
                    errors.append({'creative_id': cid, 'field': field, 'error': str(exc)})
    key = post_key(row[6])
    candidates = []
    for a in partnership:
        c = creatives.get(a.get('creative', {}).get('id'), {})
        serialized = json.dumps(c)
        exact = key and key in serialized
        named = 'madison' in a['name'].lower()
        coded = target['ad_code'] and target['ad_code'] in serialized
        if exact or named or coded:
            candidates.append({'ad': a, 'creative': c, 'post_shortcode_exact': bool(exact),
                               'ad_code_exact': bool(coded), 'ad_name_candidate': named})
    insight_fields = ('ad_id,ad_name,spend,actions,action_values,purchase_roas,website_purchase_roas,'
                      'reach,impressions,clicks,inline_link_clicks,video_play_actions,video_30_sec_watched_actions,'
                      'video_thruplay_watched_actions,video_p25_watched_actions,video_p50_watched_actions,'
                      'video_p75_watched_actions,video_p95_watched_actions,video_p100_watched_actions,'
                      'video_avg_time_watched_actions,date_start,date_stop')
    for candidate in candidates:
        aid = candidate['ad']['id']
        candidate['insights'] = {}
        for cutoff in sorted({'2026-09-28', end}):
            candidate['insights'][cutoff] = graph_pages(s, base + '/' + aid + '/insights', {
                'fields': insight_fields, 'time_range': json.dumps({'since': '2025-01-01', 'until': cutoff}),
                'use_unified_attribution_setting': 'true', 'limit': 100})
    comparisons = {}
    for cutoff in sorted({'2026-09-28', end}):
        values = [r for c in candidates if c['ad_code_exact'] or c['post_shortcode_exact']
                  for r in c['insights'][cutoff]]
        metrics = aggregate(values) if values else {}
        comparisons[cutoff] = {'candidate_totals': metrics, 'cells': [
            {'metric': metric, 'sheet': row[9 + i] if len(row) > 9 + i else '', 'api': metrics.get(metric)}
            for i, metric in enumerate(METRICS)],
            'reach_note': 'Per-ad reach is available in raw responses; do not sum unique reach across ads.'}
    report = {'target': target, 'account': info, 'current_cutoff': end,
              'inventory_ads': len(ads), 'recent_ads': len(recent),
              'recent_partnership_named_ads': len(partnership), 'recent_since': '2026-09-01',
              'partnership_detection': 'Ad Name contains partnership; naming is not a complete API classification',
              'ads': partnership, 'creatives': creatives, 'field_errors': errors,
              'candidates': candidates, 'comparison': comparisons,
              'identity_note': 'Ad Name match and numerical equality alone are not proof of source-post identity.'}
    output = BASE / 'data/processed/kol_tracker_audit/madison_ad_identity.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    safe = {'target': {k: v for k, v in target.items() if k not in ['ad_code', 'values', 'headers']},
            'candidates': [{'ad': c['ad'], 'post_shortcode_exact': c['post_shortcode_exact'],
                            'ad_code_exact': c['ad_code_exact']} for c in candidates],
            'comparison': comparisons, 'field_errors': errors}
    print(json.dumps(safe, ensure_ascii=False, indent=2), flush=True)
    print('LOCAL_REPORT', output)


if __name__ == '__main__':
    main()
