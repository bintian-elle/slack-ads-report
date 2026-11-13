import sys,json,unittest
from pathlib import Path
from decimal import Decimal as D
sys.path.insert(0,str(Path(__file__).parents[1]))
import ai_report as ai
class AITests(unittest.TestCase):
 def test_threshold_and_roas_relative(self):
  a={'spend':D('115'),'revenue':D('200'),'orders':D('8')}
  b={'spend':D('100'),'revenue':D('200'),'orders':D('8')}
  s=ai.candidates(a,b)
  self.assertIn('spend',s);self.assertNotIn('revenue',s);self.assertNotIn('roas',s)
 def test_zero_and_small_base(self):
  s=ai.candidates({'revenue':D('50'),'orders':D('2')},{'revenue':D('0'),'orders':D('1')})
  self.assertIn('from zero',s['revenue']['change']);self.assertIn('small base',s['orders']['change'])
 def test_zero_base_formats_roas_and_money_without_scientific_notation(self):
  current={'google_value':D('120.52'),'google_spend':D('321.58'),'revenue':D('123.456')}
  prior={'google_value':D('0'),'google_spend':D('250'),'revenue':D('0')}
  changes=ai.candidates(current,prior)
  self.assertEqual(changes['google_roas']['change'],'0.00 → 0.37 (from zero)')
  self.assertEqual(changes['revenue']['change'],'$0.00 → $123.46 (from zero)')
  self.assertEqual(D(changes['google_roas']['before']),D('0'))
  self.assertGreater(D(changes['google_roas']['after']),D('0.374'))
 def test_output_is_short_and_only_eligible(self):
  s={'spend':{'label':'Spend','change':'+20.0%'}}
  self.assertIn('Spend',ai.render('{"changes":[{"metric":"spend","reason":"More PMax delivery increased spending."}]}',s))
  for raw in ['{"changes":[{"metric":"revenue","reason":"Wrong."}]}','{"changes":[{"metric":"spend","reason":"'+('word '*30)+'"}]}','not json','{"changes":[]}']:
   with self.assertRaises(ValueError):ai.render(raw,s)
 def test_more_than_three_changes_all_render(self):
  eligible={k:{'label':k,'change':'+20.0%'} for k in ['spend','revenue','orders','roas','meta_spend']}
  raw=json.dumps({'changes':[{'metric':k,'reason':'Verified business driver.'} for k in eligible]})
  text=ai.render(raw,eligible)
  self.assertEqual(len(text.splitlines()),5)
  for k in eligible:self.assertIn(k,text)
 def test_missing_eligible_metric_is_rejected(self):
  eligible={k:{'label':k,'change':'+20.0%'} for k in ['spend','revenue','orders','roas']}
  raw=json.dumps({'changes':[{'metric':'spend','reason':'More PMax delivery.'}]})
  with self.assertRaises(ValueError):ai.render(raw,eligible)
 def test_grouped_reason_preserves_each_metric_and_change(self):
  eligible={'spend':{'label':'Spend','change':'+20.0%'},'google_spend':{'label':'Google Spend','change':'+40.0%'}}
  raw=json.dumps({'changes':[{'metrics':['spend','google_spend'],'reason':'Mainly driven by PMax delivery.'}]})
  text=ai.render(raw,eligible)
  self.assertEqual(len(text.splitlines()),1)
  self.assertIn('Spend +20.0%',text);self.assertIn('Google Spend +40.0%',text)
 def test_grouped_metrics_cannot_repeat_or_invent(self):
  eligible={'spend':{'label':'Spend','change':'+20%'}}
  for keys in [['spend','spend'],['spend','unknown'],[]]:
   with self.assertRaises(ValueError):ai.render(json.dumps({'changes':[{'metrics':keys,'reason':'Driver.'}]}),eligible)
 def test_reason_must_be_one_sentence(self):
  with self.assertRaises(ValueError):ai.render('{"changes":[{"metric":"spend","reason":"More clicks. Budget changed."}]}',{'spend':{'label':'Spend','change':'+20%'}})
 def test_details_required_and_safe_for_thread(self):
  self.assertEqual(ai.details('{"details":"Observed PMax expansion explains most additional spend."}'),'Observed PMax expansion explains most additional spend.')
  for raw in ['{}','{"details":""}','{"details":"<!channel> hi"}']:
   with self.assertRaises(ValueError):ai.details(raw)
 def test_colored_attachments_are_inline_and_semantic(self):
  eligible={'revenue':{'label':'Revenue','change':'+20%','before':'100','after':'120'},'roas':{'label':'ROAS','change':'-20%','before':'2','after':'1.6'},'spend':{'label':'Spend','change':'+30%','before':'100','after':'130'}}
  raw=json.dumps({'changes':[{'metric':k,'reason':'Verified business driver.'} for k in eligible]})
  items=ai.colored_attachments(raw,eligible)
  self.assertEqual([a['color'] for a in items],['#2EB67D','#E01E5A','#ECB22E'])
  for a in items:self.assertNotIn('\n',a['text']);self.assertIn(':* ',a['text'])
 def test_grouped_color_keeps_all_metrics(self):
  eligible={k:{'label':k,'change':'+20%','before':'100','after':'120'} for k in ['spend','google_spend']}
  raw=json.dumps({'changes':[{'metrics':list(eligible),'reason':'More PMax delivery.'}]})
  item=ai.colored_attachments(raw,eligible)[0]
  self.assertIn('spend +20%',item['text']);self.assertIn('google_spend +20%',item['text'])
  self.assertEqual(item['color'],'#ECB22E')
 def test_no_changes_requires_no_invented_highlight(self):
  self.assertIn('No core metric',ai.render('{"changes":[]}',{}))
 def test_fingerprint_changes_when_data_changes(self):
  self.assertNotEqual(ai.fingerprint({'a':1}),ai.fingerprint({'a':2}))
 def test_unknown_start_never_restarts(self):
  with self.assertRaises(RuntimeError): ai.generation_action({'status':'starting'},'x')
  with self.assertRaises(RuntimeError): ai.generation_action({'status':'running','fingerprint':'y','thread_id':'t'},'x')
  self.assertEqual(ai.generation_action({'status':'running','fingerprint':'x','thread_id':'t'},'x'),'reconcile')
  self.assertEqual(ai.generation_action({'status':'complete','fingerprint':'x'},'x'),'cached')
