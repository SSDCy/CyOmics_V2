"""SQL-shaped memory pool only; no actual PostgreSQL, RLS, grants or engine."""
import asyncio,copy,json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID,uuid4
import test_raw_qc_contract as f
import cyomics_projects
cyomics_projects.__path__.insert(0,str(Path(__file__).resolve().parents[1]/'cyomics_projects'))
from cyomics_projects import preprocess_repository as p
from cyomics_projects.settings import Budget,Fault

OWNER,OTHER,PROJECT,DATASET=f.OWNER,f.OTHER,f.PROJECT,f.DATASET
EXECUTION={'contract':'log2_local_source_proposal_v0.1','entry_kind':'native_R','r_version':'R version 4.6.1',
    **copy.deepcopy(p.FIXED_PINS),'runner':{'bytes':21566,'sha256':'b9731437092d34d9143242c4ea3ec68593ca13869b99e910adc30efeb60f5db8'},
    'limits':dict(p.LIMITS),'process_policy_sha256':p.POLICY_SHA256,'registered':False}
EXECUTION_BYTES=p.canonical(EXECUTION).encode()
def body(rid=None,family='Intensity',flags=None):
    params=json.loads(p.contract.canonical_parameters(flags or {'reverse':False,'contaminant':False,'site_only':False}))
    return p.canonical({'request_id':rid or str(uuid4()),'quant_family':family,'parameters':params}).encode()

class Cursor:
    def __init__(self,row=None):self.row=row
    async def fetchone(self):return copy.deepcopy(self.row)

class DB:
    def __init__(self):
        self.rows={};self.lock=asyncio.Lock();self.sql=[];self.connections=[]
        self.commit_error=False;self.insert_error=False;self.close_mode=None;self.bad_raw=False;self.bad_report=False
    def pool(self):return Pool(self)

class Pool:
    def __init__(self,db):self.db=db;self.acquires=0;self.releases=0
    async def getconn(self):
        self.acquires+=1;c=Connection(self.db);self.db.connections.append(c);return c
    def release(self,c):
        assert c.closed is True
        self.releases+=1

class Connection:
    def __init__(self,db):
        self.db=db;self.pgconn=self;self.transaction_status=f.TS.IDLE;self._closed=False
        self.subject=None;self.pending={};self.held=False;self.committed=False;self.getter_faulted=False;self.close_calls=0
    @property
    def closed(self):
        if self.committed and self.db.close_mode=='getter' and not self.getter_faulted:
            self.getter_faulted=True;raise BaseException('PRIVATE_CLOSED_GETTER')
        return self._closed
    def unlock(self):
        if self.held:self.held=False;self.db.lock.release()
    def finish(self):
        self.close_calls+=1;self._closed=True;self.transaction_status=f.TS.UNKNOWN;self.unlock()
        if self.committed and self.db.close_mode=='finish_after_closed':raise BaseException('PRIVATE_FINISH')
    async def execute(self,sql,params=()):
        self.db.sql.append((sql,params))
        if sql.startswith('BEGIN'):
            self.transaction_status=f.TS.INTRANS;return Cursor()
        if "set_config('app.user_id'" in sql:self.subject=params[0];return Cursor()
        if sql.startswith('SELECT pg_catalog.set_config'):return Cursor()
        if sql=='COMMIT':
            self.db.rows.update(copy.deepcopy(self.pending));self.pending={};self.committed=True
            self.transaction_status=f.TS.IDLE;self.unlock()
            if self.db.commit_error:raise OSError('PRIVATE_LOST_COMMIT')
            return Cursor()
        if sql=='ROLLBACK':
            self.pending={};self.transaction_status=f.TS.IDLE;self.unlock();return Cursor()
        if 'pg_advisory_xact_lock' in sql:
            await self.db.lock.acquire();self.held=True;return Cursor()
        if 'FROM cyomics_app.projects' in sql:
            return Cursor((UUID(PROJECT),) if self.subject==OWNER and str(params[0])==OWNER and str(params[1])==PROJECT else None)
        if 'FROM cyomics_app.dataset_versions' in sql:
            row=list(f.DATASET_ROW)
            if self.db.bad_report:row[10]='{}'
            return Cursor(row if str(params[0])==PROJECT and str(params[1])==DATASET else None)
        if 'FROM cyomics_app.raw_uploads' in sql:
            row=list(f.RAW_META)
            if self.db.bad_raw:row[4]='0'*64
            return Cursor(row)
        if 'FROM cyomics_app.preprocessing_jobs' in sql:
            rows=[r for r in {**self.db.rows,**self.pending}.values() if r[1]==params[0]]
            if 'AND dataset_version_id=' in sql:rows=[r for r in rows if r[2]==params[1] and r[5]==params[2]]
            elif 'AND request_id=' in sql:rows=[r for r in rows if r[5]==params[1]]
            else:rows=[r for r in rows if r[0]==params[1]]
            return Cursor(rows[0] if rows else None)
        if sql.startswith('INSERT INTO cyomics_app.preprocessing_jobs'):
            if self.db.insert_error:raise OSError('PRIVATE_INSERT')
            row=list(params)+['queued',f.STAMP]
            assert not any(r[1]==row[1] and r[5]==row[5] for r in self.db.rows.values())
            self.pending[str(row[0])]=row;return Cursor(row)
        raise AssertionError('UNHANDLED_FIXED_SQL')

