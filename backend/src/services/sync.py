import hashlib,json,os,secrets
from fastapi import HTTPException
from src.models.entities import SyncBatch,SyncReport,SyncSubscription
from src.services.business import dump
from src.services import changelog
from src.utils.time import now_utc
MAX_EVENTS=int(os.getenv('SYNC_MAX_EVENTS','100'))
MAX_BYTES=int(os.getenv('SYNC_MAX_BYTES','131072'))
PULL_LIMIT=int(os.getenv('SYNC_PULL_LIMIT','20'))
PENDING='pending';CONFIRMED='confirmed';SUPERSEDED='superseded'
CORE=changelog.CORE

def hash_token(t):return hashlib.sha256(t.encode()).hexdigest()
def new_token():return 'psync_'+secrets.token_urlsafe(24)
def event_checksum(events):
 return hashlib.sha256(json.dumps(events,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def _chunks(events):
 out=[];cur=[];size=0
 for e in events:
  b=len(json.dumps(e,ensure_ascii=False,separators=(',',':')))
  if cur and (len(cur)>=MAX_EVENTS or size+b>MAX_BYTES):out.append(cur);cur=[];size=0
  cur.append(e);size+=b
 if cur:out.append(cur)
 return out
def sub_row(s,i,lock=False):
 q=s.query(SyncSubscription).filter(SyncSubscription.id==i)
 if lock:q=q.with_for_update()
 r=q.one_or_none()
 if not r:raise HTTPException(404,'同步订阅不存在')
 return r
def batch_row(s,i,lock=False):
 q=s.query(SyncBatch).filter(SyncBatch.id==i)
 if lock:q=q.with_for_update()
 r=q.one_or_none()
 if not r:raise HTTPException(404,'同步批次不存在')
 return r
def envelope(b):
 return dict(id=b.id,subscription_id=b.subscription_id,kind=b.kind,seq_from=b.seq_from,seq_to=b.seq_to,checksum=b.checksum,status=b.status,created_at=b.created_at,confirmed_at=b.confirmed_at,events=b.events or [])
def public_sub(r):
 d=dump(r);d.pop('token_hash',None);return d

def _chained(s,sub,kind,start,end,chunks,now):
 """Chunk ranges stay contiguous so a receiver can walk them strictly in order."""
 seq=start;created=[]
 for i,chunk in enumerate(chunks):
  last=chunk[-1]['seq'] if chunk else seq-1
  if i==len(chunks)-1:last=end
  last=max(last,seq-1)
  b=SyncBatch(subscription_id=sub.id,kind=kind,seq_from=seq,seq_to=last,events=chunk,checksum=event_checksum(chunk),status=PENDING,created_at=now.isoformat())
  s.add(b);s.flush();created.append(b);seq=last+1
 return created

def _create_snapshot(s,sub,head,now):
 """One frozen station snapshot at head; later revisions arrive as increments."""
 sub.bootstrap_to=head
 events=changelog.snapshot_events(s,sub.station,head,sub.dataset)
 return _chained(s,sub,'snapshot',0,head,_chunks(events) or [[]],now)

def _create_incremental(s,sub,covered,head,now,kind='incremental'):
 events=changelog.events_between(s,sub.station,covered,head,sub.dataset)
 return _chained(s,sub,kind,covered+1,head,_chunks(events) or [[]],now)

def _supersede(s,sub,rows):
 for b in rows:b.status=SUPERSEDED

def pull(s,sub_id,limit=PULL_LIMIT):
 changelog.serialize_changes(s)
 sub=sub_row(s,sub_id,lock=True);now=now_utc()
 if not sub.enabled:raise HTTPException(409,'同步订阅已暂停')
 head=changelog.max_seq(s,sub.dataset)
 pending=s.query(SyncBatch).filter(SyncBatch.subscription_id==sub.id,SyncBatch.status==PENDING).order_by(SyncBatch.seq_from,SyncBatch.id).all()
 if not sub.bootstrapped:
  # Bootstrap chain is a contiguous run of snapshot batches: 0..bootstrap_to.
  # Once a prefix is confirmed the cursor advances inside that same run, so the
  # remaining run starts at cursor_seq+1 rather than restarting from 0.
  expected=0 if sub.cursor_seq==0 else sub.cursor_seq+1
  valid=[]
  for b in pending:
   if b.kind!='snapshot' or b.seq_from!=expected:
    b.status=SUPERSEDED;continue
   valid.append(b);expected=b.seq_to+1
  covered=valid[-1].seq_to if valid else (None if sub.cursor_seq==0 else sub.cursor_seq)
  if covered is None:
   _supersede(s,sub,pending);valid=_create_snapshot(s,sub,head,now)
  elif covered<head:
   # Freeze the base at the original watermark; later edits replay as ordered events.
   if sub.bootstrap_to is None:sub.bootstrap_to=head
   else:sub.bootstrap_to=max(sub.bootstrap_to,head)
   valid+=_create_incremental(s,sub,covered,head,now,kind='snapshot')
 else:
  expected=sub.cursor_seq+1;valid=[]
  for b in pending:
   if b.seq_from!=expected:
    b.status=SUPERSEDED;continue
   valid.append(b);expected=b.seq_to+1
  covered=valid[-1].seq_to if valid else sub.cursor_seq
  if covered<head:valid+=_create_incremental(s,sub,covered,head,now)
 s.flush()
 pending=s.query(SyncBatch).filter(SyncBatch.subscription_id==sub.id,SyncBatch.status==PENDING).order_by(SyncBatch.seq_from,SyncBatch.id).all()
 sub.last_pull_at=now.isoformat();s.commit()
 rows=pending[:limit]
 return dict(subscription=public_sub(sub),station=sub.station,generation=sub.generation,head_seq=head,cursor_seq=sub.cursor_seq,bootstrapped=sub.bootstrapped,bootstrap_to=sub.bootstrap_to,up_to_date=sub.cursor_seq>=head and not pending,has_more=len(pending)>len(rows),batches=[envelope(b) for b in rows])

def confirm(s,batch_id,checksum,sub_id=None):
 b0=batch_row(s,batch_id)
 if sub_id is not None and b0.subscription_id!=sub_id:raise HTTPException(403,'该批次不属于此接收端订阅')
 sub=sub_row(s,b0.subscription_id,lock=True);b=batch_row(s,batch_id,lock=True)
 now=now_utc()
 if b.checksum!=checksum:raise HTTPException(409,'批次内容校验不一致，请重新拉取原内容')
 if b.status==SUPERSEDED:raise HTTPException(409,'批次已被重置，请按当前游标重新拉取')
 if b.status==CONFIRMED:return dict(batch=envelope(b),subscription=public_sub(sub),duplicate=True,advanced=False)
 if b.seq_from>sub.cursor_seq+1:raise HTTPException(409,'存在更早的待确认批次，请按顺序确认')
 b.status=CONFIRMED;b.confirmed_at=now.isoformat()
 advanced=b.seq_to>sub.cursor_seq
 if advanced:
  sub.cursor_seq=b.seq_to;sub.last_confirm_at=now.isoformat()
  # Any earlier batch the receiver skipped past is covered by this confirmation.
  for x in s.query(SyncBatch).filter(SyncBatch.subscription_id==sub.id,SyncBatch.status==PENDING,SyncBatch.seq_to<=sub.cursor_seq).all():
   x.status=CONFIRMED;x.confirmed_at=now.isoformat()
 if b.kind=='snapshot' and sub.bootstrap_to is not None and sub.cursor_seq>=sub.bootstrap_to:sub.bootstrapped=True
 s.commit()
 return dict(batch=envelope(b),subscription=public_sub(sub),duplicate=False,advanced=advanced)

def reset(s,sub_id,reason=''):
 """Rebuild from a fresh snapshot. The generation bump tells receivers to wipe their
 local ledger first, otherwise records that left the station would linger there."""
 sub=sub_row(s,sub_id,lock=True)
 for b in s.query(SyncBatch).filter(SyncBatch.subscription_id==sub.id,SyncBatch.status==PENDING).all():b.status=SUPERSEDED
 sub.cursor_seq=0;sub.bootstrapped=False;sub.bootstrap_to=None;sub.generation=(sub.generation or 1)+1
 s.commit();return public_sub(sub)

def _fields(r):return {k:r.get(k) for k in CORE}|{'revision':r.get('revision')}
def _compare(server,records):
 by_id={r['id']:r for r in records}
 missing=[i for i,p in server.items() if i not in by_id]
 extra=[i for i in by_id if i not in server]
 changed=[i for i,p in server.items() if i in by_id and _fields(by_id[i])!=_fields(p)]
 details=[]
 for i in sorted(missing):details.append({'id':i,'code':server[i].get('code'),'problem':'接收端缺失','fields':[]})
 for i in sorted(extra):details.append({'id':i,'code':by_id[i].get('code'),'problem':'接收端多出（应撤回）','fields':[]})
 for i in sorted(changed):
  fields=[k for k in CORE if by_id[i].get(k)!=server[i].get(k)]
  if by_id[i].get('revision')!=server[i].get('revision'):fields.append('revision')
  details.append({'id':i,'code':server[i].get('code'),'problem':'字段不一致','fields':fields})
 return sorted(missing),sorted(extra),sorted(changed),details

def _latest_report(s,sub):
 return s.query(SyncReport).filter(SyncReport.subscription_id==sub.id,SyncReport.generation==(sub.generation or 1)).order_by(SyncReport.id.desc()).first()

def diff(s,sub_id,report=None):
 sub=sub_row(s,sub_id)
 if report is None:report=_latest_report(s,sub)
 if not report:
  server=changelog.station_state(s,sub.station,sub.cursor_seq,sub.dataset)
  return dict(subscription_id=sub.id,station=sub.station,applied_seq=None,confirmed_seq=None,server_seq=sub.cursor_seq,in_sync=False,status='unreported',server_count=len(server),receiver_count=0,missing=[],extra=[],changed=[],details=[],checked_at=now_utc().isoformat())
 server=changelog.station_state(s,sub.station,report.applied_seq,sub.dataset)
 missing,extra,changed,details=_compare(server,report.records or [])
 ok=not(missing or extra or changed)
 return dict(subscription_id=sub.id,station=sub.station,applied_seq=report.applied_seq,confirmed_seq=report.confirmed_seq,server_seq=report.applied_seq,in_sync=ok,status='ok' if ok else 'mismatch',server_count=len(server),receiver_count=len(report.records or []),missing=missing,extra=extra,changed=changed,details=details,checked_at=now_utc().isoformat())

def diff_against(s,sub_id,applied_seq,records):
 """Compare a receiver's own ledger against the server state at its applied watermark."""
 sub=sub_row(s,sub_id)
 server=changelog.station_state(s,sub.station,applied_seq,sub.dataset)
 missing,extra,changed,details=_compare(server,records)
 ok=not(missing or extra or changed)
 return dict(subscription_id=sub.id,station=sub.station,applied_seq=applied_seq,server_seq=applied_seq,in_sync=ok,status='ok' if ok else 'mismatch',server_count=len(server),receiver_count=len(records),missing=missing,extra=extra,changed=changed,details=details,checked_at=now_utc().isoformat())

def upload_report(s,sub_id,data):
 sub=sub_row(s,sub_id,lock=True);head=changelog.max_seq(s,sub.dataset)
 if data.applied_seq>head:raise HTTPException(409,'applied_seq超过服务端当前序号 '+str(head))
 if data.confirmed_seq>data.applied_seq:raise HTTPException(409,'confirmed_seq不能大于applied_seq')
 if data.applied_seq<sub.cursor_seq:raise HTTPException(409,'applied_seq低于服务端已确认游标，请重新拉取')
 if data.confirmed_seq!=sub.cursor_seq:raise HTTPException(409,'confirmed_seq须等于服务端已确认游标 '+str(sub.cursor_seq))
 corrupt=[r for r in data.records if changelog.checksum(r.model_dump())!=r.checksum]
 server=changelog.station_state(s,sub.station,data.applied_seq,sub.dataset)
 missing,extra,changed,details=_compare(server,[r.model_dump() for r in data.records])
 seen={d['id'] for d in details}
 for r in corrupt:
  if r.id not in seen:details.append({'id':r.id,'code':r.code,'problem':'本端记录校验失败（可能被改写）','fields':['checksum']})
  changed=sorted(set(changed)|{r.id})
 now=now_utc();ok=not(missing or extra or changed)
 report=SyncReport(subscription_id=sub.id,generation=sub.generation or 1,applied_seq=data.applied_seq,confirmed_seq=data.confirmed_seq,record_count=len(data.records),records=[r.model_dump() for r in data.records],note=data.note,reported_at=now.isoformat())
 s.add(report);sub.last_report_at=now.isoformat();s.flush()
 result=dict(report_id=report.id,in_sync=ok,status='ok' if ok else 'mismatch',missing=missing,extra=extra,changed=changed,details=details,server_count=len(server),receiver_count=len(data.records),applied_seq=data.applied_seq,confirmed_seq=data.confirmed_seq,checked_at=now.isoformat())
 s.commit();return result

def progress(s,sub_id):
 sub=sub_row(s,sub_id);head=changelog.max_seq(s,sub.dataset)
 pending=s.query(SyncBatch).filter(SyncBatch.subscription_id==sub.id,SyncBatch.status==PENDING).order_by(SyncBatch.seq_from).all()
 report=_latest_report(s,sub)
 return dict(subscription=public_sub(sub),head_seq=head,cursor_seq=sub.cursor_seq,lag=head-sub.cursor_seq,bootstrapped=sub.bootstrapped,bootstrap_to=sub.bootstrap_to,up_to_date=sub.cursor_seq>=head and not pending,pending_batches=[{'id':b.id,'kind':b.kind,'seq_from':b.seq_from,'seq_to':b.seq_to,'event_count':len(b.events or []),'created_at':b.created_at} for b in pending],station_count=len(changelog.station_state(s,sub.station,sub.cursor_seq,sub.dataset)),report=None if not report else {'id':report.id,'applied_seq':report.applied_seq,'confirmed_seq':report.confirmed_seq,'record_count':report.record_count,'reported_at':report.reported_at,'note':report.note},diff=diff(s,sub.id,report))
