"""Exact identity selection, preserving original sheet row coordinates."""
from urllib.parse import urlsplit
from kol_link_resolution import canonical_post

START={'ads-meta':2,'ads-tiktok':2,'organic-meta':2,'organic-tiktok':1,'organic-youtube':1}

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
    return [row if index<START[kind] or row_keys(kind,row,aliases)&set(targets) else []
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
