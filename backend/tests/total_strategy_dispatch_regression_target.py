"""Only new three-way dispatch regression; no native/DB/Auth/gate arming.
Original full shape validator and original run_once with explicit boundary spies.
"""
import asyncio,json,sys,subprocess,socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
# Imports fixture definitions only. Its old main/full numerical target is never run.
sys.path.insert(0,str(Path(__file__).resolve().parent))
import total_processed_strategy_target as fixture
from cyomics_projects import preprocess_dev_probe as probe
from cyomics_projects import preprocess_contract as c,preprocess_repository as p,preprocess_execution as e
from cyomics_projects import median_processed_profile as median,total_processed_profile as total
from cyomics_projects import median_processed_io,total_processed_io,settings
from cyomics_projects.raw_qc_checkpoint import WorkerRoots
from cyomics_projects.settings import Budget

def source_pin(n):
 f=fixture.CANDIDATE/'cyomics_projects'/n
 if not f.exists():f=fixture.BASE/'cyomics_projects'/n
 return fixture.filepin(f)

def validate_profile_scope(method,proposal):
 extra=set()
 for m in ('median','total'):
  extra.update((m+'_processed_profile.py',m+'_processed_io.py',m+'_processed_runner.py',m+'_processed_validator.py',
    'r_'+m+'_processed/'+m+'_processed.R','r_normalization_'+m+'/'+m+'_core.R'))
 manifest={n:source_pin(n) for n in sorted(set(probe.REQUIRED_SOURCES)|extra)}
 raw_profile=e.profile_bytes(proposal,c.digest(manifest),'a'*64)
 scope={'schemaVersion':1,'kind':'preprocess_worker','epoch_id':str(uuid4()),'registration_id':fixture.REG,
 'issued_at_ms':1000,'expires_at_ms':2000,'max_jobs':1,'allow_confirmed_ack':True,'project_id':fixture.PROJECT,
 'qa_refs':{k:v for k,v in zip(('owner_id','workspace_id','project_id','file_id','dataset_version_id','request_id'),
 (fixture.OWNER,str(uuid4()),fixture.PROJECT,fixture.FILE,str(uuid4()),str(uuid4())))},
 'proposal_json':proposal.decode(),'profile_json':raw_profile.decode(),'source_manifest':manifest,
 'expected_namespaces':{'pid':'pid:[1]','net':'net:[2]','mnt':'mnt:[3]'},
 'native_directory_pins':[{}]*5,'auth_policy_evidence_sha256':'b'*64,
 'db_target':{'host':'172.19.0.2','port':5432,'database':'cyomicsv2_business_dev','app_role':'cyomicsv2_app_runtime','worker_role':'cyomicsv2_raw_qc_worker_runtime'},
 'row_budget':{'maximum':3,'registration':0,'workspace':0,'project':0,'raw_upload':0,'raw_dataset':0,'preprocessing_job':0,'job_run':1,'processed_version':1,'artifact_bundle':1,'default_delete':False,'reuse_real_owner':False}}
 # Actual pure shape/pin function; no Root/bootstrap/physical/activation path called.
 value=probe.validate_scope(c.canonical(scope));assert value==scope
 return value

async def run_case(method,root):
 db,selected,legacy,unused_total=fixture.make_fixture()
 proposal={'none':legacy,'median':median.proposal_bytes(),'total':total.proposal_bytes()}[method]
 scope=validate_profile_scope(method,proposal)
 db.registration.update(proposal_sha256=fixture.sha(proposal),profile_json=scope['profile_json'],
  profile_sha256=fixture.sha(scope['profile_json'].encode()))
 params=c._parameter_value({'reverse':True,'contaminant':True,'site_only':True}) if method=='none' else {'median':median,'total':total}[method].parameters(
  {'reverse':True,'contaminant':True,'site_only':True},{'processed_dataset_version_id':fixture.PARENT,'manifest_sha256':db.metadata(fixture.PARENT)['manifest_sha256']})
 body=c.canonical({'request_id':str(uuid4()),'quant_family':'LFQ intensity','parameters':params})
 owner=p.PreprocessRepository(db,legacy,enabled=True,median_execution_json=median.proposal_bytes(),total_execution_json=total.proposal_bytes())
 queued=await owner.enqueue(fixture.OWNER,fixture.PROJECT,db.source['dataset_version_id'],body,Budget())
 task=queued.job_public()
 paths=[]
 for name in ('control','inputs','staging','published','receipts'):
  f=root/name;f.mkdir(mode=0o700,parents=True);paths.append(f)
 roots=WorkerRoots(*paths);roots.verify()
 repo=p.PreprocessWorkerRepository(db,enabled=True);calls=[];seen=[]
 parent=(db.metadata(fixture.PARENT),db.versions[fixture.PARENT][1])
 async def load_median(*a):calls.append('load_median');return parent
 async def load_total(*a):calls.append('load_total');return parent
 # Explicit sentinels at the prepared-input boundary, never used by an executor.
 sentinels={n:SimpleNamespace(source_sha256=task['source']['source_sha256'],selected_sha256=task['source']['selected_columns_sha256'],
  parameters_sha256=task['parameters_sha256'],strategy=n) for n in ('none','median','total')}
 def prepare_none(bound,roots):calls.append('prepare_none');return sentinels['none']
 def prepare_median(bound,roots,stored,*,check):assert stored is parent;calls.append('prepare_median');return sentinels['median']
 def prepare_total(bound,roots,stored,*,check):assert stored is parent;calls.append('prepare_total');return sentinels['total']
 class StopBeforeNative(BaseException):pass
 def stop_config(capsule,prepared,roots,pins):
  seen.append(prepared);assert prepared is sentinels[method];raise StopBeforeNative()
 def forbidden(*a,**kw):raise AssertionError('No executor/native/socket/gate activation in this target')
 worker=e.PreprocessWorker(repo,roots,verify=lambda **kw:None,executor_factory=forbidden,prepare=prepare_none,
  registration_id=fixture.REG,scope_sha256=fixture.SCOPE,budget_factory=lambda:Budget(10))
 with patch.object(repo,'load_median_parent',load_median),patch.object(repo,'load_total_parent',load_total),\
      patch.object(median_processed_io,'prepare_median_input',prepare_median),patch.object(total_processed_io,'prepare_total_input',prepare_total),\
      patch.object(e,'runner_config',stop_config),patch.object(subprocess,'Popen',forbidden),patch.object(socket.socket,'connect',forbidden):
  out=await worker.run_once(expected_job_id=task['job_id'])
 expected=['prepare_none'] if method=='none' else ['load_'+method,'prepare_'+method]
 assert calls==expected and len(seen)==1 and seen[0] is sentinels[method],(method,calls,seen)
 assert out.reason=='worker_source_or_checkpoint_unknown' and db.published==0
 return {'method':method,'scope':'original validate_scope PASS','preparation_calls':calls,'sentinel_kept':True,'stopped_before_native':True}

async def main():
 before=(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)
 assert before==(False,False)
 root=Path(fixture.opts.audit_root);assert root.is_dir()
 results=[]
 for method in ('none','median','total'):results.append(await run_case(method,root/method))
 assert (settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)==before
 print(json.dumps({'status':'PASS_DISPATCH_ONLY_REVISION01','cases':results,'native_children':0,'actual_DB':False,'old_full_targets':0,'gates_unchanged':True}))

if __name__=='__main__':asyncio.run(main())
