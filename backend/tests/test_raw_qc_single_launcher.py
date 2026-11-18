"""Only fake process/file/repository interactions; no real subprocess/DB/R."""
import asyncio,copy,contextlib,importlib.util,io,json,pathlib,sys,types,unittest
from dataclasses import dataclass
from unittest.mock import patch
H=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('single_launcher_candidate',H/'cyomics_projects/raw_qc_single_launcher.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
A='00000000-0000-0000-0000-000000000001';B='00000000-0000-0000-0000-000000000002';C='00000000-0000-0000-0000-000000000003';D='00000000-0000-0000-0000-000000000004'
RAW=json.dumps({'request_id':D,'quant_family':'Intensity'}).encode()
class Fault(Exception):
 def __init__(self,code='raw_qc_unavailable',status=503):self.code=code;self.status=status;super().__init__(code)
@dataclass(frozen=True,slots=True)
class Observation:
 _job_json:str
 created:bool=True
 commit_confirmed:bool=True
 teardown_confirmed:bool=True
 def job_public(self):return json.loads(self._job_json)
 @property
 def may_notify_new(self):return self.created is self.commit_confirmed is self.teardown_confirmed is True
class Repo:
 def __init__(self,*,created=True,commit=True,closed=True):
  self._enqueue_observation_enabled=True;self.enqueue_observation_locked=False;self.created=created;self.commit=commit;self.closed=closed;self.writes=self.reads=0;self.gate=None
 async def enqueue_observed(self,*args):
  self.writes+=1
  try:
   if self.gate is not None:await self.gate.wait()
  except BaseException:self.enqueue_observation_locked=True;raise
  return Observation(json.dumps({'job_id':A,'status':'queued','observation':'confirmed'}),self.created,self.commit,self.closed)
 async def read_request(self,*args):self.reads+=1;return {'job_id':A,'status':'queued','observation':'confirmed'}
 async def read_job(self,*args):self.reads+=1;return {'job_id':args[2]}
class Stream:
 def __init__(self,b):self.b=b
 async def read(self,n):out=self.b[:n];self.b=self.b[n:];return out
class Transport:
 def __init__(self):self._finished=True;self.closed=False;self.close_calls=0;self.fail=False
 def close(self):
  self.close_calls+=1
  if self.fail:raise OSError('SYNTHETIC_PRIVATE_CLOSE')
  self.closed=True
 def is_closing(self):return self.closed
 def get_pipe_transport(self,fd):return None
def receipt(actual_job=B):
 return {'schemaVersion':1,'action':'run_once','status':'confirmed_succeeded','reason':'succeeded','exit_code':0,'dispatch_called':True,'dispatch_status':'confirmed','dispatch_reason':'succeeded','attempt_id':C,'job_id':actual_job,'teardown':'confirmed','loop_created':True,'loop_closed':True,'pending_tasks_seen':False,'async_resources_remaining':False,'interrupted':False,'automatic_retry':False,'ACK_calls':0,'next_jobs':0,'Owner_or_API_E2E':False}
class Proc:
 def __init__(self,*,value=None,stderr=b'',running=False):
  self.pid=999001;self.returncode=None if running else 0;self._transport=Transport();self._transport._finished=not running
  self.stdout=Stream((json.dumps(receipt() if value is None else value)+'\n').encode());self.stderr=Stream(stderr);self.finished=asyncio.Event()
  if not running:self.finished.set()
 async def wait(self):await self.finished.wait();return self.returncode
 def finish(self):self.returncode=0;self._transport._finished=True;self.finished.set()
def config():return m.Config(True,'a'*64,'b'*64,'c'*64,'d'*64)
@contextlib.contextmanager
def fakes(spawn):
 with contextlib.ExitStack() as stack:
  for name,value in {'_sources':lambda cfg:None,'_reserve':lambda key:None,'_repo_types':lambda:(Repo,Observation),'_request':lambda raw:json.loads(raw),'_job':lambda value,*args:value,'_fault':lambda code='raw_qc_unavailable',status=503:Fault(code,status),'_spawn':spawn,'_pin_child':lambda p:None}.items():stack.enter_context(patch.object(m,name,value))
  yield
class AsyncTests(unittest.IsolatedAsyncioTestCase):
 async def test_default_off_no_dependency_or_IO(self):
  with patch.object(m,'_repo_types',side_effect=AssertionError()),patch.object(m,'_sources',side_effect=AssertionError()):
   with self.assertRaises(Fault):
    with patch.object(m,'_fault',side_effect=lambda *a,**k:Fault()):await m.SingleRawQCLauncher().submit(object(),A,B,C,RAW,None)
 async def test_new_committed_once_and_different_global_job_not_mapped_to_POST(self):
  calls=[];proc=Proc()
  async def spawn():calls.append(1);return proc
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());repo=Repo();value=await c.submit(repo,A,B,C,RAW,None);await c._monitor
   self.assertEqual(value['job_id'],A);self.assertEqual(c.snapshot()['worker_outcome']['actual_job_id'],B);self.assertEqual(c.snapshot()['notification_job_id'],A);self.assertFalse(c.snapshot()['post_job_success_inferred'])
   self.assertEqual(c._state,'observed');self.assertEqual(calls,[1]);self.assertEqual(repo.writes,1)
   await c.submit(repo,A,B,C,RAW,None);self.assertEqual(repo.writes,1);self.assertEqual(repo.reads,1)
   other=json.dumps({'request_id':B,'quant_family':'Intensity'}).encode()
   with self.assertRaises(Fault):await c.submit(repo,A,B,C,other,None)
   self.assertEqual(calls,[1]);self.assertEqual(proc._transport.close_calls,1)
 async def test_first_replay_and_unconfirmed_observations_never_spawn(self):
  calls=[]
  async def spawn():calls.append(1);return Proc()
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());await c.submit(Repo(created=False),A,B,C,RAW,None);self.assertEqual(c._state,'replayed');self.assertIsNone(c._monitor)
   for repo in (Repo(commit=False),Repo(closed=False)):
    c=m.SingleRawQCLauncher(config())
    with self.assertRaises(m.Refused):await c.submit(repo,A,B,C,RAW,None)
    self.assertEqual(c._state,'unknown')
   self.assertEqual(calls,[])
 async def test_concurrent_POST_is_blocked_before_second_enqueue(self):
  async def spawn():return Proc()
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());repo=Repo();repo.gate=asyncio.Event()
   first=asyncio.create_task(c.submit(repo,A,B,C,RAW,None));await asyncio.sleep(0)
   self.assertEqual(repo.writes,1)
   with self.assertRaises(Fault):await c.submit(repo,A,B,C,RAW,None)
   self.assertEqual(repo.writes,1);repo.gate.set();await first;await c._monitor
 async def test_cancel_during_enqueue_latches_unknown_without_child(self):
  calls=[]
  async def spawn():calls.append(1);return Proc()
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());repo=Repo();repo.gate=asyncio.Event();task=asyncio.create_task(c.submit(repo,A,B,C,RAW,None));await asyncio.sleep(0);task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
   self.assertEqual(c._state,'unknown');self.assertTrue(repo.enqueue_observation_locked)
   with self.assertRaises(Fault):await c.submit(repo,A,B,C,RAW,None)
   self.assertEqual(calls,[])
 async def test_reservation_close_failure_or_existing_latch_precedes_DB(self):
  async def spawn():raise AssertionError()
  with fakes(spawn):
   for error in (FileExistsError(),OSError('SYNTHETIC_CLOSE')):
    c=m.SingleRawQCLauncher(config());repo=Repo()
    with patch.object(m,'_reserve',side_effect=error):
     with self.assertRaises(type(error)):await c.submit(repo,A,B,C,RAW,None)
    self.assertEqual(repo.writes,0);self.assertEqual(c._state,'unknown')
 async def test_child_close_exception_never_washed_by_later_closed_true(self):
  proc=Proc();proc._transport.fail=True
  async def spawn():return proc
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());repo=Repo();await c.submit(repo,A,B,C,RAW,None);await c._monitor
   self.assertEqual(c._state,'unknown');self.assertIsNotNone(c.snapshot()['worker_outcome']);self.assertEqual(proc._transport.close_calls,1)
   proc._transport.fail=False;proc._transport.closed=True;await c.close();self.assertEqual(c._state,'unknown');self.assertEqual(proc._transport.close_calls,1)
 async def test_spawn_exception_no_handle_no_retry_no_private_log(self):
  calls=[]
  async def spawn():calls.append(1);raise RuntimeError('PRIVATE_STARTUP_VALUE')
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());repo=Repo();await c.submit(repo,A,B,C,RAW,None);await c._monitor
   self.assertEqual(c._state,'unknown');self.assertEqual(calls,[1]);self.assertNotIn('PRIVATE',json.dumps(c.snapshot()))
   with self.assertRaises(Fault):await c.submit(repo,A,B,C,RAW,None)
 async def test_close_before_monitor_scheduled_never_starts_process(self):
  calls=[]
  async def spawn():calls.append(1);return Proc()
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());await c.submit(Repo(),A,B,C,RAW,None);await c.close();await c._monitor
   self.assertEqual(c._state,'unknown');self.assertEqual(calls,[])
 async def test_late_spawn_after_timeout_consumed_never_unlocks(self):
  gate=asyncio.Event();proc=Proc();calls=[];original=asyncio.wait
  async def spawn():calls.append(1);await gate.wait();return proc
  async def forced(tasks,**kwargs):
   if kwargs.get('timeout')==m.SPAWN_SECONDS:return set(),set(tasks)
   return await original(tasks,**kwargs)
  with fakes(spawn),patch.object(m.asyncio,'wait',side_effect=forced):
   c=m.SingleRawQCLauncher(config());await c.submit(Repo(),A,B,C,RAW,None);await c._monitor;self.assertEqual(c._state,'unknown')
   pending=next(t for t in c._tasks if not t.done());gate.set();await pending;await asyncio.sleep(0)
   late=[t for t in c._tasks if t is not pending and not t.done()]
   if late:await asyncio.wait(late)
   self.assertEqual(c._state,'unknown');self.assertEqual(calls,[1]);self.assertEqual(proc._transport.close_calls,1)
 async def test_canceled_monitor_during_spawn_owns_late_handle(self):
  gate=asyncio.Event();entered=asyncio.Event();proc=Proc()
  async def spawn():entered.set();await gate.wait();return proc
  with fakes(spawn):
   c=m.SingleRawQCLauncher(config());await c.submit(Repo(),A,B,C,RAW,None);await entered.wait();c._monitor.cancel();await c._monitor
   self.assertEqual(c._state,'unknown');pending=next(t for t in c._tasks if not t.done());gate.set();await pending;await asyncio.sleep(0)
   late=[t for t in c._tasks if t is not pending and not t.done()]
   if late:await asyncio.wait(late)
   self.assertEqual(c._state,'unknown');self.assertEqual(proc._transport.close_calls,1)
 async def test_excess_output_stderr_or_bad_child_receipt_is_unknown(self):
  for proc in (Proc(stderr=b'PRIVATE'),Proc(value=dict(receipt(),loop_closed=False)),Proc(value=dict(receipt(),ACK_calls=1))):
   async def spawn(p=proc):return p
   with fakes(spawn):
    c=m.SingleRawQCLauncher(config());await c.submit(Repo(),A,B,C,RAW,None);await c._monitor;self.assertEqual(c._state,'unknown');self.assertNotIn('PRIVATE',json.dumps(c.snapshot()))
  stream=Stream(b'x'*(m.STDOUT_CAP+1))
  with self.assertRaises(m.Refused):await m._read(stream,m.STDOUT_CAP)
 async def test_child_timeout_one_owned_signal_then_unknown_never_releases(self):
  proc=Proc(running=True);signalled=[];closed=[];original=asyncio.wait
  async def spawn():return proc
  async def forced(tasks,**kw):
   if 'return_when' in kw:
    await asyncio.sleep(0);return {t for t in tasks if t.done()},{t for t in tasks if not t.done()}
   return await original(tasks,**kw)
  def signal(fd,number,*args):signalled.append((fd,number));proc.finish()
  with fakes(spawn),patch.object(m,'_pin_child',return_value=12345),patch.object(m.asyncio,'wait',side_effect=forced),patch.object(m.signal,'pidfd_send_signal',side_effect=signal,create=True),patch.object(m.os,'close',side_effect=lambda fd:closed.append(fd)):
   c=m.SingleRawQCLauncher(config());repo=Repo();await c.submit(repo,A,B,C,RAW,None);await c._monitor
   self.assertEqual(c._state,'unknown');self.assertEqual(len(signalled),1);self.assertEqual(closed,[12345]);self.assertEqual(c.snapshot()['captures']['child_exit'],0)
   await c.close();self.assertEqual(len(signalled),1)
   with self.assertRaises(Fault):await c.submit(repo,A,B,C,RAW,None)
