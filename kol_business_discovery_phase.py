"""KOL-only Views-first gate; counters/cursors, never full-sheet backups."""
import hashlib
import json
from datetime import datetime, timedelta

from kol_meta_runtime import reporting_window


def prepare_views_phase(env, output, apply):
    from kol_live_insert import open_tab
    from kol_row_updates import root_path
    from kol_tracker import save_json
    from kol_organic_meta import post_key
    from kol_organic_meta_totals import fetch_totals,views_only_plan,write_updates
    from kol_failures import temporary_reason
    session,endpoint,prop,native,values=open_tab(env,'organic-meta')
    runtime=env.get('_KOL_META_RUNTIME')
    cycle=reporting_window(runtime.now() if runtime else None)
    inputs=[[r[i] if len(r)>i else '' for i in (0,1,3,4,6,17)]
            for r in values[2:] if len(r)>3 and r[0] and post_key(r[3])]
    from pathlib import Path
    alias_text=Path(__file__).with_name('kol_meta_creator_aliases.json').read_text()
    config=[env.get('KOL_ORGANIC_META_IG_USER_ID','17841448894150543'),env.get('KOL_ORGANIC_META_YEAR','2026'),env.get('KOL_TRACKER_META_API_VERSION','v23.0'),alias_text]
    signature=hashlib.sha256(json.dumps([endpoint,cycle,inputs,config],sort_keys=True).encode()).hexdigest()
    path=root_path(env)/'business-discovery-phase.json'
    try:saved=json.loads(path.read_text())
    except (FileNotFoundError,ValueError):saved={}
    reuse=saved.get('signature')==signature and saved.get('done')
    dates=[]
    for row in values[2:]:
        if len(row)<4 or not post_key(row[3]):continue
        day=row[1]
        try:
            day=(datetime(1899,12,30)+timedelta(days=day)).date().isoformat() if isinstance(day,(int,float)) else datetime.fromisoformat(str(day)).date().isoformat()
        except (ValueError,TypeError,OverflowError):continue
        if day.startswith(str(env.get('KOL_ORGANIC_META_YEAR','2026'))):dates.append(day)
    cutoff=min(dates) if dates else str(env.get('KOL_ORGANIC_META_YEAR','2026'))+'-01-01'
    scoped=dict(env,_KOL_BD_CUTOFF=cutoff,_KOL_BD_ONLY=True)
    scoped.pop('_KOL_BD_CONTENT',None)
    if saved.get('cycle')==cycle and saved.get('done') and saved.get('config')==config:
        scoped['_KOL_BD_SEED']=saved.get('content',{})
    content=saved['content'] if reuse else fetch_totals(scoped,values)[0]
    planned=views_only_plan(values,content)
    needed=[item for item in planned if any((values[item['row']-1]+['']*30)[col]!=value for col,value in item['changes'].items())
            or (values[item['row']-1]+['']*30)[16]!=item['reason']]
    # Only this phase's H/N (Views/CPM) and managed notes are written.
    verified=write_updates(session,endpoint,prop,native,values,needed,[],output,apply) if needed else {}
    pending=[item for item in content.values() if temporary_reason(item.get('_error',''))]
    unwritten=[item for item in needed if 7 in item['changes'] and 7 not in (verified or {}).get(item['row'],set())]
    done=not pending and not unwritten
    if apply:save_json(path,{'signature':signature,'cycle':cycle,'cutoff':cutoff,'config':config,'done':done,'content':content})
    print('Business Discovery phase: %d Views returned; %d temporarily pending; cutoff %s'%
          (sum('view_count' in x for x in content.values()),len(pending),cutoff),flush=True)
    env['_KOL_BD_CONTENT']=content
    if unwritten:print('Business Discovery protected Views retained; other metrics may continue: '+','.join(str(x['row']) for x in unwritten),flush=True)
    return done
