"""Synthetic DB/executor; real private POSIX journal. No R/DB/Auth activation."""
import os,unittest
if os.name!='posix':
    raise unittest.SkipTest('ACK journal tests require POSIX; no executor stub fallback')
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_raw_qc_worker_once as f
from cyomics_projects.raw_qc_worker import WorkerOnce
cp=f.cp

class AckTests(unittest.TestCase):
    setUp=f.WorkerTests.setUp
    worker=f.WorkerTests.worker
    once=f.WorkerTests.once

    def confirmed(self,failed=False):
        if failed:self.executor.error='output_protocol_invalid'
        self.assertEqual(self.once().status,'confirmed')
        state=f.state(self.roots);self.repo.calls.clear()
        return state
    def ack(self,state,worker=None):
        return asyncio.run((worker or self.worker()).acknowledge_confirmed(
            expected_attempt_id=state['attempt_id'],expected_job_id=state['job_id']))
    def put(self,state):
        with cp.Journal(self.roots).locked() as journal:journal.load();journal.save(state)
    def checkpoint(self):return (self.roots.control/'checkpoint.json').read_bytes()
    def history(self):
        return {str(p):p.read_bytes() for root in (self.roots.receipts,self.roots.inputs,self.roots.staging,self.roots.published)
                for p in root.rglob('*') if p.is_file()}

    def test_succeeded_exact_ack_idle_preserves_all_history(self):
        state=self.confirmed();history=self.history();db=deepcopy(self.repo.current)
        sequence=f.json.loads(self.checkpoint())['sequence']
        result=self.ack(state)
        self.assertEqual(result.status,'acknowledged');self.assertEqual(f.state(self.roots),{'phase':'idle'})
        self.assertEqual(f.json.loads(self.checkpoint())['sequence'],sequence+1)
        self.assertEqual(self.repo.calls,['read_attempt']);self.assertEqual(self.repo.current,db)
        self.assertEqual(self.history(),history);self.assertEqual(self.executor.calls,1)
    def test_failed_terminal_exact_ack_does_not_change_error(self):
        state=self.confirmed(True);db=deepcopy(self.repo.current);history=self.history()
        self.assertEqual(self.ack(state).status,'acknowledged')
        self.assertEqual(self.repo.current,db);self.assertEqual(self.history(),history)
        self.assertEqual(db['status'],'failed');self.assertEqual(self.repo.calls,['read_attempt'])
    def test_missing_config_or_invalid_ids_zero_lock_and_RPC(self):
        with patch.object(cp.Journal,'locked',side_effect=AssertionError('must not lock')):
            result=asyncio.run(WorkerOnce().acknowledge_confirmed(expected_attempt_id=None,expected_job_id=None))
            self.assertEqual(result.status,'off')
            for value in (None,{},'../x','ABCDEFAB-1111-4111-8111-111111111111'):
                result=asyncio.run(self.worker().acknowledge_confirmed(expected_attempt_id=value,expected_job_id=self.job['job_id']))
                self.assertEqual(result.status,'refused')
        self.assertEqual(self.repo.calls,[]);self.assertFalse((self.roots.control/'worker.lock').exists())
    def test_expected_ids_mismatch_or_nested_job_mismatch_before_RPC(self):
        state=self.confirmed();original=self.checkpoint()
        for key in ('attempt_id','job_id'):
            other=dict(state);other[key]=str(f.uuid4())
            self.assertEqual(self.ack(other).status,'refused')
            self.assertEqual(self.checkpoint(),original)
        bad=deepcopy(state);bad['job']['job_id']=str(f.uuid4());self.put(bad)
        self.assertEqual(self.ack(state).status,'refused');self.assertEqual(self.repo.calls,[])
    def test_every_nonconfirmed_phase_refuses_without_recovery(self):
        state=self.confirmed()
        for phase in cp.PHASES-{'confirmed'}:
            self.put(dict(state,phase=phase));before=self.checkpoint()
            self.assertEqual(self.ack(state).status,'refused');self.assertEqual(self.checkpoint(),before)
        self.assertEqual(self.repo.calls,[])
    def test_missing_or_extra_confirmed_fields_and_incomplete_next(self):
        state=self.confirmed()
        for bad in ({k:v for k,v in state.items() if k!='confirmed_job'},dict(state,extra=True)):
            self.put(bad);before=self.checkpoint()
            self.assertEqual(self.ack(state).status,'refused');self.assertEqual(self.checkpoint(),before)
        self.put(state);next_file=self.roots.control/'checkpoint.next';next_file.write_bytes(b'incomplete');next_file.chmod(0o600)
        self.assertEqual(self.ack(state).status,'refused');self.assertEqual(next_file.read_bytes(),b'incomplete')
        self.assertEqual(self.repo.calls,[])
    def test_DB_full_canonical_drift_including_finished_time_refuses(self):
        state=self.confirmed();saved=deepcopy(self.repo.current);before=self.checkpoint()
        changes={'finished_at':f.c.stamp(f.STAMP+timedelta(seconds=11)),'revision':99,'status':'failed',
                 'job_id':str(f.uuid4()),'result':None,'execution':{},'source':{}}
        for key,value in changes.items():
            self.repo.current=deepcopy(saved);self.repo.current[key]=value
            self.assertEqual(self.ack(state).status,'refused');self.assertEqual(self.checkpoint(),before)
        self.assertEqual(self.executor.calls,1)
    def test_private_receipt_damage_refuses_without_repair(self):
        state=self.confirmed();path=next(self.roots.receipts.iterdir());content=path.read_bytes()
        path.chmod(0o644);self.assertEqual(self.ack(state).status,'refused');self.assertEqual(path.read_bytes(),content)
        path.chmod(0o600);path.write_bytes(b'{}')
        self.assertEqual(self.ack(state).status,'refused');self.assertEqual(path.read_bytes(),b'{}')
        self.assertEqual(f.state(self.roots)['phase'],'confirmed')
    def test_corrupt_checkpoint_hash_or_permissions_refuses_before_RPC(self):
        state=self.confirmed();path=self.roots.control/'checkpoint.json';before=path.read_bytes()
        path.write_bytes(b'{}');self.assertEqual(self.ack(state).status,'refused');self.assertEqual(path.read_bytes(),b'{}')
        path.write_bytes(before);path.chmod(0o644)
        self.assertEqual(self.ack(state).status,'refused');self.assertEqual(path.stat().st_mode&0o777,0o644)
        self.assertEqual(self.repo.calls,[])
    def test_missing_lock_never_recreates_it(self):
        state=self.confirmed();lock=self.roots.control/'worker.lock';lock.unlink()
        self.assertEqual(self.ack(state).status,'refused');self.assertFalse(lock.exists());self.assertEqual(self.repo.calls,[])
    def test_lock_or_root_drift_during_await_blocks_idle(self):
        state=self.confirmed();original=self.repo.read_attempt;before=self.checkpoint()
        async def read(*args):
            value=await original(*args);self.roots.staging.chmod(0o777);return value
        self.repo.read_attempt=read
        self.assertEqual(self.ack(state).status,'refused');self.assertEqual(self.checkpoint(),before)
        self.roots.staging.chmod(0o700)
        async def replaced(*args):
            value=await original(*args);p=self.roots.control/'worker.lock';p.unlink();p.write_bytes(b'');p.chmod(0o600);return value
        self.repo.read_attempt=replaced
        self.assertEqual(self.ack(state).status,'refused');self.assertEqual(self.checkpoint(),before)
    def test_read_failure_or_cancel_never_ACKs_or_retries(self):
        state=self.confirmed();before=self.checkpoint()
        for exception in (f.Fault(503,'raw_qc_unavailable'),asyncio.CancelledError(),BaseException('SYNTHETIC_PRIVATE_ACK')):
            async def read(*args):raise exception
            self.repo.read_attempt=read
            result=self.ack(state);self.assertEqual(result.status,'refused');self.assertNotIn('PRIVATE',result.reason)
            self.assertEqual(self.checkpoint(),before)
        self.assertEqual(self.executor.calls,1)
    def test_file_fsync_failure_unknown_keeps_partial_next(self):
        state=self.confirmed();before=self.checkpoint()
        with patch.object(cp,'fsync',side_effect=OSError('synthetic fsync')):result=self.ack(state)
        self.assertEqual(result.status,'unknown');self.assertEqual(self.checkpoint(),before)
        self.assertTrue((self.roots.control/'checkpoint.next').exists())
        self.assertEqual(self.repo.calls,['read_attempt']);self.assertEqual(self.executor.calls,1)
    def test_directory_fsync_failure_after_replace_unknown_despite_idle(self):
        state=self.confirmed();original=cp.fsync
        def sync(fd):
            if f.os.readlink('/proc/self/fd/'+str(fd))==str(self.roots.control):raise OSError('synthetic dir fsync')
            return original(fd)
        with patch.object(cp,'fsync',side_effect=sync):result=self.ack(state)
        self.assertEqual(result.status,'unknown');self.assertEqual(f.state(self.roots),{'phase':'idle'})
        self.assertEqual(self.executor.calls,1)
    def test_replace_failure_unknown_keeps_next(self):
        state=self.confirmed();before=self.checkpoint()
        with patch.object(cp.os,'replace',side_effect=OSError('synthetic replace')):result=self.ack(state)
        self.assertEqual(result.status,'unknown');self.assertEqual(self.checkpoint(),before)
        self.assertTrue((self.roots.control/'checkpoint.next').exists())
    def test_post_idle_readback_failure_is_unknown(self):
        state=self.confirmed();original=cp.Journal.load;calls=[]
        def load(journal):
            calls.append(True)
            if len(calls)>1:raise cp.JournalFailure('synthetic readback')
            return original(journal)
        with patch.object(cp.Journal,'load',load):result=self.ack(state)
        self.assertEqual(result.status,'unknown');self.assertEqual(f.state(self.roots),{'phase':'idle'})
    def test_context_exit_failure_after_idle_is_unknown(self):
        state=self.confirmed();original=cp.Journal.locked
        @contextmanager
        def broken(journal,**kwargs):
            with original(journal,**kwargs):yield journal
            raise OSError('synthetic close uncertainty')
        with patch.object(cp.Journal,'locked',broken):result=self.ack(state)
        self.assertEqual(result.status,'unknown');self.assertEqual(f.state(self.roots),{'phase':'idle'})
    def test_two_ACKs_and_run_once_are_busy_through_read_await(self):
        state=self.confirmed();original=self.repo.read_attempt;entered=asyncio.Event();gate=asyncio.Event()
        async def read(*args):entered.set();await gate.wait();return await original(*args)
        self.repo.read_attempt=read
        async def race():
            worker=self.worker();task=asyncio.create_task(worker.acknowledge_confirmed(expected_attempt_id=state['attempt_id'],expected_job_id=state['job_id']))
            await entered.wait()
            other=await worker.acknowledge_confirmed(expected_attempt_id=state['attempt_id'],expected_job_id=state['job_id'])
            once=await worker.run_once();gate.set();first=await task
            return first,other,once
        first,other,once=asyncio.run(race())
        self.assertEqual((first.status,other.status,once.status),('acknowledged','busy','busy'))
        self.assertEqual(self.repo.calls,['read_attempt']);self.assertEqual(self.executor.calls,1)
    def test_repeated_ACK_refuses_idle_and_quarantine_remains_set(self):
        state=self.confirmed();self.executor._quarantined=True
        self.assertEqual(self.ack(state).status,'acknowledged');self.assertTrue(self.executor._quarantined)
        calls=list(self.repo.calls);self.assertEqual(self.ack(state).status,'refused');self.assertEqual(self.repo.calls,calls)
        self.assertEqual(self.once().status,'off');self.assertEqual(self.executor.calls,1)

if __name__=='__main__':unittest.main(verbosity=2)
