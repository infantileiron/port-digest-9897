import os
from datetime import datetime,timedelta,timezone
from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified
from src.models.entities import Arrival,SyncDrillRun,SyncSubscription
from src.services import changelog,sync
from src.services.business import dump
from src.utils.time import now_utc,SH

STEPS=('station_out','revise_twice','new_and_late','station_back')
STEP_LABELS={'station_out':'改站移出北站','revise_twice':'同一班次连改两次','new_and_late':'增量补录与新班次','station_back':'改站移回北站'}
ACTIONS=('sync_one','sync_all','crash_after_apply','resume','reconnect','offline','report','reset_ledger')

def _code(run,n):return f'DR{run.id}-{n}'
def _at(day,hh,mm):return datetime.combine(day,datetime.min.time(),tzinfo=SH)+timedelta(hours=hh,minutes=mm)
def _utc(d):return d.astimezone(timezone.utc).isoformat()
def safe_dump(run):
 d=dump(run);summary={k:v for k,v in (d.get('summary') or {}).items() if k!='receivers'}
 summary['receivers']=[{k:v for k,v in r.items() if k!='token'} for r in (d.get('summary') or {}).get('receivers',[])]
 d['summary']=summary
 d['summary']['step_labels']=STEP_LABELS
 return d

def setup(s,actor,receivers=('快端A','慢端B')):
 run=SyncDrillRun(actor=actor,created_at=now_utc().isoformat(),status='running',summary={})
 s.add(run);s.flush()
 dataset=f'drill{run.id}';day=now_utc().astimezone(SH).date()-timedelta(days=2)
 seed=[('001','北站',_at(day,9,10),None,'演练：待改站'),('002','北站',_at(day,10,20),None,'演练：待多次修订'),
       ('003','北站',_at(day,11,30),None,'演练：待补实际'),('004','南站',_at(day,12,40),None,'演练：南站基线'),
       ('005','东港',_at(day,13,50),None,'演练：东港基线')]
 for n,station,planned,actual,note in seed:
  s.add(Arrival(code=_code(run,n),station=station,planned_at=_utc(planned),actual_at=actual and _utc(actual),note=note,dataset=dataset))
 s.flush();changelog.backfill_changes(s)
 subs=[]
 base=os.getenv('RECEIVER_BASE_URL','http://127.0.0.1:8000').rstrip('/')
 for label in receivers:
  token=sync.new_token()
  sub=SyncSubscription(name=f'演练{run.id}·{label}',station='北站',receiver=label,dataset=dataset,enabled=True,
   token_hash=sync.hash_token(token),generation=1,cursor_seq=0,bootstrapped=False,created_at=now_utc().isoformat())
  s.add(sub);s.flush()
  subs.append({'id':sub.id,'subscription_id':sub.id,'name':sub.name,'receiver':label,'station':'北站','token':token,
   'db':os.path.join(os.getenv('SYNC_DRILL_DIR','/tmp/port-digest-drills'),f'{run.id}-{label}.sqlite'),'base':base,'offline':False})
 run.summary={'dataset':dataset,'receivers':subs,'steps':[],'actions':[]}
 flag_modified(run,'summary');s.commit()
 return safe_dump(run)

def _arrival(s,run,n):
 a=s.query(Arrival).filter(Arrival.code==_code(run,n)).one_or_none()
 if not a:raise HTTPException(409,'演练数据缺失：'+_code(run,n))
 return a

def revise(s,run,n,changes,actor):
 changelog.serialize_changes(s)
 a=_arrival(s,run,n);old=a.station
 for k,v in changes.items():setattr(a,k,v)
 s.flush();rev=changelog.next_revision(s,a.id,a.code)
 changelog.record_change(s,a,old,rev,actor,a.dataset)
 return a,rev

