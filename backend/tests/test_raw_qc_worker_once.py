"""Synthetic repository/executor lifecycle; actual private POSIX fsync/flock.

No database, R, network or runtime activation. Uses public v3 source directly.
"""
import os
import unittest
if os.name != 'posix':
    raise unittest.SkipTest('Worker filesystem lifecycle tests require POSIX; no executor stub fallback')
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import sys
import tempfile
import threading
from types import SimpleNamespace,MappingProxyType
import unittest
from unittest.mock import patch
from uuid import UUID,uuid4

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from cyomics_projects import raw_qc_contract as c,raw_qc_artifacts as artifacts
from cyomics_projects import raw_qc_checkpoint as cp,raw_qc_input_export as ie
from cyomics_projects.raw_qc_worker import WorkerOnce
from cyomics_projects.r_raw_qc.raw_qc_runner import Limits,Pin,RunResult,RunnerFailure
from cyomics_projects.import_report import SourceRef
from cyomics_projects.protein_groups_parser import parse_protein_groups
from dataclasses import asdict
from cyomics_projects.settings import Fault

RAW=b'Protein IDs\tIntensity s\tLFQ intensity s\nP001\t0\t1\nP002\t\t2\n'
PROJECT='33333333-3333-4333-8333-333333333333';FILE='44444444-4444-4444-8444-444444444444'
STAMP=datetime(2026,11,18,tzinfo=timezone.utc)
V3='fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0'


def state(roots):return json.loads((roots.control/'checkpoint.json').read_text())['state']


def fixture(roots):
    sha=hashlib.sha256(RAW).hexdigest();dataset='55555555-5555-4555-8555-555555555555'
    parsed=parse_protein_groups(SourceRef(PROJECT,FILE,'raw_bytea_v1:'+FILE+':'+sha,len(RAW),sha),(RAW,)).parsed
    selected=[asdict(x) for x in parsed.columns if x.quant_family=='Intensity']
    execution={'script':{'bytes':4949,'sha256':c.SCRIPT_SHA},'runner':{'bytes':477,'sha256':'a'*64},
        'output_validator':{'bytes':456,'sha256':'b'*64},'executable':{'bytes':789,'sha256':'c'*64},
        'r_version':'R version 4.6.1','entry_kind':'native_R','limits_profile':'raw_qc_limits_v3','limits_sha256':V3}
    registration=c.ExecutionRegistration(c.canonical(execution))
    src={'project_id':PROJECT,'dataset_version_id':dataset,'file_id':FILE,
        'source_bytes':len(RAW),'source_sha256':sha,'parser_contract':'protein_groups_txt_v1',
        'config_sha256':'1'*64,'report_sha256':'2'*64,'row_count':len(parsed.rows),
        'column_count':len(parsed.columns),'selected_columns_sha256':c.digest(c.canonical(selected))}
    request=c.request_sha(PROJECT,dataset,'Intensity')
    job={'schemaVersion':1,'job_id':str(uuid4()),'request_id':str(uuid4()),'request_sha256':request,
        'binding_sha256':c.binding_sha(request,src,execution,'Intensity'),'source':src,'quant_family':'Intensity',
        'params':{},'execution':execution,'status':'running','revision':2,'created_at':c.stamp(STAMP),
        'started_at':c.stamp(STAMP+timedelta(seconds=1)),'finished_at':None,'observation':'confirmed','error':None,'result':None}
    return registration,job


