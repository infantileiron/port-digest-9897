"""End-to-end self test for the incremental station feed, driven by a real receiver.

Runs against a live backend (default http://127.0.0.1:19212) using the admin demo
account. It creates throwaway sync subscriptions and an isolated station dataset,
then asserts:

  1. first full sync equals the server station ledger;
  2. a station move retracts from the old station and appears in the new one;
  3. repeated revisions apply in order, latest wins;
  4. a batch re-pulled before confirm has the identical checksum;
  5. crash after apply / before confirm resumes with no loss and no duplicate;
  6. a slow receiver does not block a fast one, and each reports its own progress;
  7. the receiver ledger reconciles with the server at its applied watermark.

Usage:
  python -m scripts.sync_selftest --url http://127.0.0.1:19212
"""
import argparse,json,os,sys,tempfile,time,urllib.error,urllib.request
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from receiver.engine import Receiver
from receiver.client import SyncError

class Api:
 def __init__(self,base,email,password):
  self.base=base.rstrip('/');self.token=None
  self.login(email,password)
 def call(self,method,path,body=None,token=None,auth=True):
  data=None if body is None else json.dumps(body,ensure_ascii=False).encode()
  headers={'Content-Type':'application/json'}
  if auth:headers['Authorization']='Bearer '+(token or self.token)
  req=urllib.request.Request(self.base+path,data=data,method=method,headers=headers)
  try:
   with urllib.request.urlopen(req,timeout=20) as r:return r.status,json.loads(r.read() or b'{}')
  except urllib.error.HTTPError as e:
   return e.code,json.loads(e.read() or b'{}')
 def login(self,email,password):
  st,d=self.call('POST','/auth/login',{'email':email,'password':password},auth=False)
  assert st==200,d;self.token=d['token'];return d

def check(name,cond,detail=''):
 print(('PASS ' if cond else 'FAIL ')+name+((' :: '+str(detail)) if detail and not cond else ''))
 if not cond:raise SystemExit(1)

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--url',default='http://127.0.0.1:19212')
 p.add_argument('--admin',default='admin@demo.local')
 p.add_argument('--analyst',default='analyst@demo.local')
 p.add_argument('--password',default='Demo123!')
 a=p.parse_args()
 admin=Api(a.url,a.admin,a.password);analyst=Api(a.url,a.analyst,a.password)
 stamp=str(int(time.time()))
 day='2026-11-13'
 work=tempfile.mkdtemp(prefix='sync-selftest-')

 def arrival(n,station,planned='09:00',actual=None,note='自检'):
  st,d=analyst.call('POST','/arrivals',{'code':f'ST-{stamp}-{n}','station':station,
   'planned_at':f'{day}T{planned}:00+08:00','actual_at':f'{day}T{actual}:00+08:00' if actual else None,'note':note})
  assert st==200,d;return d

 def edit(row,**kw):
  body={'code':row['code'],'station':kw.get('station',row['station']),'planned_at':row['planned_at'],
   'actual_at':(f"{day}T{kw['actual_at']}:00+08:00" if 'actual_at' in kw else row['actual_at']),'note':kw.get('note',row['note'])}
  st,d=analyst.call('PUT',f"/arrivals/{row['id']}",body);assert st==200,d;return d

 def subscribe(name,station):
  st,d=admin.call('POST','/sync-subscriptions',{'name':name,'station':station,'receiver':name})
  assert st==200,d;return d

 def progress(sub):return admin.call('GET',f"/sync-subscriptions/{sub['id']}/progress")[1]

 north=[arrival(i,'北站',planned=f'{8+i:02d}:00') for i in range(4)]
 south=[arrival(10,'南站',planned='11:00')]
 subs=[subscribe('自检快端'+stamp,'北站'),subscribe('自检慢端'+stamp,'北站')]
 fast=Receiver(a.url,subs[0]['token'],os.path.join(work,'fast.sqlite'))
 slow=Receiver(a.url,subs[1]['token'],os.path.join(work,'slow.sqlite'))

 # 1. first full sync
 fast.sync(count=10000);slow.sync(count=10000)
 server=admin.call('GET',f"/sync-subscriptions/{subs[0]['id']}/progress")[1]['station_count']
 check('首次全量与服务站台账一致',fast.store.count()==server and slow.store.count()==server,(fast.store.count(),server))
 check('首次全量后对账一致',fast.report()['status']=='ok')

 # 2. station move out and back
 edit(north[0],station='南站',note='移到南站')
 fast.sync(count=10000)
 check('改站移出后本端撤回',fast.store.record(north[0]['id']) is None)
 slow_before=slow.store.count()
 check('慢端未跟进仍在旧状态',slow.store.record(north[0]['id']) is not None and slow.store.count()==slow_before)
 edit(north[0],station='北站',note='移回北站')
 fast.sync(count=10000)
 check('改站移回后本端补全',fast.store.record(north[0]['id']) is not None)

 # 3. repeated revisions, later one wins
 edit(north[1],actual_at='10:11',note='第一次修订')
 edit(north[1],actual_at='10:22',note='第二次修订')
 fast.sync(count=10000)
 rec=json.loads(fast.store.record(north[1]['id'])['payload'])
 check('多次修订按先后生效',rec['note']=='第二次修订' and rec['revision']>=3,rec.get('revision'))

 # 4. re-pull before confirm returns identical content
 page=fast.client.pull();pending=[b['id'] for b in page['batches']]
 check('确认前无待确认批次时拉取稳定',pending==[],pending)
 # create a fresh change for both, then slow pulls without confirming
 edit(north[2],actual_at='12:34',note='待确认批次')
 fresh=slow.client.pull()
 first=fresh['batches'][0]
 again=slow.client.pull()['batches'][0]
 check('确认前可重取原内容（校验和一致）',first['checksum']==again['checksum'])

 # 5. crash after apply, before confirm
 res=slow.sync(count=1,crash_after_apply=True)
 check('落库后确认前中断被记录',res.get('crashed_after_apply') and slow.store.applied_seq()>slow.store.confirmed_seq(),res)
 fast.sync(count=10000)
 check('快端不被慢端阻塞',progress(subs[0])['lag']==0)
 res=slow.sync(count=10000)
 check('断点续跑后无重复落库',res.get('confirmed',0)>=1 and slow.store.count()==fast.store.count(),(slow.store.count(),fast.store.count()))
 check('断点续跑后无漏变更',progress(subs[1])['lag']==0 and slow.store.confirmed_seq()==progress(subs[1])['head_seq'])

 # 6. each side reports its own progress
 check('两端各自对账一致',fast.report()['status']=='ok' and slow.report()['status']=='ok')
 view=admin.call('GET',f"/sync-drills")  # reachable, irrelevant
 check('进度接口可见两端独立游标',progress(subs[0])['cursor_seq']==progress(subs[1])['cursor_seq'])

 # 7. drift detection
 victim=fast.local_records()[0]['id']
 fast.store.db.execute('DELETE FROM records WHERE arrival_id=?',(victim,));fast.store.db.commit()
 rep=fast.report('drift')
 check('落库后差异可被对账发现',rep['status']=='mismatch' and victim in rep['missing'],rep['missing'][:3])

 print('\n全部自检通过。接收端台账目录：'+work)
 return 0

if __name__=='__main__':
 try:sys.exit(main())
 except SyncError as e:
  print('同步协议错误：',e);sys.exit(1)
