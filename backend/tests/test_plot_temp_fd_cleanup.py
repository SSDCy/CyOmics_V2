"""ONE finite exit0/FD regression target. Real short Python child, pipes,
pidfd, selectors and scandir close; explicit observer/limit/signal doubles.
No R, DB/Auth, service, runtime incident files, old suite or render publication.
"""
from pathlib import Path
import os,resource,selectors,signal,socket,subprocess,sys,tempfile,time,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects import log2_matrix_runner as shared,volcano_runner as volcano,heatmap_runner as heatmap
from cyomics_projects.limma_bound_runner import LimmaBoundRunner
REAL_POPEN=subprocess.Popen
class Scanner:
 def __init__(self,path,tracker,mode):
  self.iterator=os.scandir(path);self.path=path;self.calls=0;self.closed=False;self.mode=mode;tracker.append(self)
 def __iter__(self):return self
 def __next__(self):return next(self.iterator)
 def __enter__(self):return self
 def __exit__(self,*args):self.close()
 def close(self):
  self.calls+=1;self.iterator.close();self.closed=True
  if self.mode=='close' and self.path.name=='cache':raise OSError('synthetic close acknowledgement failure')
class OsDouble:
 def __init__(self,tracker,mode):self.tracker=tracker;self.mode=mode
 def __getattr__(self,name):return getattr(os,name)
 def scandir(self,path):
  if self.mode=='open' and path.name=='cache':raise OSError('synthetic scandir open failure')
  return Scanner(path,self.tracker,self.mode)
class ObserverDouble:
 def __init__(self,child,fd,limits):
  self.child=child;self.bound={child.pid:types.SimpleNamespace(proc=types.SimpleNamespace(pid=child.pid,starttick=1,inode=1,uid=os.getuid()))}
 def bind_top(self):pass
 def observe(self,check):check()
 def finish(self,check):check();assert self.child.poll()==0
 def evidence(self):return {'cleanup':{'complete_for_observed_identities':self.child.poll()==0},'uncertainty':[],'synthetic_observer':True}
 def close(self):pass
 def stop(self):return self.child.poll()==0
