"""Offline synthetic bytes and fake OS/process lifecycle; never native R/DB."""
import contextlib
from dataclasses import asdict,dataclass,replace
import hashlib
from pathlib import Path
import stat
import types
import unittest
from unittest.mock import Mock,patch
from cyomics_projects import log2_matrix_runner as r

FAKE_ROOT=Path(Path(__file__).resolve().anchor)/'source-only-log2-runner'
FIXTURES=Path(r.validator.__file__).resolve().parents[1]/'tests/fixtures/log2_matrix/fixtures'
RAW=(FIXTURES/'proteinGroups.txt').read_bytes()
SELECTED={kind:(FIXTURES/('selected-'+kind+'.tsv')).read_bytes() for kind in ('intensity','lfq')}
OUTPUTS={kind:{name:(FIXTURES/'actual'/kind/name).read_bytes() for name in r.validator.OUTPUTS} for kind in SELECTED}


@dataclass
class Limits:
    wall_seconds:float=60
    cpu_seconds:int=45
    address_space_bytes:int=2147483648
    per_file_bytes:int=67108864
    output_bytes:int=268435456
    stdout_bytes:int=65536
    stderr_bytes:int=65536
    monitor_seconds:float=0.05
    cleanup_seconds:float=2
    open_files:int=256
    uid_nproc_limit:int=512
    core_bytes:int=0
    job_tasks:int=8
    uid_admission_tasks:int=480


def info(size=5,mode=stat.S_IFREG|0o400,uid=1000,inode=8):
    return types.SimpleNamespace(st_dev=1,st_ino=inode,st_mode=mode,st_nlink=1,st_size=size,
                                 st_mtime_ns=1,st_ctime_ns=1,st_uid=uid,st_blocks=0)


def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


class Stream:
    def __init__(self,fd):self.fd=fd;self.closes=0;self.failure=False
    def fileno(self):return self.fd
    def close(self):
        self.closes+=1
        if self.failure:raise RuntimeError('SYNTHETIC_PRIVATE_CLOSE')


class Selector:
    def __init__(self):self.items={};self.closes=0;self.failure=False
    def register(self,stream,event,name):self.items[stream.fd]=types.SimpleNamespace(fileobj=stream,data=name)
    def unregister(self,stream):del self.items[stream.fd]
    def get_map(self):return self.items
    def select(self,timeout):return [(value,1) for value in list(self.items.values())]
    def close(self):
        self.closes+=1
        if self.failure:raise RuntimeError('SYNTHETIC_PRIVATE_SELECTOR')


class Process:
    def __init__(self,rc=0):self.pid=10;self.stdout=Stream(11);self.stderr=Stream(12);self.returncode=None;self.rc=rc
    def poll(self):self.returncode=self.rc;return self.returncode
    def wait(self,timeout):self.returncode=self.rc;return self.returncode


class Watch:
    def __init__(self,*_):self.closes=0;self.stops=0;self.failure=False;self.uncertainty=[];self.observe_hook=lambda:None
    def bind_top(self):pass
    def observe(self,check):check();self.observe_hook()
    def finish(self,check):check()
    def evidence(self):return {'cleanup':{'complete_for_observed_identities':True,'status':'no_observed_live_tasks'},'uncertainty':self.uncertainty}
    def stop(self):
        self.stops+=1
        # Real OwnedTasks.stop polls/reaps its Popen-owned child before claiming
        # complete observed cleanup. The initial fake omitted this witness.
        self.process.poll()
        return True
    def close(self):
        self.closes+=1
        if self.failure:raise RuntimeError('SYNTHETIC_PRIVATE_WATCH')


