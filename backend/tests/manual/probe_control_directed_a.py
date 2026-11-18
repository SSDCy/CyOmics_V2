"""Directed offline Root callback tests; synthetic approval/receipts under temp.

No bootstrap/gate arming, actual approval, credential/Auth/DB/native/service I/O.
Uses pinned existing pure Root helpers, actual self identity and public sources.
"""
import ast,copy,hashlib,importlib.util,json,os,socket,stat,subprocess,sys,tempfile,time,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock,patch
HERE=Path(__file__).resolve().parent
PROJECT=Path('/home/user01/Cy_Omics_v2.0')
BASE=HERE.parents[1] # explicit A-only manual suite, current checkout backend
sys.path.insert(0,str(BASE))
import cyomics_projects
from cyomics_projects import preprocess_probe_control as adapter,preprocess_dev_probe as probe,preprocess_contract as c,preprocess_execution as execution,settings
LEGACY_SOURCE=PROJECT/'audit/RAW-QC-LIVE-CONTROL-001/preview_plan_ops_draft.py'
assert len(LEGACY_SOURCE.read_bytes())==8726 and hashlib.sha256(LEGACY_SOURCE.read_bytes()).hexdigest()==adapter.LEGACY_PIN['sha256']
spec=importlib.util.spec_from_file_location('pinned_existing_Root_pure_ops',LEGACY_SOURCE)
legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
GUARDS={'network':0,'subprocess':0,'native':0,'old_start_or_save':0,'actual_private_read':0}

def forbidden(kind):
 def stop(*args,**kwargs):GUARDS[kind]+=1;raise AssertionError('OFFLINE_FORBIDDEN_'+kind)
 return stop

def audit(event,args):
 if event=='open' and not isinstance(args[0],int):
  path=Path(os.fsdecode(args[0])).absolute()
  private=(path.is_relative_to(PROJECT/'runtime') or path.is_relative_to(PROJECT/'releases') and 'runtime' in path.relative_to(PROJECT/'releases').parts
   or path.parent==LEGACY_SOURCE.parent and path.name in ('runtime-scope-approval.json','api-process.json','node-process.json'))
  if private:forbidden('actual_private_read')()
  if path==Path('/usr/lib/R/bin/exec/R') or path.name=='libR.so':forbidden('native')()
 elif event=='socket.__new__' and args[1] in (socket.AF_INET,socket.AF_INET6):forbidden('network')()
 elif event=='subprocess.Popen' or event.startswith('os.exec') or event in ('os.system','os.posix_spawn'):forbidden('subprocess')()
sys.addaudithook(audit)