class CompactDailyTests(unittest.TestCase):
 def raw(self):
  return json.dumps({'format_version':3,'changes':[{'metric':'revenue','reason':'More orders drove sales.','next_step':'Verify recovery orders.'}],'product_update':'GBP checkout is now live.','details':'Sales\nShopify $100 | GA4 $50'})
 def test_main_includes_bold_cause_action_and_product_update(self):
  eligible={'revenue':{'label':'Revenue','change':'+20%','before':'100','after':'120'}}
  attachments=ai.daily_attachments(self.raw(),eligible)
  self.assertEqual(len(attachments),2)
  self.assertIn('*Suggested cause:*',attachments[0]['text'])
  self.assertIn('*Next step:*',attachments[0]['text'])
  self.assertNotIn('\n',attachments[0]['text'])
  self.assertIn('Product & Business Update',attachments[-1]['text'])
 def test_new_format_rejects_missing_action_product_and_long_comment(self):
  for field in ['next_step','product_update','long','sources']:
   data=json.loads(self.raw())
   if field=='next_step':data['changes'][0].pop(field)
   elif field=='product_update':data.pop(field)
   elif field=='long':data['details']='x'*3301
   else:data['details']='Sources: https://example.com'
   with self.assertRaises(ValueError):ai.daily_attachments(json.dumps(data),{'revenue':{'label':'Revenue','change':'+20%','before':'100','after':'120'}})

class NativeTableTests(unittest.TestCase):
 def test_native_tables_keep_columns_and_disable_wrapping(self):
  raw=json.dumps({'format_version':4,'details':'Tracking note.','detail_tables':[{'title':'Ads','headers':['Channel','Spend','ROAS'],'rows':[['Google','$299.45','0.44x'],['Meta','$344.88','1.02x']]}]})
  payload=ai.detail_payload(raw)
  table=next(b for b in payload['blocks'] if b['type']=='table')
  self.assertEqual(len(table['rows']),3)
  self.assertTrue(all(not c['is_wrapped'] for c in table['column_settings']))
  self.assertEqual(table['rows'][1][1]['text'],'$299.45')
 def test_malformed_table_rejected(self):
  raw=json.dumps({'format_version':4,'details':'Note.','detail_tables':[{'title':'Ads','headers':['A','B'],'rows':[['one']]}]})
  with self.assertRaises(ValueError):ai.detail_payload(raw)

if __name__=='__main__':unittest.main()
