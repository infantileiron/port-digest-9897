import json,os,time
from receiver.client import SyncClient,SyncError
from receiver.store import Store

class Crash(Exception):
 """Simulated interruption between durable apply and confirm."""

class Receiver:
 """Pull -> apply to a durable ledger -> confirm, one batch at a time.

 Both the ledger and the confirmed cursor live on disk, so a restart re-pulls the same
 pending batch; applying it again is idempotent and confirmation is exactly-once by batch id.
 """
 def __init__(self,url,token,db_path,log=None):
  self.client=SyncClient(url,token);self.store=Store(db_path);self.log=log or (lambda *_:None)
 def apply(self,batch):
  for e in batch['events']:
   if e['type']=='upsert':self.store.upsert(e['arrival_id'],e['payload'],e['seq'])
   elif e['type']=='retract':self.store.retract(e['arrival_id'],e['revision'])
  self.store.mark_applied(batch['id'])
 def sync(self,count=1,limit=20,crash_after_apply=False):
  """Pull pages and walk each pending batch: apply then confirm.
  Stops after `count` confirmed batches so a slow receiver can be driven step by step."""
  confirmed=0;applied=0;pages=0;reset=False
  while confirmed<count:
   page=self.client.pull(limit=limit);pages+=1
   gen=str(page.get('generation') or 1)
   if not self.store.batches() and int(page.get('cursor_seq') or 0)>0:
    # Local ledger was wiped (fresh disk, lost file) but the publisher has already
    # confirmed a cursor. Ask it to re-base this subscription so we can rebuild
    # from a fresh snapshot instead of starting mid-stream.
    self.client.reset();self.store.reset();self.store.set_meta('generation',gen);reset=True;continue
   self.store.reconcile(page['cursor_seq'])
   known=self.store.meta('generation')
   if known is None:self.store.set_meta('generation',gen)
   elif known!=gen:
    # The publisher re-based this subscription (e.g. station change); the local ledger
    # was built for a different station and must be rebuilt from the frozen snapshot.
    self.store.reset();self.store.set_meta('generation',gen);reset=True;continue
   if not page['batches']:
    return {'confirmed':confirmed,'applied':applied,'pages':pages,'up_to_date':page['up_to_date'],'cursor_seq':page['cursor_seq'],'head_seq':page['head_seq'],'empty':True}
   progressed=False
   for b in page['batches']:
    local=self.store.batch(b['id'])
    if local and local['status']=='confirmed':continue
    if not local:self.store.put_batch(b)
    if self.store.batch(b['id'])['status']=='pending':self.apply(b);applied+=1
    if crash_after_apply:
     return {'confirmed':confirmed,'applied':applied,'pages':pages,'crashed_after_apply':True,'batch_id':b['id'],'pending_confirm':True,'cursor_seq':page['cursor_seq'],'head_seq':page['head_seq']}
    res=self.client.confirm(b['id'],b['checksum'])
    self.store.mark_confirmed(b['id'],(res.get('batch') or {}).get('confirmed_at') or '')
    confirmed+=1;progressed=True
    if confirmed>=count:break
   if not progressed:break
  return {'confirmed':confirmed,'applied':applied,'pages':pages,'reset':reset,'cursor_seq':self.store.confirmed_seq(),'head_seq':page['head_seq']}
 def rebuild(self):
  """Discard the local ledger and rebuild from a fresh authoritative snapshot."""
  self.store.reset();self.client.reset()
  return self.sync(count=100000,limit=100)
 def resume(self):
  """After a crash or a dropped link: reconcile the local ledger with the server again."""
  return self.sync(count=10000,limit=100)
 def report(self,note=''):
  return self.client.report(self.store.applied_seq(),self.store.confirmed_seq(),self.store.all_records(),note)
 def local_records(self):return self.store.all_records()
