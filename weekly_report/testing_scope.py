"""Explicit Content Testing inventory. Never fall back to whole-channel totals."""
from decimal import Decimal

REVISION = 'content-testing-v1'
SEARCH_LABEL = 'EM_Oct26_ContentTesting'
SEARCH_CAMPAIGNS = ('EM-Search-Nonbrand-Purchase-Apr26', 'EM-Search-Nonbrand-TIS-April26', 'EM-Search-Nonbrand-Competitor-IS-Jun25')
PMAX_CAMPAIGNS = ('EM-PMAX-Conquesting-June26', 'EM-PMAX-Bestseller-Oct25', 'EM-PMAX-AllProduct-Mar25')
DG_CAMPAIGN = 'EM-DG-Conversion-Prospecting-Purchase-April26'
DG_GROUP = '202844993442'
META_CAMPAIGN = '120251709189500383'
REDDIT = {
    '2532658725866221443': {'group':'Prospecting_WaterFilter + HydroHomies', 'ads':(
        'Prospecting-Video-PitcherVersusRO','Conv-WF-Video-RO-OrangeJuice','SingleImage-MicroplasticPitcherVsRO',
        'SingleImage-ReplaceFilterPitcherVsRO','SingleImage-TastePitchervsRO','SingleImage-PitcherSafeEnough-ROPOTUV-4:5')},
    '2532652033978842545': {'group':'HydroHomies','ads':(
        'Video-RO-OrangeJuice','Video-PitcherVersusRO','Freefrom-FamiliarRoutineFilterReplace','Freeform-NoRefill-No.1CounterRO')},
}


def google_rows(kind, rows, label=None):
    selected=[]
    for row in rows:
        name=row['campaign'].get('name','')
        if kind=='SEARCH':
            match=name in SEARCH_CAMPAIGNS and label in row.get('adGroupAd',{}).get('labels',[])
        elif kind=='PERFORMANCE_MAX':
            match=name in PMAX_CAMPAIGNS and 'content testing' in row.get('assetGroup',{}).get('name','').lower()
        else:
            match=name==DG_CAMPAIGN and row.get('adGroup',{}).get('id')==DG_GROUP and 'content testing' in row.get('adGroupAd',{}).get('ad',{}).get('name','').lower()
        if match:
            row={**row,'campaign':{**row['campaign'],'advertisingChannelType':kind}}
            selected.append(row)
    return selected


def google_totals(rows):
    """Aggregate only selected ads/asset groups, never their parent campaigns."""
    result={}
    for row in rows:
        c=row['campaign'];key=c['id']
        if key not in result:result[key]={'campaign':c,'metrics':{}}
        metrics=result[key]['metrics']
        for k in ['costMicros','conversions','conversionsValue','impressions','clicks']:
            metrics[k]=str(Decimal(metrics.get(k,'0'))+Decimal(str(row.get('metrics',{}).get(k,0))))
    return list(result.values())


def reddit_ads(ads, groups):
    names={str(g['id']):g['name'].strip() for g in groups}
    return [ad for ad in ads if ad.get('campaign_id') in REDDIT
            and ad.get('name','').strip() in REDDIT[ad['campaign_id']]['ads']
            and names.get(str(ad.get('ad_group_id')))==REDDIT[ad['campaign_id']]['group']]


def reddit_totals(rows):
    if not rows:raise ValueError('No Content Testing Reddit report rows; cannot use account-wide totals')
    total={'reach':None}
    for k in ['spend','impressions','clicks','conversion_purchase_clicks','conversion_purchase_views','conversion_purchase_total_value']:
        if any(row.get(k) is None for row in rows):raise ValueError('Scoped Reddit report missing '+k)
        total[k]=sum(row[k] for row in rows)
    return total


def purchase_queries(ads, groups, date_filter):
    fields='campaign.id,segments.conversion_action_name,segments.conversion_action_category,metrics.conversions,metrics.conversions_value'
    result=[]
    for resource, identity, values in [('ad_group_ad','ad_group_ad.ad.id',[row['adGroupAd']['ad']['id'] for row in ads]),('asset_group','asset_group.id',[row['assetGroup']['id'] for row in groups])]:
        ids=sorted(set(str(v) for v in values))
        if not ids:continue
        if not all(v.isdigit() for v in ids):raise ValueError('Invalid scoped entity ID')
        result.append('SELECT '+fields+','+identity+' FROM '+resource+' WHERE '+identity+' IN ('+','.join(ids)+')'+date_filter)
    return result