def runtime(rc=0,stdout=b'LOG2_MATRIX_COMPLETE\n',stderr=b''):
    process=Process(rc);selector=Selector();watch=Watch();watch.process=process
    queues={11:[stdout,b''],12:[stderr,b'']}
    os=types.SimpleNamespace(getuid=lambda:1000,geteuid=lambda:1000,pidfd_open=Mock(return_value=13),
       set_blocking=Mock(),read=Mock(side_effect=lambda fd,cap:queues[fd].pop(0)),close=Mock(),
       access=Mock(return_value=True),X_OK=1,path=types.SimpleNamespace(lexists=Mock(return_value=False)),
       umask=Mock(),scandir=Mock())
    signal=types.SimpleNamespace(SIGCHLD=1,SIG_DFL=0,SIGALRM=2,ITIMER_REAL=3,SIG_BLOCK=4,SIG_SETMASK=5,
       getsignal=Mock(return_value=0),getitimer=Mock(return_value=(0.0,0.0)),setitimer=Mock(),signal=Mock(),
       pthread_sigmask=Mock(return_value=set()),pidfd_send_signal=Mock())
    thread=object()
    threading=types.SimpleNamespace(current_thread=lambda:thread,main_thread=lambda:thread,active_count=lambda:1)
    resource=types.SimpleNamespace(**{name:i for i,name in enumerate(('RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_CORE','RLIMIT_NOFILE','RLIMIT_NPROC'))},setrlimit=Mock())
    popen=Mock(return_value=process)
    b=types.SimpleNamespace(os=os,signal=signal,threading=threading,resource=resource,stat=stat,asdict=asdict,
       process_policy=types.SimpleNamespace(admission=Mock(),OwnedTasks=Mock(return_value=watch),POLICY_SHA256='b'*64),
       subprocess=types.SimpleNamespace(Popen=popen,DEVNULL=-3,PIPE=-1),
       selectors=types.SimpleNamespace(DefaultSelector=Mock(return_value=selector),EVENT_READ=1),
       Limits=Limits,validate_native_directory_pins=Mock(),identity=identity,
       ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','OMP_NUM_THREADS':'1'},
       NATIVE_ENV={'R_HOME':'/usr/lib/R'},NATIVE_EXECUTABLE=FAKE_ROOT/'native/R',
       NATIVE_EXECUTABLE_SHA256='9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283')
    return b,process,selector,watch


def config(kind='intensity',raw=RAW):
    return r.Config(True,'e38afdd9-614b-4ea9-b1da-d08db4e24223',FAKE_ROOT/'server/source.tsv',r._pin(raw),
        FAKE_ROOT/'server/selected.tsv',r._pin(SELECTED[kind]),r._pin(r.validator.parameters((True,True,True))),
        FAKE_ROOT/'server/script.R',FAKE_ROOT/'stage','Intensity' if kind=='intensity' else 'LFQ intensity',
        (True,True,True),('SYNTHETIC_NATIVE_DIRECTORY_PINS',))


@contextlib.contextmanager
def assembled(kind='intensity',raw=RAW,**runtime_kwargs):
    c=config(kind,raw);runner=r.Log2MatrixRunner(c);b,process,selector,watch=runtime(**runtime_kwargs)
    outputs=dict(OUTPUTS[kind]);root=info(0,stat.S_IFDIR|0o700,inode=3)
    def read(_,path,cap,check,expected=None,private=False):
        check()
        if path==c.source:return raw,('source',)
        if path==c.selected:return SELECTED[kind],('selected',)
        if path.parent.name=='output':return outputs[path.name],identity(root)
        return b'FIXED_BYTES_PRETEND_ONLY',('fixed',)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(r,'_runtime',return_value=b))
        stack.enter_context(patch.object(r.sys,'platform','linux'))
        stack.enter_context(patch.object(r.sys,'dont_write_bytecode',True))
        stack.enter_context(patch.object(Path,'lstat',side_effect=lambda path:info(14488,stat.S_IFREG|0o755,uid=0) if path==b.NATIVE_EXECUTABLE else root,autospec=True))
        stack.enter_context(patch.object(Path,'mkdir'))
        stack.enter_context(patch.object(runner,'_parents'))
        stack.enter_context(patch.object(runner,'_read',side_effect=read))
        stack.enter_context(patch.object(runner,'_write_snapshot',return_value=('staged',)))
        scan=stack.enter_context(patch.object(runner,'_scan',return_value=1024))
        yield runner,b,process,selector,watch,outputs,scan


