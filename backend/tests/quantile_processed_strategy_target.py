"""One new synthetic Quantile job flow; real original native controller/R4.6.1.
Explicit memory SQL and synthetic saved-parent fixture; no actual DB/Auth/service.
Old None numerical targets and previous Quantile real-data target are not replayed.
"""
from pathlib import Path
import argparse,asyncio,copy,hashlib,json,os,socket,stat,subprocess,sys
from dataclasses import asdict
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import UUID,uuid4

args=argparse.ArgumentParser();args.add_argument('--baseline-backend',required=True);args.add_argument('--audit-root',required=True)
opts=args.parse_args();sys.dont_write_bytecode=True
BASE=Path(opts.baseline_backend);CANDIDATE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE));import cyomics_projects
cyomics_projects.__path__.insert(0,str(CANDIDATE/'cyomics_projects'))
from psycopg.pq import TransactionStatus as T
from cyomics_projects import preprocess_contract as c,preprocess_repository as p,preprocess_execution as e
from cyomics_projects import quantile_processed_profile as profile,quantile_processed_validator as validator
from cyomics_projects import log2_matrix_validator as legacy,import_persistence as imports
from cyomics_projects import settings,quant_diff_input as differential
from cyomics_projects.raw_qc_checkpoint import WorkerRoots
from cyomics_projects.r_raw_qc import raw_qc_runner as native
from cyomics_projects.settings import Budget,Fault

OWNER='11111111-1111-4111-8111-111111111111';PROJECT='22222222-2222-4222-8222-222222222222'
FILE='33333333-3333-4333-8333-333333333333';PARENT='44444444-4444-4444-8444-444444444444'
PARENT_JOB='55555555-5555-4555-8555-555555555555';PARENT_ATTEMPT='66666666-6666-4666-8666-666666666666'
REG='77777777-7777-4777-8777-777777777777';PARENT_REG='88888888-8888-4888-8888-888888888888'
STAMP='2026-01-01T00:00:00.000000Z';SCOPE='1'*64

def sha(raw):return hashlib.sha256(raw).hexdigest()
def det(value):return json.loads(c.canonical(value))
def filepin(path):
    raw=path.read_bytes();return {'bytes':len(raw),'sha256':sha(raw)}
def timestamp(value):return datetime.fromisoformat(value.replace('Z','+00:00'))

class Cursor:
    def __init__(self,row):self.row=row
    async def fetchone(self):return self.row

class MemorySQL:
    """Only the exact SQL used by original repositories; does not emulate PG/RLS."""
    def __init__(self,source_row,source,raw,parent,parent_bodies,parent_record,registration):
        self.source_row=source_row;self.source=source;self.raw=raw
        self.versions={PARENT:(parent,parent_bodies)};self.records={PARENT_JOB:parent_record}
        self.registration=registration;self.events=[];self.native_receipt=None;self.published=0
    async def getconn(self):return Connection(self)
    def release(self,conn):assert conn.closed
    def admission(self,job):return p.admission_row(self.records[job]['admission'])
    def metadata(self,vid):return self.versions[vid][0]
    def runrow(self,job):return tuple(self.records[job]['run'][key] for key in p.RUN_PUBLIC_COLUMNS.split(','))

