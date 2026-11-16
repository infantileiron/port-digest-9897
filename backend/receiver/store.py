import json,sqlite3,threading

class Store:
 """Durable receiver ledger. One SQLite file per subscription = one receiver instance."""
 def __init__(self,path):
  self.path=path;self._lock=threading.Lock()
  self.db=sqlite3.connect(path,check_same_thread=False)
  self.db.row_factory=sqlite3.Row
  with self._lock,self.db:
   self.db.executescript('''
   CREATE TABLE IF NOT EXISTS batches(
     id INTEGER PRIMARY KEY, kind TEXT NOT NULL, seq_from INTEGER NOT NULL, seq_to INTEGER NOT NULL,
     checksum TEXT NOT NULL, events TEXT NOT NULL, status TEXT NOT NULL, confirmed_at TEXT);
   CREATE TABLE IF NOT EXISTS records(
     arrival_id INTEGER PRIMARY KEY, code TEXT NOT NULL, revision INTEGER NOT NULL,
     applied_seq INTEGER NOT NULL, payload TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
   ''')
 def meta(self,key,default=None):
  with self._lock:
   r=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
  return r['value'] if r else default
 def set_meta(self,key,value):
  with self._lock,self.db:
   self.db.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
 def put_batch(self,b):
  with self._lock,self.db:
   self.db.execute('''INSERT INTO batches(id,kind,seq_from,seq_to,checksum,events,status,confirmed_at)
    VALUES(?,?,?,?,?,?,'pending',NULL)
    ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,seq_from=excluded.seq_from,seq_to=excluded.seq_to,
     checksum=excluded.checksum,events=excluded.events''',(b['id'],b['kind'],b['seq_from'],b['seq_to'],b['checksum'],json.dumps(b['events'],ensure_ascii=False,sort_keys=True)))
 def batch(self,i):
  with self._lock:r=self.db.execute('SELECT * FROM batches WHERE id=?',(i,)).fetchone()
  return dict(r) if r else None
 def batches(self,status=None):
  q='SELECT * FROM batches';args=()
  if status:q+=' WHERE status=?';args=(status,)
  with self._lock:rows=self.db.execute(q+' ORDER BY seq_from,id',args).fetchall()
  return [dict(r) for r in rows]
 def mark_applied(self,i):
  with self._lock,self.db:self.db.execute("UPDATE batches SET status='applied' WHERE id=?",(i,))
 def mark_confirmed(self,i,at):
  with self._lock,self.db:
   row=self.db.execute('SELECT seq_to FROM batches WHERE id=?',(i,)).fetchone()
   if not row:raise KeyError('本端无批次 %s'%i)
   self.db.execute("UPDATE batches SET status='confirmed',confirmed_at=? WHERE id=?",(at,i))
   self.db.execute("INSERT INTO meta(key,value) VALUES('confirmed_seq',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(row['seq_to']),))
 def applied_seq(self):
  with self._lock:r=self.db.execute("SELECT MAX(seq_to) FROM batches WHERE status IN ('applied','confirmed')").fetchone()
  return int(r[0] or 0)
 def confirmed_seq(self):
  with self._lock:r=self.db.execute("SELECT MAX(seq_to) FROM batches WHERE status='confirmed'").fetchone()
  return int(r[0] or 0)
 def upsert(self,arrival_id,payload,applied_seq):
  p=json.dumps(payload,ensure_ascii=False,sort_keys=True)
  with self._lock,self.db:
   self.db.execute('''INSERT INTO records(arrival_id,code,revision,applied_seq,payload) VALUES(?,?,?,?,?)
    ON CONFLICT(arrival_id) DO UPDATE SET code=excluded.code,revision=excluded.revision,
     applied_seq=excluded.applied_seq,payload=excluded.payload
    WHERE excluded.revision>=records.revision''',(arrival_id,payload['code'],payload['revision'],applied_seq,p))
 def retract(self,arrival_id,revision):
  with self._lock,self.db:self.db.execute('DELETE FROM records WHERE arrival_id=? AND revision<=?',(arrival_id,revision))
 def record(self,arrival_id):
  with self._lock:r=self.db.execute('SELECT * FROM records WHERE arrival_id=?',(arrival_id,)).fetchone()
  return dict(r) if r else None
 def all_records(self):
  with self._lock:rows=self.db.execute('SELECT * FROM records ORDER BY arrival_id').fetchall()
  out=[]
  for r in rows:
   p=json.loads(r['payload']);p['id']=r['arrival_id'];out.append(p)
  return out
 def reconcile(self,cursor):
  """Server cursor is authoritative: a local batch at or below it was confirmed,
  even if we crashed before recording the acknowledgement."""
  with self._lock,self.db:
   self.db.execute("UPDATE batches SET status='confirmed' WHERE status='applied' AND seq_to<=?",(cursor,))
   self.db.execute("INSERT INTO meta(key,value) VALUES('confirmed_seq',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(cursor),))
 def count(self):
  with self._lock:return int(self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0])
 def reset(self):
  with self._lock,self.db:self.db.executescript('DELETE FROM batches;DELETE FROM records;DELETE FROM meta;')
