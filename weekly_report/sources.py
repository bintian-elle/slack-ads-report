"""Official, read-only advertising API collectors. No database or app imports."""
import base64
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
try:
    from . import testing_scope as scope
except ImportError:
    import testing_scope as scope


def request(url, *, body=None, headers=None, form=False):
    data = None if body is None else (urlencode(body).encode() if form else json.dumps(body).encode())
    h = dict(headers or {})
    if data is not None:
        h['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    try:
        with urlopen(Request(url, data=data, headers=h), timeout=90) as response:
            return json.load(response)
    except HTTPError as exc:
        # URLs may contain access tokens; never log them or the raw response.
        raise RuntimeError('API HTTP %s from %s' % (exc.code, urlparse(url).hostname)) from None


def safe_next(url, hostname):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != hostname or parsed.username or parsed.password:
        raise ValueError('Unexpected pagination origin')
    return url


def pages(url, headers=None, body=None, *, kind='meta'):
    hostname = urlparse(url).hostname
    rows, seen = [], set()
    for _ in range(1000):
        if url in seen:
            raise RuntimeError('API pagination loop')
        seen.add(url)
        payload = request(url, headers=headers, body=body)
        if kind == 'meta':
            if not isinstance(payload.get('data'), list):
                raise RuntimeError('Meta data missing')
            rows.extend(payload['data'])
            nxt = payload.get('paging', {}).get('next')
        else:
            data = payload.get('data')
            batch = data.get('metrics') if isinstance(data, dict) else data
            if not isinstance(batch, list):
                raise RuntimeError('Reddit data missing')
            rows.extend(batch)
            nxt = payload.get('pagination', {}).get('next_url')
        if not nxt:
            return rows
        url = safe_next(nxt, hostname)
    raise RuntimeError('Too many API pages')


def collect(env, start, end):
    oauth = request('https://oauth2.googleapis.com/token', form=True, body={
        'client_id': env['GOOGLE_ADS_CLIENT_ID'], 'client_secret': env['GOOGLE_ADS_SECRET'],
        'refresh_token': env['GOOGLE_ADS_TOKEN'], 'grant_type': 'refresh_token'})
    gh = {'Authorization': 'Bearer ' + oauth['access_token']}
    if env.get('GOOGLE_ADS_DEVELOPER_TOKEN'):
        gh['developer-token'] = env['GOOGLE_ADS_DEVELOPER_TOKEN']
    if env.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID'):
        gh['login-customer-id'] = env['GOOGLE_ADS_LOGIN_CUSTOMER_ID'].replace('-', '')
    customer = env['GOOGLE_ADS_CUSTOMER_ID'].replace('-', '')
    if not customer.isdigit():
        raise ValueError('Invalid Google customer ID')
    def google(query):
        batches = request('https://googleads.googleapis.com/v24/customers/' + customer + '/googleAds:searchStream',
                          body={'query': query}, headers=gh)
        if not isinstance(batches, list):
            raise RuntimeError('Invalid Google response')
        return [row for batch in batches for row in batch.get('results', [])]
    period = " WHERE segments.date BETWEEN '%s' AND '%s'" % (start, end)
    fields = 'metrics.cost_micros,metrics.conversions,metrics.conversions_value,metrics.impressions,metrics.clicks'
    labels = google("SELECT label.resource_name,label.name FROM label WHERE label.name='"+scope.SEARCH_LABEL+"'")
    if len(labels)!=1:raise RuntimeError('Content Testing Search label missing or ambiguous; no broad fallback')
    label=labels[0]['label']['resourceName']
    in_names=lambda names:','.join("'"+name+"'" for name in names)
    ad_fields='campaign.id,campaign.name,campaign.advertising_channel_type,ad_group.id,ad_group.name,ad_group_ad.ad.id,ad_group_ad.ad.name,ad_group_ad.ad.responsive_search_ad.headlines,'
    date_filter=" AND segments.date BETWEEN '%s' AND '%s'" % (start,end)
    search=scope.google_rows('SEARCH',google('SELECT '+ad_fields+'ad_group_ad.labels,'+fields+' FROM ad_group_ad WHERE campaign.name IN ('+in_names(scope.SEARCH_CAMPAIGNS)+") AND ad_group_ad.labels CONTAINS ANY ('"+label+"')"+date_filter),label)
    dg=scope.google_rows('DEMAND_GEN',google('SELECT '+ad_fields+fields+" FROM ad_group_ad WHERE campaign.name='"+scope.DG_CAMPAIGN+"' AND ad_group.id="+scope.DG_GROUP+" AND ad_group_ad.ad.name LIKE '%content testing%'"+date_filter))
    pmax=scope.google_rows('PERFORMANCE_MAX',google('SELECT campaign.id,campaign.name,asset_group.id,asset_group.name,'+fields+' FROM asset_group WHERE campaign.name IN ('+in_names(scope.PMAX_CAMPAIGNS)+") AND asset_group.name LIKE '%Content Testing%'"+date_filter))
    if not search or not dg or not pmax:raise RuntimeError('Content Testing Google inventory missing; no whole-channel fallback')
    totals=scope.google_totals(search+dg+pmax)
    purchase_audit=[row for query in scope.purchase_queries(search+dg,pmax,date_filter) for row in google(query)]
    for row in purchase_audit:
        if row['segments']['conversionActionCategory']!='PURCHASE' and float(row.get('metrics',{}).get('conversions',0)):
            raise RuntimeError('Google primary conversions include non-purchase actions; cannot label them Orders')
    g={'account':google('SELECT customer.id,customer.currency_code,customer.time_zone FROM customer'),
       'campaigns':totals,'ads':search+dg,'pmax':pmax,
       'purchase':[{'campaign':row['campaign'],'segments':{'conversionActionName':'Scoped testing entities; purchase-category primary actions verified','conversionActionCategory':'PURCHASE'},'metrics':{k:row['metrics'][k] for k in ['conversions','conversionsValue']}} for row in totals],
       'reach':{'unavailable':True,'reason':'No exact deduplicated reach for selected test ads/asset groups; never substitute parent campaign reach'}}
    token = env['META_ACCESS_TOKEN']
    account = env.get('META_AD_ACCOUNT_ID', '1010265054378515').removeprefix('act_')
    def meta(path, params):
        return 'https://graph.facebook.com/v25.0/' + path + '?' + urlencode({**params, 'access_token': token})
    common = {'time_range': json.dumps({'since': start, 'until': end}), 'limit': '500'}
    mf = 'spend,impressions,reach,clicks,inline_link_clicks,actions,action_values'
    m = {'metadata': request(meta('act_' + account, {'fields': 'currency,timezone_name'}))}
    meta_campaign=request(meta(scope.META_CAMPAIGN,{'fields':'id,name,account_id'}))
    if meta_campaign['account_id']!=account:raise RuntimeError('Content Testing Meta account mismatch')
    for level in ('account', 'ad'):
        fields_m = mf if level == 'account' else 'ad_id,ad_name,campaign_id,campaign_name,' + mf
        m[level] = pages(meta(scope.META_CAMPAIGN + '/insights', {**common, 'level': 'campaign' if level=='account' else 'ad', 'fields': fields_m}))
    c = json.loads(Path(env['REDDIT_CREDENTIALS_FILE']).read_text())
    auth = base64.b64encode((c['CLIENT_ID'] + ':' + c['CLIENT_SECRET']).encode()).decode()
    rt = request('https://www.reddit.com/api/v1/access_token', body={'grant_type': 'refresh_token', 'refresh_token': c['refresh_token']},
                 headers={'Authorization': 'Basic ' + auth, 'User-Agent': 'bluevua-weekly/1.0'}, form=True)
    rh = {'Authorization': 'Bearer ' + rt['access_token'], 'User-Agent': 'bluevua-weekly/1.0'}
    rb = 'https://ads-api.reddit.com/api/v3/ad_accounts/' + c['Ad_Account_ID']
    r = {'metadata': request(rb, headers=rh)}
    inventory=pages(rb+'/ads',headers=rh,kind='reddit')
    groups=pages(rb+'/ad_groups',headers=rh,kind='reddit')
    r['names']=scope.reddit_ads(inventory,groups)
    if not r['names']:raise RuntimeError('Content Testing Reddit inventory missing; no account fallback')
    allowed={str(ad['id']) for ad in r['names']}
    report=pages(rb+'/reports',headers=rh,kind='reddit',body={'data':{
        'starts_at':start+'T00:00:00Z','ends_at':(date.fromisoformat(end)+timedelta(days=1)).isoformat()+'T00:00:00Z',
        'breakdowns':['AD_ID'],'fields':['IMPRESSIONS','CLICKS','SPEND','REACH','CONVERSION_PURCHASE_CLICKS','CONVERSION_PURCHASE_VIEWS','CONVERSION_PURCHASE_TOTAL_VALUE']}})
    r['ads']=[row for row in report if str(row.get('ad_id')) in allowed]
    by_id={str(ad['id']):ad for ad in r['names']}
    for row in r['ads']:row['campaign_id']=by_id[str(row['ad_id'])]['campaign_id']
    r['account']=[scope.reddit_totals(r['ads'])]
    r['campaigns']=[{**scope.reddit_totals([row for row in r['ads'] if row['campaign_id']==cid]),'campaign_id':cid} for cid in scope.REDDIT if any(row['campaign_id']==cid for row in r['ads'])]
    if not m['account'] or not r['account']:
        raise RuntimeError('Missing account report: unavailable is not zero')
    return {'source': 'Official APIs only; no databases', 'period': {'start': start, 'end': end},
            'scope_revision':scope.REVISION,
            'scope':'Content Testing ONLY: scoped Search label, PMax testing asset groups, DG testing ad group/ads, dedicated Meta test campaign, and Reddit allowlisted test ads. Exclude every non-test campaign/ad.',
            'scope_evidence':{'search_label':scope.SEARCH_LABEL,'search_label_resource':label,'meta_campaign':meta_campaign,'reddit_ad_ids':sorted(allowed),'reddit_reach':'Unavailable for exact test subset; ad reach must not be summed','purchase_categories':[row['segments']['conversionActionCategory'] for row in purchase_audit]},
            'metric_definitions': {'google_spend': 'costMicros / 1000000', 'reddit_spend': 'spend / 1000000',
                'reddit_revenue': 'conversion_purchase_total_value / 100', 'reddit_orders': 'click orders + view orders',
                'meta_orders_revenue': 'omni_purchase', 'ctr': 'total clicks / total impressions; Meta all clicks',
                'reach': 'period deduplicated where returned; never add campaign/platform reach',
                'attribution': 'Platform attributed; cannot sum revenue/orders as actual business totals',
                'timezones': 'Google/Meta account metadata; Reddit requested in UTC'},
            'google': g, 'meta': m, 'reddit': r}
