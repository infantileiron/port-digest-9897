from sqlalchemy import BigInteger,Boolean,Column,ForeignKey,Index,Integer,JSON,String,Text,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Arrival(Base):
 __tablename__='arrivals'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);station=Column(String(30));planned_at=Column(String(40));actual_at=Column(String(40),nullable=True);note=Column(Text,default='');dataset=Column(String(40),default='live',nullable=False)
class ArrivalChange(Base):
 __tablename__='arrival_changes'
 __table_args__=(UniqueConstraint('arrival_id','revision',name='uq_change_arrival_revision'),Index('ix_change_station_seq','new_station','id'),Index('ix_change_old_station_seq','old_station','id'))
 id=Column(BigInteger,primary_key=True,autoincrement=True)
 arrival_id=Column(Integer,nullable=False);code=Column(String(60),nullable=False);revision=Column(Integer,nullable=False)
 old_station=Column(String(30),nullable=True);new_station=Column(String(30),nullable=False)
 payload=Column(JSON);changed_at=Column(String(40));actor=Column(String(100),default='');dataset=Column(String(40),default='live',nullable=False)
class Subscription(Base):
 __tablename__='subscriptions'
 id=Column(Integer,primary_key=True);name=Column(String(100),unique=True,nullable=False);station=Column(String(30));daily_at=Column(String(5));recipient=Column(String(80));enabled=Column(Boolean,default=False);next_run_at=Column(String(40));last_run_at=Column(String(40),nullable=True)
class Receipt(Base):
 __tablename__='receipts'
 __table_args__=(UniqueConstraint('subscription_id','report_day',name='uq_receipt_subscription_day'),)
 id=Column(Integer,primary_key=True);subscription_id=Column(Integer,ForeignKey('subscriptions.id'));report_day=Column(String(10));recipient=Column(String(80));created_at=Column(String(40));state=Column(String(20),default='delivered');payload=Column(JSON);review_note=Column(Text,default='')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
class SyncSubscription(Base):
 __tablename__='sync_subscriptions'
 id=Column(Integer,primary_key=True);name=Column(String(100),unique=True,nullable=False);station=Column(String(30),nullable=False);receiver=Column(String(80),default='');dataset=Column(String(40),default='live',nullable=False)
 enabled=Column(Boolean,default=True);token_hash=Column(String(64),unique=True,nullable=False);generation=Column(Integer,default=1,nullable=False)
 cursor_seq=Column(BigInteger,default=0,nullable=False);bootstrapped=Column(Boolean,default=False,nullable=False);bootstrap_to=Column(BigInteger,nullable=True)
 created_at=Column(String(40));last_pull_at=Column(String(40),nullable=True);last_confirm_at=Column(String(40),nullable=True);last_report_at=Column(String(40),nullable=True)
class SyncBatch(Base):
 __tablename__='sync_batches'
 __table_args__=(Index('ix_sync_batch_sub_status','subscription_id','status'),)
 id=Column(Integer,primary_key=True);subscription_id=Column(Integer,ForeignKey('sync_subscriptions.id'),nullable=False)
 kind=Column(String(20),nullable=False);seq_from=Column(BigInteger,nullable=False);seq_to=Column(BigInteger,nullable=False)
 events=Column(JSON);checksum=Column(String(64));status=Column(String(20),default='pending');created_at=Column(String(40));confirmed_at=Column(String(40),nullable=True)
class SyncReport(Base):
 __tablename__='sync_reports'
 __table_args__=(Index('ix_sync_report_sub_id','subscription_id','id'),)
 id=Column(Integer,primary_key=True);subscription_id=Column(Integer,ForeignKey('sync_subscriptions.id'),nullable=False)
 generation=Column(Integer,default=1,nullable=False)
 applied_seq=Column(BigInteger,nullable=False);confirmed_seq=Column(BigInteger,nullable=False);record_count=Column(Integer,default=0)
 records=Column(JSON);note=Column(Text,default='');reported_at=Column(String(40))
class SyncDrillRun(Base):
 __tablename__='sync_drill_runs'
 id=Column(Integer,primary_key=True);actor=Column(String(100));created_at=Column(String(40));status=Column(String(20));summary=Column(JSON)
