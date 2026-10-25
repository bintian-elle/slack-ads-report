"""One complete, canonical Meta inventory shared by KOL writers only."""
import requests

FIELDS=('id,name,status,created_time,effective_status,campaign{id,name},adset{id,name},'
        'creative{id,branded_content,source_instagram_media_id,instagram_permalink_url}')


def inventory(env):
    from sync_creator_tracker import get_json,graph_pages
    base='https://graph.facebook.com/'+env.get('KOL_TRACKER_META_API_VERSION','v23.0')
    account='act_'+env['META_AD_ACCOUNT_ID'].removeprefix('act_')
    scope=(base,account)
    cached=env.get('_KOL_CANONICAL_INVENTORY')
    if cached and cached['scope']==scope:return cached['info'],cached['ads']
    session=requests.Session();session.headers['Authorization']='Bearer '+env['META_ACCESS_TOKEN']
    try:
        info=get_json(session,base+'/'+account,{'fields':'id,name,currency,timezone_name'})
        ads=graph_pages(session,base+'/'+account+'/ads',{'fields':FIELDS,'limit':100})
    finally:session.close()
    # Never publish a partial inventory if any page raised an exception.
    env['_KOL_CANONICAL_INVENTORY']={'scope':scope,'info':info,'ads':ads}
    return info,ads