class Tests(unittest.TestCase):
    def failure(self,runner,code=None,**kwargs):
        with self.assertRaises(r.Log2RunFailure) as caught:runner.run(**kwargs)
        if code:self.assertEqual(caught.exception.code,code)
        self.assertNotIn('SYNTHETIC_PRIVATE',str(caught.exception))
        return caught.exception

    def test_default_off_import_and_disabled_run_never_load_POSIX_or_touch_resources(self):
        with patch.object(r,'_runtime') as load:
            for instance in (r.Log2MatrixRunner(),r.Log2MatrixRunner(r.Config())):
                self.failure(instance,'log2_runner_disabled');self.assertFalse(instance.quarantined)
            load.assert_not_called()

    def test_pure_wrong_parameters_flags_paths_and_bool_pins_refuse_before_executor(self):
        c=config()
        for candidate in (replace(c,parameters_pin=r.Pin(1,'a'*64)),replace(c,flags=(1,True,True)),
                          replace(c,source_pin=r.Pin(True,'a'*64)),replace(c,source=Path('relative')),
                          replace(c,job_id='E38AFDD9-614B-4EA9-B1DA-D08DB4E24223')):
            with patch.object(r,'_runtime') as load:
                self.failure(r.Log2MatrixRunner(candidate));load.assert_not_called()

    def test_complete_actual_synthetic_byte_families_single_fake_child_and_detached_receipt(self):
        for kind in ('intensity','lfq'):
            with assembled(kind) as (runner,b,process,selector,watch,outputs,scan):
                result=runner.run()
                self.assertEqual(result.bundle.retained_rows,4)
                self.assertEqual(len(result.bundle.artifacts),11)
                self.assertEqual(result.receipt['source']['sha256'],hashlib.sha256(RAW).hexdigest())
                self.assertEqual(result.receipt['parameters']['sha256'],config(kind).parameters_pin.sha256)
                self.assertTrue(result.receipt['close_confirmed'])
                self.assertFalse(result.bundle.tool_execution_attested)
                self.assertFalse(result.receipt['published'])
                receipt=result.receipt;receipt['close_confirmed']=False
                self.assertTrue(result.receipt['close_confirmed'])
                b.subprocess.Popen.assert_called_once()
                self.assertEqual((watch.closes,selector.closes,process.stdout.closes,process.stderr.closes),(1,1,1,1))
                self.assertEqual(b.os.close.call_args_list[0].args,(13,))

    def test_fixed_11_arguments_native_environment_and_unchanged_resource_limits(self):
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            runner.run();args,kwargs=b.subprocess.Popen.call_args
            argv=args[0];self.assertEqual(argv[:3],[str(b.NATIVE_EXECUTABLE),'--vanilla','--slave'])
            self.assertEqual(argv[4],'--args');self.assertEqual(len(argv[5:]),11)
            self.assertEqual(argv[7:15],['Intensity','true','true','true','to_NA','positive_only','none','none'])
            self.assertFalse(kwargs['shell']);self.assertTrue(kwargs['close_fds']);self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['env'],dict(b.ENV,**b.NATIVE_ENV,TZ='UTC'))
            kwargs['preexec_fn']()
            self.assertEqual([call.args[1] for call in b.resource.setrlimit.call_args_list],
                             [(45,45),(2147483648,2147483648),(67108864,67108864),(0,0),(256,256),(512,512)])

    def test_mixed_named_flags_are_reverse_contaminant_site_only_not_JSON_sort_order(self):
        flags=(False,True,False)
        params=r.validator.parameters(flags)
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            runner._config=replace(runner._config,flags=flags,parameters_pin=r._pin(params))
            self.assertEqual(runner._configuration().flags,flags)
            rc,*_=runner._spawn_and_wait(b,Path('C:/stage/job'),{},lambda:None,r.time.monotonic()+60,Limits())
            self.assertEqual(rc,0)
            self.assertEqual(b.subprocess.Popen.call_args.args[0][8:11],['false','true','false'])
            parsed=r.json.loads(params)
            self.assertEqual(len(parsed),12)
            self.assertEqual(parsed['filter_flags'],{'reverse':False,'contaminant':True,'site_only':False})

    def test_cancel_before_launch_and_after_owned_creation_is_sticky_no_second_child(self):
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            self.failure(runner,'log2_cancelled',cancelled=lambda:True)
            self.assertTrue(runner.quarantined);b.subprocess.Popen.assert_not_called()
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            def cancel():return b.subprocess.Popen.called
            self.failure(runner,'log2_cancelled',cancelled=cancel)
            self.assertEqual(watch.stops,1);self.assertTrue(runner.quarantined)
            self.failure(runner,'log2_runner_quarantined')
            b.subprocess.Popen.assert_called_once()

    def test_wall_expiry_uses_owned_cleanup_and_preserves_fixed_error_only(self):
        clock=[0.0]
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            watch.observe_hook=lambda:clock.__setitem__(0,70.0)
            with patch.object(r.time,'monotonic',side_effect=lambda:clock[0]):
                self.failure(runner,'log2_timeout')
            self.assertEqual(watch.stops,1);self.assertTrue(runner.quarantined)

    def test_known_nonzero_stderr_or_wrong_marker_never_returns_bundle(self):
        for kwargs in ({'rc':2},{'stdout':b'WRONG\n'},{'stderr':b'SYNTHETIC_PRIVATE_ERROR'}):
            with assembled(**kwargs) as (runner,b,*_):
                self.failure(runner);self.assertTrue(runner.quarantined)
                b.subprocess.Popen.assert_called_once()

    def test_bad_semantics_missing_and_changed_parameters_never_return_success(self):
        for mutate in (lambda out:out.__setitem__('working-matrix.tsv',b'WRONG\n'),
                       lambda out:out.pop('original-zero-mask.tsv'),
                       lambda out:out.__setitem__('parameters.json',out['parameters.json']+b'\n')):
            with assembled() as (runner,b,process,selector,watch,outputs,scan):
                mutate(outputs);self.failure(runner);self.assertTrue(runner.quarantined)
                self.assertEqual(watch.closes,1)

    def test_close_failures_after_valid_exit_never_wash_to_success_and_all_other_resources_close(self):
        for target in ('watch','selector','stdout','stderr','pidfd'):
            with assembled() as (runner,b,process,selector,watch,outputs,scan):
                if target=='pidfd':b.os.close.side_effect=OSError('SYNTHETIC_PRIVATE_PIDFD')
                else:{'watch':watch,'selector':selector,'stdout':process.stdout,'stderr':process.stderr}[target].failure=True
                self.failure(runner,'log2_cleanup_unconfirmed')
                self.assertTrue(runner.quarantined)
                self.assertEqual((watch.closes,selector.closes,process.stdout.closes,process.stderr.closes),(1,1,1,1))
                self.assertEqual(b.os.close.call_count,1)

    def test_identity_acquisition_and_observer_unknown_never_numeric_kill_or_retry(self):
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            b.os.pidfd_open.side_effect=OSError('SYNTHETIC_PRIVATE_BIND')
            self.failure(runner,'log2_cleanup_unconfirmed')
            self.assertTrue(runner.quarantined);b.signal.pidfd_send_signal.assert_not_called()
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            watch.uncertainty=['synthetic_unknown']
            self.failure(runner,'log2_process_cleanup_unconfirmed')
            self.assertEqual(watch.stops,1);self.assertTrue(runner.quarantined)

    def test_empty_filtered_exit3_is_explicit_failure_and_cannot_supply_complete_bundle(self):
        lines=RAW.decode().splitlines();header=lines[0].split('\t');index=header.index('Reverse')
        rows=[line.split('\t') for line in lines[1:]]
        for row in rows:row[index]='+'
        raw=('\n'.join([lines[0]]+['\t'.join(row) for row in rows])+'\n').encode()
        with assembled(raw=raw,rc=3,stdout=b'LOG2_MATRIX_EMPTY\n') as (runner,b,process,selector,watch,outputs,scan):
            for name in set(outputs)-r.EMPTY_OUTPUTS:outputs.pop(name)
            self.failure(runner,'empty_not_analyzable')
            self.assertTrue(runner.quarantined);b.subprocess.Popen.assert_called_once()

    def test_bounded_reader_short_read_hash_mutation_cap_and_close_are_fail_closed(self):
        for case in ('short','hash','metadata','cap','close'):
            runner=r.Log2MatrixRunner();s=info(3);path=Mock();path.lstat.return_value=s
            b=types.SimpleNamespace(open_regular=Mock(return_value=19),identity=identity,stat=stat,
                os=types.SimpleNamespace(fstat=Mock(return_value=s),pread=Mock(side_effect=[b'12' if case=='short' else b'123',b'']),close=Mock()))
            if case=='metadata':path.lstat.return_value=info(3,inode=9)
            if case=='close':b.os.close.side_effect=OSError('SYNTHETIC_PRIVATE_READ_CLOSE')
            with patch.object(runner,'_parents'):
                with self.assertRaises(r.Log2RunFailure) as caught:
                    runner._read(b,path,2 if case=='cap' else 3,lambda:None,
                                 r.Pin(3,'a'*64) if case=='hash' else None,private=True)
            self.assertNotIn('SYNTHETIC_PRIVATE',str(caught.exception));b.os.close.assert_called_once_with(19)
            if case=='close':self.assertTrue(runner.quarantined)

    def test_collect_rejects_extra_missing_and_swapped_final_identity(self):
        runner=r.Log2MatrixRunner();root=Path('C:/fake/job');staged={}
        b=types.SimpleNamespace(identity=identity)
        with patch.object(runner,'_scan',side_effect=r.Log2RunFailure('missing_or_extra_log2_output')):
            with self.assertRaises(r.Log2RunFailure):runner._collect(b,root,staged,lambda:None,r.validator.OUTPUTS)
        with patch.object(runner,'_scan'),patch.object(runner,'_read',return_value=(b'data',('old',))),patch.object(Path,'lstat',return_value=info()):
            with self.assertRaises(r.Log2RunFailure) as caught:runner._collect(b,root,staged,lambda:None,r.validator.OUTPUTS)
            self.assertEqual(caught.exception.code,'log2_output_changed')

    def test_actual_scan_code_exact_allowlist_regular_singlelink_private_size_and_bounded_entries(self):
        class Entries:
            def __init__(self,names):self.names=names
            def __enter__(self):return iter(types.SimpleNamespace(name=name) for name in self.names)
            def __exit__(self,*_):return False
        job=Path('C:/stage/job');out=job/'output';runner=r.Log2MatrixRunner()
        base_names=list(r.validator.OUTPUTS)
        for case in ('good','extra','missing','symlink','hardlink','mode','size'):
            names=base_names.copy();file=info(3,stat.S_IFREG|0o600)
            if case=='extra':names.append('not-approved.tsv')
            if case=='missing':names.pop()
            if case=='symlink':file.st_mode=stat.S_IFLNK|0o777
            if case=='hardlink':file.st_nlink=2
            if case=='mode':file.st_mode=stat.S_IFREG|0o644
            if case=='size':file.st_size=r.validator.MAX_FILE+1
            def lstat(path):
                return info(0,stat.S_IFDIR|0o700) if path in (job,out) else file
            b=types.SimpleNamespace(stat=stat,identity=identity,os=types.SimpleNamespace(
                path=types.SimpleNamespace(lexists=lambda _:True),scandir=lambda path:Entries(['output'] if path==job else names)))
            with patch.object(Path,'lstat',lstat):
                if case=='good':self.assertEqual(runner._scan(b,job,{},lambda:None,r.validator.OUTPUTS),33)
                else:
                    with self.assertRaises(r.Log2RunFailure):runner._scan(b,job,{},lambda:None,r.validator.OUTPUTS)

    def test_no_result_if_final_signal_restore_or_fixed_source_recheck_fails(self):
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            b.signal.signal.side_effect=[None,RuntimeError('SYNTHETIC_PRIVATE_SIGNAL')]
            self.failure(runner,'log2_cleanup_unconfirmed');self.assertTrue(runner.quarantined)
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            old=runner._read.side_effect;count=[0]
            def changed(*args,**kwargs):
                count[0]+=1
                if count[0]>16:raise r.Log2RunFailure('log2_file_pin_mismatch')
                return old(*args,**kwargs)
            runner._read.side_effect=changed
            self.failure(runner,'log2_file_pin_mismatch');self.assertTrue(runner.quarantined)