class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=DB();self.pool=self.db.pool();self.repo=p.PreprocessRepository(self.pool,EXECUTION_BYTES,enabled=True)
    async def enqueue(self,raw=None,subject=OWNER,repo=None,dataset=DATASET):
        return await (repo or self.repo).enqueue(subject,PROJECT,dataset,raw or body(),Budget())
    async def error(self,code,awaitable):
        with self.assertRaises(Fault) as caught:await awaitable
        self.assertEqual(caught.exception.code,code)
        self.assertNotIn('PRIVATE',str(vars(caught.exception)))
        return caught.exception

    async def test_default_off_missing_pool_or_execution_and_bad_body_are_zero_IO(self):
        with patch.object(p,'_transactions') as tx:
            for repo in (p.PreprocessRepository(),p.PreprocessRepository(self.pool,EXECUTION_BYTES),
                         p.PreprocessRepository(None,EXECUTION_BYTES,enabled=True),p.PreprocessRepository(self.pool,enabled=True)):
                await self.error('preprocessing_unavailable',self.enqueue(repo=repo))
            await self.error('invalid_preprocessing_request',self.enqueue(b'not json'))
            tx.assert_not_called()
        self.assertEqual(self.pool.acquires,0)

    async def test_owner_create_complete_queued_dto_then_read_request_and_job(self):
        raw=body();observation=await self.enqueue(raw);value=observation.job_public()
        self.assertEqual((observation.created,observation.commit_confirmed,observation.teardown_confirmed),(True,True,True))
        self.assertEqual(value['source']['source_sha256'],f.SHA)
        self.assertEqual(value['status'],'queued');self.assertIsNone(value['result'])
        self.assertFalse(any(value[k] for k in ('computation_performed','worker_started','execution_registered')))
        self.assertEqual(value,await self.repo.read_job(OWNER,PROJECT,value['job_id'],Budget()))
        self.assertEqual(value,await self.repo.read_request(OWNER,PROJECT,DATASET,value['request_id'],Budget()))
        self.assertEqual(self.pool.releases,3)
        self.assertTrue(all(c.closed for c in self.db.connections))
        self.assertTrue(any(' READ ONLY' in sql for sql,_ in self.db.sql))
        value['parameters']['filter_flags']['reverse']=True
        self.assertFalse(observation.job_public()['parameters']['filter_flags']['reverse'])

    async def test_foreign_owner_and_missing_project_have_same_denial_no_queue_or_source_query(self):
        saved=(await self.enqueue()).job_public();start=len(self.db.sql)
        await self.error('dataset_not_found',self.enqueue(subject=OTHER))
        await self.error('dataset_not_found',self.repo.read_job(OTHER,PROJECT,saved['job_id'],Budget()))
        tail=self.db.sql[start:]
        self.assertFalse(any('preprocessing_jobs' in sql or 'dataset_versions' in sql or 'raw_uploads' in sql for sql,_ in tail))
        await self.error('dataset_not_found',self.repo.read_job(OWNER,str(uuid4()),saved['job_id'],Budget()))

    async def test_same_request_replays_without_new_create_and_drift_is_conflict(self):
        raw=body();request=json.loads(raw)['request_id'];first=await self.enqueue(raw)
        replay=await self.enqueue(raw)
        self.assertFalse(replay.created);self.assertEqual(first.job_public(),replay.job_public())
        await self.error('preprocessing_request_conflict',self.enqueue(body(request,'LFQ intensity')))
        await self.error('preprocessing_request_conflict',self.enqueue(body(request,flags={'reverse':False,'contaminant':True,'site_only':False})))
        await self.error('preprocessing_request_conflict',self.enqueue(raw,dataset=str(uuid4())))
        self.assertEqual(len(self.db.rows),1)

    async def test_replay_retains_original_execution_across_server_runner_pin_change(self):
        raw=body();first=await self.enqueue(raw);changed=copy.deepcopy(EXECUTION);changed['runner']['sha256']='f'*64
        new=p.PreprocessRepository(self.pool,p.canonical(changed).encode(),enabled=True)
        replay=await self.enqueue(raw,repo=new)
        self.assertFalse(replay.created);self.assertEqual(first.job_public(),replay.job_public())

    async def test_source_and_full_report_corruption_refuse_before_insert_and_on_replay(self):
        raw=body();await self.enqueue(raw)
        for flag in ('bad_raw','bad_report'):
            setattr(self.db,flag,True)
            await self.error('preprocessing_persisted_invalid',self.enqueue(raw))
            await self.error('preprocessing_persisted_invalid',self.enqueue())
            setattr(self.db,flag,False)
        self.assertEqual(len(self.db.rows),1)

    async def test_unknown_dataset_absent_enabled_flag_and_missing_job_are_fixed(self):
        await self.error('dataset_not_found',self.enqueue(dataset=str(uuid4())))
        await self.error('preprocessing_flags_unavailable',self.enqueue(body(flags={'reverse':True,'contaminant':False,'site_only':False})))
        await self.error('preprocessing_job_not_found',self.repo.read_job(OWNER,PROJECT,str(uuid4()),Budget()))
        self.assertFalse(self.db.rows)

    async def test_commit_unknown_is_sticky_no_success_or_retry_even_if_fake_row_committed(self):
        raw=body();self.db.commit_error=True
        await self.error('preprocessing_submission_unknown',self.enqueue(raw))
        self.assertTrue(self.repo.quarantined);self.assertEqual(len(self.db.rows),1)
        count=len(self.db.sql)
        await self.error('preprocessing_unavailable',self.enqueue(raw));self.assertEqual(len(self.db.sql),count)
        self.assertTrue(self.db.connections[-1].closed)

    async def test_close_exception_after_closed_true_never_released_or_reported_as_success(self):
        for mode in ('finish_after_closed','getter'):
            db=DB();db.close_mode=mode;pool=db.pool();repo=p.PreprocessRepository(pool,EXECUTION_BYTES,enabled=True)
            await self.error('preprocessing_submission_unknown',self.enqueue(repo=repo))
            self.assertEqual(len(db.rows),1);self.assertEqual(pool.releases,0)
            self.assertTrue(repo.quarantined)
            if mode=='finish_after_closed':self.assertTrue(db.connections[-1].closed)

    async def test_read_close_unknown_never_returns_private_dto(self):
        value=(await self.enqueue()).job_public();self.db.close_mode='finish_after_closed'
        await self.error('preprocessing_unavailable',self.repo.read_job(OWNER,PROJECT,value['job_id'],Budget()))
        self.assertTrue(self.repo.quarantined)

    async def test_expired_budget_is_zero_connection_and_no_private_error_text(self):
        class Expired(Budget):
            def check(self):raise TimeoutError('PRIVATE_EXPIRED')
        await self.error('preprocessing_timeout',self.repo.enqueue(OWNER,PROJECT,DATASET,body(),Expired()))
        self.assertEqual(self.pool.acquires,0)

    async def test_insert_failure_rolls_back_and_does_not_invent_commit_observation(self):
        self.db.insert_error=True
        await self.error('preprocessing_unavailable',self.enqueue())
        self.assertFalse(self.db.rows);self.assertTrue(any(sql=='ROLLBACK' for sql,_ in self.db.sql))
        self.assertTrue(self.db.connections[-1].closed);self.assertEqual(self.pool.releases,1)

    async def test_two_same_request_calls_serialize_one_insert_not_a_second_job(self):
        raw=body();a,b=await asyncio.gather(self.enqueue(raw),self.enqueue(raw))
        self.assertEqual(a.job_public(),b.job_public());self.assertEqual(sorted([a.created,b.created]),[False,True])
        self.assertEqual(len(self.db.rows),1)
        self.assertEqual(sum(sql.startswith('INSERT INTO') for sql,_ in self.db.sql),1)

    async def test_tampered_persisted_binding_owner_and_unimplemented_status_never_decode(self):
        saved=(await self.enqueue()).job_public();key=saved['job_id'];original=copy.deepcopy(self.db.rows[key])
        for index,value in ((7,'0'*64),(8,'{}'),(10,'{}'),(11,'{}'),(12,'succeeded')):
            self.db.rows[key]=copy.deepcopy(original);self.db.rows[key][index]=value
            await self.error('preprocessing_persisted_invalid',self.repo.read_job(OWNER,PROJECT,key,Budget()))
        self.db.rows[key]=copy.deepcopy(original);self.db.rows[key][4]=UUID(OTHER)
        await self.error('preprocessing_job_not_found',self.repo.read_job(OWNER,PROJECT,key,Budget()))

    async def test_fixed_sql_parameterization_and_client_server_fields_not_accepted(self):
        for key,value in (('owner_id',OTHER),('execution',{}),('source','PRIVATE_PATH')):
            raw=json.loads(body());raw[key]=value
            await self.error('invalid_preprocessing_request',self.enqueue(json.dumps(raw).encode()))
        self.assertEqual(self.pool.acquires,0)
        await self.enqueue()
        self.assertTrue(all(OWNER not in sql and PROJECT not in sql for sql,_ in self.db.sql))
        self.assertTrue(any("set_config('app.user_id', %s, true)" in sql and params[0]==OWNER for sql,params in self.db.sql))

