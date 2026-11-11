"""Offline acceptance: real SQLite/parser; no Slack sends or Sheet writes."""
import json
import tempfile
import unittest
import os
import shutil
import subprocess
from pathlib import Path

from kol_live_updates import launch_events, enqueue_jobs, dispatch_jobs


def payload(event_id, message, **extra):
    return dict(type='event_callback', event_id=event_id, team_id='T',
                api_app_id='A', event=dict(type='message', channel='C', **message), **extra)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from kol_slack_events import EventStore
        self.store = EventStore(Path(self.tmp.name), 'C', 'A', now=lambda: 200)
        self.addCleanup(self.store.close)

    def test_commit_before_ack_and_duplicate_never_replays(self):
        from kol_slack_events import accept_envelope
        p = payload('E1', {'ts':'100', 'text':"Eva's content is live\nYT: https://youtu.be/m3voSsAfZQw"})
        acks=[]
        def ack(envelope):
            self.assertEqual(len(self.store.snapshot()[0]),0 if acks else 1)
            acks.append(envelope)
        accept_envelope(self.store, 'env1', p, ack)
        messages, ticket = self.store.snapshot()
        self.store.finish(ticket)
        accept_envelope(self.store, 'env2', p, ack)
        self.assertEqual(acks,['env1','env2'])
        self.assertEqual(self.store.snapshot(),([],{}))

    def test_persistence_failure_is_not_acknowledged(self):
        from kol_slack_events import accept_envelope
        self.store.close()
        acks=[]
        with self.assertRaises(Exception):
            accept_envelope(self.store,'x',payload('E',{'ts':'100','text':'x'}),acks.append)
        self.assertEqual(acks,[])

    def test_wrong_channel_and_app_are_acknowledged_but_not_stored(self):
        from kol_slack_events import accept_envelope
        p=payload('E',{'ts':'100','text':'private'});p['event']['channel']='OTHER'
        acks=[];accept_envelope(self.store,'x',p,acks.append)
        p['event']['channel']='C';p['api_app_id']='OTHER'
        accept_envelope(self.store,'y',p,acks.append)
        self.assertEqual(acks,['x','y']);self.assertEqual(self.store.snapshot(),([],{}))

    def test_old_reply_keeps_parent_link_and_creator(self):
        self.store.ingest(payload('E1',{'ts':'1','text':"[KOL Content is Live]\nEva's content is live\nIG: https://instagram.com/reel/ABC/"}))
        self.store.finish(self.store.snapshot()[1])
        self.store.ingest(payload('E2',{'ts':'190','thread_ts':'1','text':'Q9jTCode'}))
        messages,_=self.store.snapshot()
        events=launch_events(messages,0)
        self.assertEqual([(e['creator'],e['ad_code']) for e in events],[('Eva','Q9jTCode')])

    def test_edit_and_out_of_order_history_keep_latest_revision(self):
        self.store.ingest(payload('E1',{'ts':'10','text':'old'}))
        self.store.ingest(payload('E2',{'subtype':'message_changed','ts':'190',
            'message':{'ts':'10','text':'new','edited':{'ts':'190'}}}))
        self.store.merge_history([{'ts':'10','text':'old'}],since=100)
        self.assertEqual(self.store.snapshot()[0][0]['text'],'new')

    def test_arrival_while_worker_busy_survives_finish(self):
        self.store.ingest(payload('E1',{'ts':'10','text':'old'}))
        _,ticket=self.store.snapshot()
        self.store.ingest(payload('E2',{'ts':'190','thread_ts':'10','text':'reply'}))
        self.store.finish(ticket)
        self.assertEqual(len(self.store.snapshot()[0]),2)

    def test_restart_retains_unfinished_work(self):
        from kol_slack_events import EventStore
        self.store.ingest(payload('E1',{'ts':'10','text':'work'}))
        self.store.close()
        self.store=EventStore(Path(self.tmp.name),'C','A',now=lambda:201)
        self.assertEqual(self.store.snapshot()[0][0]['text'],'work')

    def test_recovery_cursor_survives_failure_and_retries_old_thread(self):
        self.store.seed({'cursor':100,'threads':{'1':{'ts':'1','text':"Eva's content is live",'reply_count':1}}})
        self.store.request_recovery(200)
        calls=[]
        def fetch(method,params):
            calls.append((method,dict(params)))
            if method=='conversations.history':
                if not params.get('cursor'):return {'messages':[], 'response_metadata':{'next_cursor':'p2'}}
                return {'messages':[], 'response_metadata':{}}
            return {'messages':[{'ts':'1','text':"Eva's content is live"},
                {'ts':'150','thread_ts':'1','text':'YT: https://youtu.be/m3voSsAfZQw'}]}
        self.store.recover(fetch,limit=1)
        def fail(*a):raise TimeoutError('offline')
        with self.assertRaises(TimeoutError):self.store.recover(fail,limit=1)
        self.store.recover(fetch,limit=10)
        self.assertEqual(calls[1][1]['cursor'],'p2')
        self.assertTrue(any(m=='conversations.replies' and p['ts']=='1' for m,p in calls))
        self.assertEqual(launch_events(self.store.snapshot()[0],0)[0]['creator'],'Eva')
        self.assertFalse(self.store.recovery_pending())

    def test_unknown_old_root_reply_fetches_context(self):
        self.store.ingest(payload('E1',{'ts':'190','thread_ts':'1','text':'Q9jTCode'}))
        def fetch(method,p):
            self.assertEqual((method,p['ts']),('conversations.replies','1'))
            return {'messages':[{'ts':'1','text':"Eva's content is live\nIG: https://instagram.com/reel/ABC/"},
                {'ts':'190','thread_ts':'1','text':'Q9jTCode'}]}
        self.store.recover(fetch,limit=1)
        self.assertEqual(launch_events(self.store.snapshot()[0],0)[0]['ad_code'],'Q9jTCode')

    def test_idle_has_no_fetch_calls_and_no_dirty_work(self):
        for _ in range(100):self.store.recover(lambda *a:self.fail('idle network call'))
        self.assertEqual(self.store.snapshot(),([],{}))

    def test_edited_completed_job_requeues_changed_inputs_only(self):
        event={'platform':'Meta','creator':'Eva','post_link':'https://instagram.com/reel/ABC/',
               'ts':'100','ad_code':'Q9jTOld','revision':100}
        state={};enqueue_jobs(state,[event],100)
        handlers={k:lambda jobs:{'ig:ABC'} for k in ('ads-meta','organic-meta')}
        dispatch_jobs(state,handlers,lambda *a:None,lambda:None,110)
        enqueue_jobs(state,[dict(event,revision=120)],120)
        self.assertFalse(state['jobs'])
        enqueue_jobs(state,[dict(event,ad_code='Q9jTNew',revision=130)],130)
        self.assertEqual(len(state['jobs']),2)

    def test_missing_app_identity_fails_closed(self):
        p=payload('E',{'ts':'100','text':'x'});p.pop('api_app_id')
        self.assertFalse(self.store.ingest(p));self.assertEqual(self.store.snapshot(),([],{}))

    def test_snapshot_worker_does_not_read_history_when_idle(self):
        from kol_slack_events import event_snapshot
        env={'KOL_TRACKER_STATE_DIR':self.tmp.name,'KOL_TRACKER_SLACK_CHANNEL_ID':'C',
             'KOL_TRACKER_SLACK_APP_ID':'A'}
        messages,oldest,ticket=event_snapshot(env,{},200,fetch=lambda *a:self.fail('idle HTTP'))
        self.assertEqual((messages,oldest,ticket),([],0,{}))

    def test_parser_revision_tracks_late_code_reply(self):
        events=launch_events([{'ts':'1','text':"Eva's content is live\nIG: https://instagram.com/reel/ABC/"},
            {'ts':'190','thread_ts':'1','text':'Q9jTCode'}],0)
        self.assertEqual(events[0]['revision'],190)

    def test_input_change_updates_waiting_job(self):
        event={'platform':'Meta','creator':'Eva','post_link':'https://instagram.com/reel/ABC/',
               'ts':'100','ad_code':'Q9jTOld','revision':100}
        state={};enqueue_jobs(state,[event],100)
        enqueue_jobs(state,[dict(event,ad_code='Q9jTNew',revision=130)],130)
        self.assertEqual({j['ad_code'] for j in state['jobs'].values()},{'Q9jTNew'})

    def test_socket_hello_validates_app_and_schedules_recovery(self):
        from kol_slack_socket import Receiver
        wake=[];ack=[]
        r=Receiver(self.store,lambda:wake.append(True),ack.append)
        r.receive({'type':'hello','connection_info':{'app_id':'A'}})
        self.assertTrue(self.store.recovery_pending())
        r.receive({'type':'events_api','envelope_id':'x','payload':payload('E',{'ts':'100','text':'new'})})
        self.assertEqual(ack,['x']);self.assertEqual(len(wake),2)

    def test_wrong_socket_app_never_accepts_payload(self):
        from kol_slack_socket import Receiver
        r=Receiver(self.store,lambda:None,lambda x:self.fail('ack wrong app'))
        with self.assertRaises(RuntimeError):r.receive({'type':'hello','connection_info':{'app_id':'ROBOT'}})
        with self.assertRaises(RuntimeError):r.receive({'type':'events_api','envelope_id':'x',
            'payload':payload('E',{'ts':'100','text':'x'})})
        self.assertEqual(self.store.snapshot(),([],{}))

    def test_isolation_rejects_robot_credentials(self):
        from kol_slack_socket import validate_config
        env={'KOL_TRACKER_SLACK_CHANNEL_ID':'C','KOL_TRACKER_SLACK_APP_TOKEN':'xapp-a',
             'KOL_TRACKER_SLACK_BOT_TOKEN':'xoxb-b','SLACK_APP_TOKEN':'xapp-a'}
        with self.assertRaises(RuntimeError):validate_config(env)

    def test_idle_condition_does_not_start_heavy_poll(self):
        from kol_slack_events import work_due
        env={'KOL_TRACKER_STATE_DIR':self.tmp.name,'KOL_TRACKER_SLACK_CHANNEL_ID':'C','KOL_TRACKER_SLACK_APP_ID':'A'}
        (Path(self.tmp.name)/'slack-events.enabled').touch()
        self.assertFalse(work_due(env,200))
        self.store.ingest(payload('E',{'ts':'100','text':'new'}))
        self.assertTrue(work_due(env,200))

    def test_recovery_does_not_expose_partial_old_thread_context(self):
        self.store.seed({'cursor':100,'threads':{}})
        self.store.request_recovery(200)
        def fetch(method,p):
            if method=='conversations.history':
                return {'messages':[{'ts':'1','text':"Eva's content is live\nIG: https://instagram.com/reel/ABC/",
                    'edited':{'ts':'190'},'reply_count':1}]}
            return {'messages':[{'ts':'1','text':"Eva's content is live\nIG: https://instagram.com/reel/ABC/",'edited':{'ts':'190'}},
                {'ts':'50','thread_ts':'1','text':'Q9jTCode'}]}
        self.store.recover(fetch,limit=1)
        self.assertEqual(self.store.snapshot(),([],{}))
        self.store.recover(fetch,limit=1)
        self.assertEqual(launch_events(self.store.snapshot()[0],0)[0]['ad_code'],'Q9jTCode')

    def test_handshake_deadline_fails_instead_of_false_active(self):
        from kol_slack_socket import Receiver
        r=Receiver(self.store,lambda:None,lambda x:None)
        with self.assertRaises(RuntimeError):r.check_handshake(started=100,now=131)
        r.receive({'type':'hello','connection_info':{'app_id':'A'}})
        r.check_handshake(started=100,now=131)
        self.assertTrue(self.store.is_ready(199))
        r.receive({'type':'disconnect'})
        self.assertFalse(self.store.is_ready(199))

    def test_installer_resolves_project_before_external_steps(self):
        root=Path(self.tmp.name)/'project';(root/'deploy').mkdir(parents=True)
        script=root/'deploy/install_kol_socket.sh'
        shutil.copyfile(Path(__file__).resolve().parents[1]/'deploy/install_kol_socket.sh',script)
        python=root/'.venv-kol/bin/python';python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\n[ "$PWD" = "$EXPECTED_ROOT" ] || exit 44\nexit 49\n')
        python.chmod(0o700);(root/'.env').touch()
        result=subprocess.run(['bash',str(script)],cwd=self.tmp.name,
                              env=dict(os.environ,EXPECTED_ROOT=str(root)),capture_output=True)
        self.assertEqual(result.returncode,49)

    def test_meta_cooldown_does_not_prevent_tiktok_retry(self):
        from kol_slack_events import work_due
        root=Path(self.tmp.name);(root/'slack-events.enabled').touch()
        (root/'meta-request-progress.json').write_text(json.dumps({'blocked_until':300,
            'pending':{'organic-tiktok':{'retry_at':100}}}))
        env={'KOL_TRACKER_STATE_DIR':self.tmp.name,'KOL_TRACKER_SLACK_CHANNEL_ID':'C','KOL_TRACKER_SLACK_APP_ID':'A'}
        self.assertTrue(work_due(env,200))

    def test_metric_hold_does_not_wake_worker_for_metrics_only(self):
        from kol_slack_events import work_due
        root=Path(self.tmp.name);(root/'slack-events.enabled').touch()
        (root/'meta-request-progress.json').write_text(json.dumps({'pending':{'organic-meta':{'retry_at':100}}}))
        (root/'metric-schedule-control.json').write_text(json.dumps({'not_before':300}))
        env={'KOL_TRACKER_STATE_DIR':self.tmp.name,'KOL_TRACKER_SLACK_CHANNEL_ID':'C','KOL_TRACKER_SLACK_APP_ID':'A'}
        self.assertFalse(work_due(env,200))

    def test_busy_daily_does_not_spawn_repeated_poll_workers(self):
        import fcntl
        from kol_slack_events import work_due
        root=Path(self.tmp.name);(root/'slack-events.enabled').touch()
        self.store.ingest(payload('E',{'ts':'100','text':'new'}))
        env={'KOL_TRACKER_STATE_DIR':self.tmp.name,'KOL_TRACKER_SLACK_CHANNEL_ID':'C','KOL_TRACKER_SLACK_APP_ID':'A'}
        with (root/'task.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertFalse(work_due(env,200))
        self.assertTrue(work_due(env,200))
