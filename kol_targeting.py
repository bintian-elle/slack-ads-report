"""Exact identity selection, preserving original sheet row coordinates."""
from urllib.parse import urlsplit
from kol_link_resolution import canonical_post
import json
from pathlib import Path

START={'ads-meta':2,'ads-tiktok':2,'organic-meta':2,'organic-tiktok':1,'organic-youtube':1}

def load_link_aliases(env):
    from sync_creator_tracker import BASE
    path=Path(env.get('KOL_TRACKER_STATE_DIR',str(BASE/'data/processed/kol_tracker_runtime')))/'live-metric-jobs.json'
    if not path.exists():return {}
    aliases=json.loads(path.read_text()).get('link_aliases',{})
    if not isinstance(aliases,dict):raise RuntimeError('Invalid persisted link aliases')
    return aliases

def matches_keys(keys,targets):
    posts={k for k in keys if not k.startswith(('igcode:','ttcode:'))}
    requested={k for k in targets if not k.startswith(('igcode:','ttcode:'))}
    return bool(posts&requested) if posts and requested else bool(set(keys)&set(targets))

def matches_target(kind,row,targets,aliases=None):
    return matches_keys(row_keys(kind,row,aliases),targets)

def row_keys(kind,row,aliases=None):
    post_col={'ads-meta':6,'ads-tiktok':3,'organic-meta':3,'organic-tiktok':2,'organic-youtube':2}[kind]
    keys=set();link=str(row[post_col]) if len(row)>post_col else ''
    link=(aliases or {}).get(link,link);canonical=canonical_post(link)
    if canonical:
        u=urlsplit(canonical)
        if u.hostname=='www.instagram.com':keys.add('ig:'+u.path.strip('/').split('/')[-1])
        elif u.hostname=='www.tiktok.com':keys.add('tt:'+u.path.strip('/').split('/')[-1])
        else:keys.add('yt:'+canonical.split('v=')[-1])
    elif link:keys.add('url:'+link)
    code_col={'ads-meta':7,'ads-tiktok':2}.get(kind)
    if code_col is not None and len(row)>code_col and row[code_col]:
        keys.add(('igcode:' if kind=='ads-meta' else 'ttcode:')+str(row[code_col]).strip().lstrip('#'))
    return keys

def scope_values(values,kind,targets,aliases=None):
    if targets is None:return values
    return [row if index<START[kind] or matches_target(kind,row,targets,aliases) else []
            for index,row in enumerate(values)]

def resolve_values(values,kind,aliases=None):
    if not aliases:return values
    col={'ads-meta':6,'ads-tiktok':3,'organic-meta':3,'organic-tiktok':2,'organic-youtube':2}[kind]
    result=[]
    for index,row in enumerate(values):
        fresh=list(row)
        if index>=START[kind] and len(row)>col and str(row[col]) in aliases:
            canonical=canonical_post(aliases[str(row[col])])
            if canonical:fresh[col]=canonical
        result.append(fresh)
    return result
