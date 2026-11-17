"""SQL-shaped in-memory tests; no database, R, Auth, network or file export."""
import asyncio
import gc
import copy
from dataclasses import dataclass,FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from uuid import uuid4,UUID

BASE=Path(os.environ.get('CYOMICS_WORKER_SOURCE_BASELINE',Path(__file__).resolve().parents[1]))
CONTRACT=Path(os.environ.get('CYOMICS_WORKER_SOURCE_CONTRACT',BASE))
os.environ['CYOMICS_RAW_QC_BASELINE']=str(BASE)
f=runpy.run_path(str(CONTRACT/'tests/test_raw_qc_contract.py'))
sys.modules['cyomics_projects'].__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
from cyomics_projects.raw_qc_worker_source import WorkerBoundSource,SQL,MAX_CONTENT
from cyomics_projects import raw_qc_contract as c
from cyomics_projects.settings import Budget,Fault
T=f['TS'];JOB=str(uuid4());ATTEMPT=str(uuid4())

@dataclass(frozen=True)
class BoundRaw:
    job_id:str
    attempt_id:str
    source:dict
    execution:dict
    content:bytes

def row():return (f['record']()[8],c.canonical(f['SYNTHETIC_EXECUTION']),f['RAW'])

class Cursor:
    def __init__(self,rows):self.rows=list(rows)
    async def fetchone(self):return self.rows.pop(0) if self.rows else None

class Connection:
    def __init__(self,rows):
        self.rows=rows;self.pgconn=self;self.transaction_status=T.IDLE;self.closed=False
        self.subject='poisoned-old-session';self.tx_subject=None;self.calls=[]
        self.fail=None;self.commit_bad=False;self.finish_error=False;self.returned_error=None
    async def execute(self,sql,params=()):
        self.calls.append((sql,params))
        if self.fail==sql:raise RuntimeError('SYNTHETIC secret must not escape')
        if sql.startswith('BEGIN'):self.transaction_status=T.INTRANS
        elif "set_config('app.user_id'" in sql:self.tx_subject=params[0]
        elif sql==SQL:
            assert self.tx_subject=='' and self.transaction_status==T.INTRANS
            return Cursor(self.rows)
        elif sql=='COMMIT':
            if not self.commit_bad:self.transaction_status=T.IDLE
        else:raise AssertionError('Unexpected SQL')
        return Cursor([])
    def finish(self):
        if self.finish_error:raise RuntimeError('SYNTHETIC finish secret')
        self.closed=True;self.subject=None;self.tx_subject=None;self.transaction_status=T.UNKNOWN

class Pool:
    def __init__(self,rows=None):self.connection=Connection([row()] if rows is None else rows);self.gets=0;self.releases=[];self.error=False
    async def getconn(self):
        self.gets+=1
        if self.error:raise RuntimeError('SYNTHETIC pool secret')
        return self.connection
    def release(self,connection):
        assert connection.closed,'Uncertain socket reused'
        self.releases.append(connection)