class Connection:
    def __init__(self,db):
        self.db=db;self.closed=False;self.actor=''
        self.pgconn=SimpleNamespace(transaction_status=T.IDLE,finish=self.finish)
    def finish(self):self.closed=True
    async def execute(self,sql,values=()):
        db=self.db;db.events.append(sql)
        if sql.startswith('BEGIN'):self.pgconn.transaction_status=T.INTRANS;return Cursor(None)
        if sql in ('COMMIT','ROLLBACK'):self.pgconn.transaction_status=T.IDLE;return Cursor(None)
        if "set_config('app.user_id'" in sql:self.actor=values[0];return Cursor(None)
        if 'set_config' in sql or 'pg_advisory_xact_lock' in sql:return Cursor(None)
        if sql.startswith('SELECT project_id FROM'):return Cursor((UUID(PROJECT),) if tuple(map(str,values))==(OWNER,PROJECT) else None)
        if sql.startswith('SELECT '+p.DATASET_COLUMNS+' FROM'):
            return Cursor(db.source_row if tuple(map(str,values))==(PROJECT,db.source['dataset_version_id']) else None)
        if sql.startswith('SELECT file_id,project_id,format,bytes,sha256,status FROM'):
            return Cursor((UUID(FILE),UUID(PROJECT),'proteinGroups_txt',len(db.raw),sha(db.raw),'stored'))
        if sql.startswith('SELECT job_id FROM cyomics_app.processed_dataset_versions'):
            vid=str(values[1]);return Cursor((UUID(db.metadata(vid)['job_id']),) if vid in db.versions else None)
        if sql.startswith('SELECT '+p.COLUMNS+' FROM'):
            key=str(values[1]);lookup='request_id' if 'request_id=%s' in sql else 'job_id'
            found=next((job for job,v in db.records.items() if v['admission'][lookup]==key),None)
            return Cursor(None if found is None else db.admission(found))
        if sql.startswith('SELECT '+p.RUN_PUBLIC_COLUMNS+' FROM'):
            job=str(values[0]);return Cursor(db.runrow(job) if job in db.records and db.records[job]['run'] is not None else None)
        if sql.startswith('SELECT registration_id,profile_sha256,attestation_sha256 FROM'):
            reg=next(v['registration'] for v in db.records.values() if v['registration']['registration_id']==str(values[0]))
            return Cursor((UUID(reg['registration_id']),reg['profile_sha256'],reg['attestation_sha256']))
        if sql.startswith('SELECT metadata_json,metadata_sha256,manifest_sha256,job_id'):
            md=db.metadata(str(values[1]));text=c.canonical(md).decode()
            return Cursor((text,sha(text.encode()),md['manifest_sha256'],UUID(md['job_id']),UUID(PROJECT),
                UUID(md['parent_dataset_version_id']),UUID(FILE),UUID(OWNER),timestamp(md['created_at'])))
        if sql.startswith('SELECT manifest_json,manifest_sha256'):
            md,bodies=db.versions[str(values[0])];text=c.canonical(md['artifacts']).decode();row=[text,md['manifest_sha256']]
            if 'octet_length' in sql:
                for name in c.ARTIFACT_NAMES:row.extend((len(bodies[name]),sha(bodies[name])))
            return Cursor(tuple(row))
        if sql.startswith('SELECT ') and ' FROM cyomics_app.processed_dataset_artifact_bundles ' in sql:
            column=sql[7:sql.index(' FROM')];name=next(k for k,v in p.ARTIFACT_COLUMNS.items() if v==column)
            return Cursor((db.versions[str(values[0])][1][name],))
        if sql.startswith('INSERT INTO cyomics_app.preprocessing_jobs'):
            # Actual repository derives all IDs/source/request/proposal hashes.
            row=list(values)+['queued',datetime.now(timezone.utc)]
            admission={key:(str(value) if isinstance(value,UUID) else e.stamp_now() if isinstance(value,datetime) else value)
                for key,value in zip(p.COLUMNS.split(','),row)}
            db.records[admission['job_id']]={'admission':admission,'run':None,'registration':det(db.registration)}
            return Cursor(p.admission_row(admission))
        if sql.startswith('SELECT cyomics_app.preprocessing_claim'):
            job,attempt,reg,scope=map(str,values);record=db.records[job]
            assert record['run'] is None and reg==REG and scope==SCOPE
            base=p.PreprocessRepository()._decode(db.admission(job),OWNER,PROJECT)
            record['run']={'job_id':job,'attempt_id':attempt,'registration_id':reg,'runtime_binding_sha256':
                c.runtime_binding(job,base['binding_sha256'],base['proposal_sha256'],reg,db.registration['profile_sha256'],attempt),
                'status':'running','revision':2,'observation':'confirmed','started_at':e.stamp_now(),'finished_at':None,
                'error_code':None,'receipt_sha256':None,'result_version_id':None,'manifest_sha256':None}
            return Cursor((det(record),))
        if sql.startswith('SELECT cyomics_app.preprocessing_run_record'):
            job,attempt=map(str,values);record=db.records[job];assert record['run']['attempt_id']==attempt
            return Cursor((det(record),))
        if sql.startswith('SELECT record,dataset_json,raw_meta,content FROM cyomics_app.preprocessing_bound_source'):
            job,attempt=map(str,values);record=db.records[job];assert record['run']['attempt_id']==attempt
            dataset={key:(str(v) if isinstance(v,UUID) else STAMP if isinstance(v,datetime) else v)
                for key,v in zip(p.DATASET_COLUMNS.split(','),db.source_row)}
            return Cursor((det(record),dataset,{'file_id':FILE,'project_id':PROJECT,'format':'proteinGroups_txt',
                'bytes':len(db.raw),'sha256':sha(db.raw),'status':'stored'},db.raw))
        if 'FROM cyomics_app.preprocessing_quantile_parent_bundle' in sql:
            job,attempt=map(str,values);assert db.records[job]['run']['attempt_id']==attempt
            md,bodies=db.versions[PARENT]
            return Cursor((c.canonical(md).decode(),c.canonical(md['artifacts']).decode(),*(bodies[n] for n in c.ARTIFACT_NAMES)))
        if sql.startswith('SELECT cyomics_app.preprocessing_publish'):
            job,attempt,revision,vid,md,manifest,receipt,*data=values
            job,attempt,vid=map(str,(job,attempt,vid));record=db.records[job]
            assert record['run']['revision']==revision and record['run']['attempt_id']==attempt
            metadata=json.loads(md);db.versions[vid]=(metadata,dict(zip(('filter-log.tsv','row-map.tsv','summary.tsv',
                'raw-retained.tsv','working-matrix.tsv','original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv',
                'imputed-mask.tsv','R-session-info.txt','parameters.json'),data)))
            db.native_receipt=json.loads(receipt);db.published+=1
            record['run'].update(status='succeeded',revision=revision+1,finished_at=metadata['created_at'],
                receipt_sha256=sha(receipt.encode()),result_version_id=vid,manifest_sha256=metadata['manifest_sha256'])
            return Cursor((det(record),))
        raise AssertionError('unexpected memory SQL '+sql[:120])

