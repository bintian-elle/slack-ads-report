#!/usr/bin/env python3
"""Independent Biceek daily pipeline. No imports from the existing Slack bot."""
import argparse
import calendar
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SHEET_ID = '10LqUF6xwo9Ldx82VPUfEu7wMaoXVDY8UCY657EuMi-Q'
CHANNEL = 'C0BFLLEMP2M'
RECIPIENT = 'U0BN7GWSX0S'
BOT_USER = 'U0BNDKK342Y'
PURCHASE = 'offsite_conversion.fb_pixel_purchase'
FEED_ONLY_CAMPAIGN = '24435460967'
FEED_ONLY_START = date(2026,11,5)
GOOGLE_GROUPS = ('pmax','feed_only','search')
EASTERN = ZoneInfo('America/New_York')


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def atomic(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def report_day(now=None):
    return (now or datetime.now(EASTERN)).astimezone(EASTERN).date() - timedelta(days=1)


def number(value):
    if value is None or value == '': return None
    n = Decimal(str(value))
    if not n.is_finite(): raise ValueError('Nonfinite metric')
    return n


def ratio(value, spend):
    return value / spend if value is not None and spend is not None and spend > 0 else None


def total(values):
    present = [v for v in values if v is not None]
    return sum(present, Decimal(0)) if present else None


def google_group(campaign):
    kind=campaign['advertisingChannelType']
    if kind=='PERFORMANCE_MAX':
        return 'feed_only' if str(campaign.get('id'))==FEED_ONLY_CAMPAIGN else 'pmax'
    if kind=='SEARCH':return 'search'
    return None


def normalize(day, google, meta, shop):
    result = []
    for n in range(1, day.day + 1):
        current = day.replace(day=n).isoformat()
        row = {'date': current}
        grows = [r for r in google if r['segments']['date'] == current]
        for key in GOOGLE_GROUPS:
            selected = [r for r in grows if google_group(r['campaign']) == key]
            row[key+'_spend'] = total([number(r['metrics']['costMicros']) / 1000000 for r in selected])
            vals = [number(r['metrics'].get('conversionsValue')) for r in selected]
            row[key+'_value'] = total(vals) if selected and all(v is not None for v in vals) else None
        if any(r['campaign']['advertisingChannelType'] not in {'SEARCH','PERFORMANCE_MAX'} and number(r['metrics']['costMicros']) != 0 for r in grows):
            raise ValueError('Unmapped Google channel: review sheet layout')
        row['google_spend'] = total([row[k+'_spend'] for k in GOOGLE_GROUPS])
        row['google_value'] = (total([row[k+'_value'] for k in GOOGLE_GROUPS])
             if all(row[k+'_value'] is not None for k in GOOGLE_GROUPS if row[k+'_spend'] is not None) else None)
        clicks=[number(r['metrics'].get('clicks')) for r in grows]
        row['google_clicks']=total(clicks) if clicks and all(v is not None for v in clicks) else None
        matches = [r for r in meta if r['date_start'] == current]
        if len(matches) > 1: raise ValueError('Duplicate Meta account day')
        m = matches[0] if matches else {}
        row['meta_spend'] = number(m.get('spend'))
        row['meta_clicks'] = number(m.get('clicks'))
        row['meta_value'] = next((number(a['value']) for a in m.get('action_values', []) if a['action_type'] == PURCHASE), None)
        sales = [r for r in shop if r['day'] == current]
        if len(sales) > 1: raise ValueError('Duplicate Shopify day')
        row['revenue'] = number(sales[0]['total_sales']) if sales else None
        row['orders'] = number(sales[0].get('orders')) if sales else None
        row['spend'] = (row['google_spend'] + row['meta_spend']
                        if row['google_spend'] is not None and row['meta_spend'] is not None else None)
        result.append(row)
    return result


def business_rows(result, allowed_root):
    expected, seen = [], set()
    node = result
    for _ in range(8):
        if 'count' in node: expected.append(node['count'])
        if node.get('export_file'):
            p = Path(node['export_file']).resolve(strict=True)
            if allowed_root.resolve() not in p.parents or p in seen or p.stat().st_size > 64*1024*1024:
                raise ValueError('Untrusted or excessive export')
            seen.add(p)
            if p.suffix == '.json':
                node = json.loads(p.read_text()); continue
            if p.suffix != '.jsonl': raise ValueError('Unexpected export format')
            lines = p.read_text().splitlines()
            if set(json.loads(lines[0])) != {'metadata'}: raise ValueError('Missing export metadata')
            rows = [json.loads(x) for x in lines[1:] if x.strip()]
        else:
            rows = node.get('data')
        if not isinstance(rows, list) or not expected or len(rows) > 1000 or any(len(rows) != n for n in expected):
            raise ValueError('Export count mismatch')
        return rows
    raise ValueError('Nested export limit')


def cli(config, platform, args, body):
    cmd = [str(Path(config['anc_root'])/'.venv/bin'/('anc-'+platform)), *args,
           '--account', platform+'.biceek', '--input-file', '-']
    p = subprocess.run(cmd, input=json.dumps(body), capture_output=True, text=True, timeout=150)
    try: d = json.loads(p.stdout)
    except (ValueError, TypeError): raise RuntimeError('CLI invalid output') from None
    if p.returncode or d.get('status') not in {'ok','empty'} or not d.get('identity_verified'):
        raise RuntimeError('CLI failed: '+str(d.get('status','unknown'))) from None
    if d.get('currency') not in {'USD'}: raise ValueError('Unexpected source currency')
    if d.get('coverage', {}).get('complete') is not True: raise ValueError('Incomplete API coverage')
    expected_zone = 'America/New_York' if platform == 'meta-ads' else 'America/Los_Angeles'
    if d.get('timezone') != expected_zone: raise ValueError('Source timezone changed')
    return d


def audit_google_purchases(grows,prows):
    purchase_values = {}
    for p in prows:
        key = (p['segments']['date'],p['campaign']['id'])
        purchase_values[key] = purchase_values.get(key,Decimal(0)) + number(p['metrics']['conversionsValue'])
    for r in grows:
        key = (r['segments']['date'],r['campaign']['id'])
        r['metrics']['conversionsValue'] = purchase_values.get(key, 0 if number(r['metrics'].get('conversionsValue')) == 0 else None)


def collect(config, day):
    period = {'start': day.replace(day=1).isoformat(), 'end': (day+timedelta(days=1)).isoformat()}
    g = cli(config, 'google-ads', ['reports','query'], {'resource':'campaign','period':period,
        'fields':['segments.date','campaign.id','campaign.advertising_channel_type','metrics.cost_micros','metrics.conversions_value','metrics.clicks'], 'max_rows':1000})
    purchases = cli(config, 'google-ads', ['reports','query'], {'resource':'campaign','period':period,
        'fields':['segments.date','campaign.id','segments.conversion_action_category','metrics.conversions_value'],
        'filters':[{'field':'segments.conversion_action_category','op':'=','value':'PURCHASE'}], 'max_rows':1000})
    m = cli(config,'meta-ads',['insights','query'], {'period':period,'level':'account','time_increment':1,
        'fields':['spend','action_values','clicks'],'max_rows':1000})
    s = cli(config,'shopify',['reports','query'], {'start':period['start'],'end':period['end'],
        'metrics':['orders','total_sales'],'timeseries':'day'})
    gtotal = cli(config,'google-ads',['reports','query'],{'resource':'customer','period':period,
        'fields':['metrics.cost_micros'],'max_rows':10})
    mtotal = cli(config,'meta-ads',['reports','summary'],{'period':period,'compare_previous':False})
    exports = Path(config['anc_root'])/'.local/exports'
    grows, prows, mrows = (business_rows(x,exports) for x in [g,purchases,m])
    audit_google_purchases(grows,prows)
    sales = s.get('data',{}).get('rows')
    if not isinstance(sales,list): raise ValueError('Missing sales rows')
    rows=normalize(day,grows,mrows,sales)
    if any(r['revenue'] is None for r in rows):
        raise ValueError('Shopify missing day in requested month')
    account_rows=business_rows(gtotal,exports)
    if len(account_rows)!=1: raise ValueError('Missing Google monthly account total')
    google_total=number(account_rows[0]['metrics']['costMicros'])/1000000
    meta_total=number(mtotal.get('data',{}).get('current',{}).get('spend'))
    if meta_total is None: raise ValueError('Missing Meta monthly account total')
    for key,expected in [('google_spend',google_total),('meta_spend',meta_total)]:
        observed=total([r[key] for r in rows])
        if observed is None or observed!=expected: raise ValueError('Monthly detail and account total differ')
    return rows, {'google':g,'purchase_audit':purchases,'meta':m,'shopify':s,
                  'monthly_account_totals':{'google':google_total,'meta':meta_total,'google_evidence':gtotal,'meta_evidence':mtotal}}


def quote_tab(tab): return "'"+tab.replace("'","''")+"'"


def month_tab(day): return f'{day:%y} {calendar.month_abbr[day.month]} - Budget Pacing'


def cellval(value):
    if value is None: return 'N/A'
    return float(value) if isinstance(value,Decimal) else value


def sheet_values(day, values, rows):
    if len(values) < 99 or len(values[66]) < 15 or values[66][5:7] != ['Total Shopify Revenue','Total Shopify ROAS']:
        raise ValueError('Unexpected Biceek sheet layout')
    if values[66][9:15] != ['PMAX Spend','PMAX ROAS','Search Spend','Search ROAS','Meta Spend','Meta ROAS']:
        raise ValueError('Channel layout changed')
    feed_columns=values[66][15:17]==['PMAX Feed Only','Feed Only ROAS']
    if not feed_columns and any(row.get('feed_only_spend') is not None for row in rows):
        raise ValueError('Missing Biceek Feed Only pacing columns')
    days = calendar.monthrange(day.year,day.month)[1]
    for d in range(1,days+1):
        if values[66+d][0] != f'{day.month}/{d}/{day.year}': raise ValueError('Sheet calendar mismatch')
    tab = quote_tab(month_tab(day)); changes = []
    def put(address, vals): changes.append({'range':tab+'!'+address,'values':vals})
    for row in rows:
        n = 67 + date.fromisoformat(row['date']).day
        spend = row['spend']
        put(f'D{n}:H{n}', [[cellval(spend) if spend is not None else '', cellval(spend) if spend is not None else '',
                           cellval(row['revenue']), f'=IFERROR(F{n}/D{n},"N/A")',
                           f'=IFERROR(D{n}/D{n-1}-1,"N/A")']])
        put(f'J{n}:O{n}', [[cellval(row[k+'_spend']) if i%2==0 else cellval(ratio(row[k+'_value'],row[k+'_spend']))
                            for i,k in enumerate(['pmax','pmax','search','search','meta','meta'])]])
        if feed_columns and date.fromisoformat(row['date'])>=FEED_ONLY_START:
            put(f'P{n}:Q{n}',[[cellval(row.get('feed_only_spend')),cellval(ratio(row.get('feed_only_value'),row.get('feed_only_spend')))]])
    last = 67+day.day
    for c in ('FJLNP' if feed_columns else 'FJLN'): put(f'{c}99',[[f'=SUM({c}68:{c}{last})']])
    # Individual missing dates stay N/A. Month totals are independently checked
    # against each platform's account aggregate before any sheet writes.
    spend_ranges=[f'{c}68:{c}{last}' for c in ('JLNP' if feed_columns else 'JLN')]
    put('E99',[['=SUM('+','.join(spend_ranges)+')']])
    if feed_columns:
        reported=[r for r in rows if r.get('feed_only_spend') is not None]
        value=total([r.get('feed_only_value') for r in reported]) if all(r.get('feed_only_value') is not None for r in reported) else None
        put('Q99',[[cellval(ratio(value,total([r['feed_only_spend'] for r in reported])))]])
    put('D99',[['=E99']])
    put('G99',[['=IFERROR(F99/E99,"N/A")']])
    put('D102',[[f'=IFERROR(E99/{day.day}*{days}/E34,"N/A")']])
    put('F102',[[f'=IFERROR(F99/{day.day}*{days}/F34,"N/A")']])
    put('D108',[[f'=IFERROR((E34-E99)/({days}-{day.day}),"N/A")']])
    for address,formula in {'T115':'=E99','T116':'=F99','T117':'=IFERROR(F99/E99,"N/A")','T118':'=D108','T122':'=D102','T123':'=F102'}.items(): put(address,[[formula]])
    return changes


class Sheets:
    def __init__(self, config):
        from google.oauth2.service_account import Credentials
        from google.auth.transport.requests import AuthorizedSession
        self.session = AuthorizedSession(Credentials.from_service_account_file(config['sheets_credentials'],
                    scopes=['https://www.googleapis.com/auth/spreadsheets']))
        self.base = 'https://sheets.googleapis.com/v4/spreadsheets/'+SHEET_ID

    def request(self, method='GET', suffix='', **kwargs):
        r = self.session.request(method,self.base+suffix,timeout=40,**kwargs)
        if r.status_code != 200: raise RuntimeError('Sheets HTTP '+str(r.status_code))
        return r.json()

    def read(self, tab, cells='A1:T123', mode='FORMATTED_VALUE'):
        return self.request('GET','/values/'+urllib.parse.quote(quote_tab(tab)+'!'+cells,safe=''),
                            params={'valueRenderOption':mode,'dateTimeRenderOption':'FORMATTED_STRING'}).get('values',[])

    def prepare(self,day):
        tab=month_tab(day)
        names=[s['properties']['title'] for s in self.request(params={'fields':'sheets(properties(title))'})['sheets']]
        if tab not in names: raise ValueError('Missing monthly Biceek pacing tab')
        values=self.read(tab)
        budget=self.read(tab,'D34:F34','UNFORMATTED_VALUE')
        if not budget or len(budget[0])<3 or any(number(budget[0][i]) is None or number(budget[0][i])<=0 for i in [1,2]):
            raise ValueError('Monthly paid budget or revenue target missing')
        # Reject unconverted monthly templates rather than write USD under EUR labels.
        if any('€' in str(x) for row in values for x in row): raise ValueError('Monthly sheet still uses EUR')
        return values

    def update(self,day,rows):
        values=self.prepare(day)
        changes=sheet_values(day,values,rows)
        self.request('POST','/values:batchUpdate',json={'valueInputOption':'USER_ENTERED','data':changes})
        # Read back every submitted value/formula, not just the summary.
        out=self.request('GET','/values:batchGet',params=[('ranges',x['range']) for x in changes]+[('valueRenderOption','FORMULA')])
        for change,actual in zip(changes,out.get('valueRanges',[])):
            want=change['values']; got=actual.get('values',[])
            if len(got)!=len(want): raise ValueError('Sheet read-back row mismatch')
            for wr,gr in zip(want,got):
                padded=gr+['']*max(0,len(wr)-len(gr))
                for w,g in zip(wr,padded):
                    if isinstance(w,(float,int)):
                        if abs(Decimal(str(w))-Decimal(str(g)))>Decimal('0.000001'): raise ValueError('Sheet numeric read-back mismatch')
                    elif w!=g: raise ValueError('Sheet formula read-back mismatch')
        if len(out.get('valueRanges',[]))!=len(changes): raise ValueError('Incomplete sheet read-back')
        summary={}
        for address in ['T115','T116','T117','T118','T122','T123']:
            found=self.read(month_tab(day),address)
            text=str(found[0][0]) if found and found[0] else 'N/A'
            if text.startswith('#'): raise ValueError('Sheet summary formula error')
            summary[address]=text
        return summary


def money(n): return f'${n:,.2f}' if n is not None else 'N/A'
def efficiency(n): return f'{n:.2f}' if n is not None else 'N/A'


def api_summary(rows):
    spend = total([r['google_spend'] for r in rows]) + total([r['meta_spend'] for r in rows])
    revenue = total([r['revenue'] for r in rows])
    return {'T115': money(spend), 'T116': money(revenue), 'T117': efficiency(ratio(revenue,spend))}


def key_changes(current, previous):
    """Report observed day-over-day drivers; never infer causal attribution."""
    if previous is None:
        return '*Key Changes*\n• Previous-day comparison unavailable; no change percentages calculated.'
    def pct(a,b):
        return (a/b-1)*100 if a is not None and b is not None and b>0 else None
    def metric(row,key):
        if key=='roas': return ratio(row.get('revenue'),row.get('spend'))
        if key.endswith('_roas'):
            channel=key[:-5]
            return ratio(row.get(channel+'_value'),row.get(channel+'_spend'))
        return row.get(key)
    def display(value,key):
        if key=='orders': return f'{value:.0f}'
        if key.endswith('roas'): return efficiency(value)
        return money(value)
    def direction(a,b): return 'increased' if a>b else 'decreased' if a<b else 'was unchanged'
    lines=[f'*Key Changes — vs. {date.fromisoformat(previous["date"]):%m/%d}*']
    metrics=[('revenue','Revenue'),('orders','Orders'),('spend','Total Spend'),('roas','Overall ROAS'),
             ('google_spend','Google Spend'),('google_roas','Google ROAS'),
             ('meta_spend','Meta Spend'),('meta_roas','Meta ROAS')]
    flagged=[]
    for key,label in metrics:
        a,b=metric(current,key),metric(previous,key)
        change=pct(a,b)
        if a is None or b is None or a==b: continue
        if b!=0 and (change is None or abs(change)<15): continue
        arrow='↑' if a>b else '↓'
        if key=='orders' and min(a,b)<10:
            delta=f'{a-b:+.0f} orders; small base'
        elif b==0:
            delta='from zero; percentage undefined'
        else: delta=f'{change:+.1f}%'
        lines.append(f'• {arrow} *{label}:* {display(b,key)} → {display(a,key)} ({delta}).')
        flagged.append(key)
    if not flagged:
        lines.append('• No comparable core metric changed by 15% or more.')
        return '\n'.join(lines)
    explanations=[]
    if 'revenue' in flagged or 'orders' in flagged:
        r0,r1=previous.get('revenue'),current.get('revenue')
        o0,o1=previous.get('orders'),current.get('orders')
        if all(v is not None for v in [r0,r1,o0,o1]) and o0>0 and o1>0:
            v0,v1=r0/o0,r1/o1
            volume=(o1-o0)*(v0+v1)/2
            basket=(v1-v0)*(o0+o1)/2
            lead='order volume' if abs(volume)>=abs(basket) else 'revenue per order'
            explanations.append(f'Revenue drivers: {lead} had the larger arithmetic contribution. Orders {o0:.0f} → {o1:.0f}; revenue per order {money(v0)} → {money(v1)}. Order-volume contribution: {money(volume)}; revenue-per-order contribution: {money(basket)}.')
            if min(o0,o1)<10:
                explanations.append('Low daily order counts make percentage swings sensitive to individual orders.')
        else: explanations.append('Revenue driver cannot be split reliably without positive order counts for both days.')
    if any(k in flagged for k in ['spend','google_spend','meta_spend']):
        deltas=[]
        for k,label in [('google','Google'),('meta','Meta')]:
            a,b=current.get(k+'_spend'),previous.get(k+'_spend')
            if a is not None and b is not None: deltas.append((a-b,label))
        if len(deltas)==2:
            deltas.sort(key=lambda item:abs(item[0]),reverse=True)
            explanations.append('Spend drivers: '+ '; '.join(f'{label} {money(delta)}' for delta,label in deltas)+'.')
        a,b=current.get('google_spend'),previous.get('google_spend')
        if a is not None and b is not None and a!=b:
            parts=[]
            for k,label in [('search','Search'),('pmax','PMax')]:
                x,y=current.get(k+'_spend'),previous.get(k+'_spend')
                if x is not None and y is not None: parts.append(f'{label} {money(x-y)}')
            if len(parts)==2: explanations.append('Within Google: '+ '; '.join(parts)+'.')
    if 'roas' in flagged:
        r0,r1=previous.get('revenue'),current.get('revenue')
        s0,s1=previous.get('spend'),current.get('spend')
        if all(v is not None for v in [r0,r1,s0,s1]):
            explanations.append(f'Overall ROAS drivers: Shopify revenue {money(r0)} → {money(r1)} ({pct(r1,r0):+.1f}%) versus combined spend {pct(s1,s0):+.1f}%; spend {direction(s1,s0)}. This uses Shopify sales, not channel attribution.') if r0>0 and s0>0 else explanations.append('Overall ROAS changed with Shopify revenue and combined spend; a zero baseline prevents percentage decomposition.')
    for channel,label in [('google','Google'),('meta','Meta')]:
        if channel+'_spend' in flagged:
            c0,c1=previous.get(channel+'_clicks'),current.get(channel+'_clicks')
            s0,s1=previous.get(channel+'_spend'),current.get(channel+'_spend')
            if c0 is not None and c1 is not None and c0>0 and c1>0:
                explanations.append(f'{label} traffic/cost: clicks {c0:.0f} → {c1:.0f}; CPC {money(s0/c0)} → {money(s1/c1)}. These are platform-reported clicks, not website sessions.')
        if channel+'_roas' not in flagged: continue
        v0,v1=previous.get(channel+'_value'),current.get(channel+'_value')
        s0,s1=previous.get(channel+'_spend'),current.get(channel+'_spend')
        explanations.append(f'{label} ROAS drivers: attributed purchase value {money(v0)} → {money(v1)}, against spend {money(s0)} → {money(s1)}. This is platform attribution, not Shopify revenue.')
    lines.extend('  '+text for text in explanations)
    if any(k.endswith('_roas') for k in flagged):
        lines.append('  Recent advertising attribution may revise. These figures identify observed contributions; they do not establish why customer demand changed.')
    return '\n'.join(lines)


def comparison_report(config, day, rows):
    previous_day=day-timedelta(days=1)
    if day.day>1:
        previous=next((r for r in rows if r['date']==str(previous_day)),None)
    else:
        prior_rows,_=collect(config,previous_day)
        previous=prior_rows[-1] if prior_rows else None
    return key_changes(rows[-1],previous)


def channel_report_line(row,key,label):
    return f"• *{label} Spend:* {money(row.get(key+'_spend'))} | *ROAS:* {efficiency(ratio(row.get(key+'_value'),row.get(key+'_spend')))}"


def format_report(day,row,summary):
    lines=[f'🚲 *Biceek Daily Report {day:%m/%d}*',
           f"*Total Spend:* {money(row['spend'])} | *Total Revenue:* {money(row['revenue'])} | *Orders:* {format(row['orders'], '.0f') if row.get('orders') is not None else 'N/A'} | *ROAS:* {efficiency(ratio(row['revenue'],row['spend']))}",
           '-'*50]
    for key,label in [('pmax','PMax'),('feed_only','PMax Feed Only'),('search','Google Search'),('meta','Meta')]:
        if key!='feed_only' or (day>=FEED_ONLY_START and 'feed_only_spend' in row):
            lines.append(channel_report_line(row,key,label))
    lines += [f"• *Google Ads Spend:* {money(row['google_spend'])} | *Google Ads ROAS:* {efficiency(ratio(row['google_value'],row['google_spend']))}",'-'*50]
    for address,label in [('T115','MTD Paid Media Spend'),('T116','MTD Total Revenue'),('T117','MTD ROAS'),('T118','Avg Daily Budget Remaining'),('T122','MTD Spend Pacing %'),('T123','MTD Total Revenue Pacing %')]:
        if address in summary: lines.append(f'• *{label}:* {summary[address]}')
    return '\n'.join(lines)


class SlackFailure(RuntimeError):
    def __init__(self,message,ambiguous):
        super().__init__(message)
        self.ambiguous=ambiguous


class Slack:
    def __init__(self, token): self.token=token

    def call(self,method,body):
        req=urllib.request.Request('https://slack.com/api/'+method,data=json.dumps(body).encode(),
              headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json; charset=utf-8'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=35) as r: d=json.load(r)
        except urllib.error.HTTPError as exc:
            raise SlackFailure('Slack HTTP '+str(exc.code),exc.code>=500) from None
        except (urllib.error.URLError,TimeoutError,OSError): raise SlackFailure('Slack unknown transport outcome',True) from None
        if not d.get('ok'): raise SlackFailure('Slack '+str(d.get('error','failed')),False) from None
        return d


def delivery_action(state,history):
    if state.get('status')=='sent': return 'done',state.get('ts')
    if state.get('status')=='sending':
        for m in history:
            # Slack normalizes the bicycle emoji to :bike:.
            payload=state.get('ai_provenance',{}).get('slack_payload',{'text':state.get('message')})
            expected=payload.get('attachments',[]);actual=m.get('attachments',[])
            def normal(text):
                for alias,emoji in [(':bike:','🚲'),(':large_green_circle:','🟢'),(':red_circle:','🔴'),(':large_orange_circle:','🟠')]:text=text.replace(alias,emoji)
                return text
            matching=len(expected)==len(actual) and all(normal(a.get('text',''))==normal(b.get('text','')) and a.get('color','').lstrip('#').lower()==b.get('color','').lstrip('#').lower() for a,b in zip(expected,actual))
            if m.get('user')==state.get('bot_user') and normal(m.get('text',''))==normal(payload.get('text','')) and matching:
                return 'reconciled',m['ts']
        return 'blocked',None
    return 'send',None


def deliver(slack,path,state):
    from ai_report import fingerprint
    payload=state.get('ai_provenance',{}).get('slack_payload')
    if payload is not None and state['ai_provenance'].get('payload_sha256')!=fingerprint(payload):raise ValueError('Colored payload provenance mismatch')
    history=[]
    if state.get('status')=='sending':
        try: history=slack.call('conversations.history',{'channel':CHANNEL,'oldest':str(state['send_started']-5),'limit':100}).get('messages',[])
        except RuntimeError: pass
    action,ts=delivery_action(state,history)
    if action=='blocked': raise RuntimeError('Unknown Slack send outcome; manual reconciliation required')
    if action in {'done','reconciled'}:
        state.update(status='sent',ts=ts,attempts=0);atomic(path,state);return
    state.update(status='sending',send_started=time.time(),bot_user=BOT_USER)
    atomic(path,state)  # Durable before crossing the send boundary.
    try:
        result=slack.call('chat.postMessage',{'channel':CHANNEL,**(payload if payload is not None else {'text':state['message']}),
            'client_msg_id':state.setdefault('client_msg_id',str(uuid.uuid4())), 'unfurl_links':False,'unfurl_media':False})
    except SlackFailure as exc:
        if not exc.ambiguous:
            state['status']='prepared';atomic(path,state)
        raise
    state.update(status='sent',ts=result['ts'],sent_at=time.time(),attempts=0);atomic(path,state)


def deliver_details(slack,path,state):
    if state.get('details_status')=='sent':return
    if state.get('details_status')=='sending':raise RuntimeError('Unknown Slack thread send outcome; manual review required')
    if state.get('status')!='sent' or not state.get('ts'):raise ValueError('Main report not confirmed')
    text=state.get('ai_details','')
    if not text or state.get('ai_provenance',{}).get('details_sha256')!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('Missing verified thread analysis')
    payload=state.get('ai_provenance',{}).get('details_payload',{'text':text})
    from ai_report import fingerprint
    expected=state.get('ai_provenance',{}).get('details_payload_sha256')
    if expected and fingerprint(payload)!=expected:raise ValueError('Thread payload provenance mismatch')
    state.update(details_status='sending',details_send_started=time.time());atomic(path,state)
    try:
        result=slack.call('chat.postMessage',{'channel':CHANNEL,'thread_ts':state['ts'],**payload,'unfurl_links':False,'unfurl_media':False})
    except SlackFailure as exc:
        if not exc.ambiguous:state['details_status']='pending';atomic(path,state)
        raise
    state.update(details_status='sent',details_ts=result['ts'],attempts=0);atomic(path,state)


def queue_alert(path,day,stage,error):
    alerts=read_json(path,{})
    key=day+'|'+stage+'|'+error
    if key not in alerts: alerts[key]={'day':day,'stage':stage,'error':error,'status':'pending','created':time.time()}
    atomic(path,alerts)


def can_notify(config):
    return not config.get('send_not_before') or datetime.now(EASTERN).date()>=date.fromisoformat(config['send_not_before'])


def flush_alerts(slack,path,config=None):
    if config is not None and not can_notify(config):return
    alerts=read_json(path,{})
    for key,a in alerts.items():
        if a['status']!='pending': continue
        a['status']='sending';atomic(path,alerts)
        # An uncertain DM outcome is not automatically resent.
        message=f"🚲 Biceek daily report failed\nReport date: {a['day']}\nStage: {a['stage']}\nError: {a['error']}\nThe job will retry safely. If Slack delivery is uncertain, it stops for manual review. Bluevua is unchanged."
        try:
            d=slack.call('chat.postMessage',{'channel':RECIPIENT,'text':message,'unfurl_links':False})
        except SlackFailure as exc:
            if not exc.ambiguous: a['status']='pending'
            atomic(path,alerts);continue
        a.update(status='sent',ts=d['ts']);atomic(path,alerts)


def load_config():
    c=read_json(ROOT/'config.json')
    if not c or c.get('spreadsheet_id')!=SHEET_ID or c.get('channel_id')!=CHANNEL or c.get('failure_user_id')!=RECIPIENT:
        raise ValueError('Unexpected Biceek configuration')
    return c


class PacingFailure(RuntimeError):
    pass


def pacing_summary(config,day,rows,update_pacing=True):
    summary=api_summary(rows)
    if not config.get('pacing_updates_enabled') or not update_pacing:return summary
    addresses=['T118','T122','T123']
    try:
        values=Sheets(config).update(day,rows)
    except ValueError as exc:
        if str(exc)=='Missing monthly Biceek pacing tab':
            summary.update({key:'N/A' for key in addresses});return summary
        raise PacingFailure('Pacing sheet update failed: ValueError') from None
    except Exception as exc:
        raise PacingFailure('Pacing sheet update failed: '+type(exc).__name__) from None
    summary.update({key:values.get(key,'N/A') for key in addresses})
    return summary


def prepare_ai(config,day,update_pacing=True):
    from ai_report import candidates,fingerprint,render,details,daily_attachments,detail_payload
    folder=ROOT/'state';snapshot_path=folder/('snapshot-'+str(day)+'.json')
    if (folder/('ai-'+str(day)+'.json')).exists():
        snapshot=read_json(snapshot_path)
        if not snapshot or snapshot.get('day')!=str(day): raise ValueError('Missing AI source snapshot')
    else:
        rows,evidence=collect(config,day)
        previous_day=day-timedelta(days=1)
        if day.day>1: previous=next((r for r in rows if r['date']==str(previous_day)),None)
        else:
            prior,_=collect(config,previous_day);previous=prior[-1] if prior else None
        eligible=candidates(rows[-1],previous)
        snapshot={'protocol':2,'day':str(day),'rows':rows,'previous':previous,'sources':evidence,'eligible':eligible}
        atomic(snapshot_path,snapshot)
    python=config.get('ai_python','/home/ubuntu/work/anc-automation/.venv/bin/python')
    p=subprocess.run([python,str(ROOT/'ai_worker.py'),str(snapshot_path)],capture_output=True,text=True,timeout=1800)
    if p.returncode:raise RuntimeError('Remote AI generation failed; no template fallback')
    ai=read_json(folder/('ai-'+str(day)+'.json'))
    if not ai or ai.get('status')!='complete' or ai.get('fingerprint')!=fingerprint(snapshot):raise ValueError('AI provenance mismatch')
    rows=[{k:number(v) if k!='date' else v for k,v in r.items()} for r in snapshot['rows']]
    summary=pacing_summary(config,day,rows,update_pacing)
    header=format_report(day,rows[-1],summary)+'\n*Key Changes vs. '+(day-timedelta(days=1)).strftime('%m/%d')+'*'
    text=header+'\n'+render(ai['raw'],snapshot['eligible'])
    attachments=daily_attachments(ai['raw'],snapshot['eligible'])
    product=json.loads(ai['raw']).get('product_update')
    if product:text+='\n*Product & Business Update*\n'+product
    payload={'text':header,'attachments':attachments} if attachments else {'text':text}
    long_analysis=details(ai['raw'])
    thread_payload=detail_payload(ai['raw'])
    return text,{'thread_id':ai['thread_id'],'turn_id':ai['turn_id'],'fingerprint':ai['fingerprint'],'message_sha256':hashlib.sha256(text.encode()).hexdigest(),'details_sha256':hashlib.sha256(long_analysis.encode()).hexdigest(),'details_payload':thread_payload,'details_payload_sha256':fingerprint(thread_payload),'slack_payload':payload,'payload_sha256':fingerprint(payload),'sheet_status':('updated' if summary.get('T122') not in [None,'N/A'] else 'unavailable' if config.get('pacing_updates_enabled') and update_pacing else 'paused')},long_analysis


def run(config, day, mode):
    state_dir=ROOT/'state'; path=state_dir/(day.isoformat()+'.json')
    state=read_json(path,{})
    if config.get('ai_enabled') and mode=='send' and datetime.now(EASTERN).date()<date.fromisoformat(config['send_not_before']):
        print('{"status":"before_start_date"}');return
    if config.get('ai_enabled') and mode=='prepare' and state.get('status')=='sent':
        print('{"status":"already_sent"}');return
    if mode=='send' and state.get('status')=='sent' and (not config.get('ai_enabled') or not state.get('ai_details') or state.get('details_status')=='sent'):
        print(json.dumps({'status':'already_sent','day':str(day),'ts':state.get('ts')}));return
    slack=Slack(config['slack_bot_token']);stage='collect'
    try:
        if mode=='send' and state.get('status')=='sending':
            stage='slack_reconciliation';deliver(slack,path,state)
            if config.get('ai_enabled') and state.get('ai_details'):
                stage='thread_send';deliver_details(slack,path,state)
            return
        if config.get('ai_enabled'):
            if mode=='send':
                stage='ai_delivery'
                if day!=report_day():raise ValueError('Refuse stale report date')
                if state.get('status')=='sent' and state.get('ai_details'):
                    stage='thread_send';deliver_details(slack,path,state);return
                ai=state.get('ai_provenance',{})
                if state.get('status')!='prepared' or not ai.get('thread_id') or not ai.get('turn_id') or ai.get('message_sha256')!=hashlib.sha256(state.get('message','').encode()).hexdigest():raise ValueError('Missing prepared AI report')
                if slack.call('auth.test',{}).get('user_id')!=BOT_USER:raise ValueError('Unexpected Slack bot')
                deliver(slack,path,state)
                stage='thread_send';deliver_details(slack,path,state)
                print(json.dumps({'status':state['status'],'day':str(day),'ts':state.get('ts')}));return
            stage='remote_ai'
            if state.get('status')=='sending':raise RuntimeError('Unknown Slack send outcome; cannot prepare')
            text,provenance,long_analysis=prepare_ai(config,day,update_pacing=mode!='preview')
            if mode=='preview':print(text);return
            state.update(status='sent' if state.get('status')=='sent' else 'prepared',message=text,ai_provenance=provenance,ai_details=long_analysis,details_status='pending',report_date=str(day),prepared_at=time.time(),sheet_status=provenance.get('sheet_status','paused'),attempts=0)
            state.pop('last_error',None);atomic(path,state)
            print(json.dumps({'status':state['status'],'day':str(day),'report':text},ensure_ascii=False));return
        stage='collect';rows,evidence=collect(config,day)
        if not rows: raise ValueError('No report rows')
        summary=api_summary(rows)
        sheet_status='paused'
        if mode!='preview' and config.get('pacing_updates_enabled',True):
            stage='sheet_update'
            try:
                sheet_summary=Sheets(config).update(day,rows)
                summary.update({k:v for k,v in sheet_summary.items() if k in {'T118','T122','T123'}})
                sheet_status='updated'
            except ValueError as exc:
                if str(exc)!='Missing monthly Biceek pacing tab': raise
                sheet_status='missing_month'

        text=format_report(day,rows[-1],summary)+'\n'+comparison_report(config,day,rows)
        if mode=='preview': print(text);return
        evidence_file=state_dir/(day.isoformat()+'-evidence.json')
        atomic(evidence_file,{'rows':rows,'sources':evidence,'summary':summary,'sheet_status':sheet_status})
        previous_status=state.get('status')
        state.update(status='sent' if previous_status=='sent' else 'prepared',message=text,
                     attempts=state.get('attempts',0)+1,report_date=str(day),prepared_at=time.time(),sheet_status=sheet_status)
        state.pop('last_error',None)
        atomic(path,state)
        if mode=='send':
            stage='slack_send'
            if slack.call('auth.test',{}).get('user_id')!=BOT_USER: raise ValueError('Unexpected Slack bot')
            deliver(slack,path,state)
        print(json.dumps({'status':state['status'],'day':str(day),'report':text},ensure_ascii=False))
    except Exception as exc:
        if isinstance(exc,PacingFailure):stage='sheet_update'
        # Never serialize raw upstream exceptions or credential-bearing URLs.
        error=type(exc).__name__
        if isinstance(exc,(ValueError,RuntimeError)):
            msg=str(exc)
            if re.fullmatch(r'[A-Za-z0-9 _;:./-]{1,180}',msg): error+=': '+msg
        fresh=read_json(path,state)
        fresh.update(last_error={'stage':stage,'error':error,'at':time.time()},attempts=fresh.get('attempts',0)+1)
        atomic(path,fresh)
        queue_alert(state_dir/'alerts.json',str(day),stage,error)
        if mode!='preview':flush_alerts(slack,state_dir/'alerts.json',config)
        print(json.dumps({'status':'failed','day':str(day),'stage':stage,'error':error}))
        raise SystemExit(1)


def report_complete(state,ai_required=False):
    return state.get('status')=='sent' and (not ai_required or bool(state.get('ai_details') and state.get('details_status')=='sent'))


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument('--date',type=date.fromisoformat)
    p.add_argument('--mode',choices=['preview','prepare','send','retry','watchdog','alerts'],default='preview')
    args=p.parse_args();(ROOT/'state').mkdir(mode=0o700,exist_ok=True)
    with (ROOT/'state/run.lock').open('a') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX | (0 if args.mode in {'send','prepare','preview'} else fcntl.LOCK_NB))
        except BlockingIOError: print('{"status":"busy"}');return
        config=load_config();slack=Slack(config['slack_bot_token']);day=args.date or report_day()
        if args.mode=='alerts': flush_alerts(slack,ROOT/'state/alerts.json',config);return
        if args.mode=='watchdog':
            if not can_notify(config):return
            now=datetime.now(EASTERN)
            if now.hour>=10:
                state=read_json(ROOT/'state'/(str(day)+'.json'),{})
                if not report_complete(state,config.get('ai_enabled',False)):queue_alert(ROOT/'state/alerts.json',str(day),'watchdog','Main report or detailed thread incomplete by 10:00 Eastern')
            flush_alerts(slack,ROOT/'state/alerts.json',config);return
        if args.mode=='retry':
            state=read_json(ROOT/'state'/(str(day)+'.json'),{})
            thread_pending=state.get('status')=='sent' and state.get('ai_details') and state.get('details_status')!='sent'
            if (state.get('last_error') and state.get('status')!='sent' or thread_pending) and state.get('attempts',0)<6:
                if config.get('ai_enabled') and not state.get('ai_provenance'):run(config,day,'prepare')
                if datetime.now(EASTERN).hour>=9:run(config,day,'send')
            flush_alerts(slack,ROOT/'state/alerts.json',config);return
        run(config,day,args.mode)


if __name__=='__main__': main()
