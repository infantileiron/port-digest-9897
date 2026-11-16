from sqlalchemy import text
ADDITIVE={
 'arrivals':[('dataset',"character varying(40) NOT NULL DEFAULT 'live'")],
 'arrival_changes':[('dataset',"character varying(40) NOT NULL DEFAULT 'live'")],
 'sync_subscriptions':[('dataset',"character varying(40) NOT NULL DEFAULT 'live'"),('generation',"integer NOT NULL DEFAULT 1"),('bootstrap_to','bigint'),('last_report_at','character varying(40)')],
 'sync_reports':[('generation',"integer NOT NULL DEFAULT 1")],
}
def ensure_additive(engine):
 """create_all() makes new tables but never alters existing ones. These are
 additive, idempotent columns only; the full DDL lives in migrations/."""
 if engine.dialect.name!='postgresql':return
 with engine.begin() as c:
  for table,cols in ADDITIVE.items():
   exists=c.execute(text("SELECT to_regclass(:t)"),{'t':'public.'+table}).scalar()
   if not exists:continue
   for name,ddl in cols:
    c.execute(text(f'ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS {name} {ddl}'))
