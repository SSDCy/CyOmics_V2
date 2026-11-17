"""P2 regression with successful export witnesses and read-only recovery."""
import os
import unittest
if os.name != 'posix':
    raise unittest.SkipTest('Worker filesystem lifecycle tests require POSIX; no executor stub fallback')
import hashlib,os,stat,unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_raw_qc_worker_once as f

class RootsR1(unittest.TestCase):
    setUp=f.WorkerTests.setUp
    worker=f.WorkerTests.worker
    once=f.WorkerTests.once

    def test_successful_source_export_then_private_root_drift_blocks_execution_and_recovery_is_GET_only(self):
        original=self.loader.load_bound_source
        async def load(*args):
            value=await original(*args)
            self.roots.staging.chmod(0o777)
            return value
        self.loader.load_bound_source=load
        outcome=self.once()
        self.assertEqual(outcome.status,'reconciliation')
        checkpoint=f.state(self.roots)
        self.assertEqual(checkpoint['phase'],'source_ready')
        self.assertEqual(checkpoint['job_id'],self.job['job_id'])
        snapshots=list(self.roots.inputs.iterdir());self.assertEqual(len(snapshots),1)
        path=snapshots[0];s=path.lstat();content=path.read_bytes()
        self.assertTrue(stat.S_ISREG(s.st_mode));self.assertEqual(stat.S_IMODE(s.st_mode),0o400)
        self.assertEqual(s.st_nlink,1);self.assertEqual(s.st_uid,os.geteuid())
        self.assertEqual(s.st_size,len(f.RAW));self.assertEqual(content,f.RAW)
        self.assertEqual(hashlib.sha256(content).hexdigest(),self.job['source']['source_sha256'])
        self.assertEqual(path.name,checkpoint['attempt_id']+'.tsv')
        self.assertEqual(checkpoint['input'],{'server_key':path.name,'bytes':len(f.RAW),
            'sha256':self.job['source']['source_sha256'],
            'selected_columns_sha256':self.job['source']['selected_columns_sha256']})
        self.assertEqual(self.executor.calls,0);self.assertEqual(self.repo.calls.count('claim'),1)
        self.assertNotIn('transition',self.repo.calls);self.assertEqual(self.loader.calls,1)
        self.assertEqual(list(self.roots.published.iterdir()),[])
        self.assertEqual(list(self.roots.receipts.iterdir()),[])
        # Restore the trusted root but never resume this unresolved attempt.
        self.roots.staging.chmod(0o700);before=len(self.repo.calls)
        recovered=self.once()
        self.assertEqual(recovered.status,'reconciliation')
        self.assertEqual(recovered.reason,'unresolved_attempt_never_resumes_R')
        self.assertEqual(self.repo.calls[before:],['read_attempt'])
        self.assertEqual(self.repo.calls.count('claim'),1);self.assertNotIn('transition',self.repo.calls)
        self.assertEqual(self.executor.calls,0);self.assertEqual(self.loader.calls,1)
        self.assertEqual(f.state(self.roots),checkpoint)
        self.assertEqual(path.read_bytes(),content);self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o400)

if __name__=='__main__':unittest.main(verbosity=2)