def make_fixture():
    fixture=BASE/'tests/fixtures/log2_matrix/fixtures'
    raw=(fixture/'proteinGroups.txt').read_bytes();selected=(fixture/'selected-lfq.tsv').read_bytes()
    stored=imports.StoredRaw(FILE,PROJECT,'proteinGroups_txt',len(raw),sha(raw),'stored',raw)
    ref=imports.trusted_source(stored,PROJECT,FILE,lambda:None)
    config=imports.canonical_json(imports.configuration(),4096);dataset=imports.version_key(FILE,config)
    imported=imports.build_candidate(stored,ref,config,dataset,lambda:None,lambda:False)
    report=json.loads(imported.report_json);columns=[v for v in report['sample_columns'] if v['quant_family']=='LFQ intensity']
    source={'project_id':PROJECT,'dataset_version_id':dataset,'file_id':FILE,'source_bytes':len(raw),
        'source_sha256':sha(raw),'parser_contract':'protein_groups_txt_v1','config_sha256':imported.config_sha256,
        'report_sha256':imported.report_sha256,'row_count':8,'column_count':9,'selected_columns_sha256':c.digest(columns)}
    row=(UUID(dataset),UUID(PROJECT),UUID(FILE),'protein_groups_txt_v1',imported.config_sha256,config,len(raw),sha(raw),
        'raw_backed',False,imported.report_json,imported.report_sha256,timestamp(STAMP))
    bodies={n:(fixture/'actual/lfq'/n).read_bytes() for n in c.ARTIFACT_NAMES}
    legacy_bundle=legacy.validate(raw,selected,'LFQ intensity',(True,True,True),bodies,check=lambda:None)
    legacy_runner=BASE/'cyomics_projects/log2_matrix_runner.py'
    legacy_proposal=e.proposal_bytes(filepin(legacy_runner))
    params=json.loads(bodies['parameters.json'])
    req=c.request_input(c.canonical({'request_id':'99999999-9999-4999-8999-999999999999','quant_family':'LFQ intensity','parameters':params}))
    admission={'job_id':PARENT_JOB,'project_id':PROJECT,'dataset_version_id':dataset,'file_id':FILE,'owner_id':OWNER,
        'request_id':req.request_id,'request_sha256':p.scoped_request_hash(PROJECT,dataset,req),
        'source_json':p.canonical(source),'quant_family':'LFQ intensity','params_json':req.parameters_bytes.decode(),
        'execution_json':legacy_proposal.decode(),'status':'queued','created_at':STAMP}
    admission['binding_sha256']=p.binding_hash(admission['request_sha256'],source,req,p.execution_value(legacy_proposal.decode()))
    parent_registration={'registration_id':PARENT_REG,'project_id':PROJECT,'startup_scope_sha256':SCOPE,
        'proposal_sha256':sha(legacy_proposal),'profile_json':'{}','profile_sha256':sha(b'{}'),
        'attestation_json':'{}','attestation_sha256':sha(b'{}')}
    rb=c.runtime_binding(PARENT_JOB,admission['binding_sha256'],sha(legacy_proposal),PARENT_REG,sha(b'{}'),PARENT_ATTEMPT)
    runtime={'registration_id':PARENT_REG,'profile_sha256':sha(b'{}'),'runtime_binding_sha256':rb,
        'attestation_sha256':sha(b'{}'),'receipt_sha256':'2'*64}
    parent={'schemaVersion':2,'processed_dataset_version_id':PARENT,'project_id':PROJECT,'parent_dataset_version_id':dataset,
        'file_id':FILE,'created_by':OWNER,'created_at':STAMP,'job_id':PARENT_JOB,'attempt_id':PARENT_ATTEMPT,'source':source,
        'quant_family':'LFQ intensity','steps':list(c.STEPS),'parameters':params,'parameters_sha256':sha(bodies['parameters.json']),
        'proposal_sha256':sha(legacy_proposal),'runtime':runtime,'tool':p.public_tool(p.execution_value(legacy_proposal.decode())),
        'manifest_sha256':legacy_bundle.manifest_sha256,'artifacts':json.loads(legacy_bundle.manifest_bytes),'analysis_ready':False}
    c.version_value(parent)
    run={'job_id':PARENT_JOB,'attempt_id':PARENT_ATTEMPT,'registration_id':PARENT_REG,'runtime_binding_sha256':rb,
        'status':'succeeded','revision':3,'observation':'confirmed','started_at':STAMP,'finished_at':STAMP,'error_code':None,
        'receipt_sha256':'2'*64,'result_version_id':PARENT,'manifest_sha256':parent['manifest_sha256']}
    new_proposal=profile.proposal_bytes()
    names=('preprocess_contract.py','preprocess_repository.py','preprocess_execution.py','preprocess_dev_probe.py',
        'log2_matrix_runner.py','log2_matrix_validator.py','quantile_processed_profile.py','quantile_processed_io.py',
        'quantile_processed_runner.py','quantile_processed_validator.py','r_quantile_processed/quantile_processed.R',
        'r_normalization_quantile/quantile_core.R','r_raw_qc/raw_qc_runner.py','r_raw_qc/process_policy.py')
    source_pins={name:filepin((CANDIDATE/'cyomics_projects'/name) if (CANDIDATE/'cyomics_projects'/name).exists() else BASE/'cyomics_projects'/name) for name in names}
    profile_json=e.profile_bytes(new_proposal,c.digest(source_pins),c.digest(dict(native.ENV,**native.NATIVE_ENV,TZ='UTC'))).decode()
    registration={'registration_id':REG,'project_id':PROJECT,'startup_scope_sha256':SCOPE,'proposal_sha256':sha(new_proposal),
        'profile_json':profile_json,'profile_sha256':sha(profile_json.encode()),'attestation_json':'{"synthetic_memory_registration":true}',
        'attestation_sha256':sha(b'{"synthetic_memory_registration":true}')}
    return MemorySQL(row,source,raw,parent,bodies,{'admission':admission,'run':run,'registration':parent_registration},registration),selected,legacy_proposal,new_proposal


