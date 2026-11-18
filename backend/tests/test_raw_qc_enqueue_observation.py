"""SQL-shaped fake transactions only. No DB/R/network/launcher/provision."""
import asyncio
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch
import test_raw_qc_repository as f
from cyomics_projects.raw_qc_repository import EnqueueObservation,RawQCRepository

class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=f.Database();self.pool=self.db.pool()
        self.repo=RawQCRepository(self.pool,f.REGISTRATION,enqueue_observation_enabled=True)

    async def observed(self,raw=None,repo=None):
        return await (repo or self.repo).enqueue_observed(f.OWNER,f.PROJECT,f.DATASET,raw or f.body(),f.Budget())

    async def assert_fault(self,code,operation):
        with self.assertRaises(f.Fault) as caught:await operation
        self.assertEqual(caught.exception.code,code)
        self.assertNotIn('PRIVATE',str(caught.exception))

    async def test_default_disabled_strict_flag_before_any_acquire(self):
        repo=RawQCRepository(self.pool,f.REGISTRATION)
        with patch.object(self.pool,'getconn',side_effect=AssertionError('NO_ACQUIRE')):
            await self.assert_fault('raw_qc_unavailable',self.observed(repo=repo))
        self.assertEqual(self.db.sql,[])
        for flag in (1,'true',None):
            with self.assertRaises(ValueError):RawQCRepository(self.pool,f.REGISTRATION,enqueue_observation_enabled=flag)
        self.assertFalse(repo.enqueue_observation_locked)

    async def test_created_after_commit_close_release_and_isolated_frozen_snapshot(self):
        events=[];finish=f.Connection.finish;release=self.pool.release
        def closed(connection):events.append('finish');finish(connection)
        def released(connection):
            self.assertTrue(connection.closed)
            self.assertTrue(self.db.rows) # commit acknowledged before release
            events.append('release');release(connection)
        with patch.object(f.Connection,'finish',closed),patch.object(self.pool,'release',released):
            obs=await self.observed()
        self.assertIs(type(obs),EnqueueObservation)
        self.assertEqual(events,['finish','release']);self.assertTrue(obs.may_notify_new)
        self.assertTrue(obs.created);self.assertTrue(obs.commit_confirmed);self.assertTrue(obs.teardown_confirmed)
        job=obs.job_public();job['source']['source_sha256']='0'*64
        self.assertNotEqual(obs.job_public()['source']['source_sha256'],'0'*64)
        with self.assertRaises(FrozenInstanceError):obs.created=False
        self.assertNotIn(obs.job_public()['job_id'],repr(obs))
        self.assertFalse(self.repo.enqueue_observation_locked)

    async def test_same_request_replay_and_concurrent_request_local_created(self):
        raw=f.body()
        first,second=await asyncio.gather(self.observed(raw),self.observed(raw))
        self.assertEqual(sum(x.created for x in (first,second)),1)
        self.assertEqual(sum(x.may_notify_new for x in (first,second)),1)
        self.assertEqual(first.job_public(),second.job_public());self.assertEqual(len(self.db.rows),1)
        replay=await self.observed(raw)
        self.assertFalse(replay.created);self.assertTrue(replay.commit_confirmed);self.assertTrue(replay.teardown_confirmed)
        self.assertFalse(replay.may_notify_new)

    async def test_commit_unknown_sticky_lock_even_when_socket_closed_then_no_acquire(self):
        self.db.commit_error=True
        await self.assert_fault('raw_qc_submission_unknown',self.observed())
        self.assertEqual(len(self.db.rows),1);self.assertTrue(self.db.connections[0].closed)
        self.assertTrue(self.repo.enqueue_observation_locked)
        before=len(self.db.sql)
        with patch.object(self.pool,'getconn',side_effect=AssertionError('NO_RETRY')):
            await self.assert_fault('raw_qc_unavailable',self.observed())
        self.assertEqual(len(self.db.sql),before)

    async def test_close_throws_after_closed_true_never_releases_or_returns_observation(self):
        finish=f.Connection.finish;calls=[]
        def bad(connection):
            calls.append('finish');finish(connection);raise BaseException('PRIVATE_FINISH_AFTER_CLOSED')
        with patch.object(f.Connection,'finish',bad):
            await self.assert_fault('raw_qc_submission_unknown',self.observed())
        self.assertEqual(calls,['finish']);self.assertTrue(self.db.connections[0].closed)
        self.assertEqual(self.pool.releases,0);self.assertTrue(self.repo.enqueue_observation_locked)
        self.assertEqual(len(self.db.rows),1)

    async def test_release_exception_after_normal_commit_never_observation_or_retry(self):
        calls=[]
        def bad(connection):
            calls.append(connection);raise BaseException('PRIVATE_RELEASE')
        with patch.object(self.pool,'release',bad):
            await self.assert_fault('raw_qc_submission_unknown',self.observed())
        self.assertEqual(len(calls),1);self.assertTrue(calls[0].closed)
        self.assertEqual(len(self.db.rows),1);self.assertTrue(self.repo.enqueue_observation_locked)

    async def test_known_precommit_owner_or_conflict_fault_preserved_with_clean_teardown(self):
        raw=f.body();await self.observed(raw)
        await self.assert_fault('dataset_not_found',self.repo.enqueue_observed(f.OTHER,f.PROJECT,f.DATASET,b'bad',f.Budget()))
        await self.assert_fault('raw_qc_request_conflict',self.observed(f.body(f.json.loads(raw)['request_id'],'lfq')))
        self.assertFalse(self.repo.enqueue_observation_locked)
        self.assertFalse((await self.observed(raw)).may_notify_new)

    async def test_legacy_cancel_also_locks_future_observation_without_changing_cancel(self):
        started=asyncio.Event()
        async def acquire():started.set();await asyncio.Event().wait()
        with patch.object(self.pool,'getconn',side_effect=acquire):
            task=asyncio.create_task(self.repo.enqueue(f.OWNER,f.PROJECT,f.DATASET,f.body(),f.Budget()))
            await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.repo.enqueue_observation_locked)
        with patch.object(self.pool,'getconn',side_effect=AssertionError('NO_RETRY')):
            await self.assert_fault('raw_qc_unavailable',self.observed())

    async def test_cancelled_acquire_late_socket_closed_once_lock_without_callback_exception(self):
        started=asyncio.Event();gate=asyncio.Event();finished=[];connection=f.Connection(self.db,False)
        oldfinish=connection.finish
        def finish():finished.append('finish');oldfinish()
        connection.finish=finish
        async def acquire():
            started.set()
            try:await gate.wait()
            except asyncio.CancelledError:await gate.wait()
            return connection
        seen=[];loop=asyncio.get_running_loop();previous=loop.get_exception_handler()
        loop.set_exception_handler(lambda *args:seen.append('UNEXPECTED'))
        try:
            with patch.object(self.pool,'getconn',side_effect=acquire):
                task=asyncio.create_task(self.observed());await started.wait();task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(self.repo.enqueue_observation_locked)
                gate.set()
                for _ in range(5):await asyncio.sleep(0)
            self.assertTrue(connection.closed);self.assertEqual(finished,['finish'])
            self.assertEqual(self.pool.releases,1);self.assertEqual(seen,[]);self.assertEqual(self.db.rows,{})
        finally:gate.set();loop.set_exception_handler(previous)

    async def test_legacy_dto_and_worker_no_new_teardown_promise_or_callback(self):
        # Root explicitly preserves the earlier legacy release-error policy.
        with patch.object(self.pool,'release',side_effect=BaseException('PRIVATE_RELEASE')):
            dto=await self.repo.enqueue(f.OWNER,f.PROJECT,f.DATASET,f.body(),f.Budget())
        self.assertIs(type(dto),dict);self.assertEqual(set(dto),set(f.decode_record(next(iter(self.db.rows.values())))))
        self.assertTrue(self.repo.enqueue_observation_locked)
        await self.assert_fault('raw_qc_unavailable',self.observed())
        workerpool=self.db.pool(worker=True);worker=f.RawQCWorkerRepository(workerpool)
        with patch.object(workerpool,'release',side_effect=BaseException('PRIVATE_WORKER_RELEASE')):
            result=await worker.claim(str(f.uuid4()),f.Budget())
        self.assertEqual(result['job']['status'],'running')

if __name__=='__main__':unittest.main(verbosity=2)
