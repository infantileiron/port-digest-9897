"""Run one or more standalone station receivers against the live sync feed.

Two ways to get receiver tokens:
  * pass them explicitly:  --tokens 北站=psync_... 南站=psync_...
  * let the script bootstrap: --bootstrap 北站,南站,东港 (uses the admin demo login to
    find or create each station's sync subscription and rotate a fresh token).
    Tokens are cached in <db-dir>/tokens.json so restarts reuse them.

Each receiver keeps its own SQLite ledger, so receivers advance independently.
"""
import argparse,json,os,threading,time,urllib.error,urllib.request
from receiver.engine import Receiver
from receiver.client import SyncError

class AdminApi:
 def __init__(self,base,email,password):
  self.base=base.rstrip('/');self.token=None;self.email=email;self.password=password
 def call(self,method,path,body=None,retry=True):
  data=None if body is None else json.dumps(body,ensure_ascii=False).encode()
  headers={'Content-Type':'application/json'}
  if self.token:headers['Authorization']='Bearer '+self.token
  req=urllib.request.Request(self.base+path,data=data,method=method,headers=headers)
  try:
   with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read() or b'{}')
  except urllib.error.HTTPError as e:
   if e.code==401 and retry:
    self.login();return self.call(method,path,body,retry=False)
   raise
 def login(self):
  self.token=self.call('POST','/auth/login',{'email':self.email,'password':self.password},retry=False)['token']

def bootstrap_tokens(api,stations,cache_path):
 cache={}
 if os.path.exists(cache_path):
  try:cache=json.load(open(cache_path))
  except Exception:cache={}
 existing={p['subscription']['name']:p['subscription'] for p in api.call('GET','/sync-subscriptions')}
 tokens={}
 for station in stations:
  name='增量·'+station+'台账';sub=existing.get(name)
  if not sub:
   sub=api.call('POST','/sync-subscriptions',{'name':name,'station':station,'receiver':station+'值班接收端'})
   tokens[station]=sub['token']
  elif cache.get(station):
   tokens[station]=cache[station]
  else:
   tokens[station]=api.call('POST',f"/sync-subscriptions/{sub['id']}/rotate-token")['token']
 os.makedirs(os.path.dirname(cache_path),exist_ok=True)
 json.dump(tokens,open(cache_path,'w'),ensure_ascii=False,indent=2)
 return tokens

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--url',default=os.getenv('SYNC_BASE_URL','http://127.0.0.1:8000'))
 p.add_argument('--tokens',nargs='*',default=[],help='label=token ...')
 p.add_argument('--bootstrap',default=os.getenv('SYNC_BOOTSTRAP_STATIONS',''),help='逗号分隔港站，自动取令牌')
 p.add_argument('--admin-email',default=os.getenv('SYNC_ADMIN_EMAIL','admin@demo.local'))
 p.add_argument('--admin-password',default=os.getenv('SYNC_ADMIN_PASSWORD','Demo123!'))
 p.add_argument('--db-dir',default=os.getenv('SYNC_DB_DIR','/tmp/port-digest-receivers'))
 p.add_argument('--interval',type=float,default=3.0)
 p.add_argument('--rounds',type=int,default=0,help='0 = loop forever')
 p.add_argument('--report-every',type=int,default=5)
 p.add_argument('--heartbeat',default=os.getenv('RECEIVER_HEARTBEAT','/tmp/receiver-heartbeat'))
 a=p.parse_args()
 os.makedirs(a.db_dir,exist_ok=True)
 pairs=[]
 for item in a.tokens or []:
  label,token=item.split('=',1);pairs.append((label,token))
 if a.bootstrap:
  stations=[x.strip() for x in a.bootstrap.split(',') if x.strip()]
  api=AdminApi(a.url,a.admin_email,a.admin_password);api.login()
  tokens=bootstrap_tokens(api,stations,os.path.join(a.db_dir,'tokens.json'))
  pairs+= [(s,t) for s,t in tokens.items()]
 if not pairs:raise SystemExit('需要 --tokens 或 --bootstrap')
 receivers=[]
 for label,token in pairs:
  r=Receiver(a.url,token,os.path.join(a.db_dir,label+'.sqlite'),log=lambda *x,m=label:print(m,*x,flush=True))
  receivers.append((label,r))
 def worker(label,r):
  rounds=0
  while True:
   try:
    open(a.heartbeat,'w').write(str(time.time()))
    res=r.sync(count=1000,limit=50);rounds+=1
    if res.get('confirmed') or res.get('reset'):print(label,'sync',res,flush=True)
    if rounds%a.report_every==0:print(label,'report',r.report('standalone')['status'],flush=True)
   except SyncError as e:print(label,'sync error',e,flush=True)
   except Exception as e:print(label,'error',e,flush=True)
   if a.rounds and rounds>=a.rounds:break
   time.sleep(a.interval)
 threads=[threading.Thread(target=worker,args=(l,r),daemon=True) for l,r in receivers]
 for t in threads:t.start()
 for t in threads: t.join()

if __name__=='__main__':main()
