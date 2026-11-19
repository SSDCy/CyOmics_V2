"""Single pure-mock signal-cleanup regression; no actual loop/signal/factory/R/DB."""
from contextlib import ExitStack,redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch
import io,json,socket,subprocess,sys,unittest

BACKEND=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BACKEND))
from cyomics_projects import volcano_on_demand as v,raw_qc_once_entry as original

class SignalCleanupRegression(unittest.TestCase):
    def test_restore_failure_must_not_confirm(self):
        state=original._State();configuration=object();operation=object();calls=[]
        class LoopDouble:
            def set_exception_handler(self,handler):calls.append('loop_handler')
            def run_until_complete(self,awaitable):
                self_test.assertIs(awaitable,operation);calls.append('synthetic_ready');return 'ready'
        self_test=self;loop=LoopDouble()
        def install(fence):
            calls.append('install_double')
            fence.saved=[(original.signal.SIGINT,object()),(original.signal.SIGTERM,object())]
        notification={'owner_id':'11111111-1111-4111-8111-111111111111','project_id':'22222222-2222-4222-8222-222222222222',
            'request_id':'33333333-3333-4333-8333-333333333333','job_id':'44444444-4444-4444-8444-444444444444',
            'derived_version_id':'55555555-5555-4555-8555-555555555555','binding_sha256':'a'*64}
        activation=Mock(side_effect=AssertionError('real factory forbidden'));output=io.StringIO()
        with ExitStack() as stack,redirect_stdout(output):
            network=stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')))
            native=stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('process forbidden')))
            async_native=stack.enter_context(patch('asyncio.create_subprocess_exec',side_effect=AssertionError('process forbidden')))
            stack.enter_context(patch.object(v,'_State',return_value=state))
            stack.enter_context(patch.object(v,'prepare',side_effect=lambda c:c))
            stack.enter_context(patch.object(v,'_eligible_context',return_value=True))
            stack.enter_context(patch.object(original._Signals,'install',install))
            # Execute ORIGINAL _Signals.close. Every restoration call is a mock
            # failure, so the original helper sets cleanup_error without raising.
            restore=stack.enter_context(patch.object(original.signal,'signal',side_effect=OSError('synthetic_restore_failure')))
            stack.enter_context(patch.object(v.asyncio,'new_event_loop',return_value=loop))
            factory_double=Mock(return_value=operation)
            stack.enter_context(patch.object(v,'_from_factory',new=factory_double))
            settle=stack.enter_context(patch.object(v,'_settle_and_close',return_value=(False,False,True)))
            code=v.main_from_server(v.shared.canonical(notification)+b'\n',config=configuration,
                activate_factory=activation,verify=lambda:None,native_pins=())
            self.assertEqual(restore.call_count,2)
            self.assertEqual([args.args[0] for args in restore.call_args_list],[original.signal.SIGTERM,original.signal.SIGINT])
            activation.assert_not_called();factory_double.assert_called_once();settle.assert_called_once_with(loop,state)
            self.assertEqual((network.call_count,native.call_count,async_native.call_count),(0,0,0))
        self.assertTrue(state.cleanup_error);self.assertFalse(state.loop_error);self.assertFalse(state.interrupted)
        value=json.loads(output.getvalue());self.assertEqual(code,1);self.assertEqual(value['exit_code'],1)
        self.assertEqual((value['state'],value['disposition'],value['teardown']),('unknown','unknown','unknown'))
        self.assertTrue(value['loop_closed']);self.assertFalse(value['pending_tasks_seen']);self.assertFalse(value['async_resources_remaining'])
        self.assertEqual(value['render_id'],notification['job_id']);self.assertEqual(value['binding_sha256'],notification['binding_sha256'])
        print(json.dumps({'single_regression':'PASS',
            'original_signal_close_executed_with_mock_restore_failures':restore.call_count,'cleanup_error':state.cleanup_error,
            'new_entry_exit_code':code,'new_entry_payload':value,'actual_signals_factory_R_DB_network_processes':0,
            'loop_and_factory_are_inert_doubles':True,'prior_three_group_combination_reruns':0}))

if __name__=='__main__':unittest.main()
