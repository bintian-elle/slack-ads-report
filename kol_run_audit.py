"""Small KOL run summaries; never change durable state or provider caches."""
import argparse
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

RUN = re.compile(r'\d{8}T\d{6}\d{6}Z\Z')
SNAPSHOT = re.compile(r'(?:\d{8}T\d{6}|Meta-insert|TikTok-insert|TikTok-daily|'
    r'Organic-Meta|Organic-Meta-Totals|Organic-Meta-discovery|Organic-TikTok|'
    r'Organic-YTB-metrics|Organic-YTB-insert|organic-tiktok-insert|organic-youtube-insert)'
    r'-(?:before|after)\.json\Z')


def run_artifact(path):
    """Only timestamped KOL run artifacts, not sibling checkpoint files."""
    path=Path(path)
    for parent in path.parents:
        if RUN.fullmatch(parent.name) and parent.parent.name=='runs':return True
    return False


def summary(value):
    result={'format':'kol-run-summary-v1','counts':{},'rows':[]}
    if not isinstance(value,dict):
        result['counts']['items']=len(value) if isinstance(value,list) else 1
        return result
    for key,item in list(value.items())[:24]:
        if isinstance(item,(list,dict)):result['counts'][str(key)[:60]]=len(item)
        elif key in ('successful_rows','requests','messages','blocked','cutoff','new_row') and isinstance(item,(str,int,float,bool,type(None))):
            result[key]=item[:100] if isinstance(item,str) else item
    for key in ('planned','updated_rows','skipped','note_updates','issues'):
        items=value.get(key,[])
        if not isinstance(items,list):continue
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('row'),int):continue
            entry={'row':item['row'],'kind':key}
            if item.get('reason'):entry['reason']=str(item['reason'])[:160]
            if isinstance(item.get('changes'),dict):entry['columns']=list(item['changes'])[:24]
            result['rows'].append(entry)
            if len(result['rows'])==8:return result
    return result


def timestamp_runs(root):
    runs=Path(root)/'runs'
    if runs.is_symlink():raise RuntimeError('Refusing symlinked KOL runs directory')
    if not runs.exists():return []
    result=[]
    for path in runs.iterdir():
        if path.is_symlink() or not path.is_dir() or not RUN.fullmatch(path.name):continue
        try:datetime.strptime(path.name,'%Y%m%dT%H%M%S%fZ')
        except ValueError:continue
        result.append(path)
    return result


def clean_backups(root,apply=False):
    """Explicitly authorized migration: delete only recognized backup names."""
    result={'files':0,'bytes':0,'applied':apply}
    for run in timestamp_runs(root):
        for directory,dirs,names in os.walk(run,followlinks=False):
            dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()]
            for name in names:
                if name not in ('before.json','after.json','native-before.json') and not SNAPSHOT.fullmatch(name):continue
                path=Path(directory)/name
                if path.is_symlink() or not path.is_file():continue
                size=path.stat().st_size
                if apply:path.unlink()
                result['files']+=1;result['bytes']+=size
    return result


def prune_logs(root,now=None,keep_days=7):
    """Called under task.lock; only expired timestamped run directories."""
    cutoff=(time.time() if now is None else now)-keep_days*86400
    removed=0
    for path in timestamp_runs(root):
        stamp=datetime.strptime(path.name,'%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc).timestamp()
        if stamp<cutoff:
            shutil.rmtree(path);removed+=1
    return removed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state_root',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    # Use exactly the same lock as KOL workers; never unlink an in-flight audit.
    import fcntl
    with (args.state_root/'task.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        print(json.dumps(clean_backups(args.state_root,apply=args.apply)))
