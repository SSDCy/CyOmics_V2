"""Targeted in-memory async SQL-shape models, never database/Authority evidence."""
import asyncio
from datetime import datetime,timezone,timedelta
from dataclasses import FrozenInstanceError
from enum import IntEnum
import hashlib,json,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

# Deliberate isolated source-test driver stub. It cannot connect anywhere.
class TS(IntEnum):IDLE=0;ACTIVE=1;INTRANS=2;INERROR=3;UNKNOWN=4
pg=types.ModuleType('psycopg');pq=types.ModuleType('psycopg.pq');pq.TransactionStatus=TS
class NeverConnect:
    @staticmethod
    async def connect(*args,**kwargs):raise AssertionError('NO_REAL_CONNECTION')
pg.AsyncConnection=NeverConnect;pg.pq=pq
sys.modules.update({'psycopg':pg,'psycopg.pq':pq})
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects.fasta_source_repository import FastaSourceRepository,SQL
from cyomics_projects.settings import Budget,Fault

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
P='33333333-3333-4333-8333-333333333333'
S='44444444-4444-4444-8444-444444444444'
REQUEST='55555555-5555-4555-8555-555555555555'
RAW=b'>SYNTHETIC_PRIVATE\nAC\n'
STAMP=datetime(2026,11,18,1,2,3,4,tzinfo=timezone.utc)
def row():return [UUID(S),UUID(P),UUID(REQUEST),'Synthetic.fasta','protein_fasta',len(RAW),
    hashlib.sha256(RAW).hexdigest(),'stored',STAMP,RAW]

class Cursor:
    def __init__(self,rows):self.rows=list(rows);self.fetches=0
    async def fetchone(self):
        self.fetches+=1
        return self.rows.pop(0) if self.rows else None

class Connection:
    def __init__(self,rows=None):
        self.pgconn=self;self.transaction_status=TS.IDLE;self.closed=False
        self.rows=[row()] if rows is None else rows;self.calls=[];self.finishes=0
        self.session_actor='poisoned';self.local_actor=None
        self.commit_failure=False;self.finish_failure=False;self.sql_failure=None;self.cursor=None
    async def execute(self,sql,params=()):
        self.calls.append((sql,params))
        if sql==self.sql_failure:raise RuntimeError('SYNTHETIC_PRIVATE_SQL')
        if sql.startswith('BEGIN'):self.transaction_status=TS.INTRANS
        elif "set_config('app.user_id'" in sql:self.local_actor=params[0]
        elif sql==SQL:
            assert self.transaction_status==TS.INTRANS and self.local_actor==str(params[0])
            assert params[1]==params[2] and str(params[3])==S
            self.cursor=Cursor(self.rows if str(params[0])==A else [])
            return self.cursor
        elif sql in ('COMMIT','ROLLBACK'):
            self.transaction_status=TS.IDLE
            if sql=='COMMIT' and self.commit_failure:raise RuntimeError('SYNTHETIC_PRIVATE_COMMIT')
        elif "set_config('statement_timeout'" not in sql:raise AssertionError('UNEXPECTED_SQL')
        return Cursor([])
    def finish(self):
        self.finishes+=1
        if self.finish_failure:raise RuntimeError('SYNTHETIC_PRIVATE_FINISH')
        self.closed=True;self.transaction_status=TS.UNKNOWN;self.session_actor=self.local_actor=None

class Pool:
    def __init__(self,connection=None):
        self.connection=connection or Connection();self.gets=0;self.releases=[];self.release_failure=False
    async def getconn(self):self.gets+=1;return self.connection
    def release(self,c):
        assert c.closed is True
        self.releases.append(c)
        if self.release_failure:raise RuntimeError('SYNTHETIC_PRIVATE_RELEASE')

