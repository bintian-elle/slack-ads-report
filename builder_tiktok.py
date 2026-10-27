"""Dashboard Builder's read-only reuse of the Bluevua daily TikTok adapter."""
import json
import math
from pathlib import Path
from datetime import datetime
import requests
from tiktok_service import TIKTOK_REPORT_URL, parse_advertiser_ids

METRICS=['spend','complete_payment_roas','impressions','clicks','complete_payment']

def normalize_reports(payloads):
    """Same purchase-revenue definition as the daily report: spend × purchase ROAS."""
    output=[]
    for payload in payloads:
        if payload.get('code')!=0 or not isinstance(payload.get('data',{}).get('list'),list):
            raise ValueError('TikTok account report unavailable')
        for row in payload['data']['list']:
            values=row.get('metrics',{})
            if any(values.get(key) in (None,'') for key in METRICS):
                raise ValueError('TikTok report is missing requested metrics')
            numbers={key:float(values[key]) for key in METRICS}
            if any(not math.isfinite(value) or value<0 for value in numbers.values()):
                raise ValueError('TikTok report contains invalid metric values')
            day=row.get('dimensions',{}).get('stat_time_day','')[:10]
            datetime.strptime(day,'%Y-%m-%d')
            output.append(dict(date=day,spend=numbers['spend'],revenue=numbers['spend']*numbers['complete_payment_roas'],conversions=numbers['complete_payment'],impressions=numbers['impressions'],clicks=numbers['clicks']))
    return output

def fetch_daily_reporting(env,start,end,context_file):
    ids=list(parse_advertiser_ids(env['TIKTOK_ADVERTISER_IDS']))
    context=json.loads(Path(context_file).read_text())
    # Only the advertiser configuration already used by this Bluevua daily report.
    if context.get('advertiserIds')!=ids or context.get('project')!='bluevua' or context.get('currency')!='USD':
        raise ValueError('TikTok daily reporting configuration does not match the verified local context')
    evidence=[]
    for advertiser in ids:
        page=1
        while True:
            response=requests.get(TIKTOK_REPORT_URL,headers={'Access-Token':env['TIKTOK_ACCESS_TOKEN']},params={
                'advertiser_id':advertiser,'report_type':'BASIC','data_level':'AUCTION_ADVERTISER',
                'dimensions':json.dumps(['stat_time_day']),'metrics':json.dumps(METRICS),
                'start_date':start,'end_date':end,'page':page,'page_size':1000},timeout=30)
            if not response.ok:
                raise ValueError('TikTok daily report HTTP '+str(response.status_code))
            data=response.json()
            if data.get('code')!=0:
                raise ValueError('TikTok daily report API code '+str(data.get('code')))
            evidence.append(data)
            total=int(data['data'].get('page_info',{}).get('total_page',1))
            if page>=total:break
            page+=1
            if page>100:raise ValueError('TikTok report exceeded pagination limit')
    rows=normalize_reports(evidence)
    return rows,context['currency'],context['timezone'],{'reportingContext':context,'reports':evidence}
