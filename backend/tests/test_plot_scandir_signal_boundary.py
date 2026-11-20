"""ONE new R1 target: real SIGALRM in THIS test interpreter at scandir-return
line boundary, real directory handles, original FailureFacts calculation.
Already-exit0 process facts are explicit synthetic inputs; no R/DB/native
worker/service or incident mutation. R0 target is never replayed.
"""
from pathlib import Path
import ast,hashlib,os,signal,socket,subprocess,sys,tempfile,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cyomics_projects import log2_matrix_runner as shared,volcano_runner as volcano,heatmap_runner as heatmap
from cyomics_projects.limma_bound_runner import LimmaBoundRunner
R0=Path('/home/user01/Cy_Omics_v2.0/audit/S1-VOLCANO-FD-CLEANUP-FIX-001/candidate/backend/cyomics_projects/volcano_runner.py')
class BoundaryAlarm(RuntimeError):pass
class DirectoryHandle:
 def __init__(self,path,handles):self.iterator=os.scandir(path);self.close_calls=0;self.closed=False;handles.append(self)
 def __iter__(self):return self
 def __next__(self):return next(self.iterator)
 def close(self):self.close_calls+=1;self.iterator.close();self.closed=True
class ScandirSignalTarget(unittest.TestCase):
 def test_acquired_handle_boundary_stays_unknown(self):
  self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0));self.assertIsNone(sys.gettrace())
  # Compile ONLY the sealed R0 method as a historical regression baseline,
  # not an old target/module/factory, and retain its original line numbers.
  module=ast.parse(R0.read_text());node=next(n for n in ast.walk(module) if isinstance(n,ast.FunctionDef) and n.name=='_temp_scan')
  namespace={'shared':shared,'__package__':'cyomics_projects'};exec(compile(ast.Module(body=[node],type_ignores=[]),str(R0),'exec'),namespace)
  legacy=namespace['_temp_scan'];seen=[]
  def check_case(adapter,method,interrupt):
   handles=[];trigger={'sent':False,'opened_at_signal':None,'entries_was_assigned':False};saved_handler=signal.getsignal(signal.SIGALRM);saved_trace=sys.gettrace()
   def alarm(number,frame):
    self.assertEqual(number,signal.SIGALRM);raise BoundaryAlarm('synthetic SIGALRM after scandir return')
   filename=method.__code__.co_filename
   def trace(frame,event,arg):
    if interrupt and not trigger['sent'] and event=='line' and frame.f_code.co_name=='walk' and frame.f_code.co_filename==filename and 'entries' in frame.f_locals:
     trigger.update(sent=True,opened_at_signal=frame.f_locals['opened'],entries_was_assigned=True)
     # Actual signal to this finite test process only, never a service/PID from production.
     os.kill(os.getpid(),signal.SIGALRM)
    return trace
   with tempfile.TemporaryDirectory(prefix='synthetic-scandir-signal-') as directory:
    b=types.SimpleNamespace(os=types.SimpleNamespace(scandir=lambda p:DirectoryHandle(p,handles)),stat=__import__('stat'))
    class Harness(adapter):
     _temp_scan=method
     def __init__(self):
      shared.Log2MatrixRunner.__init__(self);self._config=types.SimpleNamespace(render_id='11111111-1111-4111-8111-111111111111',
       attempt_id='22222222-2222-4222-8222-222222222222',binding_sha256='a'*64,profile_json=b'{}')
     def _run_native(self,*,cancelled=lambda:False):
      # Fixed synthetic already-exited process facts isolate the FD accounting boundary.
      self._failure_trace.update(popen_attempted=True,launch_state='started',process_identity={'pid':1,'starttick':1,'proc_inode':1,'uid':1000},
       exit_code=0,child_reaped=True,observed_tasks_stopped=True,process_evidence_sha256='b'*64)
      signal.signal(signal.SIGALRM,alarm);sys.settrace(trace)
      try:return self._temp_scan(b,Path(directory),lambda:None)
      finally:sys.settrace(saved_trace);signal.signal(signal.SIGALRM,saved_handler)
    runner=Harness();facts=None;value=None
    try:
     try:value=shared.Log2MatrixRunner.run(runner)
     except shared.Log2RunFailure as error:
      self.assertEqual(error.code,'log2_run_unconfirmed');self.assertIs(type(error.facts),shared.FailureFacts);facts=error.facts.snapshot()
     if interrupt:
      self.assertTrue(trigger['sent'] and trigger['entries_was_assigned']);self.assertEqual(len(handles),1)
      self.assertFalse(handles[0].closed);self.assertEqual(handles[0].close_calls,0)
      self.assertIsNotNone(facts);self.assertEqual(facts['exit_code'],0);self.assertTrue(facts['child_reaped'] and facts['observed_tasks_stopped'])
     else:
      self.assertIsNone(facts);self.assertEqual(value,0);self.assertEqual(len(handles),1);self.assertTrue(handles[0].closed);self.assertEqual(handles[0].close_calls,1)
     seen.append({'method':method.__qualname__,'real_signal':trigger['sent'],'opened_at_signal':trigger['opened_at_signal'],
      'owned_fds_closed':None if facts is None else facts['owned_fds_closed'],'cleanup_known':None if facts is None else facts['cleanup_known']})
     return facts,runner.quarantined,trigger
    finally:
     sys.settrace(saved_trace);signal.signal(signal.SIGALRM,saved_handler)
     for handle in handles:
      if not handle.closed:handle.close() # Test-owned leak cleanup AFTER evidence checks, never incident state.
     self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))
  with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')),patch.object(subprocess,'Popen',side_effect=AssertionError('child forbidden')):
   old,quarantine,trigger=check_case(volcano.VolcanoRunner,legacy,True)
   self.assertEqual(trigger['opened_at_signal'],0);self.assertTrue(old['owned_fds_closed'] and old['cleanup_known']);self.assertFalse(quarantine)
   for adapter in (volcano.VolcanoRunner,heatmap.HeatmapRunner):
    facts,quarantine,trigger=check_case(adapter,adapter._temp_scan,True)
    self.assertEqual(trigger['opened_at_signal'],1);self.assertFalse(facts['owned_fds_closed'] or facts['cleanup_known']);self.assertTrue(quarantine)
    check_case(adapter,adapter._temp_scan,False)
  self.assertIsNone(sys.gettrace());self.assertEqual(len(seen),5)
  # Execute only actual staging-creation/fontconfig AST statements from each
  # adapter; no native/factory/profile execution. Then ORIGINAL scanner checks
  # the actual fixture directory, not merely expected text/mocked permissions.
  import inspect,stat,xml.etree.ElementTree as ET
  stage_checks=[]
  for adapter in (volcano.VolcanoRunner,heatmap.HeatmapRunner):
   filename=inspect.getsourcefile(adapter);tree=ast.parse(Path(filename).read_text())
   native=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_run_native')
   selected=[]
   for n in ast.walk(native):
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('job','fontconfig') for t in n.targets):selected.append(n)
    elif isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='mkdir':selected.append(n)
   selected.sort(key=lambda n:(n.lineno,n.col_offset));self.assertEqual(len(selected),6)
   with tempfile.TemporaryDirectory(prefix='synthetic-private-font-cache-') as directory:
    namespace={'root':Path(directory),'c':types.SimpleNamespace(render_id='33333333-3333-4333-8333-333333333333',attempt_id='44444444-4444-4444-8444-444444444444')}
    exec(compile(ast.Module(body=selected,type_ignores=[]),filename,'exec'),namespace)
    job=namespace['job'];cache=job/'tmp/font-cache'
    self.assertEqual(stat.S_IMODE(cache.lstat().st_mode),0o700);self.assertEqual(cache.lstat().st_uid,os.getuid())
    rootxml=ET.fromstring(namespace['fontconfig']);self.assertEqual([x.text for x in rootxml.findall('cachedir')],[str(cache)])
    fonts=job/'fonts.conf';fonts.write_bytes(namespace['fontconfig']);fonts.chmod(0o400)
    def identity(st):return (st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
    b=types.SimpleNamespace(os=os,stat=stat,identity=identity)
    self.assertEqual(LimmaBoundRunner._temp_scan(types.SimpleNamespace(_failure_trace=None),b,job/'tmp',lambda:None),0)
    self.assertEqual(adapter()._scan(b,job,{'fonts.conf':identity(fonts.lstat())},lambda:None),0)
    stage_checks.append({'adapter':adapter.__name__,'cache_mode':'0700','cachedir_matches':True,'original_temp_and_job_scans':'PASS','R_run':False})
  import json
  print(json.dumps({'R1_boundary_cases':seen,'font_cache_staging':stage_checks,'scope':'real selfSIGALRM + actual scandir; synthetic exited0 process facts; no R/worker/service'},sort_keys=True))
if __name__=='__main__':unittest.main()