class TimerCleanupRegression(unittest.TestCase):
    def timer_failure(self,mode):
        with assembled() as (runner,b,process,selector,watch,outputs,scan):
            def timer(kind,value):
                if value==0:raise BaseException('PRIVATE_TIMER_DISABLE')
            b.signal.setitimer.side_effect=timer
            with self.assertRaises(r.Log2RunFailure) as caught:
                runner.run(cancelled=lambda:mode=='cancel_live' and b.subprocess.Popen.called)
            self.assertEqual(caught.exception.code,'log2_cleanup_unconfirmed')
            self.assertNotIn('PRIVATE',str(caught.exception))
            self.assertTrue(runner.quarantined)
            self.assertEqual((watch.closes,selector.closes,process.stdout.closes,process.stderr.closes,
                              b.os.close.call_count),(1,1,1,1,1))
            self.assertEqual(watch.stops,1 if mode=='cancel_live' else 0)
            self.assertEqual(b.os.close.call_args.args,(13,))
            b.subprocess.Popen.assert_called_once()
            with self.assertRaises(r.Log2RunFailure) as again:runner.run()
            self.assertEqual(again.exception.code,'log2_runner_quarantined')
            b.subprocess.Popen.assert_called_once()
            self.assertEqual((watch.closes,selector.closes,process.stdout.closes,process.stderr.closes,
                              b.os.close.call_count),(1,1,1,1,1))

    def test_cancel_live_timer_disable_BaseException_still_stops_and_closes_once_no_success_retry(self):
        self.timer_failure('cancel_live')

    def test_normal_exit_timer_disable_BaseException_still_closes_once_sticky_unknown_no_success_retry(self):
        self.timer_failure('normal_exit')
