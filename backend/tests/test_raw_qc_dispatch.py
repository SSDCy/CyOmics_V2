"""Synthetic assembly signatures/lifecycle. No POSIX import, credential, DB or R."""
import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects import raw_qc_dispatch as d
A='aaaaaaaa-1111-4111-8111-111111111111';J='bbbbbbbb-2222-4222-8222-222222222222'
@dataclass
class NativeConfig:
    input_root:object;staging_root:object;publish_root:object;expected_uid:int
@dataclass
class Roots:
    control:object;inputs:object;staging:object;published:object;receipts:object
    def verify(self):pass
class Runner:
    def __init__(self,c):self.config=c;self._quarantined=False
class Registration:
    def snapshot(self):return {'script':{'bytes':1,'sha256':'synthetic-script'},'executable':{'bytes':2,'sha256':'synthetic-exe'},'entry_kind':'native_R','r_version':'synthetic-R','limits_profile':'synthetic-limits','limits_sha256':'synthetic-hash'}
@dataclass
class Pin:
    bytes:int;sha256:str
@dataclass
class PoolConfig:expected_uid:int
@dataclass
class OnceOutcome:
    status:str;reason:str;attempt_id:object=None;job_id:object=None
@dataclass
class AckOutcome:
    status:str;reason:str;attempt_id:object=None;job_id:object=None