class FakeRepository:
    def __init__(self,roots,job):
        self.roots=roots;self.job=deepcopy(job);self.current=None;self.attempt=None;self.calls=[]
        self.claim_mode=None;self.commit_mode=None;self.read_failures=0;self.paused=None;self.release=None
    async def claim(self,attempt,budget):
        self.calls.append('claim');before=state(self.roots)
        assert before['phase']=='claim_intent' and before['attempt_id']==attempt
        if self.paused is not None:self.paused.set();await self.release.wait()
        self.attempt=attempt
        if self.claim_mode=='none':return None
        if self.claim_mode=='unknown_no_row':raise Fault(504,'raw_qc_transition_unknown')
        self.current=deepcopy(self.job)
        if self.claim_mode=='unknown':raise Fault(504,'raw_qc_transition_unknown')
        return {'job':deepcopy(self.current),'attempt_id':attempt}
    async def recover_claim(self,attempt,budget):
        self.calls.append('recover_claim');assert attempt==self.attempt
        return deepcopy(self.current)
    async def read_attempt(self,job,attempt,budget):
        self.calls.append('read_attempt');assert attempt==self.attempt and job==self.job['job_id']
        if self.read_failures:
            self.read_failures-=1;raise Fault(503,'raw_qc_unavailable')
        return deepcopy(self.current)
    async def transition(self,job,attempt,revision,target,budget,*,result_bytes=None,error_code=None):
        self.calls.append('transition');intent=state(self.roots)
        assert intent['phase']=='commit_intent' and intent['attempt_id']==attempt
        assert (self.roots.receipts/intent['private_record']['name']).exists()
        if self.commit_mode=='conflict':
            self.current['revision']+=1;self.current['observation']='reconciliation_pending'
            raise Fault(409,'raw_qc_attempt_conflict')
        assert self.current['revision']==revision
        self.current.update(status=target,revision=revision+1,observation='confirmed',
            finished_at=c.stamp(STAMP+timedelta(seconds=10)),
            error=None if error_code is None else {'code':error_code},
            result=None if result_bytes is None else json.loads(result_bytes))
        if self.commit_mode=='unknown':
            self.read_failures=1;raise Fault(504,'raw_qc_transition_unknown')
        return deepcopy(self.current)


class FakeLoader:
    def __init__(self,repo):
        self.repo=repo;self.source=deepcopy(repo.job['source']);self.execution=deepcopy(repo.job['execution'])
        self.content=RAW;self.calls=0;self.wrong_attempt=False
    async def load_bound_source(self,job,attempt,budget):
        self.calls+=1
        return ie.BoundRaw(job,str(uuid4()) if self.wrong_attempt else attempt,
            json.loads(c.canonical(self.source)),json.loads(c.canonical(self.execution)),self.content)


class Handle:
    def __init__(self,fd,version):self.fd=fd;self.version=version
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None


