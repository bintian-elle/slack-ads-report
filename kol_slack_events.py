"""Private, durable KOL-only Slack inbox. No Slack sends or Sheet writes here."""
import json
import fcntl
import os
import sqlite3
import threading
import time
from decimal import Decimal
from pathlib import Path


def revision(message):
    return str(max(Decimal(str(message['ts'])),
                   Decimal(str((message.get('edited') or {}).get('ts', message['ts'])))))


class EventStore:
    def __init__(self, root, channel, app_id, now=time.time, retention=90):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.channel=channel;self.app_id=app_id;self.now=now;self.retention=retention
        self.lock=threading.RLock()
        path=self.root/'slack-events.sqlite3'
        self.db=sqlite3.connect(str(path),timeout=2,check_same_thread=False)
        os.chmod(path,0o600)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, received REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages
                (ts TEXT PRIMARY KEY, root TEXT NOT NULL, version TEXT NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS message_roots ON messages(root);
            CREATE TABLE IF NOT EXISTS dirty (root TEXT PRIMARY KEY, generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS hydrate (root TEXT PRIMARY KEY, cursor TEXT NOT NULL DEFAULT '');
        ''')
        with self.lock,self.db:
            identity=[channel,app_id]
            if self._get('identity',identity)!=identity:raise RuntimeError('KOL event identity changed')
            self._put('identity',identity)

    def close(self):
        with self.lock:self.db.close()

    def _get(self,key,default=None):
        row=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _put(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,json.dumps(value)))

    def _merge(self,message,since=None,dirty=True):
        if not message.get('ts'):return
        message=dict(message);ts=message['ts'];root=message.get('thread_ts') or ts
        old=self.db.execute('SELECT version,body FROM messages WHERE ts=?',(ts,)).fetchone()
        version=revision(message)
        if old and Decimal(old[0])>Decimal(version):return
        if old and Decimal(old[0])==Decimal(version):
            # History can carry fresher reply metadata without a new text revision.
            previous=json.loads(old[1])
            if previous.get('text')!=message.get('text') or previous.get('_deleted'):
                return
            message=dict(previous,**message)
            if Decimal(str(previous.get('latest_reply',0)))>Decimal(str(message.get('latest_reply',0))):
                message['latest_reply']=previous['latest_reply']
            if message==previous:return
        self.db.execute('INSERT OR REPLACE INTO messages VALUES (?,?,?,?)',
                        (ts,root,version,json.dumps(message)))
        fresh=since is None or max(float(version),float(message.get('latest_reply',0)))>=since
        if dirty and fresh:
            self.db.execute('INSERT INTO dirty VALUES (?,1) ON CONFLICT(root) DO UPDATE SET generation=generation+1',(root,))
        if root!=ts and not self.db.execute('SELECT 1 FROM messages WHERE ts=?',(root,)).fetchone():
            self.db.execute('INSERT OR IGNORE INTO hydrate(root) VALUES (?)',(root,))

    def ingest(self,payload):
        event=payload.get('event',{})
        if (payload.get('type')!='event_callback' or payload.get('api_app_id')!=self.app_id
                or event.get('channel')!=self.channel or event.get('type')!='message'
                or not payload.get('event_id')):return False
        subtype=event.get('subtype')
        message=event.get('message',{}) if subtype=='message_changed' else event
        if subtype=='message_deleted':
            message=dict(event.get('previous_message') or {},ts=event.get('deleted_ts'),
                         text='',_deleted=True,edited={'ts':event.get('event_ts',event.get('ts'))})
        elif subtype=='message_replied':
            message=event.get('message',{})
        if not message.get('ts'):return False
        with self.lock,self.db:
            added=self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?)',
                                  (payload['event_id'],self.now())).rowcount
            if not added:return False
            self._merge(message)
        return True

    def merge_history(self,messages,since):
        with self.lock,self.db:
            for message in messages:self._merge(message,since)

    def seed(self,state):
        with self.lock,self.db:
            if self._get('seeded'):return
            if state and state.get('channel',self.channel)!=self.channel:raise RuntimeError('KOL channel mismatch')
            for message in state.get('threads',{}).values():self._merge(message,dirty=False)
            self._put('coverage',float(state.get('cursor',self.now()-7*86400)))
            self._put('seeded',True)

    def request_recovery(self,through=None):
        with self.lock,self.db:
            through=self.now() if through is None else through
            current=self._get('recovery')
            if current:
                self._put('recover_again',max(through,self._get('recover_again',0)))
            else:
                self._put('recovery',{'through':through,'since':self._get('coverage',through-7*86400),
                    'phase':'history','cursor':'','roots':[]})

    def recovery_pending(self):
        with self.lock:return bool(self._get('recovery') or self.db.execute('SELECT 1 FROM hydrate LIMIT 1').fetchone())

    def recover(self,fetch,limit=5):
        """Network outside transactions; pages commit before advancing their cursor."""
        for _ in range(limit):
            with self.lock:
                recovery=self._get('recovery')
                hydration=self.db.execute('SELECT root,cursor FROM hydrate ORDER BY root LIMIT 1').fetchone()
            if hydration:
                root,cursor=hydration
                data=fetch('conversations.replies',{'channel':self.channel,'ts':root,'cursor':cursor})
                with self.lock,self.db:
                    for message in data.get('messages',[]):self._merge(message)
                    cursor=data.get('response_metadata',{}).get('next_cursor','')
                    if cursor:self.db.execute('UPDATE hydrate SET cursor=? WHERE root=?',(cursor,root))
                    else:
                        if not self.db.execute('SELECT 1 FROM messages WHERE ts=?',(root,)).fetchone():
                            raise RuntimeError('Slack parent context unavailable')
                        self.db.execute('DELETE FROM hydrate WHERE root=?',(root,))
                continue
            if not recovery:return
            params={'channel':self.channel,'cursor':recovery['cursor'],'latest':recovery['through'],'inclusive':True}
            method='conversations.history' if recovery['phase']=='history' else 'conversations.replies'
            if method=='conversations.history':params['oldest']=recovery['through']-self.retention*86400
            else:params['ts']=recovery['roots'][0]
            data=fetch(method,params)
            with self.lock,self.db:
                for message in data.get('messages',[]):self._merge(message,recovery['since'])
                recovery['cursor']=data.get('response_metadata',{}).get('next_cursor','')
                if not recovery['cursor']:
                    if recovery['phase']=='history':
                        # Re-read retained threads once per actual recovery, including edits
                        # to replies whose root's latest_reply did not change.
                        roots=[]
                        for ts,body in self.db.execute('SELECT ts,body FROM messages WHERE ts=root'):
                            msg=json.loads(body)
                            if msg.get('reply_count') or self.db.execute('SELECT 1 FROM messages WHERE root=? AND ts!=root',(ts,)).fetchone():roots.append(ts)
                        recovery.update(phase='threads',roots=roots)
                    else:recovery['roots'].pop(0)
                if recovery['phase']=='threads' and not recovery['roots']:
                    self._put('coverage',recovery['through']);self._put('recovery',None)
                    again=self._get('recover_again',0);self._put('recover_again',0)
                    if again>recovery['through']:
                        self._put('recovery',dict(through=again,since=recovery['through'],phase='history',cursor='',roots=[]))
                else:self._put('recovery',recovery)

    def snapshot(self,limit=50):
        with self.lock:
            recovery=self._get('recovery')
            blocked=set(recovery['roots']) if recovery and recovery['phase']=='threads' else set()
            if recovery and recovery['phase']=='history':
                # Context may contain old replies that have not reached this cache
                # yet. Do not acknowledge a partial thread before its final page.
                for ts,body in self.db.execute('SELECT ts,body FROM messages WHERE ts=root'):
                    if json.loads(body).get('reply_count') or self.db.execute('SELECT 1 FROM messages WHERE root=? AND ts!=root',(ts,)).fetchone():blocked.add(ts)
            ticket={}
            for root,generation in self.db.execute('SELECT root,generation FROM dirty WHERE root NOT IN (SELECT root FROM hydrate) ORDER BY CAST(root AS REAL) DESC'):
                if root not in blocked:ticket[root]=generation
                if len(ticket)>=limit:break
            messages=[]
            for root in ticket:
                messages.extend(json.loads(row[0]) for row in self.db.execute('SELECT body FROM messages WHERE root=?',(root,)) if not json.loads(row[0]).get('_deleted'))
            return messages,ticket

    def connection(self,ready):
        with self.lock,self.db:
            self._put('connected',ready)
            if ready:self._put('hello_at',self.now())

    def is_ready(self,since):
        with self.lock:return bool(self._get('connected',False) and self._get('hello_at',0)>=since)

    def finish(self,ticket):
        with self.lock,self.db:
            for root,generation in ticket.items():
                self.db.execute('DELETE FROM dirty WHERE root=? AND generation=?',(root,generation))

    def has_work(self):
        with self.lock:
            return self.recovery_pending() or bool(self.db.execute('SELECT 1 FROM dirty LIMIT 1').fetchone())

    def prune(self):
        with self.lock,self.db:
            if self._get('pruned_at',0)>self.now()-86400:return
            cutoff=self.now()-self.retention*86400
            for root, in self.db.execute('SELECT DISTINCT root FROM messages').fetchall():
                rows=self.db.execute('SELECT version,body FROM messages WHERE root=?',(root,)).fetchall()
                newest=max(max(float(v),float(json.loads(b).get('latest_reply',0))) for v,b in rows)
                protected=self.db.execute('SELECT 1 FROM dirty WHERE root=? UNION SELECT 1 FROM hydrate WHERE root=?',(root,root)).fetchone()
                if newest<cutoff and not protected:self.db.execute('DELETE FROM messages WHERE root=?',(root,))
            self.db.execute('DELETE FROM events WHERE received<?',(self.now()-7*86400,))
            self._put('pruned_at',self.now())


def accept_envelope(store,envelope_id,payload,ack):
    changed=store.ingest(payload)  # Disk failure deliberately prevents acknowledgment.
    ack(envelope_id)
    return changed


def state_root(env):
    return Path(env.get('KOL_TRACKER_STATE_DIR',str(Path(__file__).resolve().parent/'data/processed/kol_tracker_runtime')))


def event_mode(env):
    return (state_root(env)/'slack-events.enabled').is_file()


def open_store(env):
    return EventStore(state_root(env),env['KOL_TRACKER_SLACK_CHANNEL_ID'],
                      env.get('KOL_TRACKER_SLACK_APP_ID','A0C2JJX96KW'),
                      retention=int(env.get('KOL_TRACKER_SLACK_RETENTION_DAYS','90')))


def event_snapshot(env,state,now,fetch=None):
    store=open_store(env)
    try:
        store.seed(state)
        if fetch is None:
            import requests
            from sync_creator_tracker import get_json
            session=requests.Session()
            session.headers['Authorization']='Bearer '+env['KOL_TRACKER_SLACK_BOT_TOKEN']
            def fetch(method,params):
                return get_json(session,'https://slack.com/api/'+method,dict(params,limit=100))
        if store._get('recovery_retry_at',0)<=now:
            try:
                store.recover(fetch)
                with store.lock,store.db:store._put('recovery_retry_at',0)
            except Exception:
                with store.lock,store.db:store._put('recovery_retry_at',now+300)
                print('KOL Slack recovery deferred; durable cursor retained',flush=True)
        messages,ticket=store.snapshot()
        store.prune()
        return messages,0,ticket
    finally:store.close()


def finish_snapshot(env,ticket):
    store=open_store(env)
    try:store.finish(ticket)
    finally:store.close()


def work_due(env,now=None):
    """Cheap ExecCondition: no provider requests or heavy reporting imports."""
    now=time.time() if now is None else now
    if not event_mode(env):return True
    # Daily and event writers serialize on this existing lock. Avoid importing
    # the reporting stack every few seconds only to discover that daily is busy.
    with (state_root(env)/'task.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
    store=open_store(env)
    try:
        if store.snapshot(limit=1)[1]:return True
        if store.recovery_pending() and store._get('recovery_retry_at',0)<=now:return True
    finally:store.close()
    control=state_root(env)/'metric-schedule-control.json'
    held=control.exists() and json.loads(control.read_text()).get('not_before',0)>now
    for filename,key in [('live-metric-jobs.json','jobs'),('meta-request-progress.json','pending')]:
        path=state_root(env)/filename
        if not path.exists():continue
        data=json.loads(path.read_text())
        for name,job in data.get(key,{}).items():
            if float(job.get('retry_at',0))>now:continue
            if filename.startswith('meta-'):
                if held:continue
                if name in ('ads-meta','organic-meta','organic-views') and data.get('blocked_until',0)>now:continue
            return True
        if filename.startswith('live-') and any(x.get('retry_at',0)<=now for x in data.get('link_errors',{}).values()):return True
    return False
