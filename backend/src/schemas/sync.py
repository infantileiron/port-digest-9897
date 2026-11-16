from typing import Literal,Optional
from pydantic import BaseModel,Field,field_validator
STATION=Literal['北站','南站','东港']
NOWORK=('id','code','station','planned_at','actual_at','note','revision','checksum')
class SyncSubscriptionInput(BaseModel):
 name:str=Field(min_length=2,max_length=100)
 station:STATION
 receiver:str=Field(default='站内接收端',max_length=80)
 enabled:bool=True
class SyncRecord(BaseModel):
 id:int
 code:str
 station:STATION
 planned_at:str
 actual_at:Optional[str]=None
 note:str=''
 revision:int=Field(ge=1)
 checksum:str
class SyncReportInput(BaseModel):
 applied_seq:int=Field(ge=0)
 confirmed_seq:int=Field(ge=0)
 records:list[SyncRecord]=Field(default_factory=list)
 note:str=Field(default='',max_length=500)
 @field_validator('records')
 @classmethod
 def unique(cls,v):
  ids=[r.id for r in v]
  if len(ids)!=len(set(ids)):raise ValueError('同一接收端台账内记录ID不能重复')
  return v
class SyncConfirmInput(BaseModel):
 checksum:str=Field(min_length=16,max_length=64)
class SyncPullInput(BaseModel):
 limit:int=Field(default=20,ge=1,le=100)
