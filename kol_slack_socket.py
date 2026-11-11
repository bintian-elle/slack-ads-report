"""Independent KOL receiver; never imports Robot or calls a Sheets writer."""
import argparse
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time

from kol_slack_events import EventStore,accept_envelope,open_store,state_root,work_due


def validate_config(env):
    for key in ('KOL_TRACKER_SLACK_CHANNEL_ID','KOL_TRACKER_SLACK_APP_TOKEN','KOL_TRACKER_SLACK_BOT_TOKEN'):
        if not env.get(key):raise RuntimeError('Missing '+key)
    for kol,robot in [('KOL_TRACKER_SLACK_APP_TOKEN','SLACK_APP_TOKEN'),
                      ('KOL_TRACKER_SLACK_BOT_TOKEN','SLACK_BOT_TOKEN')]:
        if env.get(kol)==env.get(robot):raise RuntimeError('KOL must not share Robot credentials')


class Receiver:
    def __init__(self,store,wake,ack):
        self.store=store;self.wake=wake;self.ack=ack;self.ready=False
        self.events=0;self.hellos=0;self.max_ack_ms=0

    def receive(self,message):
        kind=message.get('type')
        if kind=='hello':
            self.ready=False
            if message.get('connection_info',{}).get('app_id')!=self.store.app_id:
                raise RuntimeError('Socket App ID does not match KOL App')
            self.store.request_recovery();self.store.connection(True)
            self.ready=True;self.hellos+=1;self.wake()
        elif kind=='disconnect':
            self.ready=False;self.store.connection(False)
        elif message.get('envelope_id'):
            if not self.ready:raise RuntimeError('KOL socket identity not verified')
            started=time.monotonic()
            changed=accept_envelope(self.store,message['envelope_id'],message.get('payload',{}),self.ack)
            self.max_ack_ms=max(self.max_ack_ms,(time.monotonic()-started)*1000)
            if changed:self.events+=1;self.wake()

    def check_handshake(self,started,now):
        if not self.hellos and now-started>=30:
            raise RuntimeError('KOL initial socket handshake timed out')


def serve(env,probe=False,seconds=20):
    validate_config(env)
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse
    logger=logging.getLogger('kol.socket.transport')
    logger.handlers=[logging.NullHandler()];logger.propagate=False;logger.setLevel(logging.CRITICAL+1)
    web=WebClient(token=env['KOL_TRACKER_SLACK_BOT_TOKEN'],logger=logger,timeout=20)
    identity=web.auth_test()
    app=web.bots_info(bot=identity['bot_id'])['bot']['app_id']
    expected=env.get('KOL_TRACKER_SLACK_APP_ID','A0C2JJX96KW')
    if app!=expected:raise RuntimeError('KOL Bot App ID mismatch')
    channel=web.conversations_info(channel=env['KOL_TRACKER_SLACK_CHANNEL_ID'])['channel']
    if not channel.get('is_member') or channel.get('is_private'):
        raise RuntimeError('KOL receiver requires the already authorized public channel')
    client=SocketModeClient(app_token=env['KOL_TRACKER_SLACK_APP_TOKEN'],web_client=web,
                          logger=logger,concurrency=1,ping_interval=30)
    temporary=tempfile.TemporaryDirectory(prefix='kol-socket-probe-') if probe else None
    store=EventStore(Path(temporary.name),env['KOL_TRACKER_SLACK_CHANNEL_ID'],expected) if probe else open_store(env)
    state=state_root(env)/'live-metric-jobs.json'
    store.seed(json.loads(state.read_text()) if state.exists() else {})
    wake_signal=threading.Event()
    def wake():
        if not probe:wake_signal.set()
    receiver=Receiver(store,wake,lambda envelope:client.send_socket_mode_response(SocketModeResponse(envelope_id=envelope)))
    failed=[]
    def receive(raw):
        try:receiver.receive(json.loads(raw))
        except Exception as error:
            # No raw messages, URLs, tokens, or exception text in the journal.
            print('KOL socket receive failed ('+type(error).__name__+'); event not acknowledged',flush=True)
            failed.append(type(error).__name__)
    client.on_message_listeners.append(receive)
    started=time.monotonic();cpu=time.process_time();last_wake=0
    try:
        client.connect()
        while not failed and (not probe or time.monotonic()-started<seconds):
            receiver.check_handshake(started,time.monotonic())
            if not probe and (wake_signal.is_set() or time.monotonic()-last_wake>=30):
                wake_signal.clear()
                if work_due(env):(state_root(env)/'slack-events.wake').touch(mode=0o600)
                last_wake=time.monotonic()
            time.sleep(0.25 if probe else 1)
        if failed or not receiver.hellos:raise RuntimeError('KOL socket handshake/ingestion failed')
        result={'hello_verified':True,'app_id':expected,'target_events_observed':receiver.events,
                'subscription_delivery_verified':receiver.events>0,
                'max_ack_ms':round(receiver.max_ack_ms,2),'cpu_seconds':round(time.process_time()-cpu,4),
                'elapsed_seconds':round(time.monotonic()-started,2),'sheet_writes':0,'slack_sends':0}
        print(json.dumps(result),flush=True)
        return result
    finally:
        client.close();store.connection(False);store.close()
        if temporary:temporary.cleanup()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',action='store_true',help='Read-only short connection; no worker wake')
    parser.add_argument('--seconds',type=int,default=20)
    parser.add_argument('--work-due',action='store_true',help='Cheap systemd ExecCondition; 1 means idle')
    parser.add_argument('--ready-since',type=float,help='Exit 0 only after this receiver has verified its App ID')
    args=parser.parse_args()
    os.umask(0o077)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent/'.env')
    env=dict(os.environ)
    if args.work_due:raise SystemExit(0 if work_due(env) else 1)
    if args.ready_since is not None:
        store=open_store(env)
        try:ready=store.is_ready(args.ready_since)
        finally:store.close()
        raise SystemExit(0 if ready else 1)
    try:serve(env,args.probe,max(1,min(args.seconds,60)))
    except Exception as error:
        print('KOL socket stopped ('+type(error).__name__+'); inspect configuration, no credentials logged',flush=True)
        raise SystemExit(1)


if __name__=='__main__':main()