def verify_four_profile_shapes(legacy,quantile):
    from cyomics_projects import preprocess_dev_probe as probe,median_processed_profile as m,total_processed_profile as t
    names=set(probe.REQUIRED_SOURCES)
    for method in ('median','total','quantile'):
        names.update((method+'_processed_profile.py',method+'_processed_io.py',method+'_processed_runner.py',method+'_processed_validator.py','r_'+method+'_processed/'+method+'_processed.R','r_normalization_'+method+'/'+method+'_core.R'))
    manifest={n:filepin(CANDIDATE/'cyomics_projects'/n if (CANDIDATE/'cyomics_projects'/n).exists() else BASE/'cyomics_projects'/n) for n in names}
    for method,proposal in [('none',legacy),('median',m.proposal_bytes()),('total',t.proposal_bytes()),('quantile',quantile)]:
        profile_json=e.profile_bytes(proposal,c.digest(manifest),'a'*64).decode()
        scope={'schemaVersion':1,'kind':'preprocess_worker','epoch_id':str(uuid4()),'registration_id':REG,'project_id':PROJECT,
          'issued_at_ms':1000,'expires_at_ms':2000,'max_jobs':1,'allow_confirmed_ack':True,
          'qa_refs':dict(zip(('owner_id','workspace_id','project_id','file_id','dataset_version_id','request_id'),(OWNER,str(uuid4()),PROJECT,FILE,str(uuid4()),str(uuid4())))),
          'proposal_json':proposal.decode(),'profile_json':profile_json,'source_manifest':manifest,
          'expected_namespaces':{'pid':'pid:[1]','net':'net:[2]','mnt':'mnt:[3]'},'native_directory_pins':[{}]*5,
          'db_target':{'host':'172.19.0.2','port':5432,'database':'cyomicsv2_business_dev','app_role':'cyomicsv2_app_runtime','worker_role':'cyomicsv2_raw_qc_worker_runtime'},
          'auth_policy_evidence_sha256':'b'*64,'row_budget':{'maximum':3,'registration':0,'workspace':0,'project':0,'raw_upload':0,'raw_dataset':0,'preprocessing_job':0,'job_run':1,'processed_version':1,'artifact_bundle':1,'default_delete':False,'reuse_real_owner':False}}
        assert probe.validate_scope(c.canonical(scope))==scope
        assert ('dependencies' in json.loads(profile_json))==(method=='quantile')