class SourceShapeTests(unittest.TestCase):
    def test_execution_source_proposal_cannot_self_register_or_supply_paths(self):
        for key,value in (('registered',True),('path','PRIVATE_PATH'),('limits',{}),('r_version','other')):
            changed=copy.deepcopy(EXECUTION);changed[key]=value
            with self.assertRaises(Fault):p.execution_value(p.canonical(changed))
        self.assertFalse(p.execution_value(EXECUTION_BYTES.decode())['registered'])

    def test_draft_sql_scope_force_RLS_column_grants_no_worker_or_transition(self):
        sql=(Path(__file__).resolve().parents[1]/'migrations/008_private_preprocessing_jobs.sql').read_text(encoding='utf8')
        for text in ('UNIQUE(project_id,request_id)','FORCE ROW LEVEL SECURITY','preprocessing_owner_select',
            'preprocessing_owner_insert','current_user_id()','IS DISTINCT FROM expected_source',
            'IS DISTINCT FROM expected_params','IS DISTINCT FROM expected_execution','GRANT SELECT(',
            'GRANT INSERT(','cyomics.preprocess008_reviewed',"CHECK(status='queued')"):
            self.assertIn(text,sql)
        for text in ('CREATE ROLE','SECURITY DEFINER','GRANT UPDATE','GRANT DELETE','result_json','raw_qc_claim(',
                     'ALTER TABLE cyomics_app.raw_uploads','ALTER TABLE cyomics_app.dataset_versions'):
            self.assertNotIn(text,sql)
        self.assertEqual(sql.count('CREATE TABLE '),1)
