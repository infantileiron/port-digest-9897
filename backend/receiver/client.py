import json,urllib.error,urllib.request

class SyncError(Exception):
 def __init__(self,status,detail):
  super().__init__(f'{status}: {detail}');self.status=status;self.detail=detail

class SyncClient:
 """Thin HTTP client for the receiver feed. The token is a receiver credential, not a user login."""
 def __init__(self,base,token,timeout=15):
  self.base=base.rstrip('/');self.token=token;self.timeout=timeout
 def _call(self,method,path,body=None):
  data=None if body is None else json.dumps(body,ensure_ascii=False).encode()
  req=urllib.request.Request(self.base+path,data=data,method=method,headers={
   'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Accept':'application/json'})
  try:
   with urllib.request.urlopen(req,timeout=self.timeout) as r:return json.loads(r.read().decode() or '{}')
  except urllib.error.HTTPError as e:
   detail=''
   try:detail=json.loads(e.read().decode()).get('detail','')
   except Exception:pass
   raise SyncError(e.code,detail or str(e.reason)) from None
  except urllib.error.URLError as e:raise SyncError(0,'无法连接服务端：'+str(e.reason)) from None
 def pull(self,limit=20):return self._call('POST','/sync/pull',{'limit':limit})
 def confirm(self,batch_id,checksum):return self._call('POST',f'/sync/batches/{batch_id}/confirm',{'checksum':checksum})
 def report(self,applied_seq,confirmed_seq,records,note=''):
  return self._call('POST','/sync/reports',{'applied_seq':applied_seq,'confirmed_seq':confirmed_seq,'records':records,'note':note})
 def progress(self):return self._call('GET','/sync/progress')
 def reset(self):return self._call('POST','/sync/reset',{})