class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.events=[];self.run_calls=0;self.ack_calls=[];self.pools=[];self.workers=[]
        roots=Roots(*[Path('/synthetic')/n for n in ('control','inputs','staging','published','receipts')])
        native=NativeConfig(roots.inputs,roots.staging,roots.published,1000)
        native.script_pin=Pin(1,'synthetic-script');native.executable_pin=Pin(2,'synthetic-exe')
        native.entry_kind='native_R';native.expected_r_version='synthetic-R';native.limits=object()
        self.config=d.Config(native,roots,1000);self.runner=Runner(native);self.registration=Registration()
        self.run_result=OnceOutcome('confirmed','succeeded',A,J);self.ack_result=AckOutcome('acknowledged','confirmed_checkpoint_reset_idle',A,J)
        self.close_bad=None;self.action_error=None;self.create_pending=False;self.pending=None;self.gate=None
        parent=self
        class Pool:
            def __init__(self,config):
                parent.events.append('pool');self.config=config;self._held=False;self._lease=None;self._uncertain=False;self._closing=False;parent.pools.append(self)
            def close(self):
                parent.events.append('close');self._closing=True
                if parent.close_bad=='raise':raise BaseException('SYNTHETIC_PRIVATE_CLOSE')
                if parent.close_bad=='held':self._held=True
                if parent.close_bad=='uncertain':self._uncertain=True
                if parent.close_bad=='lease':self._lease=object()
                if parent.close_bad=='not_closing':self._closing=False
        class Worker:
            def __init__(self,**kw):self.dependencies=kw;parent.workers.append(self)
            async def run_once(self):
                parent.run_calls+=1
                if parent.action_error:raise parent.action_error
                if parent.create_pending:
                    parent.gate=asyncio.Event();parent.pending=asyncio.create_task(parent.gate.wait())
                return parent.run_result
            async def acknowledge_confirmed(self,**kw):
                parent.ack_calls.append(kw)
                if parent.action_error:raise parent.action_error
                return parent.ack_result
        def factory(c):self.events.append('factory_verified');return self.runner,self.registration
        self.runtime=SimpleNamespace(Config=NativeConfig,create_runtime=Mock(side_effect=factory))
        self.cp=SimpleNamespace(WorkerRoots=Roots)
        self.m=SimpleNamespace(WorkerOnce=Worker,OnceOutcome=OnceOutcome,AckOutcome=AckOutcome,
            WorkerPool=Pool,PoolConfig=PoolConfig,RawQCWorkerRepository=Mock(side_effect=lambda pool:SimpleNamespace(pool=pool)),
            WorkerBoundSource=Mock(side_effect=lambda **kw:SimpleNamespace(**kw)),BoundRaw=type('BoundRaw',(),{}),
            InputExporter=Mock(side_effect=lambda root:SimpleNamespace(root=root)),RawQCArtifacts=Mock(side_effect=lambda root,reg:SimpleNamespace(root=root,registration=reg)),
            RawQCRunner=Runner,ExecutionRegistration=Registration,execution_value=Mock(),Budget=type('Budget',(),{}),
            limits_metadata=Mock(return_value={'limits_profile':'synthetic-limits','limits_sha256':'synthetic-hash'}))
        self.context=self.stack.enter_context(patch.object(d,'_context',return_value=True))
        self.load_config=self.stack.enter_context(patch.object(d,'_configuration_modules',return_value=(self.runtime,self.cp)))
        def load_worker():self.events.append('worker_imports');return self.m
        self.load_worker=self.stack.enter_context(patch.object(d,'_worker_modules',side_effect=load_worker))
    async def test_default_None_is_inert_before_context_and_imports(self):
        result=await d.dispatch_once();self.assertEqual((result.status,result.reason),('off','dispatch_unconfigured'))
        self.context.assert_not_called();self.load_config.assert_not_called();self.load_worker.assert_not_called()
    async def test_bad_actions_and_id_combinations_refused_before_IO(self):
        for kwargs in ({'action':'unsafe/path'},{'action':[]},{'expected_job_id':J},
                       {'action':'ack_confirmed'},{'action':'ack_confirmed','expected_attempt_id':A.upper(),'expected_job_id':J}):
            result=await d.dispatch_once(self.config,**kwargs);self.assertEqual(result.status,'off')
        self.load_config.assert_not_called();self.assertEqual(self.pools,[])
    async def test_untrusted_config_and_unsupported_context(self):
        for cfg in ({},d.Config(None,None,1000),d.Config(object(),object(),True)):
            self.assertEqual((await d.dispatch_once(cfg)).reason,'invalid_dispatch_config')
        self.context.return_value=False
        self.assertEqual((await d.dispatch_once(self.config)).reason,'dedicated_MainThread_required');self.load_config.assert_not_called()
    async def test_fixed_factory_precedes_worker_imports_and_real_signatures_match(self):
        result=await d.dispatch_once(self.config)
        self.assertEqual((result.status,result.reason,result.teardown),('confirmed','succeeded','confirmed'))
        self.assertEqual(self.events,['factory_verified','worker_imports','pool','close']);self.assertEqual(self.run_calls,1);self.assertEqual(self.ack_calls,[])
        dep=self.workers[0].dependencies;pool=self.pools[0]
        self.assertEqual(pool.config.expected_uid,1000);self.assertIs(dep['repository'].pool,pool);self.assertIs(dep['source_loader'].pool,pool)
        self.assertIs(dep['source_loader'].bound_raw_factory,self.m.BoundRaw)
        self.assertIs(dep['executor'],self.runner);self.assertIs(dep['registration'],self.registration)
        self.assertIs(dep['roots'],self.config.roots);self.assertIs(dep['budget_factory'],self.m.Budget)
        self.assertEqual(dep['exporter'].root,self.config.roots.inputs);self.assertEqual(dep['artifacts'].root,self.config.roots.published)
    async def test_ack_is_only_one_explicit_action_with_expected_ids(self):
        result=await d.dispatch_once(self.config,action='ack_confirmed',expected_attempt_id=A,expected_job_id=J)
        self.assertEqual(result.status,'acknowledged');self.assertEqual(self.run_calls,0)
        self.assertEqual(self.ack_calls,[{'expected_attempt_id':A,'expected_job_id':J}])
    async def test_runtime_type_uid_root_and_pair_mismatches_do_not_create_pool(self):
        original=self.config.runtime
        for native in (object(),NativeConfig(original.input_root,original.staging_root,original.publish_root,999),NativeConfig(Path('/wrong'),original.staging_root,original.publish_root,1000)):
            self.assertEqual((await d.dispatch_once(d.Config(native,self.config.roots,1000))).status,'off')
        self.runner._quarantined=True
        self.assertEqual((await d.dispatch_once(self.config)).status,'off');self.assertEqual(self.pools,[])
    async def test_factory_failure_is_sanitized_and_imports_no_worker(self):
        self.runtime.create_runtime.side_effect=RuntimeError('SYNTHETIC_PRIVATE_RUNTIME_PATH')
        result=await d.dispatch_once(self.config);self.assertNotIn('PRIVATE',repr(result));self.load_worker.assert_not_called();self.assertEqual(self.pools,[])
    async def test_registration_and_runner_pins_limits_are_checked_before_pool(self):
        original=self.runner.config.script_pin;self.runner.config.script_pin=Pin(999,'wrong')
        self.assertEqual((await d.dispatch_once(self.config)).status,'off')
        self.runner.config.script_pin=original;self.m.limits_metadata.return_value={'limits_profile':'wrong','limits_sha256':'wrong'}
        self.assertEqual((await d.dispatch_once(self.config)).status,'off');self.assertEqual(self.pools,[])
    async def test_recovery_idle_and_busy_never_dispatch_twice(self):
        for value in (OnceOutcome('confirmed','terminal_readback_confirmed',A,J),OnceOutcome('idle','confirmed_no_job'),OnceOutcome('busy','worker_lock_held'),OnceOutcome('reconciliation','read_only_recovery_unresolved',A,J)):
            before=self.run_calls;self.run_result=value;result=await d.dispatch_once(self.config)
            self.assertEqual(result.status,value.status);self.assertEqual(self.run_calls,before+1)
        self.assertEqual(self.ack_calls,[])
    async def test_ack_unknown_never_acknowledges_or_runs_again(self):
        self.ack_result=AckOutcome('unknown','acknowledgement_unconfirmed',A,J)
        result=await d.dispatch_once(self.config,action='ack_confirmed',expected_attempt_id=A,expected_job_id=J)
        self.assertEqual(result.status,'unknown');self.assertEqual(len(self.ack_calls),1);self.assertEqual(self.run_calls,0)
    async def test_close_None_is_not_proof_and_business_result_is_preserved(self):
        for failure in ('raise','held','uncertain','lease','not_closing'):
            self.close_bad=failure;result=await d.dispatch_once(self.config)
            self.assertEqual((result.status,result.reason,result.teardown),('confirmed','succeeded','unknown'))
    async def test_pending_new_task_marks_teardown_unknown_without_claiming_ownership(self):
        self.create_pending=True
        result=await d.dispatch_once(self.config);self.assertEqual(result.teardown,'unknown');self.assertEqual(result.status,'confirmed')
        self.gate.set();await self.pending
    async def test_existing_pending_task_refuses_new_pool_before_configuration_IO(self):
        gate=asyncio.Event();pending=asyncio.create_task(gate.wait())
        try:
            result=await d.dispatch_once(self.config)
            self.assertEqual((result.status,result.reason,result.teardown),('off','dispatch_loop_not_exclusive','unknown'))
            self.load_config.assert_not_called();self.assertEqual(self.pools,[])
        finally:gate.set();await pending
    async def test_action_exception_and_cancellation_are_fixed_unknown_and_close_once(self):
        for error in (RuntimeError('SYNTHETIC_PRIVATE_SOURCE'),asyncio.CancelledError()):
            self.action_error=error;n=len(self.pools);result=await d.dispatch_once(self.config)
            self.assertEqual((result.status,result.reason),('unknown','dispatch_action_unconfirmed'));self.assertEqual(len(self.pools),n+1);self.assertNotIn('PRIVATE',repr(result))
    async def test_bad_result_shape_reason_or_identifiers_never_escape(self):
        for value in (SimpleNamespace(status='confirmed',reason='succeeded',attempt_id=A,job_id=J),OnceOutcome('confirmed','/private/path',A,J),OnceOutcome('confirmed','succeeded',None,None),OnceOutcome('confirmed','confirmed_no_job',A,J),OnceOutcome('idle','confirmed_no_job',A,J)):
            self.run_result=value;result=await d.dispatch_once(self.config)
            self.assertEqual(result.status,'unknown');self.assertNotIn('/private',repr(result))
    async def test_ack_false_confirmation_or_other_attempt_rejected(self):
        for value in (AckOutcome('acknowledged','confirmed_checkpoint_reset_idle'),AckOutcome('acknowledged','confirmed_checkpoint_reset_idle',J,A)):
            self.ack_result=value;result=await d.dispatch_once(self.config,action='ack_confirmed',expected_attempt_id=A,expected_job_id=J)
            self.assertEqual(result.status,'unknown');self.assertEqual(self.run_calls,0)
    async def test_confirmed_failure_is_not_relabelled_scientific_success(self):
        self.run_result=OnceOutcome('confirmed','failed',A,J)
        result=await d.dispatch_once(self.config);self.assertEqual((result.status,result.reason),('confirmed','failed'))


