from fastapi import APIRouter,Depends,Header,HTTPException
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.middlewares.auth import current,admin
from src.models.entities import Audit,SyncBatch,SyncSubscription
from src.schemas.sync import SyncConfirmInput,SyncPullInput,SyncReportInput,SyncSubscriptionInput
from src.services import changelog,drills,sync
from src.services.business import audit,dump
from src.utils.time import now_utc
router=APIRouter()
PENDING=sync.PENDING;SUPERSEDED=sync.SUPERSEDED

def receiver_token(authorization:str=Header(default='')):
 if not authorization.startswith('Bearer '):raise HTTPException(401,'接收端令牌缺失')
 raw=authorization[7:].strip()
 if not raw:raise HTTPException(401,'接收端令牌缺失')
 return raw

def by_token(s,raw,lock=False):
 q=s.query(SyncSubscription).filter(SyncSubscription.token_hash==sync.hash_token(raw))
 if lock:q=q.with_for_update()
 sub=q.one_or_none()
 if not sub:raise HTTPException(401,'接收端令牌无效')
 return sub

# ---- admin/console management ----
@router.post('/sync-subscriptions')
def create_subscription(data:SyncSubscriptionInput,u=Depends(admin),s=Depends(db)):
 token=sync.new_token()
 sub=SyncSubscription(name=data.name,station=data.station,receiver=data.receiver,dataset=changelog.LIVE,enabled=data.enabled,
  token_hash=sync.hash_token(token),generation=1,cursor_seq=0,bootstrapped=False,created_at=now_utc().isoformat())
 s.add(sub)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'同步订阅名称重复')
 audit(s,u,'创建港站增量订阅 '+sub.name)
 out=sync.public_sub(sub);out['token']=token;out['progress']=sync.progress(s,sub.id);return out

@router.get('/sync-subscriptions')
def list_subscriptions(u=Depends(current),s=Depends(db)):
 # Drill subscriptions live in their own dataset and are shown by the drill console.
 return [sync.progress(s,r.id) for r in s.query(SyncSubscription).filter(SyncSubscription.dataset==changelog.LIVE).order_by(SyncSubscription.id).all()]

@router.put('/sync-subscriptions/{i}')
def edit_subscription(i:int,data:SyncSubscriptionInput,u=Depends(admin),s=Depends(db)):
 sub=sync.sub_row(s,i,lock=True);station_changed=data.station!=sub.station
 sub.name=data.name;sub.station=data.station;sub.receiver=data.receiver;sub.enabled=data.enabled
 if station_changed:
  for b in s.query(SyncBatch).filter(SyncBatch.subscription_id==sub.id,SyncBatch.status==PENDING).all():b.status=SUPERSEDED
  sub.cursor_seq=0;sub.bootstrapped=False;sub.bootstrap_to=None;sub.generation=(sub.generation or 1)+1
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'同步订阅名称重复')
 audit(s,u,('改站并重置游标 ' if station_changed else '修订港站增量订阅 ')+sub.name)
 return sync.progress(s,sub.id)

@router.post('/sync-subscriptions/{i}/rotate-token')
def rotate_token(i:int,u=Depends(admin),s=Depends(db)):
 sub=sync.sub_row(s,i,lock=True);token=sync.new_token();sub.token_hash=sync.hash_token(token);s.commit()
 audit(s,u,'轮换接收端令牌 '+sub.name)
 out=sync.public_sub(sub);out['token']=token;return out

@router.post('/sync-subscriptions/{i}/reset')
def reset_cursor(i:int,u=Depends(admin),s=Depends(db)):
 out=sync.reset(s,i);audit(s,u,'重置同步游标 '+out['name']);return out

@router.get('/sync-subscriptions/{i}/progress')
def get_progress(i:int,u=Depends(current),s=Depends(db)):return sync.progress(s,i)

@router.get('/sync-subscriptions/{i}/diff')
def get_diff(i:int,u=Depends(current),s=Depends(db)):return sync.diff(s,i)

# ---- receiver-facing (Bearer receiver token) ----
@router.post('/sync/pull')
def receiver_pull(token:str=Depends(receiver_token),data:SyncPullInput=SyncPullInput(),s=Depends(db)):
 return sync.pull(s,by_token(s,token).id,data.limit)

@router.post('/sync/batches/{i}/confirm')
def receiver_confirm(i:int,data:SyncConfirmInput,token:str=Depends(receiver_token),s=Depends(db)):
 sub=by_token(s,token)
 return sync.confirm(s,i,data.checksum,sub.id)

@router.post('/sync/reports')
def receiver_report(data:SyncReportInput,token:str=Depends(receiver_token),s=Depends(db)):
 sub=by_token(s,token)
 result=sync.upload_report(s,sub.id,data)
 s.add(Audit(actor='receiver:'+sub.receiver,action='接收端对账 '+sub.name+' applied='+str(data.applied_seq)+' '+result['status'],created=now_utc().isoformat()));s.commit()
 result['receiver']=sub.receiver;result['subscription']=sync.public_sub(sub);return result

@router.get('/sync/progress')
def receiver_progress(token:str=Depends(receiver_token),s=Depends(db)):
 return sync.progress(s,by_token(s,token).id)

@router.post('/sync/reset')
def receiver_reset(token:str=Depends(receiver_token),s=Depends(db)):
 sub=by_token(s,token,lock=True)
 out=sync.reset(s,sub.id)
 s.add(Audit(actor='receiver:'+sub.receiver,action='接收端请求重建 '+sub.name,created=now_utc().isoformat()));s.commit()
 return {'subscription':out,'generation':out['generation'],'cursor_seq':out['cursor_seq']}

# ---- drill console ----
@router.get('/sync-drills')
def drills_list(u=Depends(current),s=Depends(db)):
 from src.models.entities import SyncDrillRun
 return [dump(r) for r in s.query(SyncDrillRun).order_by(SyncDrillRun.id.desc()).limit(20).all()]

@router.post('/sync-drills')
def drill_create(u=Depends(admin),s=Depends(db)):
 run=drills.setup(s,u.email);audit(s,u,'创建增量同步演练 '+str(run['id']));return run

@router.post('/sync-drills/{i}/steps/{name}')
def drill_step(i:int,name:str,u=Depends(admin),s=Depends(db)):
 out=drills.step(s,i,name,u.email)
 if not out.get('already'):audit(s,u,'演练步骤 '+name+' / '+str(i))
 return out

@router.post('/sync-drills/{i}/receivers/{key}/actions/{action}')
def drill_action(i:int,key:str,action:str,u=Depends(admin),s=Depends(db)):
 out=drills.act(s,i,key,action,u.email)
 if action not in ('offline','reconnect'):audit(s,u,'演练接收端动作 '+action+' / '+str(i)+' / '+out['receiver'])
 return out

@router.get('/sync-drills/{i}')
def drill_view(i:int,u=Depends(current),s=Depends(db)):return drills.check(s,i)