class PureTests(unittest.TestCase):
 def test_fixed_argv_env_and_Config_has_no_client_path_command_owner(self):
  self.assertEqual(m.ARGV,(m.PYTHON,'-I','-B',str(m.STARTUP),'--once'));self.assertEqual(m.ENV,{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
  self.assertEqual(set(m.Config.__dataclass_fields__),{'enabled','startup_sha256','python_sha256','backend_manifest_sha256','activation_review_sha256'})
  self.assertFalse(m.configured(None));self.assertFalse(m.configured(m.Config()))
  with self.assertRaises(m.Refused):m.configured(m.Config(True))
 def test_strict_receipt_real_exit_uuid_and_no_owner_inference(self):
  raw=(json.dumps(receipt())+'\n').encode();self.assertEqual(m._entry(raw,0)['actual_job_id'],B)
  for v in (dict(receipt(),loop_closed=1),dict(receipt(),exit_code=4),dict(receipt(),automatic_retry=True),dict(receipt(),job_id='BAD'),dict(receipt(),reason='PRIVATE')):
   with self.assertRaises((m.Refused,ValueError,TypeError)):m._entry((json.dumps(v)+'\n').encode(),0)
  with self.assertRaises(m.Refused):m._entry(raw,1)
  with self.assertRaises(m.Refused):m._entry(raw+b'{}\n',0)
 def test_startup_default_is_off(self):
  spec=importlib.util.spec_from_file_location('private_once_source',H/'cyomics_projects/raw_qc_live_once_start.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
  out=io.StringIO()
  with contextlib.redirect_stdout(out):self.assertEqual(s.main(),2)
  self.assertIn('"executed":false',out.getvalue());self.assertEqual(len(s.NATIVE),5)

if __name__=='__main__':unittest.main(verbosity=2)
