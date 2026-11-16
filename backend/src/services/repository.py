from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Arrival,Subscription,Receipt
from src.services.business import dump
from src.services import changelog
from src.utils.time import now_utc,next_due

def arrival_rows(s):return [dump(r) for r in s.query(Arrival).filter(Arrival.dataset=='live').order_by(Arrival.planned_at,Arrival.id).all()]
def save_arrival(s,data,i=None,actor=''):
 changelog.serialize_changes(s)
 r=s.get(Arrival,i) if i else Arrival()
 if not r:raise HTTPException(404,'到发记录不存在')
 old_station=r.station if i else None
 for k,v in data.model_dump().items():setattr(r,k,v)
 s.add(r)
 try:
  s.flush()
  revision=changelog.next_revision(s,r.id if i else None,r.code)
  changelog.record_change(s,r,old_station,revision,actor,r.dataset)
  s.commit()
 except IntegrityError:
  s.rollback();raise HTTPException(409,'班次编号重复')
 return r

def save_subscription(s,data,i=None):
 r=s.get(Subscription,i) if i else Subscription();new=i is None
 if not r:raise HTTPException(404,'订阅不存在')
 values=data.model_dump();first=values.pop('first_due_at')
 for k,v in values.items():setattr(r,k,v)
 # Explicit edit schedules the next future run; an initial override is for a first/catch-up delivery.
 r.next_run_at=first or next_due(r.daily_at,now_utc()).isoformat()
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'订阅名称重复')
 return r

def get_receipt(s,i):
 r=s.get(Receipt,i)
 if not r:raise HTTPException(404,'回执不存在')
 return r
