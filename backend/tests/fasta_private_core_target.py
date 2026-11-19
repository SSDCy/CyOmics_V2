"""One finite target. Explicit in-memory SQL/driver substitute; NO DB/RLS/HTTP QA.

Loads unchanged frozen dependencies in place, never copies the repository.
Run: python <this target> --frozen-backend <frozen repo>/backend
"""
import argparse
import asyncio
import copy
from enum import IntEnum
import hashlib
import json
from pathlib import Path
import sys
import time
import types

args = argparse.ArgumentParser()
args.add_argument('--frozen-backend',required=True)
opts = args.parse_args()
sys.dont_write_bytecode = True
sys.path.insert(0,opts.frozen_backend)
# Dependency import substitute only: no installed psycopg on this host.
class TransactionStatus(IntEnum):
    IDLE=0; INTRANS=2; INERROR=3
driver=types.ModuleType('psycopg');driver.AsyncConnection=object
pq=types.ModuleType('psycopg.pq');pq.TransactionStatus=TransactionStatus
driver.pq=pq
sys.modules['psycopg']=driver;sys.modules['psycopg.pq']=pq
import cyomics_projects
cyomics_projects.__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
from cyomics_projects.fasta_private_repository import FastaPrivateRepository
from cyomics_projects.upload_repository import UploadOutcome
from cyomics_projects.settings import Fault
from cyomics_projects import fasta_intake_contract as model

A='00000000-0000-4000-8000-000000000001'
B='00000000-0000-4000-8000-000000000002'
P='00000000-0000-4000-8000-000000000003'
R='00000000-0000-4000-8000-000000000004'
G='00000000-0000-4000-8000-000000000005'

class Budget:
    def __init__(self):
        self.create=UploadOutcome();self.end=time.monotonic()+180
    def remaining(self):return max(0,self.end-time.monotonic())
    def check(self):
        if not self.remaining():raise TimeoutError()
    async def run(self,op,*args,**kwargs):
        self.check();value=await op();self.check();return value

class Cursor:
    def __init__(self,rows):self.rows=rows
    async def fetchone(self):return self.rows[0] if self.rows else None
    async def fetchall(self):return self.rows

class Store:
    def __init__(self):
        self.sources={};self.indexes={};self.events=[];self.fail_index=False
    def release(self,c):assert c.pgconn.transaction_status==TransactionStatus.IDLE
    async def getconn(self):return Connection(self)

class Connection:
    closed=False
    def __init__(self,store):
        self.store=store;self.pgconn=types.SimpleNamespace(transaction_status=TransactionStatus.IDLE)
    async def execute(self,sql,params):
        db=self.store;db.events.append(sql)
        if sql.startswith('BEGIN'):
            self.pgconn.transaction_status=TransactionStatus.INTRANS
            self.snapshot=(copy.copy(db.sources),copy.copy(db.indexes))
        elif sql=='COMMIT':self.pgconn.transaction_status=TransactionStatus.IDLE
        elif sql=='ROLLBACK':
            db.sources,db.indexes=self.snapshot;self.pgconn.transaction_status=TransactionStatus.IDLE
        elif "set_config('app.user_id'" in sql:self.actor=params[0]
        elif 'set_config' in sql:pass
        elif sql.startswith('SELECT project_id FROM'):
            return Cursor([(params[1],)] if str(params[0])==A and str(params[1])==P else [])
        elif 'pg_try_advisory_xact_lock' in sql:return Cursor([(True,)])
        elif sql.startswith('SELECT s.source_id'):
            assert self.actor==A
            source=db.sources.get(tuple(map(str,params)))
            index=db.indexes.get(str(source[0])) if source else None
            return Cursor([source+(index[-2],index[-1])] if source and index else [])
        elif sql.startswith('INSERT INTO cyomics_app.fasta_sources'):
            assert self.actor==A
            key=(str(params[1]),str(params[2]));assert key not in db.sources
            db.sources[key]=tuple(params);return Cursor([(params[0],)])
        elif sql.startswith('INSERT INTO cyomics_app.fasta_index_versions'):
            if db.fail_index:raise RuntimeError('injected index write failure')
            key=str(params[2]);assert key not in db.indexes
            db.indexes[key]=tuple(params);return Cursor([(params[0],)])
        else:raise AssertionError('unexpected SQL')
        return Cursor([])