class Tests(unittest.TestCase):
 def setUp(self):
  self.original_flags=(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED,settings.production_gate,settings.load_runtime_password)
  self.stack=__import__('contextlib').ExitStack();self.addCleanup(self.stack.close)
  for module,names in [(socket.socket,['connect','connect_ex']),(socket,['create_connection']),(subprocess,['Popen','run','call','check_call','check_output'])]:
   for name in names:self.stack.enter_context(patch.object(module,name,forbidden('network' if module in (socket,socket.socket) else 'subprocess')))
  for name in ['verify_start','verify_scope','controls','save_process','stop_exact','verify_public_start_inputs']:
   self.stack.enter_context(patch.object(legacy,name,forbidden('old_start_or_save')))
  self.assertFalse(settings.AUTH_POLICY_VERIFIED);self.assertFalse(settings.RUNTIME_START_APPROVED)
 def tearDown(self):
  self.assertEqual(self.original_flags,(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED,settings.production_gate,settings.load_runtime_password))
 def fixture(self,*,scope_changes=None):
  temp=tempfile.TemporaryDirectory(prefix='probe-control-synthetic-');self.addCleanup(temp.cleanup);root=Path(temp.name)
  source=root/'cyomics_projects';source.mkdir(mode=0o700);registration=root/'registration';registration.mkdir(mode=0o700)
  manifest={}
  for name in sorted(probe.REQUIRED_SOURCES|{adapter.CONTROL_NAME}):
   src=Path(adapter.__file__) if name==adapter.CONTROL_NAME else BASE/'cyomics_projects'/name
   data=src.read_bytes();dst=source/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data);dst.chmod(0o644)
   manifest[name]={'bytes':len(data),'sha256':c.bytes_sha(data)}
  proposal=execution.proposal_bytes(manifest['log2_matrix_runner.py']);profile=execution.profile_bytes(proposal,c.digest(manifest),'a'*64)
  identity=legacy.identity(os.getpid());now=int(time.time()*1000)
  scope={'schemaVersion':1,'kind':'preprocess_dev_sdk','epoch_id':'11111111-1111-4111-8111-111111111111','registration_id':'22222222-2222-4222-8222-222222222222','issued_at_ms':now-1000,'expires_at_ms':now+60000,'max_jobs':1,'allow_confirmed_ack':True,'project_id':'33333333-3333-4333-8333-333333333333','qa_refs':{'owner_id':'44444444-4444-4444-8444-444444444444','workspace_id':'55555555-5555-4555-8555-555555555555','project_id':'33333333-3333-4333-8333-333333333333','file_id':'66666666-6666-4666-8666-666666666666','dataset_version_id':'77777777-7777-4777-8777-777777777777','request_id':'88888888-8888-4888-8888-888888888888'},'proposal_json':proposal.decode(),'profile_json':profile.decode(),'source_manifest':manifest,'expected_namespaces':identity['namespace_links'],'native_directory_pins':[{'synthetic_not_native_approval':True} for _ in range(5)],'db_target':{'host':'172.19.0.2','port':5432,'database':'cyomicsv2_business_dev','app_role':'cyomicsv2_app_runtime','worker_role':'cyomicsv2_raw_qc_worker_runtime'},'auth_policy_evidence_sha256':'b'*64,'row_budget':{'maximum':10,'registration':1,'workspace':1,'project':1,'raw_upload':1,'raw_dataset':1,'preprocessing_job':1,'job_run':1,'processed_version':1,'artifact_bundle':1,'default_delete':False,'reuse_real_owner':False}}
  if scope_changes:scope.update(scope_changes)
  raw=c.canonical(scope);approval=root/'Root-approval.json';approval.write_bytes(raw);approval.chmod(0o600)
  process={key:identity[key] for key in ('uid','cwd','executable','argv')}
  config=adapter.Config(approval,len(raw),c.bytes_sha(raw),source,c.digest(manifest),registration,scope['epoch_id'],scope['registration_id'],scope['kind'],c.canonical(process))
  expected={'kind':scope['kind'],'epoch_id':scope['epoch_id'],'scope_sha256':config.approval_sha256,'source_manifest_sha256':config.source_manifest_sha256,'pid':identity['pid'],'starttick':identity['starttick'],'uid':identity['uid']}
  return config,scope,expected
 def control(self,cfg):return adapter.ProbeControl(cfg,legacy_ops=legacy)
 def rejected(self,call):
  with self.assertRaises(adapter.ProbeControlRejected) as error:call()
  self.assertEqual(str(error.exception),'preprocessing_probe_control_rejected')
 def marker(self,cfg):return cfg.registration_root/(cfg.epoch_id+'.probe-process.json')
 def test_default_or_missing_Root_injection_is_zero_IO_and_never_authorizes(self):
  for control in [adapter.ProbeControl(),adapter.ProbeControl(None,legacy_ops=legacy),adapter.ProbeControl(object(),legacy_ops=legacy)]:
   with patch.object(legacy,'public_json') as read,patch.object(legacy,'identity') as identity:
    self.rejected(lambda:control.verify_probe_start({},{}));self.rejected(lambda:control.save_probe_process({}));read.assert_not_called();identity.assert_not_called()
 def test_normal_once_exact_callback_contract_complete_record_and_closed_descriptors(self):
  cfg,scope,expected=self.fixture();control=self.control(cfg)
  self.assertEqual(control.verify_probe_start(expected,scope),{**expected,'verified':True});self.assertFalse(self.marker(cfg).exists())
  self.assertEqual(control.save_probe_process(expected),{**expected,'recorded':True})
  file=self.marker(cfg);self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
  value=json.loads(file.read_bytes());self.assertEqual(value['expected'],expected);self.assertEqual(value['epoch_id'],cfg.epoch_id);self.assertEqual(value['registration_id'],cfg.registration_id);self.assertEqual(value['identity'],legacy.identity(os.getpid()))
  self.assertEqual(control._state,'consumed')
 def test_same_instance_and_fresh_instance_replay_are_rejected(self):
  cfg,scope,expected=self.fixture();control=self.control(cfg);control.verify_probe_start(expected,scope);control.save_probe_process(expected)
  before=self.marker(cfg).read_bytes();self.rejected(lambda:control.save_probe_process(expected));self.rejected(lambda:control.verify_probe_start(expected,scope));self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope));self.assertEqual(self.marker(cfg).read_bytes(),before)
 def test_two_verified_instances_cannot_both_register_same_epoch(self):
  cfg,scope,expected=self.fixture();first=self.control(cfg);second=self.control(cfg)
  first.verify_probe_start(expected,scope);second.verify_probe_start(expected,scope)
  self.assertEqual(first.save_probe_process(expected),{**expected,'recorded':True});saved=self.marker(cfg).read_bytes()
  self.rejected(lambda:second.save_probe_process(expected));self.assertEqual(self.marker(cfg).read_bytes(),saved)
 def test_partial_or_symlink_occupied_epoch_never_reauthorizes(self):
  for symlink in [False,True]:
   cfg,scope,expected=self.fixture();marker=self.marker(cfg)
   if symlink:marker.symlink_to(cfg.approval_path)
   else:marker.write_bytes(b'{');marker.chmod(0o600)
   self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
 def test_wrong_process_or_exact_identity_field_types_rejected(self):
  for key,value in [('pid',os.getpid()+1),('pid',True),('starttick',1),('uid',0),('uid',True)]:
   cfg,scope,expected=self.fixture();expected[key]=value;self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope));self.assertFalse(self.marker(cfg).exists())
 def test_wrong_process_spec_or_namespace_rejected(self):
  cfg,scope,expected=self.fixture();process=json.loads(cfg.process_spec_json);process['cwd']='/different/fixed/cwd'
  self.rejected(lambda:self.control(replace(cfg,process_spec_json=c.canonical(process))).verify_probe_start(expected,scope))
  cfg,scope,expected=self.fixture(scope_changes={'expected_namespaces':{'pid':'pid:[1]','net':'net:[2]','mnt':'mnt:[3]'}})
  self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
 def test_wrong_scope_epoch_hash_or_scope_argument_is_rejected(self):
  for key,value in [('epoch_id','99999999-9999-4999-8999-999999999999'),('scope_sha256','0'*64),('source_manifest_sha256','0'*64)]:
   cfg,scope,expected=self.fixture();expected[key]=value;self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
  cfg,scope,expected=self.fixture();changed=copy.deepcopy(scope);changed['max_jobs']=True
  self.rejected(lambda:self.control(cfg).verify_probe_start(expected,changed))
 def test_old_api_node_credentials_cannot_satisfy_new_probe(self):
  for role in ['api','node']:
   cfg,scope,expected=self.fixture();old={'schemaVersion':1,'role':role,'identity':legacy.identity(os.getpid()),'launcher_sha256':'a'*64}
   self.rejected(lambda:self.control(cfg).verify_probe_start(old,scope))
   expected['kind']=role;self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
 def test_expired_scope_and_bad_approval_permissions_rejected(self):
  cfg,scope,expected=self.fixture(scope_changes={'issued_at_ms':int(time.time()*1000)-120000,'expires_at_ms':int(time.time()*1000)-1000})
  self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
  cfg,scope,expected=self.fixture();cfg.approval_path.chmod(0o644);self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
 def test_expiry_or_monotonic_lifetime_after_verify_prevents_ack(self):
  for clock in ['wall','monotonic']:
   cfg,scope,expected=self.fixture();control=self.control(cfg);control.verify_probe_start(expected,scope)
   target='time' if clock=='wall' else 'monotonic'
   value=(scope['expires_at_ms']+1000)/1000 if clock=='wall' else control._deadline+1
   with patch.object(adapter.time,target,return_value=value):self.rejected(lambda:control.save_probe_process(expected))
   self.assertFalse(self.marker(cfg).exists());self.assertEqual(control._state,'unknown')
 def test_changed_source_or_scope_between_verify_and_save_never_records(self):
  for mutate in ['source','scope','identity']:
   cfg,scope,expected=self.fixture();control=self.control(cfg);control.verify_probe_start(expected,scope)
   if mutate=='source':(cfg.source_root/'settings.py').write_bytes(b'changed synthetic source')
   if mutate=='scope':cfg.approval_path.write_bytes(b'{}')
   if mutate=='identity':expected['starttick']+=1
   self.rejected(lambda:control.save_probe_process(expected));self.assertFalse(self.marker(cfg).exists())
 def test_write_failure_consumes_partial_epoch_without_ack_and_no_retry(self):
  cfg,scope,expected=self.fixture();control=self.control(cfg);control.verify_probe_start(expected,scope)
  with patch.object(adapter.os,'write',side_effect=OSError('SYNTHETIC_PRIVATE_WRITE')):self.rejected(lambda:control.save_probe_process(expected))
  self.assertTrue(self.marker(cfg).exists());self.assertEqual(control._state,'unknown');self.rejected(lambda:control.save_probe_process(expected));self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
 def test_file_and_directory_fsync_failures_never_authorize_even_complete_bytes(self):
  for failure_call in [1,2]:
   cfg,scope,expected=self.fixture();control=self.control(cfg);control.verify_probe_start(expected,scope);original=os.fsync;calls=[]
   def fsync(fd):
    calls.append(fd)
    if len(calls)==failure_call:raise BaseException('SYNTHETIC_PRIVATE_FSYNC')
    return original(fd)
   with patch.object(adapter.os,'fsync',side_effect=fsync):self.rejected(lambda:control.save_probe_process(expected))
   self.assertEqual(len(calls),failure_call);self.assertTrue(self.marker(cfg).exists());self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
 def test_close_failure_still_closes_other_descriptor_never_retries_or_acknowledges(self):
  cfg,scope,expected=self.fixture();control=self.control(cfg);control.verify_probe_start(expected,scope);original=os.close;record_closed=[];other_after=[];failed=False
  def close(fd):
   nonlocal failed
   target=os.readlink('/proc/self/fd/'+str(fd))
   original(fd)
   if target==str(self.marker(cfg)):
    record_closed.append(fd);failed=True;raise BaseException('SYNTHETIC_PRIVATE_CLOSE_AFTER_CLOSED')
   if failed:other_after.append(fd)
  with patch.object(adapter.os,'close',side_effect=close):self.rejected(lambda:control.save_probe_process(expected))
  self.assertEqual(len(record_closed),1);self.assertEqual(len(other_after),1);self.assertEqual(control._state,'unknown');self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
 def test_symlink_approval_and_nonprivate_registration_root_fail_closed(self):
  cfg,scope,expected=self.fixture();original=cfg.approval_path;backing=original.with_name('synthetic.json');original.rename(backing);original.symlink_to(backing)
  self.rejected(lambda:self.control(cfg).verify_probe_start(expected,scope))
  cfg,scope,expected=self.fixture();control=self.control(cfg);control.verify_probe_start(expected,scope);cfg.registration_root.chmod(0o755)
  self.rejected(lambda:control.save_probe_process(expected));self.assertFalse(self.marker(cfg).exists())

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
 print('OFFLINE_GUARDS '+json.dumps(GUARDS,sort_keys=True))
 sys.exit(0 if result.wasSuccessful() and all(x==0 for x in GUARDS.values()) else 1)
