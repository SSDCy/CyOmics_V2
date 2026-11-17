"""Independent lifecycle fault schedules; synthetic executor, actual POSIX FS."""
import os
import unittest
if os.name != 'posix':
    raise unittest.SkipTest('Worker filesystem lifecycle tests require POSIX; no executor stub fallback')
import asyncio
import gc
import os
from pathlib import Path
import warnings
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_raw_qc_worker_once as f


class Independent(unittest.TestCase):
    setUp=f.WorkerTests.setUp
    worker=f.WorkerTests.worker
    once=f.WorkerTests.once
    def test_configuration_permission_drift_after_source_await_blocks_R(self):
        original=self.loader.load_bound_source
        async def load(*args):
            value=await original(*args)
            self.roots.staging.chmod(0o777)
            return value
        self.loader.load_bound_source=load
        outcome=self.once()
        self.assertEqual(self.executor.calls,0,'Changed private staging permission must be rejected before invoking the executor')
        self.assertNotEqual(outcome.reason,'succeeded')

    def test_replaced_lock_during_claim_does_not_reexecute_or_reclaim(self):
        original=self.repo.claim
        async def claim(*args):
            result=await original(*args)
            lock=self.roots.control/'worker.lock';lock.unlink();lock.write_bytes(b'');lock.chmod(0o600)
            return result
        self.repo.claim=claim
        outcome=self.once();self.assertEqual(outcome.status,'reconciliation')
        self.assertEqual(self.executor.calls,0)
        self.once();self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.executor.calls,0)

    def test_async_executor_result_is_closed_not_awaited_or_retried(self):
        calls=[];ran=[]
        async def forbidden():ran.append(True)
        def run(request):calls.append(request.job_id);return forbidden()
        self.executor.run=run
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always');out=self.once();gc.collect()
            self.assertEqual(out.status,'reconciliation');self.assertEqual(ran,[])
            self.assertFalse(any('never awaited' in str(w.message) for w in caught))
        self.once();self.assertEqual(len(calls),1);self.assertNotIn('transition',self.repo.calls)

    def test_untyped_artifact_error_retains_executing_no_terminal_or_rerun(self):
        def fail(*args,**kw):raise RuntimeError('SYNTHETIC_PRIVATE_ARTIFACT_ERROR')
        with patch.object(self.artifacts,'build_result',side_effect=fail):
            result=self.once()
        self.assertEqual(result.status,'reconciliation');self.assertNotIn('PRIVATE',result.reason)
        self.assertEqual(f.state(self.roots)['phase'],'executing');self.assertNotIn('transition',self.repo.calls)
        self.once();self.assertEqual(self.executor.calls,1);self.assertEqual(self.repo.calls.count('claim'),1)

    def test_receipt_permission_damage_does_not_rewrite_historical_succeeded(self):
        self.assertEqual(self.once().status,'confirmed')
        current=f.deepcopy(self.repo.current)
        receipt=next(self.roots.receipts.iterdir());receipt.chmod(0o644)
        out=self.once();self.assertEqual(out.status,'reconciliation')
        self.assertEqual(self.repo.current,current);self.assertEqual(self.executor.calls,1)
        self.assertEqual(self.repo.calls.count('transition'),1)

    def test_input_hardlink_introduced_at_fsync_prevents_execution(self):
        original=f.ie.fsync;linked=[]
        def sync(fd):
            original(fd)
            paths=list(self.roots.inputs.glob('*.tsv'))
            if paths and not linked:
                os.link(paths[0],self.roots.inputs/'alias');linked.append(True)
        with patch.object(f.ie,'fsync',side_effect=sync):out=self.once()
        self.assertTrue(linked);self.assertEqual(out.status,'reconciliation')
        self.assertEqual(self.executor.calls,0);self.assertNotIn('transition',self.repo.calls)

    def test_valid_binding_with_corrupt_row_count_is_parser_rejected(self):
        self.repo.job['source']['row_count']+=1
        source=self.repo.job['source'];execution=self.repo.job['execution']
        self.repo.job['binding_sha256']=f.c.binding_sha(self.repo.job['request_sha256'],source,execution,'Intensity')
        self.loader.source=f.deepcopy(source)
        out=self.once();self.assertEqual(out.status,'confirmed')
        self.assertEqual(self.executor.calls,0);self.assertEqual(self.repo.current['error'],{'code':'input_integrity_failed'})
        self.assertEqual(list(self.roots.inputs.iterdir()),[])

    def test_loader_cancellation_never_creates_execution_or_automatic_retry(self):
        async def cancelled(*args):raise asyncio.CancelledError()
        self.loader.load_bound_source=cancelled
        self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.executor.calls,0);self.assertEqual(f.state(self.roots)['phase'],'claimed')
        self.once();self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.executor.calls,0)

if __name__=='__main__':unittest.main(verbosity=2)
