import argparse,json,sys
from receiver.engine import Crash,Receiver

def main(argv=None):
 p=argparse.ArgumentParser(description='港站增量同步接收端（演练用，仅标准库）')
 p.add_argument('--url',default='http://localhost:19212',help='后端根地址')
 p.add_argument('--token',required=True,help='接收端令牌 psync_...')
 p.add_argument('--db',required=True,help='本端台账 SQLite 文件（断点续跑依赖它）')
 p.add_argument('--mode',choices=['run','apply','confirm','report','show','reset'],default='run')
 p.add_argument('--batch',type=int,help='apply/confirm 指定批次ID')
 p.add_argument('--crash-after-apply',action='store_true',help='落库后、确认前中断，用于演练')
 p.add_argument('--count',type=int,default=100000,help='run 模式最多确认的批次数')
 p.add_argument('--note',default='')
 a=p.parse_args(argv)
 r=Receiver(a.url,a.token,a.db,log=lambda *x:print(*x,flush=True))
 if a.mode=='run':print(json.dumps(r.sync(count=a.count,limit=50),ensure_ascii=False,indent=2))
 elif a.mode=='apply':_apply(r,a)
 elif a.mode=='confirm':_confirm(r,a)
 elif a.mode=='report':print(json.dumps(r.report(a.note),ensure_ascii=False,indent=2))
 elif a.mode=='show':
  print(json.dumps({'applied_seq':r.store.applied_seq(),'confirmed_seq':r.store.confirmed_seq(),'count':r.store.count(),'records':r.local_records(),'batches':r.store.batches()},ensure_ascii=False,indent=2))
 elif a.mode=='reset':r.store.reset()
 return 0

def _apply(r,a):
 if not a.batch:raise SystemExit('--batch 必填')
 page=r.client.pull();target=next((b for b in page['batches'] if b['id']==a.batch),None)
 if not target:
  if not r.store.batch(a.batch):raise SystemExit('该批次不在服务端待确认列表，也不在本端台账')
  print('本端已落库，跳过重复应用')
 else:
  r.store.put_batch(target);r.apply(target);print('已落库批次',a.batch,'（尚未确认）')
 if a.crash_after_apply:raise Crash('模拟中断：已落库，未确认')

def _confirm(r,a):
 if not a.batch:raise SystemExit('--batch 必填')
 b=r.store.batch(a.batch)
 if not b:raise SystemExit('本端无该批次，先 apply')
 out=r.client.confirm(a.batch,b['checksum'])
 r.store.mark_confirmed(a.batch,(out.get('batch') or {}).get('confirmed_at') or '')
 print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':sys.exit(main())