async def main():
    db=Store();repo=FastaPrivateRepository(db)
    config={'config_json':model.configuration_json()}
    raw=b'>dup original header\r\nACd\r\n>dup second header\r\nA*\r\n>empty\r\n'
    async def save(data=raw,name='proof.fasta',actor=A,request=R):
        return await repo.save(actor,P,request,name,data,Budget(),**config)
    async def fails(op,status=None,code=None):
        try:await op()
        except (Fault,model.IntakeError) as error:
            if status is not None:assert error.status==status
            if code is not None:assert error.code==code
            return
        raise AssertionError('expected rejection')
    state,result=await save();assert state==201
    assert result.source.content==raw and result.source.sha256==hashlib.sha256(raw).hexdigest()
    assert result.dto()['index']['summary']['record_count']==3
    assert result.dto()['index']['summary']['invalid_canonical_record_count']==2
    state,retry=await save();assert state==200 and retry.dto()==result.dto()
    # New repository instance over the same persisted store (process restart substitute).
    reopened=FastaPrivateRepository(db)
    read=await reopened.read(A,P,R,Budget(),**config)
    assert read.dto()==result.dto() and read.source.content==raw
    verified=model.rebuild_and_verify(read.source,read.candidate,expected_project_id=P,
        expected_source_id=read.source.source_id,expected_index_version_id=read.candidate.index_version_id,
        control=model.ParseControl(checkpoint=Budget().check))
    assert verified.index.records[0].header_raw=='dup original header'
    assert verified.index.id_ordinals['dup']==(1,2)
    assert verified.index.records[1].canonical_sequence is None
    await fails(lambda:save(raw+b'\n'),409,'fasta_request_conflict')
    await fails(lambda:save(actor=B),404,'project_not_found')
    await fails(lambda:reopened.read(B,P,R,Budget(),**config),404,'project_not_found')
    await fails(lambda:save(name='../escape.fa'),code='invalid_fasta_request')
    await fails(lambda:save(name='wrong.txt'),400,'invalid_fasta_request')
    await fails(lambda:save(b'x'*(26214400+1)),413,'invalid_fasta_request')
    await fails(lambda:save(b'no header',request=G),code='fasta_index_failed')
    assert len(db.sources)==len(db.indexes)==1
    # Source insert must roll back together with failed second insert.
    db.fail_index=True
    await fails(lambda:save(request=G),503,'uploads_unavailable')
    db.fail_index=False
    assert len(db.sources)==len(db.indexes)==1
    print(json.dumps({'target':'finite private FASTA core','status':'PASS','substitute':'explicit in-memory SQL and psycopg import substitute','checks':['save/read/retry stable identity','request conflict','wrong Owner read/write denied','complete bytes/SHA/header/duplicates/invalid canonical preserved','oversize/filename/format/parser rejects','atomic rollback on index insert failure'],'actual_db_rls_http':False}))
    golden=Path('D:/R-cy-0419/huoshao_cxy/Ptenuiflora_genome_V1.prot.fasta')
    if not golden.is_file():
        print(json.dumps({'golden':'NOT_RUN','reason':'fixed local file absent'}));return
    data=golden.read_bytes();sha=hashlib.sha256(data).hexdigest()
    started=time.monotonic()
    try:
        state,saved=await save(data,golden.name,request=G)
        assert state==201 and saved.dto()['index']['summary']['record_count']==38387
        assert sha=='8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5'
        reread=await reopened.read(A,P,G,Budget(),**config)
        assert reread.dto()==saved.dto() and reread.source.content==data
        print(json.dumps({'golden':'PASS','bytes':len(data),'sha256':sha,'records':saved.dto()['index']['summary']['record_count'],'index_manifest_sha256':saved.candidate.index_manifest_sha256,'seconds':round(time.monotonic()-started,3),'stored_in':'memory substitute only'}))
    except (Fault,model.IntakeError) as error:
        print(json.dumps({'golden':'FAIL','bytes':len(data),'sha256':sha,'code':error.code,'seconds':round(time.monotonic()-started,3)}))
        raise

asyncio.run(main())
