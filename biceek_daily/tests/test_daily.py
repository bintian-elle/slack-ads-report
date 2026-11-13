import importlib.util
import sys
import tempfile
import unittest
import json
import urllib.error
from unittest.mock import patch
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

FILE = Path(__file__).parents[1] / 'daily.py'
sys.path.insert(0, str(FILE.parent))


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(FILE.exists(), 'Independent Biceek implementation is missing')
        spec = importlib.util.spec_from_file_location('biceek_daily_impl', FILE)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)

    def test_report_date_uses_eastern_and_previous_day_across_year(self):
        self.assertEqual(self.m.report_day(datetime(2027, 1, 1, 14, tzinfo=timezone.utc)), date(2026, 12, 31))

    def test_missing_purchase_is_not_zero_roas(self):
        self.assertIsNone(self.m.ratio(None, D('20')))
        self.assertIsNone(self.m.ratio(D('1'), D('0')))
        self.assertEqual(self.m.ratio(D('0'), D('20')), D('0'))

    def test_usd_totals_do_not_double_count_channels_or_meta_actions(self):
        google = [{'segments': {'date': '2026-10-28'}, 'campaign': {'advertisingChannelType': 'SEARCH'}, 'metrics': {'costMicros': '6584235', 'conversionsValue': 0}},
                  {'segments': {'date': '2026-10-28'}, 'campaign': {'advertisingChannelType': 'PERFORMANCE_MAX'}, 'metrics': {'costMicros': '11337466', 'conversionsValue': 0}}]
        meta = [{'date_start': '2026-10-28', 'spend': '221.83', 'action_values': [{'action_type': 'omni_purchase', 'value': '283.59'}, {'action_type': 'offsite_conversion.fb_pixel_purchase', 'value': '283.59'}]}]
        shop = [{'day': '2026-10-28', 'total_sales': '543.29'}]
        rows = self.m.normalize(date(2026, 10, 28), google, meta, shop)
        self.assertEqual(rows[-1]['spend'], D('239.751701'))
        self.assertEqual(rows[-1]['meta_value'], D('283.59'))
        self.assertIsNone(rows[0]['meta_spend'])
        self.assertIsNone(rows[0]['revenue'])

    def test_sheet_plan_does_not_write_budgets_or_future_actuals(self):
        values = [[] for _ in range(123)]
        values[0] = ['26 October Budget Plan (USD)']
        values[64] = ['26 October Actual Pacing']
        values[66] = ['Date', 'Day of week', 'Key events', '', 'Total Paid Media Spend', 'Total Shopify Revenue', 'Total Shopify ROAS', '', '', 'PMAX Spend', 'PMAX ROAS', 'Search Spend', 'Search ROAS', 'Meta Spend', 'Meta ROAS']
        for d in range(1, 32): values[66+d] = [f'10/{d}/2026']
        rows = self.m.normalize(date(2026, 10, 28), [], [], [{'day': f'2026-10-{d:02}', 'total_sales': '0'} for d in range(1,29)])
        changes = self.m.sheet_values(date(2026, 10, 28), values, rows)
        addresses = {r['range'].split('!')[1] for r in changes}
        self.assertNotIn('D28', addresses)
        self.assertNotIn('D96:O96', addresses)
        self.assertIn('D95:H95', addresses)
        joined = str(changes)
        self.assertNotIn('F68:F98', joined)
        self.assertIn('F68:F95', joined)

    def test_feed_only_is_split_in_report_and_pacing_without_double_count(self):
        day=date(2026,11,6)
        def ad(id,kind,micros,value):
            return {'segments':{'date':str(day)},'campaign':{'id':id,'advertisingChannelType':kind},'metrics':{'costMicros':micros,'conversionsValue':value}}
        rows=self.m.normalize(day,[ad('24435460967','PERFORMANCE_MAX',5000000,10),ad('standard','PERFORMANCE_MAX',20000000,15),ad('search','SEARCH',3000000,0)],[{'date_start':str(day),'spend':12}],[{'day':str(day),'total_sales':60}])
        row=rows[-1]
        self.assertEqual((row['pmax_spend'],row['feed_only_spend'],row['google_spend'],row['google_value'],row['spend']),(20,5,28,25,40))
        self.assertIn('PMax Feed Only Spend:* $5.00 | *ROAS:* 2.00',self.m.format_report(day,row,{}))
        self.assertIsNone(rows[4]['feed_only_spend'])
        values=[[] for _ in range(123)]
        values[66]=['Date','Day of week','Key events','','Total Paid Media Spend','Total Shopify Revenue','Total Shopify ROAS','','','PMAX Spend','PMAX ROAS','Search Spend','Search ROAS','Meta Spend','Meta ROAS','PMAX Feed Only','Feed Only ROAS']
        for d in range(1,31):values[66+d]=[f'11/{d}/2026']
        changes={x['range'].split('!')[1]:x['values'] for x in self.m.sheet_values(day,values,[row])}
        self.assertEqual(changes['P73:Q73'],[[5.0,2.0]])
        self.assertEqual(changes['J73:O73'][0][0],20.0)
        self.assertIn('P68:P73',changes['E99'][0][0])
        self.assertEqual(changes['Q99'],[[2.0]])

    def test_partial_spend_does_not_claim_total(self):
        rows=self.m.normalize(date(2026,10,28),[],[{'date_start':'2026-10-28','spend':'100'}],[])
        self.assertIsNone(rows[-1]['spend'])
        self.assertIsNone(self.m.ratio(D('500'),rows[-1]['spend']))

    def test_export_count_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.m.business_rows({'count': 2, 'data': [{'a': 1}]}, Path('/tmp'))

    def test_slack_report_matches_approved_format(self):
        r = {'spend': D('239.751701'), 'revenue': D('543.29'), 'pmax_spend': D('11.337466'), 'pmax_value': D('0'), 'search_spend': D('6.584235'), 'search_value': D('0'), 'meta_spend': D('221.83'), 'meta_value': D('283.59'), 'google_spend': D('17.921701'), 'google_value': D('0')}
        t = self.m.format_report(date(2026, 10, 28), r, {'T115': '$1,242.39', 'T116': '$4,557.07', 'T117': '3.67', 'T118': '$476.51', 'T122': '51.48%', 'T123': '71.26%'})
        self.assertTrue(t.startswith('🚲 *Biceek Daily Report 10/28*'))
        self.assertIn('$239.75', t)
        self.assertNotIn('Follower', t)
        self.assertNotIn('All amounts', t)

    def test_month_requires_correct_tab_and_dates(self):
        with self.assertRaises(ValueError): self.m.sheet_values(date(2026,11,1), [], [])

    def test_orders_and_api_mtd_without_pacing(self):
        rows = self.m.normalize(date(2026,11,2), [], [],
            [{'day':'2026-11-02','total_sales':'573.07','orders':'8'}])
        self.assertEqual(rows[-1]['orders'], D('8'))
        row=rows[-1]
        row.update(spend=D('441.336330'),google_spend=D('150.906330'),meta_spend=D('290.43'))
        summary=self.m.api_summary([row])
        text=self.m.format_report(date(2026,11,2),row,summary)
        self.assertIn('*Orders:* 8',text)
        self.assertIn('*ROAS:* 1.30',text)
        self.assertIn('*MTD Paid Media Spend:* $441.34',text)
        self.assertNotIn('Pacing %',text)
        self.assertNotIn('Budget Remaining',text)

    def test_paused_pacing_send_never_opens_sheet(self):
        row=self.m.normalize(date(2026,11,2),[],[],[{'day':'2026-11-02','total_sales':'573.07','orders':'8'}])[-1]
        row.update(spend=D('441.336330'),google_spend=D('150.906330'),meta_spend=D('290.43'))
        class FakeSlack:
            def __init__(inner,*args): pass
            def call(inner,method,body): return {'user_id':self.m.BOT_USER,'ts':'123.45'}
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'Sheets',side_effect=AssertionError('must not open sheet')), patch.object(self.m,'collect',return_value=([row],{})), patch.object(self.m,'Slack',FakeSlack):
            self.m.run({'slack_bot_token':'fake','pacing_updates_enabled':False},date(2026,11,2),'send')
            state=self.m.read_json(Path(d)/'state/2026-11-02.json')
            self.assertEqual(state['status'],'sent')
            self.assertIn('*Orders:* 8',state['message'])

    def test_missing_month_does_not_block_send(self):
        row=self.m.normalize(date(2026,11,2),[],[],[{'day':'2026-11-02','total_sales':'573.07','orders':'8'}])[-1]
        row.update(spend=D('441.336330'),google_spend=D('150.906330'),meta_spend=D('290.43'))
        class FakeSlack:
            def __init__(inner,*args): pass
            def call(inner,method,body): return {'user_id':self.m.BOT_USER,'ts':'123.45'}
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'Sheets') as sheets, patch.object(self.m,'collect',return_value=([row],{})), patch.object(self.m,'Slack',FakeSlack):
            sheets.return_value.update.side_effect=ValueError('Missing monthly Biceek pacing tab')
            self.m.run({'slack_bot_token':'fake'},date(2026,11,2),'send')
            state=self.m.read_json(Path(d)/'state/2026-11-02.json')
            self.assertEqual(state['status'],'sent')
            self.assertEqual(state['sheet_status'],'missing_month')

    def test_key_changes_includes_exact_threshold_and_rejects_small_changes(self):
        old={'date':'2026-11-03','revenue':D('100'),'orders':D('10'),'spend':D('100')}
        new={'date':'2026-11-04','revenue':D('115'),'orders':D('10'),'spend':D('114.99')}
        text=self.m.key_changes(new,old)
        self.assertIn('Revenue',text)
        self.assertIn('+15.0%',text)
        self.assertNotIn('*Total Spend',text)
        self.assertIn('revenue per order',text)

    def test_key_changes_small_order_base_and_zero_have_no_misleading_percent(self):
        old={'date':'2026-11-03','revenue':D('0'),'orders':D('1'),'spend':D('0')}
        new={'date':'2026-11-04','revenue':D('100'),'orders':D('2'),'spend':D('20')}
        text=self.m.key_changes(new,old)
        self.assertIn('small base',text)
        self.assertNotIn('+100.0%',text)
        self.assertIn('from zero',text)
        self.assertNotIn('Infinity',text)

    def test_key_changes_roas_uses_relative_change(self):
        old={'date':'2026-11-03','revenue':D('200'),'spend':D('100'),'orders':D('10')}
        new={'date':'2026-11-04','revenue':D('200'),'spend':D('125'),'orders':D('10')}
        text=self.m.key_changes(new,old)
        self.assertIn('*Overall ROAS:* 2.00 → 1.60 (-20.0%)',text)
        self.assertIn('spend increased',text)

    def test_key_changes_missing_previous_is_unavailable_not_zero(self):
        text=self.m.key_changes({'date':'2026-11-01'},None)
        self.assertIn('unavailable',text)
        self.assertNotIn('from zero',text)

    def test_channel_spend_driver_distinguishes_clicks_and_cpc(self):
        old={'date':'2026-11-03','google_spend':D('100'),'google_clicks':D('100')}
        new={'date':'2026-11-04','google_spend':D('200'),'google_clicks':D('100')}
        text=self.m.key_changes(new,old)
        self.assertIn('Google traffic/cost',text)
        self.assertIn('CPC $1.00 → $2.00',text)

    def test_ai_send_disabled_before_start_date(self):
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'Slack',side_effect=AssertionError('No Slack before start')), patch.object(self.m,'report_day',return_value=date(2026,11,4)):
            self.m.run({'ai_enabled':True,'send_not_before':'2099-01-01'},date(2026,11,4),'send')

    def test_ai_send_requires_prepared_remote_analysis(self):
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'collect',side_effect=AssertionError('No fallback collection')), patch.object(self.m,'Slack') as slack:
            with self.assertRaises(SystemExit):
                self.m.run({'ai_enabled':True,'send_not_before':'2000-01-01','slack_bot_token':'fake'},self.m.report_day(),'send')
            self.assertEqual(self.m.read_json(Path(d)/'state'/(str(self.m.report_day())+'.json'))['last_error']['error'],'ValueError: Missing prepared AI report')

    def test_ai_prepare_preserves_unknown_send(self):
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'prepare_ai',side_effect=AssertionError('Must not overwrite sending')), patch.object(self.m,'Slack'):
            p=Path(d)/'state/2026-11-04.json';self.m.atomic(p,{'status':'sending','message':'original'})
            with self.assertRaises(SystemExit):self.m.run({'ai_enabled':True,'slack_bot_token':'fake','send_not_before':'2099-01-01'},date(2026,11,4),'prepare')
            self.assertEqual(self.m.read_json(p)['status'],'sending')
            self.assertEqual(self.m.read_json(p)['message'],'original')

    def test_ai_prepare_failure_does_not_dm_before_start(self):
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'prepare_ai',side_effect=RuntimeError('test')), patch.object(self.m,'Slack') as slack:
            with self.assertRaises(SystemExit):self.m.run({'ai_enabled':True,'slack_bot_token':'fake','send_not_before':'2099-01-01'},date(2026,11,4),'prepare')
            slack.return_value.call.assert_not_called()

    def test_details_posted_as_reply_and_deduplicated(self):
        import hashlib
        class Fake:
            def __init__(inner):inner.calls=[]
            def call(inner,method,body):inner.calls.append((method,body));return {'ts':'22.33'}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json';text='Detailed analysis';state={'status':'sent','ts':'11.22','ai_details':text,'ai_provenance':{'details_sha256':hashlib.sha256(text.encode()).hexdigest()}}
            slack=Fake();self.m.deliver_details(slack,p,state);self.m.deliver_details(slack,p,state)
            self.assertEqual(len(slack.calls),1)
            self.assertEqual(slack.calls[0][1]['thread_ts'],'11.22')
            self.assertEqual(state['details_status'],'sent')

    def test_unknown_thread_delivery_is_not_reposted(self):
        class Fake:
            def call(inner,*args):raise AssertionError('Must not repost unknown thread send')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):self.m.deliver_details(Fake(),Path(d)/'state.json',{'status':'sent','ts':'11.22','details_status':'sending','ai_details':'text'})

    def test_preparing_sent_ai_report_cannot_reset_comment_delivery(self):
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'prepare_ai',side_effect=AssertionError('Cannot reset sent report')), patch.object(self.m,'Slack'):
            p=Path(d)/'state/2026-11-04.json';self.m.atomic(p,{'status':'sent','ai_details':'text','details_status':'sent'})
            self.m.run({'ai_enabled':True,'slack_bot_token':'fake'},date(2026,11,4),'prepare')
            self.assertEqual(self.m.read_json(p)['details_status'],'sent')

    def test_definitely_rejected_thread_message_can_retry(self):
        import hashlib
        class Reject:
            def call(inner,*args):raise self.m.SlackFailure('ratelimited',False)
        with tempfile.TemporaryDirectory() as d:
            text='Details';state={'status':'sent','ts':'11.22','ai_details':text,'ai_provenance':{'details_sha256':hashlib.sha256(text.encode()).hexdigest()}}
            with self.assertRaises(self.m.SlackFailure):self.m.deliver_details(Reject(),Path(d)/'state.json',state)
            self.assertEqual(state['details_status'],'pending')

    def test_parent_success_resets_retry_budget_for_comment(self):
        class Fake:
            def call(inner,*args):return {'ts':'11.22'}
        with tempfile.TemporaryDirectory() as d:
            state={'status':'prepared','message':'short','attempts':5}
            self.m.deliver(Fake(),Path(d)/'state.json',state)
            self.assertEqual(state['attempts'],0)

    def test_watchdog_requires_both_parts_when_ai_enabled(self):
        self.assertFalse(self.m.report_complete({'status':'sent','ai_details':'long','details_status':'pending'},True))
        self.assertTrue(self.m.report_complete({'status':'sent','ai_details':'long','details_status':'sent'},True))
        self.assertTrue(self.m.report_complete({'status':'sent'},False))

    def test_ai_send_posts_parent_then_comment_once(self):
        import hashlib
        calls=[]
        class Fake:
            def __init__(inner,*args):pass
            def call(inner,method,body):
                calls.append((method,body))
                return {'user_id':self.m.BOT_USER,'ts':f'123.{len(calls)}'}
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m,'Slack',Fake):
            day=self.m.report_day();text='short';long='details'
            state={'status':'prepared','message':text,'ai_details':long,'ai_provenance':{'thread_id':'t','turn_id':'u','message_sha256':hashlib.sha256(text.encode()).hexdigest(),'details_sha256':hashlib.sha256(long.encode()).hexdigest()}}
            self.m.atomic(Path(d)/'state'/(str(day)+'.json'),state)
            c={'ai_enabled':True,'send_not_before':'2000-01-01','slack_bot_token':'fake'}
            self.m.run(c,day,'send');self.m.run(c,day,'send')
            posts=[b for m,b in calls if m=='chat.postMessage']
            self.assertEqual(len(posts),2)
            self.assertNotIn('thread_ts',posts[0])
            self.assertEqual(posts[1]['thread_ts'],'123.2')

    def test_deliver_uses_verified_colored_payload(self):
        from ai_report import fingerprint
        calls=[]
        class Fake:
            def call(inner,method,body):calls.append(body);return {'ts':'1.2'}
        with tempfile.TemporaryDirectory() as d:
            payload={'text':'Header','attachments':[{'color':'#E01E5A','text':'🔴 *ROAS -20%:* Lower purchase value.'}]}
            state={'status':'prepared','message':'Plain full report','ai_provenance':{'slack_payload':payload,'payload_sha256':fingerprint(payload)}}
            self.m.deliver(Fake(),Path(d)/'state.json',state)
            self.assertEqual(calls[0]['text'],'Header');self.assertEqual(calls[0]['attachments'],payload['attachments'])
    def test_modified_colored_payload_cannot_send(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):self.m.deliver(None,Path(d)/'state.json',{'status':'prepared','message':'report','ai_provenance':{'slack_payload':{'text':'tampered'},'payload_sha256':'wrong'}})

    def test_colored_send_reconciliation_checks_attachment_content(self):
        payload={'text':'🚲 Header','attachments':[{'text':'🔴 *ROAS -20%:* Lower sales.','color':'#E01E5A'}]}
        state={'status':'sending','bot_user':'BOT','message':'plain','ai_provenance':{'slack_payload':payload}}
        message={'user':'BOT','text':':bike: Header','attachments':[{'text':':red_circle: *ROAS -20%:* Lower sales.','color':'e01e5a'}],'ts':'1.2'}
        self.assertEqual(self.m.delivery_action(state,[message]),('reconciled','1.2'))
        message['attachments'][0]['text']='Different analysis'
        self.assertEqual(self.m.delivery_action(state,[message]),('blocked',None))

    def test_pacing_summary_restores_three_budget_metrics_in_ai_mode(self):
        rows=[{'google_spend':D('100'),'meta_spend':D('200'),'revenue':D('500')}]
        with patch.object(self.m,'Sheets') as sheets:
            sheets.return_value.update.return_value={'T118':'$600.00','T122':'90.00%','T123':'60.00%','T115':'wrong'}
            result=self.m.pacing_summary({'pacing_updates_enabled':True},date(2026,11,5),rows)
            self.assertEqual(result['T115'],'$300.00')
            self.assertEqual(result['T118'],'$600.00')
            self.assertEqual(result['T122'],'90.00%')
            self.assertEqual(result['T123'],'60.00%')
    def test_pacing_disabled_or_preview_does_not_write(self):
        rows=[{'google_spend':D('100'),'meta_spend':D('200'),'revenue':D('500')}]
        with patch.object(self.m,'Sheets',side_effect=AssertionError('No sheet access')):
            self.assertNotIn('T122',self.m.pacing_summary({'pacing_updates_enabled':False},date(2026,11,5),rows))
            self.assertNotIn('T122',self.m.pacing_summary({'pacing_updates_enabled':True},date(2026,11,5),rows,False))
    def test_missing_month_keeps_daily_report_available(self):
        rows=[{'google_spend':D('100'),'meta_spend':D('200'),'revenue':D('500')}]
        with patch.object(self.m,'Sheets') as sheets:
            sheets.return_value.update.side_effect=ValueError('Missing monthly Biceek pacing tab')
            result=self.m.pacing_summary({'pacing_updates_enabled':True},date(2026,12,1),rows)
            self.assertEqual(result['T115'],'$300.00');self.assertEqual(result['T122'],'N/A')
    def test_ai_preparation_includes_pacing_in_colored_message_header(self):
        from ai_report import fingerprint
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d, patch.object(self.m,'ROOT',Path(d)), patch.object(self.m.subprocess,'run',return_value=SimpleNamespace(returncode=0)), patch.object(self.m,'pacing_summary',return_value={'T115':'$300.00','T116':'$500.00','T117':'1.67','T118':'$565.41','T122':'92.13%','T123':'42.30%'}) as summary:
            day=date(2026,11,5)
            row={'date':str(day),'spend':'300','revenue':'500','orders':'8','google_spend':'100','google_value':'10','meta_spend':'200','meta_value':'20','pmax_spend':'50','pmax_value':'5','search_spend':'50','search_value':'5'}
            snapshot={'day':str(day),'rows':[row],'eligible':{}}
            self.m.atomic(Path(d)/'state/snapshot-2026-11-05.json',snapshot)
            self.m.atomic(Path(d)/'state/ai-2026-11-05.json',{'status':'complete','fingerprint':fingerprint(snapshot),'thread_id':'t','turn_id':'u','raw':'{"changes":[],"details":"Detailed analysis."}'})
            text,prov,details=self.m.prepare_ai({'pacing_updates_enabled':True},day)
            for value in ['$565.41','92.13%','42.30%']:
                self.assertIn(value,text);self.assertIn(value,prov['slack_payload']['text'])
            self.assertEqual(prov['sheet_status'],'updated')
            self.assertEqual(details,'Detailed analysis.')

    def test_native_detail_payload_delivers_in_one_reply_with_hash_guard(self):
        import hashlib
        from ai_report import fingerprint
        from unittest.mock import Mock
        payload={'text':'Sales fallback','blocks':[{'type':'table','rows':[[{'type':'raw_text','text':'Sales'}]]}]}
        state={'status':'sent','ts':'1.2','ai_details':'Note.','ai_provenance':{'details_sha256':hashlib.sha256(b'Note.').hexdigest(),'details_payload':payload,'details_payload_sha256':fingerprint(payload)}}
        with tempfile.TemporaryDirectory() as d:
            slack=Mock();slack.call.return_value={'ts':'1.3'}
            self.m.deliver_details(slack,Path(d)/'state.json',state)
            self.assertEqual(slack.call.call_count,1)
            sent=slack.call.call_args.args[1]
            self.assertEqual(sent['blocks'],payload['blocks']);self.assertEqual(sent['thread_ts'],'1.2')
            self.assertEqual(state['details_status'],'sent')
            state['details_status']='pending';payload['text']='tampered'
            with self.assertRaises(ValueError):self.m.deliver_details(slack,Path(d)/'state.json',state)
            self.assertEqual(slack.call.call_count,1)

    def test_confirmed_delivery_never_posts_again(self):
        state = {'status': 'sent', 'ts': '1.2'}
        self.assertEqual(self.m.delivery_action(state, []), ('done', '1.2'))

    def test_unknown_send_outcome_reconciles_or_blocks(self):
        state = {'status': 'sending', 'message': 'report', 'bot_user': 'BOT'}
        self.assertEqual(self.m.delivery_action(state, [{'text':'report','user':'BOT','ts':'4.5'}]), ('reconciled','4.5'))
        self.assertEqual(self.m.delivery_action(state, []), ('blocked',None))

    def test_alerts_are_deduplicated_per_report_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'alerts.json'
            self.m.queue_alert(p,'2026-10-28','collect','RuntimeError')
            self.m.queue_alert(p,'2026-10-28','collect','RuntimeError')
            self.assertEqual(len(self.m.read_json(p,{})),1)

    def test_definitely_rejected_alert_is_requeued(self):
        class Reject:
            def call(inner,*args): raise self.m.SlackFailure('ratelimited', ambiguous=False)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'alerts.json'
            self.m.queue_alert(p,'2026-10-28','collect','RuntimeError')
            self.m.flush_alerts(Reject(),p)
            self.assertEqual(next(iter(self.m.read_json(p,{}).values()))['status'],'pending')

    def test_unknown_alert_send_is_not_reposted(self):
        class Unknown:
            calls=0
            def call(inner,*args):
                inner.calls+=1
                raise self.m.SlackFailure('transport', ambiguous=True)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'alerts.json';slack=Unknown()
            self.m.queue_alert(p,'2026-10-28','collect','RuntimeError')
            self.m.flush_alerts(slack,p);self.m.flush_alerts(slack,p)
            self.assertEqual(slack.calls,1)

    def test_crash_alert_http_rejection_can_retry(self):
        spec=importlib.util.spec_from_file_location('biceek_crash', FILE.with_name('failure_alert.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir()
            (root/'config.json').write_text(json.dumps({'failure_user_id':'U0BN7GWSX0S','slack_bot_token':'fake'}))
            module.ROOT=root
            error=urllib.error.HTTPError('https://slack.com/api/chat.postMessage',429,'limited',{},None)
            with patch('urllib.request.urlopen',side_effect=error),patch('sys.argv',['failure_alert.py']):
                with self.assertRaises(Exception): module.main()
            marker=next((root/'state').glob('crash-alert-*.json'))
            self.assertEqual(json.loads(marker.read_text())['status'],'pending')


if __name__ == '__main__': unittest.main()