class Tests(unittest.IsolatedAsyncioTestCase):
    async def fault(self,worker,code,job=JOB,attempt=ATTEMPT,budget=None):
        with self.assertRaises(Fault) as cm:await worker.load_bound_source(job,attempt,budget or Budget())
        self.assertEqual(cm.exception.code,code)
        self.assertNotIn('secret',str(cm.exception))

    async def test_default_none_factory_or_pool_has_zero_io(self):
        pool=Pool()
        await self.fault(WorkerBoundSource(pool),'raw_qc_unavailable')
        await self.fault(WorkerBoundSource(None,BoundRaw),'raw_qc_unavailable')
        self.assertEqual(pool.gets,0)

    async def test_fixed_rpc_read_only_two_fetches_and_no_identity_reuse(self):
        pool=Pool();value=await WorkerBoundSource(pool,BoundRaw).load_bound_source(JOB,ATTEMPT,Budget())
        self.assertEqual((value.job_id,value.attempt_id),(JOB,ATTEMPT))
        self.assertEqual(value.content,f['RAW']);self.assertEqual(type(value.source),dict)
        self.assertEqual(type(value.execution),dict)
        with self.assertRaises(FrozenInstanceError):value.job_id=str(uuid4())
        self.assertEqual([(s,p) for s,p in pool.connection.calls if s==SQL],[(SQL,(UUID(JOB),UUID(ATTEMPT)))])
        self.assertTrue(pool.connection.calls[0][0].endswith('READ ONLY'))
        self.assertEqual(pool.connection.calls[-1][0],'COMMIT')
        self.assertTrue(pool.connection.closed);self.assertIsNone(pool.connection.subject)
        self.assertEqual(len(pool.releases),1);self.assertEqual(pool.gets,1)

    async def test_invalid_ids_are_not_owner_path_url_or_sql_and_never_acquire(self):
        pool=Pool();worker=WorkerBoundSource(pool,BoundRaw)
        for value in (None,'../path','https://host','SELECT 1',JOB.upper(),{'owner_id':JOB}):
            await self.fault(worker,'invalid_raw_qc_request',job=value)
            await self.fault(worker,'invalid_raw_qc_request',attempt=value)
        self.assertEqual(pool.gets,0)

    async def test_expired_budget_zero_connections(self):
        pool=Pool();b=Budget();b.deadline=0
        await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_timeout',budget=b)
        self.assertEqual(pool.gets,0)

    async def test_missing_row_is_attempt_conflict_and_no_cache_or_retry(self):
        pool=Pool([]);worker=WorkerBoundSource(pool,BoundRaw)
        await self.fault(worker,'raw_qc_attempt_conflict')
        self.assertEqual(pool.gets,1);self.assertEqual(len(pool.releases),1)

    async def test_second_row_and_wrong_column_counts_rejected(self):
        for rows in ([row(),row()],[(row()[0],row()[1])],[(None,row()[1],f['RAW'])]):
            pool=Pool(rows);await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_persisted_invalid')
            self.assertTrue(pool.connection.closed);self.assertEqual(len(pool.releases),1)

    async def test_dirty_transaction_never_queries_and_closed(self):
        pool=Pool();pool.connection.transaction_status=T.INERROR
        await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_unavailable')
        self.assertEqual(pool.connection.calls,[]);self.assertTrue(pool.connection.closed)

    async def test_bad_json_utf8_duplicate_keys_caps_and_schema(self):
        s,e,b=row()
        for text in ('{}','\ufeff'+s,'\ud800',' '*4097,s[:-1]+',"file_id":"x"}',s.replace('"source_bytes":'+str(len(b)),'"source_bytes":true')):
            pool=Pool([(text,e,b)])
            await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_persisted_invalid')
        bad=json.loads(e);bad['extra_env']={}
        for execution in (json.dumps(bad),'{}',e.replace('native_R','rscript'),'汉'*4096):
            pool=Pool([(s,execution,b)])
            await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_persisted_invalid')

    async def test_memoryview_detached_and_other_byte_types_rejected(self):
        s,e,b=row();mutable=bytearray(b)
        value=await WorkerBoundSource(Pool([(s,e,memoryview(mutable))]),BoundRaw).load_bound_source(JOB,ATTEMPT,Budget())
        mutable[0]=0;self.assertEqual(value.content,b)
        for content in (bytearray(b),b'',b'x',memoryview(bytearray(b))[::2],None,b'x'*(MAX_CONTENT+1)):
            await self.fault(WorkerBoundSource(Pool([(s,e,content)]),BoundRaw),'raw_qc_persisted_invalid')

    async def test_wrong_content_sha_not_just_length(self):
        s,e,b=row();await self.fault(WorkerBoundSource(Pool([(s,e,b'X'*len(b))]),BoundRaw),'raw_qc_persisted_invalid')

    async def test_fresh_dictionaries_not_cache_or_driver_aliases(self):
        raw=row();one=await WorkerBoundSource(Pool([raw]),BoundRaw).load_bound_source(JOB,ATTEMPT,Budget())
        one.source['source_sha256']='poison';one.execution['runner']['sha256']='poison'
        two=await WorkerBoundSource(Pool([raw]),BoundRaw).load_bound_source(JOB,ATTEMPT,Budget())
        self.assertNotEqual(two.source['source_sha256'],'poison');self.assertNotEqual(two.execution['runner']['sha256'],'poison')

    async def test_factory_wrong_fields_or_mutation_not_accepted(self):
        def mutation(**kw):kw['source']['source_sha256']='a'*64;return BoundRaw(**kw)
        for factory in (lambda **kw:kw,mutation):
            pool=Pool();await self.fault(WorkerBoundSource(pool,factory),'raw_qc_persisted_invalid')
            self.assertNotIn('COMMIT',[s for s,p in pool.connection.calls]);self.assertTrue(pool.connection.closed)

    async def test_rpc_commit_and_pool_failures_sanitized_not_retried(self):
        for fail in (SQL,'COMMIT'):
            pool=Pool();pool.connection.fail=fail
            await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_persisted_invalid')
            self.assertEqual(pool.gets,1);self.assertTrue(pool.connection.closed)
        pool=Pool();pool.error=True;await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_persisted_invalid')
        self.assertEqual(pool.gets,1)

    async def test_bad_postcommit_and_abort_failure_withheld_not_reused(self):
        pool=Pool();pool.connection.commit_bad=True
        await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_persisted_invalid')
        self.assertTrue(pool.connection.closed)
        pool=Pool();pool.connection.finish_error=True
        await self.fault(WorkerBoundSource(pool,BoundRaw),'raw_qc_persisted_invalid')
        self.assertEqual(pool.releases,[])

    async def test_cancellation_closes_assigned_connection(self):
        pool=Pool();original=pool.connection.execute;entered=asyncio.Event()
        async def execute(sql,params=()):
            if sql==SQL:entered.set();await asyncio.Future()
            return await original(sql,params)
        pool.connection.execute=execute
        task=asyncio.create_task(WorkerBoundSource(pool,BoundRaw).load_bound_source(JOB,ATTEMPT,Budget()))
        await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(pool.connection.closed);self.assertEqual(len(pool.releases),1)

    async def test_late_pool_connection_is_closed_once_after_timeout(self):
        gate=asyncio.Event();entered=asyncio.Event()
        class LatePool(Pool):
            async def getconn(self):
                self.gets+=1;entered.set()
                try:await gate.wait()
                except asyncio.CancelledError:await gate.wait()
                return self.connection
        pool=LatePool();worker=WorkerBoundSource(pool,BoundRaw)
        task=asyncio.create_task(worker.load_bound_source(JOB,ATTEMPT,Budget(.15)))
        await entered.wait()
        with self.assertRaises(Fault) as cm:await task
        self.assertEqual(cm.exception.code,'raw_qc_timeout')
        self.assertEqual(pool.gets,1);self.assertEqual(pool.releases,[])
        gate.set()
        for _ in range(8):await asyncio.sleep(0)
        self.assertTrue(pool.connection.closed);self.assertEqual(len(pool.releases),1)
        self.assertEqual(pool.connection.calls,[])

    async def test_v3_contract_rejects_legacy_keys_and_budget_expiry_in_decode(self):
        s,e,b=row();execution=json.loads(e)
        self.assertEqual(execution['limits_profile'],'raw_qc_limits_v3')
        execution['limits_profile']='raw_qc_limits_v2'
        await self.fault(WorkerBoundSource(Pool([(s,json.dumps(execution),b)]),BoundRaw),'raw_qc_persisted_invalid')
        execution=json.loads(e);execution['rscript']=execution.pop('executable')
        await self.fault(WorkerBoundSource(Pool([(s,json.dumps(execution),b)]),BoundRaw),'raw_qc_persisted_invalid')
        class ExpiredFactory:
            def __call__(self,**kw):
                budget.deadline=0
                return BoundRaw(**kw)
        pool=Pool();budget=Budget()
        await self.fault(WorkerBoundSource(pool,ExpiredFactory()),'raw_qc_timeout',budget=budget)
        self.assertNotIn('COMMIT',[sql for sql,p in pool.connection.calls]);self.assertTrue(pool.connection.closed)

    async def cancellation_and_abort_failure(self, *, timeout):
        pool=Pool();pool.connection.finish_error=True
        ready=asyncio.Event();gate=asyncio.Event();cancelled=asyncio.Event();seen=[]
        original=pool.connection.execute
        async def execute(sql,params=()):
            if sql!=SQL:return await original(sql,params)
            ready.set()
            try:await gate.wait()
            except asyncio.CancelledError:
                cancelled.set()
                # Deliberately ignore cancellation once, then fail late. The
                # Budget consumption callback must retrieve this exception.
                await gate.wait()
            raise RuntimeError('SYNTHETIC_PRIVATE_PARAMETER')
        pool.connection.execute=execute
        loop=asyncio.get_running_loop();old=loop.get_exception_handler()
        loop.set_exception_handler(lambda loop,context:seen.append(context))
        task=asyncio.create_task(WorkerBoundSource(pool,BoundRaw).load_bound_source(JOB,ATTEMPT,Budget()))
        try:
            await asyncio.wait_for(ready.wait(),2)
            if not timeout:task.cancel()
            with self.assertRaises((Fault,asyncio.CancelledError)) as cm:await task
            if isinstance(cm.exception,Fault):self.assertEqual(cm.exception.code,'raw_qc_persisted_invalid')
            for _ in range(8):await asyncio.sleep(0)
            self.assertTrue(cancelled.is_set(),'Underlying RPC must receive cancellation even when pgconn.finish raises')
            self.assertFalse(pool.connection.closed)
            self.assertEqual(pool.releases,[],'An uncertain socket must never be returned for reuse')
            gate.set()
            for _ in range(16):await asyncio.sleep(0)
            gc.collect()
            for _ in range(8):await asyncio.sleep(0)
            self.assertEqual(seen,[],'No unconsumed private task error may reach event-loop logging')
            self.assertEqual(pool.gets,1)
        finally:
            gate.set()
            for _ in range(8):await asyncio.sleep(0)
            loop.set_exception_handler(old)

    async def test_WS01_cancel_plus_teardown_failure_cancels_and_consumes_late_RPC(self):
        await self.cancellation_and_abort_failure(timeout=False)

    async def test_WS01_timeout_plus_teardown_failure_cancels_and_consumes_late_RPC(self):
        await self.cancellation_and_abort_failure(timeout=True)

if __name__=='__main__':unittest.main(verbosity=2)
