"""Offline migration of KOL retries; no provider/Sheets API access."""
import json
import time
from pathlib import Path

from kol_failures import temporary_reason
from kol_meta_runtime import reporting_window


def latest_reasons(root):
    from kol_run_audit import timestamp_runs
    result={}
    for run in sorted(timestamp_runs(root)):
        for path in sorted(run.rglob('*plan.json')):
            name=path.name
            kind=('organic-meta' if name=='Organic-Meta-Totals-plan.json' else
                  'organic-tiktok' if name=='Organic-TikTok-plan.json' else
                  'organic-youtube' if name=='Organic-YTB-plan.json' else
                  'ads-tiktok' if name=='TikTok-daily-plan.json' else
                  'ads-meta' if name[:8].isdigit() and name.endswith('-plan.json') else None)
            if not kind or path.is_symlink():continue
            data=json.loads(path.read_text())
            entries=(data.get('rows',[]) if data.get('format')=='kol-run-summary-v1' else
                     data.get('planned',data.get('updated_rows',[]))+data.get('skipped',[]))
            for item in entries:
                if item.get('row') and item.get('reason'):result[(kind,item['row'])]=item['reason']
    return result


def migrate(root,apply=False,now=None):
    from kol_tracker import save_json
    root=Path(root);cycle=reporting_window(time.time() if now is None else now)
    reasons=latest_reasons(root);counts={};active={}
    for path in sorted(root.glob('row-progress-*.json')):
        data=json.loads(path.read_text());kind=data['scope'].rsplit('|',1)[-1]
        removed=0;kept=0
        for entry in data.get('rows',{}).values():
            if entry.get('cycle')==cycle or entry.get('unavailable_cycle')==cycle:continue
            if not entry.get('attempts'):
                kept+=1;continue  # Budget may have prevented the first attempt.
            reason=reasons.get((kind,entry.get('row')),entry.get('reason',''))
            if entry.get('retryable') or entry.get('inflight') or temporary_reason(reason):
                if not reason:reason='Previous update interrupted; resume from checkpoint'
                entry.update(retryable=True,reason=reason);kept+=1
            else:
                entry.update(unavailable_cycle=cycle,retryable=False,inflight=False,retry_at=0,
                    reason=reason or 'Previous update incomplete; diagnostic unavailable; manual review required')
                removed+=1
        active[kind]=kept;counts[kind]={'unavailable':removed,'retry_or_unattempted':kept}
        if apply:save_json(path,data)
    path=root/'meta-request-progress.json'
    if path.exists():
        data=json.loads(path.read_text());removed=[]
        for name in list(data.get('pending',{})):
            kind='organic-meta' if name=='organic-views' else name
            if kind in active and not active[kind]:
                reason=data['pending'][name].get('reason','')
                if temporary_reason(reason):continue
                data['pending'].pop(name);removed.append(name)
        if apply:save_json(path,data)
        counts['stages_removed']=removed
    path=root/'live-metric-jobs.json'
    if path.exists():
        data=json.loads(path.read_text());removed=0
        for key,job in list(data.get('jobs',{}).items()):
            if not job.get('attempts') or job.get('retryable') or temporary_reason(job.get('reason','')):continue
            # Old exception-only diagnostics cannot establish permanent failure.
            reason=job.get('reason','')
            if not reason or 'Immediate update failed (' in reason:continue
            data.setdefault('unavailable',{})[key]=dict(job,retry_at=0,
                reason=job.get('reason') or 'Metrics unavailable; manual review required')
            data['jobs'].pop(key);removed+=1
        if apply:save_json(path,data)
        counts['live_unavailable']=removed
    return counts
