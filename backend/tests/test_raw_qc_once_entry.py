"""Pure/fake ordinary-entry tests. Never credentials, PostgreSQL or R."""
import asyncio
from contextlib import ExitStack,redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).parent.parent))
from cyomics_projects import raw_qc_once_entry as e
from cyomics_projects import raw_qc_dispatch as d
A='aaaaaaaa-1111-4111-8111-111111111111'
J='bbbbbbbb-2222-4222-8222-222222222222'

class Fence:
    def __init__(self,state):self.state=state
    def install(self):pass
    def close(self):pass

class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(e,'_attempted',False))
        self.stack.enter_context(patch.object(e,'_uncertain_latched',False))
        self.stack.enter_context(patch.object(e,'_retained',()))
        self.stack.enter_context(patch.object(e,'_Signals',Fence))
        self.context=self.stack.enter_context(patch.object(e,'_eligible_context',return_value=True))
        self.config=d.Config(object(),object(),1000)
        self.calls=[]

    def fake(self,result=None,error=None):
        async def dispatch(config,**kw):
            self.calls.append((config,kw))
            if error is not None:raise error
            return result or d.DispatchOutcome('run_once','confirmed','succeeded',A,J)
        return self.stack.enter_context(patch.object(d,'dispatch_once',side_effect=dispatch))

    def test_default_and_bad_arguments_before_dispatch_import_or_loop(self):
        with patch.object(e,'_load_dispatcher',side_effect=AssertionError()),patch.object(e,'_new_loop',side_effect=AssertionError()):
            value=e.run_configured_once()
            self.assertEqual((value.status,value.reason,value.exit_code),('off','entry_unconfigured',2))
            self.assertFalse(value.dispatch_called);self.assertFalse(value.loop_created)
            for args in (('--path','/untrusted'),('--owner',A),('--ack',),('--once','--next'),(True,)):
                out=io.StringIO()
                with redirect_stdout(out):self.assertEqual(e.main(args),2)
                self.assertEqual(json.loads(out.getvalue())['reason'],'invalid_entry_arguments')
        self.assertFalse(e._attempted)

    def test_invalid_config_or_platform_before_loop_and_no_environment_enable(self):
        with patch.object(e,'_new_loop',side_effect=AssertionError()):
            for config in ({},d.Config(None,None,1000),d.Config(object(),object(),True),d.Config(object(),object(),1001)):
                self.assertEqual(e.run_configured_once(config).reason,'invalid_entry_config')
            self.context.return_value=False
            self.assertEqual(e.run_configured_once(self.config).reason,'dedicated_linux_MainThread_process_required')
        self.assertFalse(e._attempted)

    def test_valid_config_reaches_original_dispatch_exactly_once_and_never_ACK(self):
        call=self.fake()
        value=e.run_configured_once(self.config)
        self.assertEqual((value.status,value.exit_code,value.teardown),('confirmed_succeeded',0,'confirmed'))
        self.assertEqual(self.calls,[(self.config,{'action':'run_once'})]);self.assertEqual(call.await_count,1)
        self.assertTrue(value.loop_created);self.assertTrue(value.loop_closed)
        self.assertFalse(value.pending_tasks_seen)
        again=e.run_configured_once(self.config)
        self.assertEqual(again.reason,'entry_already_used');self.assertEqual(call.await_count,1)
        self.assertEqual(value.to_payload()['ACK_calls'],0);self.assertEqual(value.to_payload()['next_jobs'],0)

    def test_real_dispatch_function_composes_existing_ordinary_chain_in_private_mocks(self):
        # Reuse the existing public dispatcher fixture, not a new production DI.
        import test_raw_qc_dispatch as fixture
        owner=fixture.Tests(methodName='test_default_None_is_inert_before_context_and_imports')
        owner.setUp()
        try:
            value=e.run_configured_once(owner.config)
            self.assertEqual((value.status,value.exit_code),('confirmed_succeeded',0))
            self.assertEqual(owner.run_calls,1);self.assertEqual(owner.ack_calls,[])
            self.assertEqual(owner.events,['factory_verified','worker_imports','pool','close'])
            self.assertIs(owner.workers[0].dependencies['repository'].pool,owner.pools[0])
            self.assertIs(owner.workers[0].dependencies['source_loader'].pool,owner.pools[0])
        finally:owner.doCleanups()

    def test_confirmed_failed_recovery_idle_and_unknown_do_not_become_succeeded(self):
        cases=[('confirmed','failed',A,J,'confirmed','confirmed_failed',4),
            ('confirmed','terminal_readback_confirmed',A,J,'confirmed','confirmed_terminal',4),
            ('idle','confirmed_no_job',None,None,'confirmed','idle',3),
            ('confirmed','succeeded',A,J,'unknown','unknown',1)]
        for status,reason,attempt,job,teardown,expected,code in cases:
            with self.subTest(status=status,reason=reason,teardown=teardown):
                with patch.object(e,'_attempted',False),patch.object(e,'_uncertain_latched',False):
                    self.fake(d.DispatchOutcome('run_once',status,reason,attempt,job,teardown))
                    value=e.run_configured_once(self.config)
                    self.assertEqual((value.status,value.exit_code),(expected,code))
                    self.assertEqual((value.dispatch_status,value.dispatch_reason),(status,reason))
                    self.assertFalse(value.to_payload()['automatic_retry'])

    def test_malformed_or_private_dispatch_exception_sanitized_and_no_reentry(self):
        self.fake(error=BaseException('SYNTHETIC_PRIVATE_CREDENTIAL_EXCEPTION'))
        value=e.run_configured_once(self.config)
        self.assertEqual((value.status,value.teardown),('unknown','unknown'))
        self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(value.to_payload()))
        self.assertEqual(e.run_configured_once().reason,'prior_entry_disposition_unknown')
        self.assertEqual(len(self.calls),1)
        with patch.object(e,'_attempted',False),patch.object(e,'_uncertain_latched',False):
            self.fake(d.DispatchOutcome('run_once','confirmed','SYNTHETIC_PRIVATE',A,J))
            value=e.run_configured_once(self.config)
            self.assertEqual(value.status,'unknown');self.assertIsNone(value.dispatch_reason)

    def test_cancel_scrubbed_and_signal_restore_failure_cannot_be_success(self):
        self.fake(error=asyncio.CancelledError('SYNTHETIC_PRIVATE_CANCEL'))
        value=e.run_configured_once(self.config)
        self.assertEqual((value.status,value.exit_code),('cancelled',1));self.assertTrue(value.interrupted)
        self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(value.to_payload()))
        class BrokenFence(Fence):
            def close(self):raise RuntimeError('SYNTHETIC_PRIVATE_SIGNAL_CLOSE')
        with patch.object(e,'_attempted',False),patch.object(e,'_uncertain_latched',False),patch.object(e,'_Signals',BrokenFence):
            self.fake();value=e.run_configured_once(self.config)
            self.assertEqual((value.status,value.exit_code),('unknown',1))

    def test_loop_close_exception_not_erased_by_already_closed_state(self):
        self.fake();loop=asyncio.new_event_loop();real_close=loop.close;events=[]
        def close():
            events.append('close');real_close();raise RuntimeError('SYNTHETIC_PRIVATE_CLOSE')
        with patch.object(e,'_new_loop',return_value=loop),patch.object(loop,'close',side_effect=close):
            value=e.run_configured_once(self.config)
        self.assertEqual(events,['close']);self.assertTrue(loop.is_closed())
        self.assertEqual(value.status,'unknown');self.assertFalse(value.loop_closed)
        self.assertEqual(value.dispatch_status,'confirmed');self.assertEqual(value.dispatch_reason,'succeeded')
        self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(value.to_payload()))

    def test_unexpected_pending_task_is_cancelled_once_and_never_promoted(self):
        tasks=[]
        async def dispatch(config,**kw):
            self.calls.append((config,kw));tasks.append(asyncio.create_task(asyncio.Event().wait()))
            return d.DispatchOutcome('run_once','confirmed','succeeded',A,J)
        with patch.object(d,'dispatch_once',side_effect=dispatch):value=e.run_configured_once(self.config)
        self.assertEqual(len(self.calls),1);self.assertTrue(value.pending_tasks_seen)
        self.assertEqual((value.status,value.exit_code),('unknown',1))
        self.assertTrue(tasks[0].cancelled());self.assertTrue(value.loop_closed)

    def test_main_flushes_safe_receipt_before_unknown_self_exit_and_requires_once(self):
        self.fake(d.DispatchOutcome('run_once','confirmed','succeeded',A,J,'unknown'))
        out=io.StringIO();exits=[]
        with redirect_stdout(out),patch.object(e,'_terminate_unknown',side_effect=lambda:exits.append(out.getvalue())):
            code=e.main(('--once',),config=self.config)
        self.assertEqual(code,1);self.assertEqual(len(exits),1)
        value=json.loads(exits[0]);self.assertEqual(value['status'],'unknown')
        self.assertEqual(value['dispatch_status'],'confirmed');self.assertEqual(value['exit_code'],1)
        self.assertFalse(value['Owner_or_API_E2E']);self.assertEqual(value['ACK_calls'],0)
        with patch.object(e,'_attempted',False),patch.object(e,'_uncertain_latched',False),redirect_stdout(io.StringIO()):
            self.assertEqual(e.main((),config=self.config),2)

    def test_actual_child_fake_pending_ignores_cancel_exits_bounded_without_raw_stderr(self):
        # Actual local Python child only; every driver/runtime component is fake.
        # Demonstrates the CLI's own exit, NOT descendant/PG/R cleanup.
        backend=str(Path(__file__).parent.parent)
        code='''import sys,asyncio
sys.path.insert(0, BACKEND)
from cyomics_projects import raw_qc_once_entry as e,raw_qc_dispatch as d
e._eligible_context=lambda:True
class Fence:
 def __init__(self,state):pass
 def install(self):pass
 def close(self):pass
e._Signals=Fence
async def ignore():
 try:await asyncio.Event().wait()
 except asyncio.CancelledError:await asyncio.Event().wait()
async def dispatch(config,**kw):
 asyncio.create_task(ignore());await asyncio.sleep(0)
 return d.DispatchOutcome('run_once','confirmed','succeeded',ATTEMPT,JOB,'unknown')
d.dispatch_once=dispatch
raise SystemExit(e.main(('--once',),config=d.Config(object(),object(),1000)))
'''.replace('BACKEND',repr(backend)).replace('ATTEMPT',repr(A)).replace('JOB',repr(J))
        child=subprocess.run([sys.executable,'-I','-B','-c',code],capture_output=True,timeout=5)
        self.assertEqual(child.returncode,1);self.assertEqual(child.stderr,b'')
        value=json.loads(child.stdout);self.assertEqual(value['status'],'unknown')
        self.assertTrue(value['pending_tasks_seen']);self.assertTrue(value['loop_closed'])

if __name__=='__main__':unittest.main(verbosity=2)
