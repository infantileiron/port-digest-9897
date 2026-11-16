import hashlib,json
from sqlalchemy import func,text
from src.models.entities import Arrival,ArrivalChange
from src.utils.time import now_utc
CORE=('id','code','station','planned_at','actual_at','note')
LIVE='live'
def checksum(payload):
 core={k:payload.get(k) for k in CORE};core['revision']=payload.get('revision')
 return hashlib.sha256(json.dumps(core,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def payload_for(a,revision):
 p={k:getattr(a,k) for k in CORE};p['revision']=revision;p['checksum']=checksum(p);return p
def serialize_changes(s):
 """Serialize change-log writers so ArrivalChange ids commit in ascending order.
 Without this, a concurrent revision could take a lower id but commit after a snapshot
 watermark, leaving a hole that incremental readers would never see."""
 if s.bind.dialect.name=='postgresql':s.execute(text('SELECT pg_advisory_xact_lock(3113)'))
def next_revision(s,arrival_id,code):
 column=ArrivalChange.arrival_id if arrival_id else ArrivalChange.code
 return int(s.query(func.max(ArrivalChange.revision)).filter(column==(arrival_id or code)).scalar() or 0)+1
def record_change(s,a,old_station,revision,actor,dataset=None):
 p=payload_for(a,revision)
 s.add(ArrivalChange(arrival_id=a.id,code=a.code,revision=revision,old_station=old_station,new_station=a.station,payload=p,changed_at=now_utc().isoformat(),actor=actor or '',dataset=dataset or getattr(a,'dataset',LIVE) or LIVE))
def backfill_changes(s):
 """One baseline event per pre-existing arrival so the first subscription has a full log."""
 serialize_changes(s)
 logged={r[0] for r in s.query(ArrivalChange.arrival_id).distinct().all()}
 added=0
 for a in s.query(Arrival).order_by(Arrival.id).all():
  if a.id not in logged:
   record_change(s,a,None,1,'baseline',a.dataset)
   added+=1
 if added:s.commit()
 return added
def max_seq(s,dataset=LIVE):
 return int(s.query(func.max(ArrivalChange.id)).filter(ArrivalChange.dataset==dataset).scalar() or 0)
def latest_rows(s,station,upto,dataset=LIVE):
 sub=s.query(func.max(ArrivalChange.id).label('id')).filter(ArrivalChange.dataset==dataset,ArrivalChange.id<=upto).group_by(ArrivalChange.arrival_id).subquery()
 return s.query(ArrivalChange).join(sub,ArrivalChange.id==sub.c.id).filter(ArrivalChange.new_station==station).order_by(ArrivalChange.id).all()
def station_state(s,station,upto,dataset=LIVE):
 return {r.arrival_id:r.payload for r in latest_rows(s,station,upto,dataset)}
def events_between(s,station,after,upto,dataset=LIVE):
 out=[]
 for c in s.query(ArrivalChange).filter(ArrivalChange.dataset==dataset,ArrivalChange.id>after,ArrivalChange.id<=upto).order_by(ArrivalChange.id).all():
  if c.old_station==station and c.new_station!=station:
   out.append({'seq':c.id,'arrival_id':c.arrival_id,'code':c.code,'revision':c.revision,'type':'retract','payload':None})
  elif c.new_station==station:
   out.append({'seq':c.id,'arrival_id':c.arrival_id,'code':c.code,'revision':c.revision,'type':'upsert','payload':c.payload})
 return out
def snapshot_events(s,station,upto,dataset=LIVE):
 return [{'seq':r.id,'arrival_id':r.arrival_id,'code':r.code,'revision':r.revision,'type':'upsert','payload':r.payload} for r in latest_rows(s,station,upto,dataset)]
