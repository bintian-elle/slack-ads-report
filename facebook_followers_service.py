"""Independent Facebook Page follower reads; never replace the Ads token."""

from datetime import datetime, timedelta
from urllib.parse import quote
import requests

from google_sheets_service import select_budget_pacing_tab, locate_actual_pacing_row, _quoted_range


def fetch_followers(token, report_date):
    base = 'https://graph.facebook.com/v25.0/'
    def read(path, params, access_token):
        response = requests.get(base + path, params=dict(params, access_token=access_token), timeout=30)
        payload = response.json()
        if not response.ok or 'error' in payload:
            # Do not log responses or paging URLs containing credentials.
            raise RuntimeError('Facebook followers API request failed')
        return payload

    pages = read('me/accounts', {'fields': 'id,access_token', 'limit': 100}, token)
    page_token = next(p['access_token'] for p in pages.get('data', []) if p['id'] == '680910961767969')
    current = read('680910961767969', {'fields': 'followers_count'}, page_token)['followers_count']
    payload = read('680910961767969/insights', {
        'metric': 'page_daily_follows_unique', 'period': 'day',
        'since': str(report_date.replace(day=1)), 'until': str(report_date + timedelta(days=1)),
    }, page_token)
    daily = {datetime.strptime(v['end_time'], '%Y-%m-%dT%H:%M:%S%z').date() - timedelta(days=1): v['value']
             for metric in payload.get('data', []) for v in metric.get('values', [])}
    expected = [report_date.replace(day=d) for d in range(1, report_date.day + 1)]
    if any(d not in daily for d in expected):
        raise RuntimeError('Facebook followers data incomplete; existing Sheet values retained')
    return current, {d: daily[d] for d in expected}


def sync_followers(sheets, token, report_date):
    current, daily = fetch_followers(token, report_date)
    tab = select_budget_pacing_tab(sheets.list_tab_titles(), report_date)
    rows = sheets._read_tab_rows(tab)
    labels = [(r + 1, c) for r, row in enumerate(rows) for c, value in enumerate(row)
              if str(value).strip().lower() == 'current facebook followers']
    if len(labels) != 1:
        raise ValueError('Expected one Current Facebook Followers label')
    actual_header = locate_actual_pacing_row(rows, report_date.replace(day=1)) - 2
    if len(rows[actual_header]) <= 23 or str(rows[actual_header][23]).strip().lower() not in ('follower', 'followers', 'fb followers'):
        raise ValueError('Unexpected X column header; no follower writes made')
    row, col = labels[0]
    # Existing summary layout: adjacent value follows label, e.g. AJ:AK.
    def letter(index):
        result = ''
        while index:
            index, rem = divmod(index - 1, 26)
            result = chr(65 + rem) + result
        return result
    data = [{'range': _quoted_range(tab, f'X{locate_actual_pacing_row(rows, d)}'), 'values': [[value]]}
            for d, value in daily.items()]
    data.append({'range': _quoted_range(tab, f'{letter(col + 2)}{row}'), 'values': [[current]]})
    response = sheets.session.post(sheets.base_url + '/values:batchUpdate',
                                   json={'valueInputOption': 'RAW', 'data': data}, timeout=30)
    sheets._json_response(response, 'followers write')
    for item in data:
        response = sheets.session.get(sheets.base_url + '/values/' + quote(item['range'], safe=''),
                                      params={'valueRenderOption': 'UNFORMATTED_VALUE'}, timeout=30)
        if sheets._json_response(response, 'followers verification').get('values') != item['values']:
            raise RuntimeError('Facebook follower write verification failed')
    return current, sum(daily.values())
