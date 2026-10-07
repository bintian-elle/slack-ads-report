"""Polite public-page counters for Organic TikTok; never paid Ads metrics."""
import json
import math
import random
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from get_tiktok_public_data import extract_video_id, get_tiktok_metrics, TikTokBlocked

FIELDS = ('views', 'likes', 'comments', 'saves', 'shares')


def metric_changes(metrics, fee):
    if not all(isinstance(metrics.get(k), int) and not isinstance(metrics[k], bool)
               and metrics[k] >= 0 for k in FIELDS):
        raise ValueError('Missing or invalid public video counters')
    interactions = sum(metrics[k] for k in FIELDS[1:])
    result = {4: metrics['views'], 5: interactions,
              6: metrics['likes'], 7: metrics['comments'],
              8: metrics['saves'], 9: metrics['shares']}
    if isinstance(fee, (int, float)) and not isinstance(fee, bool) and math.isfinite(fee) and fee >= 0:
        if metrics['views']:
            result[10] = fee / metrics['views'] * 1000
        if interactions:
            result[11] = fee / interactions
    return result


def backoff_until(now, retry_after):
    until = now + timedelta(hours=24)
    try:
        requested = now + timedelta(seconds=int(retry_after))
    except (ValueError, TypeError):
        try:
            requested = parsedate_to_datetime(retry_after)
            if requested.tzinfo is None:
                requested = requested.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, AttributeError):
            return until.isoformat()
    return max(until, requested).isoformat()


def run(env, output, apply):
    from kol_tracker import BASE, save_json, sheet_session, read_tab, verify_literal_target
    from update_meta_tracker import write_range
    scoped = dict(env, KOL_TRACKER_GOOGLE_SHEETS_LINK=env['KOL_ORGANIC_SHEETS_LINK'])
    session, endpoint = sheet_session(scoped)
    prop, native, values = read_tab(session, endpoint, 'TikTok', validate_tracker=False)
    headers = [str(v).splitlines()[0].strip() for v in values[0]]
    expected = ['Creator', 'Organic Launch Date', 'Post Link', 'KOL Fee', 'Views',
                'Interaction', 'Likes', 'Comments', 'Saves', 'Shares', 'CPM', 'CPE']
    if headers[:12] != expected:
        raise RuntimeError('Organic TikTok schema changed')
    path = Path(env.get('KOL_TRACKER_STATE_DIR', str(BASE / 'data/processed/kol_tracker_runtime'))) / 'organic-tiktok-state.json'
    state = json.loads(path.read_text()) if path.exists() else {'videos': {}}
    # Malformed state must fail closed, not cause duplicate daily requests.
    if not isinstance(state.get('videos'), dict):
        raise RuntimeError('Invalid TikTok request state')
    now = datetime.now(timezone.utc)
    if state.get('blocked_until') and now < datetime.fromisoformat(state['blocked_until']):
        print('Organic TikTok backoff active; no public requests or writes', flush=True)
        return
    today = now.astimezone(ZoneInfo('America/Chicago')).date().isoformat()
    results, skipped, attempted, blocked = {}, [], 0, False
    for number, row in enumerate(values[1:], 2):
        if not row or not row[0] or str(row[0]).strip().lower() == 'summary':
            continue
        try:
            vid = extract_video_id(row[2] if len(row) > 2 else '')
        except ValueError:
            skipped.append({'row': number, 'reason': 'Missing or unsupported TikTok link'})
            continue
        if vid in results:
            continue
        entry = state['videos'].get(vid, {})
        if entry.get('day') == today:
            if entry.get('metrics'):
                results[vid] = entry['metrics']
            continue
        if attempted:
            time.sleep(random.uniform(3, 7))
        # Persist before HTTP so a crash cannot cause a second attempt today.
        state['videos'][vid] = {'day': today}
        save_json(path, state)
        attempted += 1
        try:
            metrics = get_tiktok_metrics(row[2])
            if str(metrics.get('video_id')) != vid:
                raise ValueError('Returned video ID mismatch')
            metric_changes(metrics, None)
            results[vid] = metrics
            state['videos'][vid]['metrics'] = metrics
        except TikTokBlocked as error:
            state['blocked_until'] = backoff_until(datetime.now(timezone.utc), error.retry_after)
            blocked = True
            print('Organic TikTok HTTP %s; stopped with persistent backoff' % error.status, flush=True)
        except Exception as error:
            skipped.append({'row': number, 'reason': type(error).__name__})
        save_json(path, state)
        if blocked:
            break
    planned = []
    for number, row in enumerate(values[1:], 2):
        if not row or not row[0]:
            continue
        try:
            vid = extract_video_id(row[2] if len(row) > 2 else '')
        except ValueError:
            continue
        if vid in results:
            planned.append({'row': number, 'changes': metric_changes(results[vid], row[3] if len(row)>3 else None)})
    save_json(output / 'Organic-TikTok-plan.json', {'planned': planned, 'skipped': skipped,
        'requests': attempted, 'blocked': blocked, 'basis': 'Public TikTok lifetime counters'})
    print('Organic TikTok: %d requests; %d planned rows' % (attempted, len(planned)), flush=True)
    if blocked:
        raise RuntimeError('TikTok access restricted; no sheet write, backoff saved')
    if not apply or not planned:
        return
    cells = native['sheets'][0]['data'][0]['rowData']
    expected_native = json.loads(json.dumps(native))
    expected_rows = expected_native['sheets'][0]['data'][0]['rowData']
    body = []
    def add(number, col, value):
        row = cells[number-1].get('values', [])
        target = row[col] if len(row)>col else {}
        if col in (10,11) and 'formulaValue' in target.get('userEnteredValue', {}):
            return
        verify_literal_target(target, value)
        request = write_range(prop['sheetId'], number, col, [value])
        body.append(request)
        dest = expected_rows[number-1].setdefault('values', [])
        dest.extend({} for _ in range(max(0,col+1-len(dest))))
        dest[col]['userEnteredValue'] = request['updateCells']['rows'][0]['values'][0]['userEnteredValue']
    for item in planned:
        for col, value in item['changes'].items():
            add(item['row'], col, value)
    add(1,4,'Views\n[%s update]' % datetime.now(ZoneInfo('America/Chicago')).strftime('%m/%d'))
    save_json(output / 'Organic-TikTok-before.json', native)
    if read_tab(session,endpoint,'TikTok',validate_tracker=False)[1] != native:
        raise RuntimeError('Organic TikTok sheet changed; no write')
    session.post(endpoint + ':batchUpdate',json={'requests':body},timeout=90).raise_for_status()
    after = read_tab(session,endpoint,'TikTok',validate_tracker=False)[1]
    save_json(output / 'Organic-TikTok-after.json',after)
    for obj in (expected_native,after):
        for row in obj['sheets'][0]['data'][0]['rowData']:
            for cell in row.get('values',[]):
                cell.pop('effectiveValue',None)
            while row.get('values') and not row['values'][-1]:
                row['values'].pop()
    if expected_native != after:
        raise RuntimeError('Organic TikTok native readback failed')
    print('Organic TikTok metrics and Views update date verified',flush=True)
