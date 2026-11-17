"""Memory/async cleanup regressions. No database, network, R or service."""
import asyncio,gc,weakref,unittest
from uuid import uuid4
from test_raw_qc_contract import OWNER,PROJECT,DATASET,REGISTRATION,Budget,Fault,body,TS
from test_raw_qc_repository import Database,Connection,Cursor
from cyomics_projects.repository import FixedPool
from cyomics_projects.raw_qc_repository import RawQCRepository,RawQCWorkerRepository

class FinishFailure(RuntimeError):pass
class ReleaseFailure(RuntimeError):pass

class CountConnection(Connection):
    def __init__(self,db,worker=True,finish_error=False):
        super().__init__(db,worker);self.finish_calls=0;self.finish_error=finish_error
    def finish(self):
        self.finish_calls+=1
        if self.finish_error:raise FinishFailure('synthetic teardown error')
        super().finish()

def pool_for(connection,late=False,release_error=False):
    pool=FixedPool();pool.gets=pool.release_calls=0;pool.ready=asyncio.Event();pool.gate=asyncio.Event()
    async def getconn():
        pool.gets+=1;await pool._slots.acquire();pool._active.add(connection);pool.ready.set()
        if late:
            try:await pool.gate.wait()
            except asyncio.CancelledError:await pool.gate.wait()
        return connection
    pool.getconn=getconn;release=pool.release
    def counted(c):
        pool.release_calls+=1
        if release_error:raise ReleaseFailure('synthetic release error')
        release(c)
    pool.release=counted
    return pool