async def main():
    assert sys.platform=='linux' and os.getuid()==1000 and sys.dont_write_bytecode
    root=Path(opts.audit_root);assert root.resolve()==root and root.is_dir()
    os.umask(0o077)
    paths=[]
    for name in ('control','inputs','staging','published','receipts'):
        path=root/name;path.mkdir(mode=0o700);paths.append(path)
    roots=WorkerRoots(*paths);roots.verify()
    before=(settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)
    assert before==(False,False)
    db,selected,legacy_proposal,quantile_proposal=make_fixture()
    verify_four_profile_shapes(legacy_proposal,quantile_proposal)
    params=profile.parameters({'reverse':True,'contaminant':True,'site_only':True},
        {'processed_dataset_version_id':PARENT,'manifest_sha256':db.metadata(PARENT)['manifest_sha256']})
    body=c.canonical({'request_id':str(uuid4()),'quant_family':'LFQ intensity','parameters':params})
    original_event_count=len(db.events)
    try:await p.PreprocessRepository(db,legacy_proposal,enabled=True).enqueue(OWNER,PROJECT,db.source['dataset_version_id'],body,Budget())
    except Fault as failure:assert (failure.status,failure.code)==(503,'preprocessing_unavailable')
    else:raise AssertionError('Quantile must be default off')
    assert len(db.events)==original_event_count
    owner=p.PreprocessRepository(db,legacy_proposal,enabled=True,quantile_execution_json=quantile_proposal)
    queued=await owner.enqueue(OWNER,PROJECT,db.source['dataset_version_id'],body,Budget())
    task=queued.job_public();assert task['status']=='queued' and task['parameters']==params
    worker_repo=p.PreprocessWorkerRepository(db,enabled=True)
    def verify(**kwargs):
        assert (settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)==before
        if 'registration' in kwargs:assert kwargs['registration']['proposal_sha256']==sha(quantile_proposal)
    worker=e.PreprocessWorker(worker_repo,roots,verify=verify,executor_factory=e.runner_factory,
        registration_id=REG,scope_sha256=SCOPE,native_directory_pins=native.capture_native_directory_pins(),budget_factory=lambda:Budget(40))
    native_calls=[];original_popen=subprocess.Popen;original_connect=socket.socket.connect
    def allowed_native(argv,*args,**kwargs):
        assert argv[0]=='/usr/lib/R/bin/exec/R' and filepin(Path(argv[0]))==profile.PINS['executable']
        assert kwargs.get('env')==dict(native.ENV,**native.NATIVE_ENV,TZ='UTC')
        native_calls.append(list(argv));assert len(native_calls)==1
        return original_popen(argv,*args,**kwargs)
    def denied(*args,**kwargs):raise AssertionError('No network or DB allowed in source target')
    subprocess.Popen=allowed_native;socket.socket.connect=denied
    try:outcome=await worker.run_once(expected_job_id=task['job_id'])
    finally:subprocess.Popen=original_popen;socket.socket.connect=original_connect
    assert outcome.status=='confirmed' and outcome.reason=='succeeded', (outcome.status,outcome.reason)
    assert len(native_calls)==1 and db.published==1
    assert db.native_receipt['dependencies']==profile.DEPENDENCIES
    succeeded=outcome.task();vid=succeeded['result']['processed_dataset_version_id']
    # Original Owner repository, independent instance, complete metadata and11 pinned downloads.
    fresh=p.PreprocessRepository(db,enabled=True)
    metadata=await fresh.read_version(OWNER,PROJECT,vid,Budget())
    outputs={}
    for name in c.ARTIFACT_NAMES:outputs[name]=(await fresh.read_artifact(OWNER,PROJECT,vid,name,Budget())).body
    c.version_value(metadata,task=succeeded)
    assert metadata['parameters']['normalization']=='quantile' and metadata['parameters']['normalization_parent']==params['normalization_parent']
    assert metadata['tool']['script_sha256']==profile.PINS['script']['sha256'] and metadata['analysis_ready'] is False
    checked=validator.validate_bundle(db.raw,selected,'LFQ intensity',(True,True,True),outputs,check=lambda:None,
        parent=db.metadata(PARENT),parent_outputs=db.versions[PARENT][1],parameters=params,owner=OWNER)
    assert checked.manifest_sha256==metadata['manifest_sha256'] and len(checked.artifacts)==11
    summary=list(legacy._table(outputs['summary.tsv']))
    assert summary[0]==list(profile.SUMMARY_COLUMNS) and len(summary)==3
    assert all(row[21]=='NA' and row[22]=='not_scalar_rank_transform' for row in summary[1:])
    assert all(float(row[18])==0 and int(row[13])==2 and int(row[14])==0 for row in summary[1:])
    assert list(legacy._table(outputs['working-matrix.tsv']))[1][2:]==['1.5','1.5']
    blocked=differential.bind(differential.ServerInput(PROJECT,vid,metadata,outputs,(),{}, {},'independent_biological'))
    assert blocked.model_input_satisfied is False and blocked.reasons==('invalid_processed_version',)
    assert (settings.AUTH_POLICY_VERIFIED,settings.RUNTIME_START_APPROVED)==before
    record={'task':'S1-QUANTILE-PROCESSED-STRATEGY-001','status':'PASS_ONE_NEW_QUANTILE_JOB_FLOW',
        'environment':'Actual A native R4.6.1/9c572a via original owned-task process policy',
        'DB':'Explicit memory SQL and synthetic saved-parent registration; not actual PG/AuthHTTP/publication',
        'flow':['request','original enqueue','claim','bound raw source','job-attempt parent bundle','original journal worker',
            'Quantile runner over original native controller','full strict Quantile validator','original repository publish',
            'fresh Owner repository version+all11 readback'],
        'native_children':len(native_calls),'old_numerical_suites':0,'old_Quantile_real_data_targets':0,
        'new_parameters':params,'summary_columns':list(profile.SUMMARY_COLUMNS),'summary_rows':len(summary)-1,
        'observed_factors':[dict(zip(summary[0],row)) for row in summary[1:]],
        'manifest_sha256':metadata['manifest_sha256'],'artifacts':metadata['artifacts'],
        'downstream_Quantile_rejected_not_relabelled_None':True,'default_Quantile_off':True,
        'unchanged_original_core_sha256':profile.CORE_PIN['sha256'],'actual_DB_HTTP_RLS_services_credentials':False}
    (root/'metadata.json').write_bytes(c.canonical(metadata));(root/'native-receipt.json').write_bytes(c.canonical(db.native_receipt))
    (root/'summary.tsv').write_bytes(outputs['summary.tsv']);(root/'parameters.json').write_bytes(outputs['parameters.json'])
    (root/'target-result.json').write_bytes(c.canonical(record));print(json.dumps(record,sort_keys=True))

if __name__=='__main__':asyncio.run(main())