class FakeExecutor:
    def __init__(self,roots,registration):
        e=registration.snapshot();self.roots=roots;self.calls=0;self._quarantined=False
        self.error=None;self.extra=False;self.interrupt=False;self.bad_receipt=False
        self.config=SimpleNamespace(input_root=roots.inputs,staging_root=roots.staging,publish_root=roots.published,
            script_pin=Pin(**e['script']),executable_pin=Pin(**e['executable']),entry_kind='native_R',
            expected_r_version='R version 4.6.1',limits=Limits())
    def open_authorized_input(self,key,version):
        assert '/' not in key and key.endswith('.tsv')
        fd=os.open(self.roots.inputs/key,os.O_RDONLY|os.O_NOFOLLOW)
        assert os.fstat(fd).st_mode&0o777==0o400
        assert hashlib.sha256(os.pread(fd,version.bytes+1,0)).hexdigest()==version.sha256
        return Handle(fd,version)
    def run(self,request):
        assert threading.current_thread() is threading.main_thread()
        assert state(self.roots)['phase']=='executing'
        self.calls+=1
        if self.interrupt:raise KeyboardInterrupt('SYNTHETIC crash boundary')
        if self.error:raise RunnerFailure(self.error)
        root=self.roots.published/request.job_id;root.mkdir(mode=0o700)
        contents={n:('SYNTHETIC ONLY lifecycle artifact '+n+'\n').encode() for n in c.OUTPUTS}
        for n,data in contents.items():
            p=root/n;p.write_bytes(data);p.chmod(0o600)
        if self.extra:(root/'private-receipt-is-forbidden.json').write_text('{}')
        v=request.input_handle.version
        receipt={'job_id':request.job_id,'dataset_version_id':v.dataset_version_id,'file_id':v.file_id,
            'input_bytes':v.bytes,'input_sha256':v.sha256,'quant_family':request.quant_family,
            'script_sha256':self.config.script_pin.sha256,'executable_sha256':self.config.executable_pin.sha256,
            'entry_kind':'native_R','limits_profile':'raw_qc_limits_v3','limits_sha256':V3,
            'limits':{'monitor_seconds':0.05},'outputs':{n:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for n,data in contents.items()}}
        if self.bad_receipt:receipt['input_sha256']='0'*64
        return RunResult(root,receipt)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        for n in ('control','inputs','staging','published','receipts'):(base/n).mkdir(mode=0o700)
        self.roots=cp.WorkerRoots(*(base/n for n in ('control','inputs','staging','published','receipts')))
        self.reg,self.job=fixture(self.roots)
        self.repo=FakeRepository(self.roots,self.job);self.loader=FakeLoader(self.repo)
        self.executor=FakeExecutor(self.roots,self.reg)
        self.artifacts=artifacts.RawQCArtifacts(self.roots.published,self.reg,clock=lambda:STAMP+timedelta(seconds=2))
        self.exporter=ie.InputExporter(self.roots.inputs)
    def worker(self,**changes):
        values=dict(repository=self.repo,registration=self.reg,executor=self.executor,artifacts=self.artifacts,
            source_loader=self.loader,exporter=self.exporter,roots=self.roots,budget_factory=object)
        values.update(changes);return WorkerOnce(**values)
    def once(self,worker=None):return asyncio.run((worker or self.worker()).run_once())

    def test_default_and_partial_dependencies_zero_calls_and_no_filesystem(self):
        with patch.object(cp,'open_root',side_effect=AssertionError('default must not touch FS')):
            self.assertEqual(asyncio.run(WorkerOnce().run_once()).status,'off')
            self.assertEqual(self.once(self.worker(source_loader=None)).status,'off')
        self.assertEqual(self.repo.calls,[]);self.assertEqual(self.executor.calls,0)

    def test_privileged_or_non_main_thread_context_is_off_before_claim(self):
        with patch.object(os,'geteuid',return_value=0):self.assertEqual(self.once().status,'off')
        with patch.object(threading,'current_thread',return_value=object()):self.assertEqual(self.once().status,'off')
        self.assertEqual(self.repo.calls,[]);self.assertEqual(self.executor.calls,0)

    def test_success_single_claim_mainthread_seven_files_and_private_receipt_outside_publish(self):
        real=artifacts.os.fsync;syncs=[]
        with patch.object(artifacts.os,'fsync',side_effect=lambda fd:(syncs.append(fd),real(fd))[1]):out=self.once()
        self.assertEqual(out.status,'confirmed');self.assertEqual(self.executor.calls,1)
        self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.repo.calls.count('transition'),1)
        self.assertEqual(len(syncs),9)
        self.assertEqual(set(p.name for p in (self.roots.published/self.job['job_id']).iterdir()),c.OUTPUTS)
        self.assertEqual(state(self.roots)['phase'],'confirmed')
        private=list(self.roots.receipts.iterdir());self.assertEqual(len(private),1)
        record=json.loads(private[0].read_text());self.assertEqual(record['transition']['target'],'succeeded')
        snapshot=next(self.roots.inputs.iterdir());self.assertEqual(snapshot.read_bytes(),RAW);self.assertEqual(snapshot.stat().st_mode&0o777,0o400)
        c.result_value(self.repo.current['result'],self.job)
        again=self.once();self.assertEqual(again.status,'confirmed');self.assertEqual(self.executor.calls,1)
        self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.repo.calls.count('transition'),1)

    def test_confirmed_no_job_resets_only_checkpoint_without_historical_receipts(self):
        self.repo.claim_mode='none'
        for _ in range(3):self.assertEqual(self.once().status,'idle')
        self.assertEqual(state(self.roots),{'phase':'idle'})
        self.assertEqual(set(p.name for p in self.roots.control.iterdir()),{'worker.lock','checkpoint.json'})
        self.assertFalse(list(self.roots.receipts.iterdir()));self.assertEqual(self.executor.calls,0)

    def test_two_async_workers_are_excluded_by_actual_kernel_flock(self):
        async def run():
            self.repo.paused=asyncio.Event();self.repo.release=asyncio.Event()
            first=asyncio.create_task(self.worker().run_once());await self.repo.paused.wait()
            second=await self.worker().run_once();self.assertEqual(second.status,'busy')
            self.assertEqual(self.repo.calls.count('claim'),1)
            self.repo.release.set();return await first
        self.assertEqual(asyncio.run(run()).status,'confirmed');self.assertEqual(self.executor.calls,1)

    @unittest.skipUnless(hasattr(os,'fork'),'actual POSIX fork/flock check')
    def test_kernel_flock_excludes_second_OS_process_without_any_executor(self):
        with cp.Journal(self.roots).locked():
            read,write=os.pipe();pid=os.fork()
            if pid==0:
                os.close(read)
                try:
                    try:
                        with cp.Journal(self.roots).locked():message=b'UNSAFE'
                    except cp.WorkerBusy:message=b'BUSY'
                    os.write(write,message)
                finally:os.close(write);os._exit(0)
            os.close(write)
            try:
                self.assertTrue(select.select([read],[],[],2)[0]);self.assertEqual(os.read(read,32),b'BUSY')
                self.assertEqual(os.waitpid(pid,0)[1],0)
            finally:os.close(read)
        self.assertEqual(self.repo.calls,[])

    def test_claim_unknown_restart_recovers_only_and_never_first_or_second_R(self):
        self.repo.claim_mode='unknown'
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(state(self.roots)['phase'],'claim_intent')
        self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.executor.calls,0);self.assertEqual(self.loader.calls,0)
        self.assertEqual(self.repo.calls.count('recover_claim'),2)

    def test_unknown_claim_read_none_is_not_a_confirmed_no_job_or_new_attempt(self):
        self.repo.claim_mode='unknown_no_row'
        self.assertEqual(self.once().status,'reconciliation');attempt=state(self.roots)['attempt_id']
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(state(self.roots)['attempt_id'],attempt)
        self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.executor.calls,0)

    def test_commit_unknown_restart_GET_confirms_exact_result_without_double_R_or_CAS(self):
        self.repo.commit_mode='unknown'
        first=self.once();self.assertEqual(first.status,'reconciliation');self.assertEqual(state(self.roots)['phase'],'commit_intent')
        checkpoint=state(self.roots);self.assertEqual(self.executor.calls,1)
        self.assertTrue((self.roots.receipts/checkpoint['private_record']['name']).exists())
        second=self.once();self.assertEqual(second.status,'confirmed');self.assertEqual(self.executor.calls,1)
        self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.repo.calls.count('transition'),1)

    def test_crash_after_durable_executing_marker_does_not_resume_R(self):
        self.executor.interrupt=True
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(state(self.roots)['phase'],'executing')
        self.executor.interrupt=False;self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.executor.calls,1);self.assertEqual(self.repo.calls.count('claim'),1)

    def test_source_bytes_mismatch_is_failed_once_with_zero_R(self):
        self.loader.content=RAW+b'x'
        self.assertEqual(self.once().status,'confirmed');self.assertEqual(self.executor.calls,0)
        self.assertEqual(self.repo.current['error'],{'code':'input_integrity_failed'})
        self.assertFalse(list(self.roots.inputs.iterdir()))

    def test_bound_source_or_attempt_mismatch_forbids_R(self):
        self.loader.wrong_attempt=True
        self.assertEqual(self.once().status,'confirmed');self.assertEqual(self.executor.calls,0)
        self.assertEqual(self.repo.current['error'],{'code':'input_integrity_failed'})

    def test_selected_column_digest_and_job_source_binding_are_rechecked(self):
        self.repo.job['source']['selected_columns_sha256']='f'*64
        self.repo.job['binding_sha256']=c.binding_sha(self.repo.job['request_sha256'],self.repo.job['source'],self.repo.job['execution'],'Intensity')
        self.loader.source=deepcopy(self.repo.job['source'])
        self.assertEqual(self.once().status,'confirmed');self.assertEqual(self.executor.calls,0)
        self.assertEqual(self.repo.current['error'],{'code':'input_integrity_failed'})

    def test_exporter_rejects_mapping_proxy_and_snapshots_mutable_loader_dicts(self):
        attempt=str(uuid4())
        bound=ie.BoundRaw(self.job['job_id'],attempt,MappingProxyType(self.job['source']),deepcopy(self.job['execution']),RAW)
        with self.assertRaises(ie.InputIntegrityFailure):self.exporter.prepare(self.job,attempt,bound,self.reg)
        self.assertFalse(list(self.roots.inputs.iterdir()))
        bound=ie.BoundRaw(self.job['job_id'],attempt,deepcopy(self.job['source']),deepcopy(self.job['execution']),RAW)
        original=ie.parse_protein_groups
        def parse(*args,**kwargs):
            bound.source['selected_columns_sha256']='f'*64
            bound.execution['runner']['sha256']='f'*64
            return original(*args,**kwargs)
        with patch.object(ie,'parse_protein_groups',side_effect=parse):
            exported=self.exporter.prepare(self.job,attempt,bound,self.reg)
        self.assertEqual(exported.selected_columns_sha256,self.job['source']['selected_columns_sha256'])
        self.assertEqual((self.roots.inputs/exported.server_key).read_bytes(),RAW)
        self.assertEqual(self.executor.calls,0)

    def test_executor_or_v2_registration_mismatch_is_off_before_claim(self):
        self.executor.config.executable_pin=Pin(1,'f'*64)
        self.assertEqual(self.once().status,'off');self.assertEqual(self.repo.calls,[])
        self.executor.config.executable_pin=Pin(**self.reg.snapshot()['executable'])
        old=deepcopy(self.job['execution']);old.update(limits_profile='raw_qc_limits_v2',limits_sha256=c.limits_sha256())
        self.assertEqual(self.once(self.worker(registration=c.ExecutionRegistration(c.canonical(old)))).status,'off')
        self.assertEqual(self.executor.calls,0)

    def test_explicit_R_failure_maps_to_existing_error_with_confirmed_cleanup(self):
        self.executor.error='wall_timeout'
        self.assertEqual(self.once().status,'confirmed');self.assertEqual(self.repo.current['error'],{'code':'resource_limit'})
        self.assertEqual(self.executor.calls,1);self.assertEqual(self.repo.calls.count('transition'),1)

    def test_quarantined_or_cleanup_unknown_stops_without_terminal_failure_or_next_job(self):
        original=self.executor.run
        def run(request):
            self.executor._quarantined=True;self.executor.error='process_cleanup_unconfirmed';return original(request)
        with patch.object(self.executor,'run',side_effect=run):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(state(self.roots)['phase'],'executing');self.assertNotIn('transition',self.repo.calls)
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.executor.calls,1)

    def test_extra_artifact_or_bad_receipt_is_not_success_and_private_record_stays_outside(self):
        self.executor.extra=True
        self.assertEqual(self.once().status,'confirmed')
        self.assertEqual(self.repo.current['status'],'failed');self.assertEqual(self.repo.current['error'],{'code':'publish_integrity_failed'})
        self.assertEqual(len(list(self.roots.receipts.iterdir())),1)
        self.assertEqual(len(list((self.roots.published/self.job['job_id']).iterdir())),8) # retained, never cleaned/deleted

    def test_CAS_conflict_retains_intent_and_never_reexecutes_or_reclaims(self):
        self.repo.commit_mode='conflict'
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(state(self.roots)['phase'],'commit_intent')
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.executor.calls,1)
        self.assertEqual(self.repo.calls.count('transition'),1);self.assertEqual(self.repo.calls.count('claim'),1)

    def test_private_receipt_tamper_prevents_unknown_commit_confirmation(self):
        self.repo.commit_mode='unknown';self.assertEqual(self.once().status,'reconciliation')
        private=next(self.roots.receipts.iterdir());private.write_bytes(b'{}')
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(state(self.roots)['phase'],'commit_intent')
        self.assertEqual(self.executor.calls,1)

    def test_historical_succeeded_not_rewritten_after_later_download_artifact_corruption(self):
        self.assertEqual(self.once().status,'confirmed')
        published=self.roots.published/self.job['job_id']/'summary.tsv'
        published.write_bytes(b'SYNTHETIC later corruption')
        self.assertEqual(self.once().status,'confirmed') # DB confirmation, not download-health assertion
        self.assertEqual(self.repo.current['status'],'succeeded')
        self.assertEqual(self.repo.calls.count('transition'),1);self.assertEqual(self.executor.calls,1)

    def test_bound_execution_mismatch_and_reverse_attempt_change_both_forbid_R(self):
        self.loader.execution['runner']['sha256']='e'*64
        self.assertEqual(self.once().status,'confirmed');self.assertEqual(self.executor.calls,0)
        self.assertEqual(self.repo.current['error'],{'code':'input_integrity_failed'})

    def test_changed_reverse_attempt_before_export_stops_reconciliation_without_R(self):
        original=self.repo.read_attempt
        async def read(*args):
            result=await original(*args);result['revision']+=1;return result
        with patch.object(self.repo,'read_attempt',side_effect=read):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.loader.calls,0);self.assertEqual(self.executor.calls,0)
        self.assertNotIn('transition',self.repo.calls)

    def test_executor_config_changed_after_source_read_is_rejected_before_R(self):
        original=self.loader.load_bound_source
        async def read(*args):
            result=await original(*args)
            self.executor.config.limits=replace(self.executor.config.limits,cpu_seconds=46)
            return result
        with patch.object(self.loader,'load_bound_source',side_effect=read):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(state(self.roots)['phase'],'source_ready');self.assertEqual(self.executor.calls,0)
        self.assertNotIn('transition',self.repo.calls)

    def test_every_unresolved_checkpoint_phase_only_READS_or_stops(self):
        for phase in ('claimed','source_ready','executing','result_ready','reconciliation'):
            with self.subTest(phase=phase):
                # The only mutable file is this test's own private checkpoint.
                self.repo.attempt=str(uuid4());self.repo.current=deepcopy(self.job)
                with cp.Journal(self.roots).locked() as journal:
                    journal.load();journal.save({'phase':phase,'attempt_id':self.repo.attempt,'job_id':self.job['job_id'],'job':self.job})
                self.assertEqual(self.once().status,'reconciliation')
        self.assertNotIn('claim',self.repo.calls);self.assertNotIn('transition',self.repo.calls)
        self.assertEqual(self.executor.calls,0);self.assertEqual(self.loader.calls,0)

    def test_input_fsync_failure_preserves_snapshot_and_prevents_R_or_CAS(self):
        with patch.object(ie,'fsync',side_effect=OSError('SYNTHETIC input fsync')):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.executor.calls,0);self.assertNotIn('transition',self.repo.calls)
        self.assertTrue(list(self.roots.inputs.iterdir()));self.assertEqual(state(self.roots)['phase'],'claimed')
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.executor.calls,0)

    def test_checkpoint_fsync_failure_before_claim_dispatch_has_zero_claims(self):
        with patch.object(cp,'fsync',side_effect=OSError('SYNTHETIC checkpoint fsync')):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.repo.calls,[]);self.assertTrue((self.roots.control/'checkpoint.next').exists())
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.repo.calls,[])

    def test_executing_and_commit_checkpoint_fsync_failures_never_cross_the_barrier(self):
        target='executing';phase=[None];original_save=cp.Journal.save;real_sync=cp.fsync
        def save(journal,value):
            phase[0]=value['phase']
            try:return original_save(journal,value)
            finally:phase[0]=None
        def sync(fd):
            if phase[0]==target:raise OSError('SYNTHETIC phase fsync')
            return real_sync(fd)
        with patch.object(cp.Journal,'save',save),patch.object(cp,'fsync',sync):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.executor.calls,0);self.assertNotIn('transition',self.repo.calls)
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.executor.calls,0)

    def test_private_receipt_fsync_failure_after_execution_never_CAS_or_R_again(self):
        real=cp.fsync
        def sync(fd):
            if '.receipt.json' in os.readlink('/proc/self/fd/'+str(fd)):raise OSError('SYNTHETIC receipt fsync')
            return real(fd)
        with patch.object(cp,'fsync',sync):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.executor.calls,1);self.assertNotIn('transition',self.repo.calls)
        self.assertEqual(state(self.roots)['phase'],'executing');self.assertTrue(list(self.roots.receipts.iterdir()))
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.executor.calls,1)

    def test_commit_intent_fsync_failure_keeps_durable_result_but_no_CAS(self):
        original=cp.Journal.save;real=cp.fsync;active=[False]
        def save(j,value):
            active[0]=value['phase']=='commit_intent'
            try:return original(j,value)
            finally:active[0]=False
        def sync(fd):
            if active[0]:raise OSError('SYNTHETIC commit intent fsync')
            return real(fd)
        with patch.object(cp.Journal,'save',save),patch.object(cp,'fsync',sync):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.executor.calls,1);self.assertNotIn('transition',self.repo.calls)
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.executor.calls,1)

    def test_confirmed_checkpoint_fsync_failure_keeps_GET_only_recovery_not_next_job(self):
        original=cp.Journal.save;real=cp.fsync;active=[False]
        def save(j,value):
            active[0]=value['phase']=='confirmed'
            try:return original(j,value)
            finally:active[0]=False
        def sync(fd):
            if active[0]:raise OSError('SYNTHETIC confirm fsync')
            return real(fd)
        with patch.object(cp.Journal,'save',save),patch.object(cp,'fsync',sync):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(self.repo.current['status'],'succeeded');self.assertEqual(self.executor.calls,1)
        self.assertEqual(self.once().status,'reconciliation') # retained checkpoint.next needs review
        self.assertEqual(self.repo.calls.count('claim'),1)

    def test_executing_directory_fsync_failure_after_rename_never_executes_on_restart(self):
        original=cp.Journal.save;real=cp.fsync;phase=[None]
        def save(j,value):
            phase[0]=value['phase']
            try:return original(j,value)
            finally:phase[0]=None
        def sync(fd):
            if phase[0]=='executing' and os.readlink('/proc/self/fd/'+str(fd))==str(self.roots.control):
                raise OSError('SYNTHETIC directory fsync after rename')
            return real(fd)
        with patch.object(cp.Journal,'save',save),patch.object(cp,'fsync',sync):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(state(self.roots)['phase'],'executing');self.assertFalse((self.roots.control/'checkpoint.next').exists())
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.executor.calls,0)
        self.assertEqual(self.repo.calls.count('claim'),1)

    def test_confirmed_directory_fsync_failure_only_GET_reconfirms_never_claims_next(self):
        original=cp.Journal.save;real=cp.fsync;phase=[None]
        def save(j,value):
            phase[0]=value['phase']
            try:return original(j,value)
            finally:phase[0]=None
        def sync(fd):
            if phase[0]=='confirmed' and os.readlink('/proc/self/fd/'+str(fd))==str(self.roots.control):
                raise OSError('SYNTHETIC confirmed directory fsync')
            return real(fd)
        with patch.object(cp.Journal,'save',save),patch.object(cp,'fsync',sync):self.assertEqual(self.once().status,'reconciliation')
        self.assertEqual(state(self.roots)['phase'],'confirmed')
        self.assertEqual(self.once().status,'confirmed');self.assertEqual(self.executor.calls,1)
        self.assertEqual(self.repo.calls.count('claim'),1);self.assertEqual(self.repo.calls.count('transition'),1)

    def test_symlink_control_or_overlapping_roots_fail_before_claim(self):
        bad=replace(self.roots,receipts=self.roots.published)
        self.assertEqual(self.once(self.worker(roots=bad)).status,'reconciliation');self.assertEqual(self.repo.calls,[])
        alias=Path(self.tmp.name)/'alias';alias.symlink_to(self.roots.control,target_is_directory=True)
        bad=replace(self.roots,control=alias)
        self.assertEqual(self.once(self.worker(roots=bad)).status,'reconciliation');self.assertEqual(self.repo.calls,[])

    def test_corrupt_checkpoint_or_stale_next_never_claims_or_executes(self):
        path=self.roots.control/'checkpoint.json';path.write_bytes(b'{}');path.chmod(0o600)
        self.assertEqual(self.once().status,'reconciliation');self.assertEqual(self.repo.calls,[])
        self.assertEqual(self.executor.calls,0)


if __name__=='__main__':unittest.main(verbosity=2)
