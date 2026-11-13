"""Validated numbers and short remote-AI explanations. No model prose fallback."""
import hashlib,json,re
from decimal import Decimal


def fingerprint(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def generation_action(state,digest):
 if not state:return 'start'
 if state.get('fingerprint')!=digest:raise RuntimeError('AI input changed; manual review required')
 if state.get('status')=='complete':return 'cached'
 if state.get('status')=='starting' or not state.get('thread_id'):raise RuntimeError('Unknown AI start; manual review required')
 return 'reconcile'


def candidates(current,previous):
 if previous is None:return {}
 def val(r,k):
  if k=='roas':a,b=r.get('revenue'),r.get('spend')
  elif k.endswith('_roas'):a,b=r.get(k[:-5]+'_value'),r.get(k[:-5]+'_spend')
  else:return r.get(k)
  return a/b if a is not None and b is not None and b>0 else None
 result={}
 for key,label in [('spend','Spend'),('revenue','Revenue'),('orders','Orders'),('roas','Overall ROAS'),('google_spend','Google Spend'),('google_roas','Google ROAS'),('meta_spend','Meta Spend'),('meta_roas','Meta ROAS')]:
  a,b=val(current,key),val(previous,key)
  if a is None or b is None or a==b:continue
  change=(a/b-1)*100 if b>0 else None
  if b!=0 and (change is None or abs(change)<15):continue
  if key=='orders' and min(a,b)<10:desc=f'{b:.0f} → {a:.0f} ({a-b:+.0f}; small base)'
  elif b==0:
   display=lambda v:f'{v:.2f}' if key.endswith('roas') else f'$'+format(v,',.2f')
   desc=f'{display(b)} → {display(a)} (from zero)'
  else:desc=f'{change:+.1f}%'
  result[key]={'label':label,'change':desc,'before':str(b),'after':str(a)}
 return result


def render(raw,eligible):
 try:
  data=json.loads(raw)
  changes=data['changes']
 except (ValueError,KeyError,TypeError):raise ValueError('Invalid AI analysis JSON') from None
 if not isinstance(changes,list) or (eligible and not changes):raise ValueError('AI highlight count invalid')
 seen=set();lines=[]
 for item in changes:
  if not isinstance(item,dict):raise ValueError('Invalid AI highlight')
  if 'metric' in item and 'metrics' in item:raise ValueError('Ambiguous AI metric group')
  keys=item.get('metrics',[item.get('metric')]);reason=item.get('reason')
  if not isinstance(keys,list) or not keys or any(not isinstance(k,str) for k in keys):raise ValueError('Invalid AI metric group')
  if len(set(keys))!=len(keys) or any(k not in eligible or k in seen for k in keys):raise ValueError('AI selected ineligible change')
  if not isinstance(reason,str) or not reason.strip() or len(reason.split())>22 or len(reason)>220 or '\n' in reason or re.search(r'[.!?]\s+\S',reason) or re.search(r'https?://|<@|<!|```',reason):raise ValueError('AI explanation must be short plain English')
  seen.update(keys)
  label='; '.join(f"{eligible[k]['label']} {eligible[k]['change']}" for k in keys)
  lines.append(f"• *{label}:* {explanation(item)}")
 if seen!=set(eligible):raise ValueError('AI omitted eligible changes')
 if not lines:lines=['• No core metric changed by 15% or more.']
 return '\n'.join(lines)


def details(raw):
 try:
  data=json.loads(raw);text=data['details']
 except (ValueError,KeyError,TypeError):raise ValueError('Missing AI detailed analysis') from None
 if not isinstance(text,str) or not text.strip() or len(text)>24000 or re.search(r'<!|<@',text):raise ValueError('Invalid AI detailed analysis')
 if data.get('format_version') in (3,4) and (len(text)>3300 or re.search(r'https?://|(?im:^\s*[#*]*(?:Sources|References)\b)',text)):raise ValueError('AI detail must be one compact comment without sources')
 return text.strip()


def colored_attachments(raw,eligible):
 """Slack color bars plus inline reasons; spend is attention unless AI verifies impact."""
 render(raw,eligible)
 items=json.loads(raw)['changes'];result=[]
 for item in items:
  keys=item.get('metrics',[item.get('metric')])
  impacts=[]
  for key in keys:
   if key.endswith('spend'):
    impact=item.get('impact','attention')
    if impact not in {'positive','negative','attention'}:raise ValueError('Invalid AI business impact')
   else:
    impact='positive' if Decimal(eligible[key]['after'])>Decimal(eligible[key]['before']) else 'negative'
   impacts.append(impact)
  impact=impacts[0] if len(set(impacts))==1 else 'attention'
  color,emoji={'positive':('#2EB67D','🟢'),'negative':('#E01E5A','🔴'),'attention':('#ECB22E','🟠')}[impact]
  label='; '.join(f"{eligible[k]['label']} {eligible[k]['change']}" for k in keys)
  text=f"{emoji} *{label}:* {explanation(item)}"
  result.append({'color':color,'text':text,'fallback':text.replace('*',''),'mrkdwn_in':['text']})
 return result


def explanation(item):
 reason=item['reason'].strip()
 if 'next_step' not in item:return reason
 action=item['next_step']
 if not isinstance(action,str) or not action.strip() or len(action.split())>14 or len(action)>160 or re.search(r'\n|https?://|<@|<!|```',action):raise ValueError('Invalid AI next step')
 return f"*Suggested cause:* {reason} *Next step:* {action.strip()}"


def daily_attachments(raw,eligible):
 data=json.loads(raw)
 attachments=colored_attachments(raw,eligible)
 if data.get('format_version') not in (3,4):return attachments
 details(raw)
 product=data.get('product_update')
 if not isinstance(product,str) or not product.strip() or len(product.split())>45 or re.search(r'\n|https?://|<@|<!|```',product):raise ValueError('Invalid AI business progress')
 for item in data['changes']:
  if not item.get('next_step'):raise ValueError('Missing AI next step')
 if not attachments:attachments.append({'color':'#ECB22E','text':'No core metric changed by 15% or more.','fallback':'No core metric changed by 15% or more.'})
 attachments.append({'color':'#36C5F0','text':'*Product & Business Update*\n'+product.strip(),'fallback':product.strip(),'mrkdwn_in':['text']})
 return attachments


def detail_payload(raw):
 data=json.loads(raw);note=details(raw)
 if data.get('format_version')!=4:return {'text':note}
 tables=data.get('detail_tables')
 if not isinstance(tables,list) or not 1<=len(tables)<=8:raise ValueError('Invalid AI detail tables')
 blocks=[];fallback=[];characters=0
 for table in tables:
  title=table.get('title');headers=table.get('headers');rows=table.get('rows')
  if not isinstance(title,str) or not 0<len(title)<=120 or not isinstance(headers,list) or not 2<=len(headers)<=20 or not isinstance(rows,list) or not 1<=len(rows)<=99:raise ValueError('Invalid AI table shape')
  cells=[headers]+rows
  if any(not isinstance(row,list) or len(row)!=len(headers) for row in cells):raise ValueError('Inconsistent AI table columns')
  if any(not isinstance(cell,str) or not cell.strip() or len(cell)>200 or re.search(r'\n|https?://|<!|<@|```',cell) for row in cells for cell in row):raise ValueError('Invalid AI table cell')
  characters+=sum(len(cell) for row in cells for cell in row)
  blocks.append({'type':'section','text':{'type':'mrkdwn','text':'*'+title+'*'}})
  blocks.append({'type':'table','column_settings':[{'is_wrapped':False,'align':'left' if i==0 else 'right'} for i in range(len(headers))],'rows':[[{'type':'raw_text','text':cell} for cell in row] for row in cells]})
  fallback.append(title+'\n'+'\n'.join(' | '.join(row) for row in cells))
 if characters>10000:raise ValueError('AI tables exceed Slack limit')
 blocks.append({'type':'section','text':{'type':'mrkdwn','text':note}})
 return {'text':'\n\n'.join(fallback)+'\n\n'+note,'blocks':blocks}
