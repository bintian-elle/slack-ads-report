#!/usr/bin/env python3
"""Independent crash notification, including failures before daily.py can import."""
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent


def main():
    os.umask(0o077)
    c=json.loads((ROOT/'config.json').read_text())
    if c.get('failure_user_id')!='U0BN7GWSX0S': raise ValueError('Unexpected notification recipient')
    today=datetime.now(ZoneInfo('America/New_York')).date()
    if c.get('send_not_before') and today.isoformat()<c['send_not_before']:return
    day=str(today-timedelta(days=1))
    with (ROOT/'state/run.lock').open('a') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        state=ROOT/'state'/(day+'.json')
        # daily.py already sends detailed, deduplicated stage notifications.
        if state.exists() and json.loads(state.read_text()).get('last_error'): return
        marker=ROOT/'state'/('crash-alert-'+day+'.json')
        if marker.exists():
            if json.loads(marker.read_text()).get('status')!='pending': return
        elif '--retry' in sys.argv: return
        fd,tmp=tempfile.mkstemp(dir=marker.parent)
        with os.fdopen(fd,'w') as f:json.dump({'status':'sending'},f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,marker)
        request=urllib.request.Request('https://slack.com/api/chat.postMessage',
            data=json.dumps({'channel':c['failure_user_id'],'text':f'🚲 Biceek daily report service failed\nReport date: {day}\nThe process exited before a normal failure record was available. Please check biceek-daily-report.service. Bluevua is unchanged.'}).encode(),
            headers={'Authorization':'Bearer '+c['slack_bot_token'],'Content-Type':'application/json; charset=utf-8'})
        try:
            with urllib.request.urlopen(request,timeout=35) as response:result=json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code<500: marker.write_text(json.dumps({'status':'pending'}))
            raise RuntimeError('Slack crash notification HTTP failure') from None
        if not result.get('ok'):
            marker.write_text(json.dumps({'status':'pending'}))
            raise RuntimeError('Slack crash notification failed')
        marker.write_text(json.dumps({'status':'sent','ts':result['ts']}))


if __name__=='__main__': main()
