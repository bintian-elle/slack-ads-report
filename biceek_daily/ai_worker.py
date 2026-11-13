#!/usr/bin/env python3
"""Remote AI Biceek execution on its existing native SDK; never sends Slack."""
import asyncio,json,sys,os
from pathlib import Path
from ai_report import fingerprint,generation_action,render,details,daily_attachments,detail_payload
from daily import atomic,read_json
ROOT=Path(__file__).resolve().parent
BUSINESS='/home/ubuntu/work/emw3-anc/biceek'
sys.path.insert(0,'/home/ubuntu/work/anc-automation/src')


def final_from(history,state):
 turns=history['turns']
 if state.get('turn_id'):
  matches=[t for t in turns if t['id']==state['turn_id']]
 else:
  # Turn-start acknowledgement was lost: recover only the existing marked turn.
  matches=[t for t in turns if any(state['marker'] in json.dumps(i) for i in t.get('items',[]) if i.get('type')=='userMessage')]
  if len(matches)==1:state['turn_id']=matches[0]['id']
 if len(matches)!=1:raise RuntimeError('AI turn not recoverable; manual review required')
 turn=matches[0]
 if turn['status'] not in {'completed','inProgress'}:raise RuntimeError('AI turn failed')
 if turn['status']!='completed':return None
 finals=[i for i in turn.get('items',[]) if i.get('type')=='agentMessage' and i.get('phase')=='final_answer' and i.get('text','').strip()]
 if not finals:raise RuntimeError('AI final answer missing')
 return finals[-1]['text']


async def generate(snapshot_path):
 from anc_automation.daily import DailyNative,load
 snapshot=read_json(snapshot_path);day=snapshot['day'];digest=fingerprint(snapshot)
 path=ROOT/'state'/('ai-'+day+'.json');state=read_json(path,{})
 action=generation_action(state,digest)
 eligible=snapshot['eligible']
 if action=='cached':
  render(state['raw'],eligible);daily_attachments(state['raw'],eligible);details(state['raw']);detail_payload(state['raw']);return state
 config=load('/home/ubuntu/work/anc-automation/config/brand-daily.json','biceek')
 async with DailyNative(BUSINESS) as sdk:
  if action=='start':
   state={'status':'starting','fingerprint':digest,'marker':'BICEEK_SHORT_AI_'+day+'_'+digest[:16]}
   atomic(path,state)
   thread=await sdk.persistent_daily(config,'short-'+day)
   state.update(thread_id=thread.id,status='thread_ready');atomic(path,state)
   legacy=Path(config['prompt']).read_text()
   prompt='Legacy Biceek long-report workflow guidance for the detailed thread analysis:\n'+legacy+'\n\nCURRENT AUTHORIZED TWO-PART OUTPUT (overrides legacy presentation/delivery):\n'+(ROOT/'scheduled_ai_prompt.md').read_text()+'\nAnalysis marker: '+state['marker']+'\nVerified snapshot file: '+str(snapshot_path)+'\nReport day: '+day+'\nEligible changes (only these may be highlighted):\n'+json.dumps(eligible)+'\n'
   state['status']='running';atomic(path,state) # Before turn-start boundary.
   turn=await sdk.start_job(thread.id,prompt,config['model'],config['effort'],config['service_tier'])
   state['turn_id']=turn.id;atomic(path,state)
   async with asyncio.timeout(1500):
    async for event in turn.stream():
     if event.method=='turn/completed':break
  else:
   # Never start another turn on retry, even if no turn_id was acknowledged.
   await sdk._ensure_initialized()
  for _ in range(100):
   raw=final_from(await sdk.history(state['thread_id']),state);atomic(path,state)
   if raw is not None:break
   await asyncio.sleep(10)
  else:raise RuntimeError('AI still running; retry reconciliation only')
  analysis=render(raw,eligible);daily_attachments(raw,eligible);long_analysis=details(raw);detail_payload(raw)
  state.update(status='complete',raw=raw,analysis=analysis,details=long_analysis);atomic(path,state)
  return state


if __name__=='__main__':
 os.umask(0o077)
 try:
  result=asyncio.run(generate(Path(sys.argv[1])))
  print(json.dumps({'status':result['status'],'thread_id':result['thread_id'],'turn_id':result['turn_id']}))
 except Exception as exc:
  print(json.dumps({'status':'failed','error':type(exc).__name__}),flush=True);raise SystemExit(1)
