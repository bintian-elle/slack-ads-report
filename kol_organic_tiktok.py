"""Polite public-page counters for Organic TikTok; never paid Ads metrics."""
import json
import math
import random
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from get_tiktok_public_data import extract_video_id, get_tiktok_metrics, normalize_counter, TikTokBlocked

FIELDS = ('views', 'likes', 'comments', 'saves', 'shares')
NOTE_HEADER = 'Reason for data update failure'
NOTE_PREFIXES = ('Missing or unsupported TikTok link', 'Invalid or missing video counters',
    'Video data unavailable', 'HTTP request failed', 'Request timed out',
    'Request already attempted today', 'TikTok HTTP ', 'Skipped due to TikTok backoff')


def failure_reason(error):
    if isinstance(error, ValueError):
        return 'Invalid or missing video counters; existing metrics retained'
    import requests
    if isinstance(error, requests.Timeout):
        return 'Request timed out; existing metrics retained'
    if isinstance(error, requests.RequestException):
        return 'HTTP request failed; existing metrics retained'
    return 'Video data unavailable; existing metrics retained'


def note_plan(values, successes, reasons):
    """Append after all populated columns; preserve non-managed manual notes."""
    header = values[0]
    if NOTE_HEADER in header:
        col = header.index(NOTE_HEADER)
    else:
        col = max((i for row in values for i, value in enumerate(row)
                   if value != ''), default=11) + 1
    updates = [{'row': 1, 'changes': {col: NOTE_HEADER}}] if col >= len(header) or header[col] != NOTE_HEADER else []
    for number, row in enumerate(values[1:],2):
        if not row or not row[0] or str(row[0]).strip().lower() == 'summary':
            continue
        old = str(row[col]) if len(row)>col else ''
        if old and not old.startswith(NOTE_PREFIXES):
            continue
        value = '' if number in successes else reasons.get(number, '')
        if number not in successes and number not in reasons:
            continue
        if value != old:
            updates.append({'row':number,'changes':{col:value}})
    return col, updates


def metric_changes(metrics, fee):
    metrics = {k: normalize_counter(metrics.get(k)) for k in FIELDS}
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
    headers = [str(v).split('\n', 1)[0].strip() for v in values[0]]
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
    backoff_active = bool(state.get('blocked_until') and now < datetime.fromisoformat(state['blocked_until']))
    today = now.astimezone(ZoneInfo('America/Chicago')).date().isoformat()
    results, skipped, attempted, blocked = {}, [], 0, backoff_active
    seen = set()
    blocked_reason = 'Skipped due to TikTok backoff; existing metrics retained'
    for number, row in enumerate(values[1:], 2):
        if not row or not row[0] or str(row[0]).strip().lower() == 'summary':
            continue
        try:
            vid = extract_video_id(row[2] if len(row) > 2 else '')
        except ValueError:
            skipped.append({'row': number, 'reason': 'Missing or unsupported TikTok link'})
            continue
        if vid in seen:
            continue
        seen.add(vid)
        if blocked:
            break
        entry = state['videos'].get(vid, {})
        if entry.get('day') == today and entry.get('metrics'):
            results[vid] = entry['metrics']
            continue
        if attempted:
            time.sleep(random.uniform(3, 7))
        # Keep diagnostics; unsuccessful attempts may retry on a later run.
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
            blocked_reason = 'TikTok HTTP %s; batch stopped, backoff active' % error.status
            state['videos'][vid]['reason'] = blocked_reason
            print('Organic TikTok HTTP %s; stopped with persistent backoff' % error.status, flush=True)
        except Exception as error:
            reason = failure_reason(error)
            state['videos'][vid]['reason'] = reason
            skipped.append({'row': number, 'reason': reason})
            print('Organic TikTok row %d skipped: %s' % (number,reason),flush=True)
        save_json(path, state)
        if blocked:
            break
    planned, reasons = [], {}
    for number, row in enumerate(values[1:], 2):
        if not row or not row[0] or str(row[0]).strip().lower() == 'summary':
            continue
        try:
            vid = extract_video_id(row[2] if len(row) > 2 else '')
        except ValueError:
            reasons[number] = 'Missing or unsupported TikTok link; existing metrics retained'
            continue
        if vid in results:
            try:
                planned.append({'row': number, 'changes': metric_changes(results[vid], row[3] if len(row)>3 else None)})
            except ValueError as error:
                reasons[number] = failure_reason(error)
        else:
            reasons[number] = (blocked_reason if blocked else
                state['videos'].get(vid,{}).get('reason',
                    'Video data unavailable; existing metrics retained'))
    note_col, notes = note_plan(values,{item['row'] for item in planned},reasons)
    if note_col >= prop['gridProperties']['columnCount']:
        raise RuntimeError('No unused column for failure reasons; no sheet write')
    save_json(output / 'Organic-TikTok-plan.json', {'planned': planned, 'skipped': skipped,
        'requests': attempted, 'blocked': blocked, 'note_updates':notes, 'basis': 'Public TikTok lifetime counters'})
    print('Organic TikTok: %d requests; %d planned rows' % (attempted, len(planned)), flush=True)
    if not apply:
        if blocked and not backoff_active:
            raise RuntimeError('TikTok access restricted; backoff saved')
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
        entered = request['updateCells']['rows'][0]['values'][0].get('userEnteredValue')
        if entered:
            dest[col]['userEnteredValue'] = entered
        else:
            dest[col].pop('userEnteredValue',None)
    for item in planned + notes:
        for col, value in item['changes'].items():
            add(item['row'], col, value)
    if planned:
        add(1,4,'Views\n[%s update]' % datetime.now(ZoneInfo('America/Chicago')).strftime('%m/%d'))
    if not body:
        return
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
    if blocked and not backoff_active:
        raise RuntimeError('TikTok access restricted; successful rows/reasons written, backoff saved')