def step(s,run_id,name,actor):
 run=s.get(SyncDrillRun,run_id)
 if not run:raise HTTPException(404,'演练不存在')
 summary=dict(run.summary or {});done=[x['step'] for x in summary.get('steps',[])]
 if name in done:return {'drill':safe_dump(run),'already':True}
 if name not in STEPS:raise HTTPException(400,'未知演练步骤')
 before=changelog.max_seq(s,summary['dataset']);day=now_utc().astimezone(SH).date()-timedelta(days=2)
 if name=='station_out':
  revise(s,run,'001',{'station':'南站'},actor);note='DR-001 北站→南站：北站应撤回，南站应新增'
 elif name=='revise_twice':
  revise(s,run,'002',{'actual_at':_utc(_at(day,10,35))},actor)
  revise(s,run,'002',{'note':'演练：第二次修订覆盖第一次'},actor);note='DR-002 连续两次修订，按先后顺序后到生效'
 elif name=='new_and_late':
  a,rev=revise(s,run,'003',{'actual_at':_utc(_at(day,12,0))},actor)
  changelog.serialize_changes(s)
  s.add(Arrival(code=_code(run,'006'),station='北站',planned_at=_utc(_at(day,15,10)),actual_at=None,note='演练：增量期间新班次',dataset=summary['dataset']))
  s.flush();changelog.backfill_changes(s);note='DR-003 补实际；新增 DR-006，均为增量事件'
 elif name=='station_back':
  revise(s,run,'001',{'station':'北站'},actor);note='DR-001 南站→北站：改站往返，回到北站台账'
 summary.setdefault('steps',[]).append({'step':name,'label':STEP_LABELS[name],'seq_from':before+1,'seq_to':changelog.max_seq(s,summary['dataset']),'note':note,'at':now_utc().isoformat()})
 run.summary=summary;flag_modified(run,'summary');s.commit()
 return {'drill':safe_dump(run),'already':False}

def _receiver(summary,key):
 rec=next((r for r in summary.get('receivers',[]) if str(r['id'])==str(key) or r['receiver']==key),None)
 if not rec:raise HTTPException(404,'演练接收端不存在：'+str(key))
 return rec

def _engine(rec):
 from receiver.engine import Receiver
 os.makedirs(os.path.dirname(rec['db']),exist_ok=True)
 return Receiver(rec['base'],rec['token'],rec['db'])

def act(s,run_id,key,action,actor):
 run=s.get(SyncDrillRun,run_id)
 if not run:raise HTTPException(404,'演练不存在')
 summary=dict(run.summary or {});rec=dict(_receiver(summary,key));label=rec['receiver']
 if action not in ACTIONS:raise HTTPException(400,'未知接收端动作')
 if action in ('offline','reconnect'):
  rec['offline']=action=='offline'
  summary['receivers']=[rec if r['id']==rec['id'] else r for r in summary['receivers']]
  run.summary=summary;flag_modified(run,'summary');s.commit();return {'receiver':label,'action':action,'offline':rec['offline']}
 if rec.get('offline'):raise HTTPException(409,label+' 当前断连，先执行重连')
 engine=_engine(rec);result={}
 try:
  if action=='sync_one':result=engine.sync(count=1,limit=1)
  elif action=='sync_all':result=engine.sync(count=10000,limit=50)
  elif action=='crash_after_apply':
   result=engine.sync(count=1,limit=1,crash_after_apply=True)
  elif action=='resume':result=engine.resume()
  elif action=='report':
   result=engine.report('演练对账')
  elif action=='reset_ledger':result=engine.rebuild()
 except Exception as e:
  raise HTTPException(502,'接收端连接失败：'+str(e)) from e
 summary.setdefault('actions',[]).append({'receiver':label,'action':action,'result':result,'at':now_utc().isoformat()})
 run.summary=summary;flag_modified(run,'summary');s.commit()
 return {'receiver':label,'action':action,'result':result}

def check(s,run_id):
 run=s.get(SyncDrillRun,run_id)
 if not run:raise HTTPException(404,'演练不存在')
 summary=dict(run.summary or {})
 out=[]
 for rec in summary.get('receivers',[]):
  sub=s.get(SyncSubscription,rec['id'])
  if not sub:continue
  p=sync.progress(s,sub.id)
  local={};eng=None
  try:
   from receiver.engine import Receiver
   eng=Receiver(rec['base'],rec['token'],rec['db'])
   local={'applied_seq':eng.store.applied_seq(),'confirmed_seq':eng.store.confirmed_seq(),'records':eng.store.count(),'ledger_batches':[{'id':b['id'],'status':b['status'],'seq_to':b['seq_to']} for b in eng.store.batches()][-8:]}
  except Exception as e:local={'error':str(e)}
  diff=p['diff']
  if eng is not None and local.get('records') is not None:
   try:diff=sync.diff_against(s,sub.id,local['applied_seq'],eng.store.all_records())
   except Exception:pass
  out.append({'receiver':rec['receiver'],'subscription_id':sub.id,'offline':bool(rec.get('offline')),'cursor_seq':p['cursor_seq'],'head_seq':p['head_seq'],'lag':p['lag'],'up_to_date':p['up_to_date'],'bootstrapped':p['bootstrapped'],'records':p['station_count'],'pending_batches':p['pending_batches'],'diff':diff,'report':p['report'],'ledger':local,'db':rec['db']})
 summary=dict(run.summary or {})
 steps=[{**st,'server_events':st.get('seq_to',0)-st.get('seq_from',0)+1} for st in summary.get('steps',[])]
 return {'drill':safe_dump(run),'receivers':out,'timeline':steps}