class FDTarget(unittest.TestCase):
 def test_one_exit0_temp_scan_resource_target(self):
  children=[]
  def case(adapter,mode,legacy=False):
   scans=[];made=[]
   def popen(argv,**kw):
    self.assertEqual(argv,[sys.executable,'-I','-B','-c','pass']);p=REAL_POPEN(argv,**kw);made.append(p);children.append(p);return p
   b=types.SimpleNamespace(os=OsDouble(scans,mode),stat=__import__('stat'),selectors=selectors,
    signal=types.SimpleNamespace(SIGALRM=signal.SIGALRM,SIG_BLOCK=signal.SIG_BLOCK,SIG_SETMASK=signal.SIG_SETMASK,ITIMER_REAL=signal.ITIMER_REAL,
      pthread_sigmask=lambda *a:set(),setitimer=lambda *a:None),
    resource=types.SimpleNamespace(**{n:getattr(resource,n) for n in ('RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_CORE','RLIMIT_NOFILE','RLIMIT_NPROC')},setrlimit=lambda *a:None),
    subprocess=types.SimpleNamespace(Popen=popen,DEVNULL=subprocess.DEVNULL,PIPE=subprocess.PIPE),
    process_policy=types.SimpleNamespace(admission=lambda limits,check,out:check(),OwnedTasks=ObserverDouble))
   limits=types.SimpleNamespace(cpu_seconds=1,address_space_bytes=2147483648,per_file_bytes=67108864,core_bytes=0,open_files=256,uid_nproc_limit=512,
    monitor_seconds=.005,cleanup_seconds=1,stdout_bytes=1024,stderr_bytes=1024)
   outer=self
   with tempfile.TemporaryDirectory(prefix='synthetic-plot-fd-') as directory:
    job=Path(directory);tmp=job/'tmp';tmp.mkdir(mode=0o700);cache=tmp/'cache';cache.mkdir(mode=0o700);file=cache/'vanish.lock';file.write_bytes(b'fixture');file.chmod(0o600)
    class Harness(adapter):
     _temp_scan=LimmaBoundRunner._temp_scan if legacy else adapter._temp_scan
     def __init__(self):
      shared.Log2MatrixRunner.__init__(self);self._config=types.SimpleNamespace(render_id='11111111-1111-4111-8111-111111111111',
       attempt_id='22222222-2222-4222-8222-222222222222',binding_sha256='a'*64,profile_json=b'{}')
     def _invocation(self,b,job):return [sys.executable,'-I','-B','-c','pass']
     def _environment(self,b,job):return {'PATH':'/usr/bin:/bin','LC_ALL':'C'}
     def _scan(self,*args):return 0 # No science outputs; original supervisor pipe/pidfd path remains real.
     def _run_native(self,*,cancelled=lambda:False):
      result=self._spawn_and_wait(b,job,{},lambda:None,time.monotonic()+5,limits)
      outer.assertEqual(result[:3],(0,b'',b''));outer.assertTrue(made[0].stdout.closed and made[0].stderr.closed)
      outer.assertFalse(self._failure_trace['fds']);outer.assertTrue(self._failure_trace['spawn_resources_closed'])
      if mode=='prior':self._failure_trace['scan_unconfirmed']=True
      original_lstat=Path.lstat
      def lstat(path,*args,**kwargs):
       if mode=='vanish' and path==file:raise FileNotFoundError('synthetic disappearing temp lock')
       return original_lstat(path,*args,**kwargs)
      try:
       with patch.object(Path,'lstat',lstat):total=self._temp_scan(b,tmp,lambda:None)
       if mode=='prior':raise shared.Log2RunFailure('volcano_execution_unconfirmed')
       return total
      except shared.Log2RunFailure:raise
      except BaseException:raise shared.Log2RunFailure('volcano_execution_unconfirmed') from None
    runner=Harness()
    try:result=shared.Log2MatrixRunner.run(runner);facts=None
    except shared.Log2RunFailure as error:
     outer.assertEqual(error.code,'volcano_execution_unconfirmed');outer.assertIs(type(error.facts),shared.FailureFacts);facts=error.facts.snapshot();result=None
    outer.assertEqual(len(made),1);outer.assertEqual(made[0].poll(),0)
    outer.assertTrue(all(s.closed and s.calls==1 for s in scans));outer.assertGreaterEqual(len(scans),1)
    if facts:
     outer.assertEqual(facts['exit_code'],0);outer.assertTrue(facts['child_reaped'] and facts['observed_tasks_stopped'])
     outer.assertTrue(facts['timer_disabled'] and facts['signal_handler_restored'])
    return result,facts,runner.quarantined
  with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
   # Exact old defect: all real scandir handles close once, yet scan flag poisons FD evidence.
   result,old,quarantined=case(volcano.VolcanoRunner,'vanish',legacy=True)
   self.assertIsNone(result);self.assertFalse(old['owned_fds_closed']);self.assertFalse(old['cleanup_known']);self.assertTrue(quarantined)
   for adapter in (volcano.VolcanoRunner,heatmap.HeatmapRunner):
    result,facts,quarantined=case(adapter,'vanish')
    self.assertIsNone(result) # Validation still FAILS; no ready/output/publication fabricated.
    self.assertTrue(facts['owned_fds_closed'] and facts['cleanup_known']);self.assertFalse(quarantined)
   for mode in ('close','open','prior'):
    result,facts,quarantined=case(volcano.VolcanoRunner,mode)
    self.assertIsNone(result);self.assertFalse(facts['owned_fds_closed'] or facts['cleanup_known']);self.assertTrue(quarantined)
   result,facts,quarantined=case(volcano.VolcanoRunner,'success')
   self.assertGreater(result,0);self.assertIsNone(facts);self.assertFalse(quarantined)
  self.assertTrue(all(p.poll()==0 and p.stdout.closed and p.stderr.closed for p in children))
  self.assertEqual(len(children),7)
if __name__=='__main__':unittest.main()