class Tests(unittest.IsolatedAsyncioTestCase):
    async def denied(self,repo,status,code,*,actor=A,project=P,source=S,budget=None):
        with self.assertRaises(Fault) as caught:
            await repo.load_owned_source(actor,project,source,budget or Budget())
        value=caught.exception
        self.assertEqual((value.status,value.code),(status,code))
        self.assertNotIn('SYNTHETIC_PRIVATE',str(value));self.assertIsNone(value.__context__)
        return value

    async def test_disabled_and_expired_zero_IO(self):
        hook=unittest.mock.Mock(side_effect=AssertionError())
        with patch('builtins.open',hook),patch('os.open',hook):
            await self.denied(FastaSourceRepository(),503,'fasta_intake_unavailable')
            with self.assertRaises(Fault):await FastaSourceRepository().load_owned_source()
        hook.assert_not_called()
        pool=Pool();await self.denied(FastaSourceRepository(pool),504,'fasta_intake_timeout',budget=Budget(-1))
        self.assertEqual(pool.gets,0)

    async def test_owner_joint_query_READ_ONLY_full_private_source_and_closed_release(self):
        pool=Pool();repo=FastaSourceRepository(pool)
        value=await repo.load_owned_source(A,P,S,Budget())
        self.assertEqual(value.content,RAW);self.assertEqual(value.source_id,S);self.assertEqual(value.project_id,P)
        self.assertNotIn('SYNTHETIC_PRIVATE',repr(value))
        with self.assertRaises(FrozenInstanceError):value.content=b'changed'
        self.assertEqual(pool.connection.calls[0][0],'BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY')
        self.assertEqual([(s,p) for s,p in pool.connection.calls if s==SQL],[(SQL,(UUID(A),UUID(P),UUID(P),UUID(S)))])
        self.assertEqual(pool.connection.cursor.fetches,2)
        self.assertEqual(pool.connection.calls[-1][0],'COMMIT')
        self.assertTrue(pool.connection.closed);self.assertEqual(pool.connection.finishes,1)
        self.assertEqual(pool.releases,[pool.connection]);self.assertIsNone(pool.connection.session_actor)
        self.assertFalse(repo.quarantined)

    async def test_missing_and_foreign_owner_have_identical_not_found(self):
        missing=Pool(Connection([]));foreign=Pool()
        one=await self.denied(FastaSourceRepository(missing),404,'fasta_source_not_found')
        two=await self.denied(FastaSourceRepository(foreign),404,'fasta_source_not_found',actor=B)
        self.assertEqual((one.status,one.code),(two.status,two.code))
        for pool in (missing,foreign):
            self.assertTrue(pool.connection.closed);self.assertEqual(len(pool.releases),1)

    async def test_invalid_actor_scope_or_client_owner_object_never_acquires(self):
        pool=Pool();repo=FastaSourceRepository(pool)
        for bad in (None,{'owner_id':A},'Bearer forged','https://host','../path','SELECT 1',True):
            await self.denied(repo,401,'unauthenticated',actor=bad)
            await self.denied(repo,400,'invalid_fasta_request',project=bad)
            await self.denied(repo,400,'invalid_fasta_request',source=bad)
        self.assertEqual(pool.gets,0)

    async def test_typed_metadata_full_length_SHA_and_model_validation(self):
        cases=[(0,S),(1,UUID(B)),(2,REQUEST),(3,'bad\ud800.fa'),(4,'proteinGroups_txt'),(5,True),
            (5,len(RAW)+1),(5,26214401),(6,'0'*64),(7,'parsed'),(8,STAMP.replace(tzinfo=None)),
            (8,STAMP.isoformat()),(9,b'SYNTHETIC_PRIVATE_CHANGED'),(9,bytearray(RAW))]
        for index,value in cases:
            bad=row();bad[index]=value
            await self.denied(FastaSourceRepository(Pool(Connection([bad]))),503,'fasta_source_integrity_failed')
        await self.denied(FastaSourceRepository(Pool(Connection([tuple(row())[:-1]]))),503,'fasta_source_integrity_failed')

    async def test_bounded_memoryview_detached_and_aware_time_normalized(self):
        buf=bytearray(RAW);r=row();r[9]=memoryview(buf)
        r[8]=STAMP.astimezone(timezone(timedelta(hours=3)))
        value=await FastaSourceRepository(Pool(Connection([r]))).load_owned_source(A,P,S,Budget())
        buf[:]=b'x'*len(buf)
        self.assertEqual(type(value.content),bytes);self.assertEqual(value.content,RAW)
        self.assertEqual(value.created_at,'2026-11-18T01:02:03.000004Z')
        bad=row();bad[9]=memoryview(bytearray(RAW))[::2]
        await self.denied(FastaSourceRepository(Pool(Connection([bad]))),503,'fasta_source_integrity_failed')

    async def test_multiple_rows_and_poisoned_transaction_do_not_return_source(self):
        await self.denied(FastaSourceRepository(Pool(Connection([row(),row()]))),503,'fasta_source_integrity_failed')
        c=Connection();c.transaction_status=TS.INTRANS;pool=Pool(c)
        await self.denied(FastaSourceRepository(pool),503,'fasta_intake_unavailable')
        self.assertEqual(c.calls,[]);self.assertTrue(c.closed);self.assertEqual(len(pool.releases),1)

    async def test_SQL_failure_sanitized_with_confirmed_rollback_and_close(self):
        c=Connection();c.sql_failure=SQL;pool=Pool(c)
        await self.denied(FastaSourceRepository(pool),503,'fasta_intake_unavailable')
        self.assertEqual(c.calls[-1][0],'ROLLBACK');self.assertEqual(c.finishes,1)
        self.assertEqual(len(pool.releases),1)

    async def test_COMMIT_unknown_not_replaced_by_cleanup_failure_or_new_SQL(self):
        for close_failure in (False,True):
            c=Connection();c.commit_failure=True;c.finish_failure=close_failure;pool=Pool(c);repo=FastaSourceRepository(pool)
            await self.denied(repo,504,'fasta_intake_timeout')
            self.assertEqual(c.calls[-1][0],'COMMIT');self.assertEqual(c.finishes,1)
            self.assertEqual(len(pool.releases),0 if close_failure else 1)
            self.assertEqual(repo.quarantined,close_failure)

    async def test_close_or_release_unknown_no_success_no_retry_and_latched(self):
        for failure in ('close','release'):
            c=Connection();pool=Pool(c);repo=FastaSourceRepository(pool)
            if failure=='close':c.finish_failure=True
            else:pool.release_failure=True
            await self.denied(repo,503,'fasta_intake_unavailable')
            self.assertTrue(repo.quarantined);self.assertEqual(c.finishes,1)
            await self.denied(repo,503,'fasta_intake_unavailable')
            self.assertEqual(pool.gets,1);self.assertEqual(len(pool.releases),0 if failure=='close' else 1)

    async def test_late_acquisition_closed_once_with_original_pool_and_no_auto_unquarantine(self):
        gate=asyncio.Event()
        class LatePool(Pool):
            async def getconn(self):
                self.gets+=1
                try:await gate.wait()
                except asyncio.CancelledError:await gate.wait()
                return self.connection
        pool=LatePool();repo=FastaSourceRepository(pool)
        await self.denied(repo,504,'fasta_intake_timeout',budget=Budget(.01))
        other=Pool();repo.pool=other
        gate.set()
        for _ in range(6):await asyncio.sleep(0)
        self.assertTrue(pool.connection.closed);self.assertEqual(pool.connection.finishes,1)
        self.assertEqual(pool.releases,[pool.connection]);self.assertEqual(other.releases,[])
        self.assertTrue(repo.quarantined)
        await self.denied(repo,503,'fasta_intake_unavailable');self.assertEqual(other.gets,0)

    async def test_pending_RPC_timeout_finish_failure_consumes_late_private_error(self):
        gate=asyncio.Event();loop=asyncio.get_running_loop();events=[];previous=loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop,context:events.append(context))
        class Pending(Connection):
            async def execute(self,sql,params=()):
                if sql==SQL:
                    self.calls.append((sql,params))
                    try:await gate.wait()
                    except asyncio.CancelledError:await gate.wait()
                    raise RuntimeError('SYNTHETIC_PRIVATE_LATE_RPC')
                return await super().execute(sql,params)
        try:
            c=Pending();c.finish_failure=True;pool=Pool(c);repo=FastaSourceRepository(pool)
            await self.denied(repo,504,'fasta_intake_timeout',budget=Budget(.02))
            self.assertTrue(repo.quarantined);self.assertEqual(c.finishes,1);self.assertEqual(pool.releases,[])
            self.assertNotIn('ROLLBACK',[sql for sql,_ in c.calls])
            gate.set()
            for _ in range(6):await asyncio.sleep(0)
            self.assertEqual(events,[])
            await self.denied(repo,503,'fasta_intake_unavailable');self.assertEqual(pool.gets,1)
        finally:gate.set();loop.set_exception_handler(previous)

    async def test_external_cancellation_preserved_without_private_exception_text(self):
        started=asyncio.Event();gate=asyncio.Event()
        class Pending(Connection):
            async def execute(self,sql,params=()):
                if sql==SQL:
                    self.calls.append((sql,params));started.set();await gate.wait()
                return await super().execute(sql,params)
        c=Pending();pool=Pool(c);repo=FastaSourceRepository(pool)
        task=asyncio.create_task(repo.load_owned_source(A,P,S,Budget()))
        await started.wait();task.cancel('SYNTHETIC_PRIVATE_CANCEL')
        with self.assertRaises(asyncio.CancelledError) as caught:await task
        self.assertEqual(caught.exception.args,())
        self.assertTrue(c.closed);self.assertEqual(c.finishes,1);self.assertEqual(len(pool.releases),1)


class StickyCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_getter_exception_stays_unknown_after_later_true(self):
        class Getter(Connection):
            def __init__(self,raise_at):
                self.armed=False;self.reads=0;self.raise_at=raise_at;self._closed_value=False
                super().__init__()
            @property
            def closed(self):
                if self.armed:
                    self.reads+=1
                    if self.reads==self.raise_at:
                        self._closed_value=True
                        raise RuntimeError('SYNTHETIC_PRIVATE_CLOSED_GETTER')
                return self._closed_value
            @closed.setter
            def closed(self,value):self._closed_value=value
            async def execute(self,sql,params=()):
                value=await super().execute(sql,params)
                if sql=='COMMIT':self.armed=True
                return value
        for raise_at in (1,2):
            c=Getter(raise_at);pool=Pool(c);repo=FastaSourceRepository(pool)
            with self.assertRaises(Fault) as caught:await repo.load_owned_source(A,P,S,Budget())
            self.assertEqual((caught.exception.status,caught.exception.code),(503,'fasta_intake_unavailable'))
            self.assertTrue(repo.quarantined);self.assertEqual(c.reads,raise_at)
            self.assertEqual(c.finishes,0 if raise_at==1 else 1);self.assertEqual(pool.releases,[])
            self.assertIs(c.closed,True)  # later observation must not erase uncertainty
            with self.assertRaises(Fault):await repo.load_owned_source(A,P,S,Budget())
            self.assertEqual(pool.gets,1);self.assertEqual(pool.releases,[])

    async def test_any_finish_BaseException_sticky_even_after_closed_true(self):
        for exception in (RuntimeError,KeyboardInterrupt,SystemExit):
            class Finish(Connection):
                def finish(self):
                    self.finishes+=1;self.closed=True;self.transaction_status=TS.UNKNOWN
                    raise exception('SYNTHETIC_PRIVATE_FINISH_AFTER_CLOSED')
            c=Finish();pool=Pool(c);repo=FastaSourceRepository(pool)
            with self.assertRaises(Fault) as caught:await repo.load_owned_source(A,P,S,Budget())
            self.assertEqual((caught.exception.status,caught.exception.code),(503,'fasta_intake_unavailable'))
            self.assertTrue(repo.quarantined);self.assertTrue(c.closed)
            self.assertEqual(c.finishes,1);self.assertEqual(pool.releases,[])
            with self.assertRaises(Fault):await repo.load_owned_source(A,P,S,Budget())
            self.assertEqual(pool.gets,1)

    async def test_COMMIT_unknown_preserved_with_sticky_finish_after_closed(self):
        class Finish(Connection):
            def finish(self):
                self.finishes+=1;self.closed=True;self.transaction_status=TS.UNKNOWN
                raise RuntimeError('SYNTHETIC_PRIVATE_FINISH_COMMIT_UNKNOWN')
        c=Finish();c.commit_failure=True;pool=Pool(c);repo=FastaSourceRepository(pool)
        with self.assertRaises(Fault) as caught:await repo.load_owned_source(A,P,S,Budget())
        self.assertEqual((caught.exception.status,caught.exception.code),(504,'fasta_intake_timeout'))
        self.assertTrue(repo.quarantined);self.assertTrue(c.closed)
        self.assertEqual(c.finishes,1);self.assertEqual(pool.releases,[])
        self.assertEqual(c.calls[-1][0],'COMMIT');self.assertNotIn('ROLLBACK',[s for s,_ in c.calls])
        self.assertNotIn('SYNTHETIC_PRIVATE',str(caught.exception));self.assertIsNone(caught.exception.__context__)

if __name__=='__main__':unittest.main(verbosity=2)