class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def turns(self):
        for _ in range(12):await asyncio.sleep(0)

    async def test_normal_close_and_release_exactly_once_no_idle_reuse(self):
        connection=CountConnection(Database());pool=pool_for(connection)
        self.assertIsNone(await RawQCWorkerRepository(pool).claim(str(uuid4()),Budget()))
        self.assertTrue(connection.closed);self.assertEqual(connection.finish_calls,1)
        self.assertEqual(pool.release_calls,1);self.assertEqual(pool.gets,1)
        self.assertFalse(pool._idle);self.assertFalse(pool._active);self.assertEqual(pool._slots._value,3)

    async def test_late_failed_close_withholds_capacity_without_callback_error(self):
        connection=CountConnection(Database(),finish_error=True);pool=pool_for(connection,late=True)
        loop=asyncio.get_running_loop();seen=[];previous=loop.get_exception_handler();loop.set_exception_handler(lambda l,c:seen.append(c))
        try:
            task=asyncio.create_task(RawQCWorkerRepository(pool).claim(str(uuid4()),Budget()))
            await pool.ready.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            pool.gate.set();await self.turns()
            self.assertFalse(connection.closed);self.assertEqual(connection.finish_calls,1)
            self.assertEqual(pool.release_calls,0);self.assertEqual(pool._slots._value,2)
            self.assertIn(connection,pool._active);self.assertFalse(pool._idle);self.assertEqual(seen,[])
        finally:pool.gate.set();loop.set_exception_handler(previous)

    async def test_owner_commit_unknown_preserved_and_open_idle_withheld(self):
        db=Database();db.commit_error=True
        connection=CountConnection(db,worker=False,finish_error=True);pool=pool_for(connection)
        with self.assertRaises(Fault) as caught:
            await RawQCRepository(pool,REGISTRATION).enqueue(OWNER,PROJECT,DATASET,body(),Budget())
        self.assertEqual(caught.exception.code,'raw_qc_submission_unknown');self.assertEqual(len(db.rows),1)
        self.assertFalse(connection.closed);self.assertEqual(connection.transaction_status,TS.IDLE)
        self.assertEqual(connection.finish_calls,1);self.assertEqual(pool.release_calls,0)
        self.assertFalse(pool._idle);self.assertIn(connection,pool._active);self.assertEqual(pool._slots._value,2)
        self.assertEqual(pool.gets,1)

    async def test_release_error_cannot_replace_dispatched_commit_unknown(self):
        db=Database();db.commit_error=True;connection=CountConnection(db);pool=pool_for(connection,release_error=True)
        with self.assertRaises(Fault) as caught:await RawQCWorkerRepository(pool).claim(str(uuid4()),Budget())
        self.assertEqual(caught.exception.code,'raw_qc_transition_unknown')
        self.assertTrue(connection.closed);self.assertEqual(connection.finish_calls,1);self.assertEqual(pool.release_calls,1)
        self.assertFalse(pool._idle);self.assertEqual(pool.gets,1)

    async def test_release_error_after_known_success_is_nonthrowing_no_retry(self):
        connection=CountConnection(Database());pool=pool_for(connection,release_error=True)
        self.assertIsNone(await RawQCWorkerRepository(pool).claim(str(uuid4()),Budget()))
        self.assertTrue(connection.closed);self.assertEqual(connection.finish_calls,1)
        self.assertEqual(pool.release_calls,1);self.assertEqual(pool.gets,1);self.assertFalse(pool._idle)
        # Deliberately withheld capacity if release itself cannot complete.
        self.assertEqual(pool._slots._value,2)

    async def test_fetch_cancel_failed_close_cancels_consumes_and_never_rolls_back(self):
        db=Database();connection=CountConnection(db,finish_error=True);pool=pool_for(connection)
        started=asyncio.Event();gate=asyncio.Event();cancelled=asyncio.Event();original=connection.execute
        class Held(Cursor):
            async def fetchone(self):
                connection.transaction_status=TS.INTRANS;started.set()
                try:await gate.wait()
                except asyncio.CancelledError:cancelled.set();raise
        async def execute(sql,params=()):
            result=await original(sql,params)
            return Held() if 'raw_qc_read_attempt' in sql else result
        connection.execute=execute
        task=asyncio.create_task(RawQCWorkerRepository(pool).read_attempt(str(uuid4()),str(uuid4()),Budget()))
        try:
            await started.wait();before=len(db.sql);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            await self.turns();self.assertTrue(cancelled.is_set())
            self.assertEqual(len(db.sql),before);self.assertNotIn('ROLLBACK',[s for s,p in db.sql])
            self.assertEqual(connection.finish_calls,1);self.assertEqual(pool.release_calls,0);self.assertFalse(pool._idle)
        finally:gate.set()

    async def test_cancellation_resistant_fetch_late_error_is_consumed(self):
        db=Database();connection=CountConnection(db,finish_error=True);pool=pool_for(connection)
        started=asyncio.Event();gate=asyncio.Event();original=connection.execute;taskref=[]
        class Held(Cursor):
            async def fetchone(self):
                taskref.append(weakref.ref(asyncio.current_task()));started.set()
                try:await gate.wait()
                except asyncio.CancelledError:await gate.wait()
                raise RuntimeError('SYNTHETIC_PRIVATE_LATE_FETCH_ERROR')
        async def execute(sql,params=()):
            result=await original(sql,params)
            return Held() if 'raw_qc_read_attempt' in sql else result
        connection.execute=execute
        loop=asyncio.get_running_loop();seen=[];previous=loop.get_exception_handler();loop.set_exception_handler(lambda l,c:seen.append(c))
        try:
            task=asyncio.create_task(RawQCWorkerRepository(pool).read_attempt(str(uuid4()),str(uuid4()),Budget()))
            await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            gate.set();await self.turns();gc.collect();await self.turns()
            self.assertEqual(seen,[]);child=taskref[0]()
            if child is not None:self.assertFalse(child._log_traceback)
            self.assertEqual(connection.finish_calls,1);self.assertEqual(pool.release_calls,0);self.assertFalse(pool._idle)
        finally:gate.set();loop.set_exception_handler(previous)

    async def test_dirty_rollback_failure_preserves_primary_fault_and_withholds(self):
        db=Database();db.rollback_error=True;connection=CountConnection(db,finish_error=True);connection.transaction_status=TS.INTRANS
        pool=pool_for(connection)
        with self.assertRaises(Fault) as caught:await RawQCWorkerRepository(pool).claim(str(uuid4()),Budget())
        self.assertEqual(caught.exception.code,'raw_qc_unavailable');self.assertEqual(connection.finish_calls,1)
        self.assertFalse(connection.closed);self.assertEqual(pool.release_calls,0);self.assertIn(connection,pool._active);self.assertFalse(pool._idle)

if __name__=='__main__':unittest.main(verbosity=2)