class StageBoundaries(unittest.IsolatedAsyncioTestCase):
    """R1-only boundary assertions; reuse synthetic setUp, not inherited tests."""
    setUp=Tests.setUp

    async def test_roots_cancellation_is_unknown_before_factory_or_pool(self):
        with patch.object(Roots,'verify',side_effect=asyncio.CancelledError()):
            result=await d.dispatch_once(self.config)
        self.assertEqual((result.status,result.reason,result.teardown),('off','dispatch_configuration_unavailable','unknown'))
        self.runtime.create_runtime.assert_not_called();self.load_worker.assert_not_called()
        self.assertEqual(self.pools,[]);self.assertEqual(self.run_calls,0)

    async def test_factory_cancellation_after_successful_roots_stays_unknown(self):
        roots=Mock(side_effect=lambda:self.events.append('roots_verified'))
        self.runtime.create_runtime.side_effect=asyncio.CancelledError()
        with patch.object(Roots,'verify',roots):
            result=await d.dispatch_once(self.config)
        self.assertEqual((result.status,result.teardown),('off','unknown'))
        self.assertEqual(self.events,['roots_verified']);roots.assert_called_once()
        self.runtime.create_runtime.assert_called_once();self.load_worker.assert_not_called()
        self.assertEqual(self.pools,[])

    async def test_exact_configuration_type_refusals_are_clean_before_roots(self):
        with patch.object(Roots,'verify') as verify:
            for config in (d.Config(object(),self.config.roots,1000),d.Config(self.config.runtime,object(),1000)):
                result=await d.dispatch_once(config)
                self.assertEqual((result.status,result.teardown),('off','confirmed'))
            verify.assert_not_called()
        self.runtime.create_runtime.assert_not_called();self.load_worker.assert_not_called()
        self.assertEqual(self.pools,[])

    async def test_uid_and_root_correspondence_refusals_are_clean_before_roots(self):
        native=self.config.runtime
        bad=(NativeConfig(native.input_root,native.staging_root,native.publish_root,999),
             NativeConfig(Path('/wrong'),native.staging_root,native.publish_root,1000))
        with patch.object(Roots,'verify') as verify:
            for config in bad:
                result=await d.dispatch_once(d.Config(config,self.config.roots,1000))
                self.assertEqual((result.status,result.teardown),('off','confirmed'))
            verify.assert_not_called()
        self.runtime.create_runtime.assert_not_called();self.load_worker.assert_not_called()
        self.assertEqual(self.pools,[])

    async def test_pure_configuration_import_failure_precedes_resource_stage(self):
        self.load_config.side_effect=ImportError('SYNTHETIC_PRIVATE_IMPORT')
        result=await d.dispatch_once(self.config)
        self.assertEqual((result.status,result.reason,result.teardown),('off','dispatch_configuration_unavailable','confirmed'))
        self.assertNotIn('PRIVATE',repr(result));self.runtime.create_runtime.assert_not_called()
        self.load_worker.assert_not_called();self.assertEqual(self.pools,[])

    async def test_pure_worker_import_failure_after_completed_setup_is_clean(self):
        self.load_worker.side_effect=ImportError('SYNTHETIC_PRIVATE_WORKER_IMPORT')
        result=await d.dispatch_once(self.config)
        self.assertEqual((result.status,result.reason,result.teardown),('off','dispatch_configuration_unavailable','confirmed'))
        self.assertEqual(self.events,['factory_verified']);self.runtime.create_runtime.assert_called_once()
        self.assertNotIn('PRIVATE',repr(result));self.assertEqual(self.pools,[])

    async def test_pair_pin_refusal_after_completed_setup_is_clean(self):
        self.runner.config.script_pin=Pin(999,'wrong')
        result=await d.dispatch_once(self.config)
        self.assertEqual((result.status,result.teardown),('off','confirmed'))
        self.runtime.create_runtime.assert_called_once();self.load_worker.assert_called_once()
        self.assertEqual(self.events,['factory_verified','worker_imports']);self.assertEqual(self.pools,[])

    async def test_successful_stages_preserve_one_action_and_clean_pool(self):
        with patch.object(Roots,'verify',side_effect=lambda:self.events.append('roots_verified')) as roots:
            result=await d.dispatch_once(self.config)
        self.assertEqual((result.status,result.reason,result.teardown),('confirmed','succeeded','confirmed'))
        roots.assert_called_once();self.assertEqual(self.events,['roots_verified','factory_verified','worker_imports','pool','close'])
        self.assertEqual(self.run_calls,1);self.assertEqual(self.ack_calls,[])

if __name__=='__main__':unittest.main(verbosity=2)
