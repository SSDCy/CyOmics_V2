"""In-memory SQL-shaped stub only. No PostgreSQL parsing, RLS, privileges or real concurrency proof."""
import asyncio
import copy
import unittest
from uuid import UUID,uuid4
from test_raw_qc_contract import *
del ContractTests  # imported fixtures must not double-count unittest discovery
from cyomics_projects.raw_qc_repository import RawQCRepository,RawQCWorkerRepository

class Cursor:
    def __init__(self,row=None):self.row=row
    async def fetchone(self):return copy.deepcopy(self.row)

class Database:
    def __init__(self):
        self.rows={};self.lock=asyncio.Lock();self.worker_lock=asyncio.Lock()
        self.commit_error=False;self.commit_late_open=False;self.rollback_error=False;self.bad_raw=False;self.bad_source=False
        self.fail_insert=False;self.sql=[];self.connections=[]
    def pool(self,worker=False):return Pool(self,worker)

class Pool:
    def __init__(self,db,worker):self.db=db;self.worker=worker;self.releases=0
    async def getconn(self):
        c=Connection(self.db,self.worker);self.db.connections.append(c);return c
    def release(self,c):self.releases+=1

class Connection:
    def __init__(self,db,worker):
        self.db=db;self.worker=worker;self.closed=False;self.pgconn=self;self.transaction_status=TS.IDLE
        self.subject=None;self.pending={};self.held=[]
    def finish(self):
        self.closed=True;self.transaction_status=TS.UNKNOWN
        self.unlock()
    def unlock(self):
        while self.held:self.held.pop().release()
    def visible(self):return dict(self.db.rows,**self.pending)
    async def execute(self,sql,params=()):
        self.db.sql.append((sql,params))
        if sql.startswith('BEGIN'):
            self.transaction_status=TS.INTRANS;return Cursor()
        if sql=='COMMIT':
            self.db.rows.update(copy.deepcopy(self.pending));self.pending={}
            self.transaction_status=TS.IDLE;self.unlock()
            if self.db.commit_error:
                self.db.commit_error=False
                if self.db.commit_late_open:self.transaction_status=TS.INTRANS
                raise OSError('lost synthetic COMMIT receipt')
            return Cursor()
        if sql=='ROLLBACK':
            self.pending={};self.transaction_status=TS.IDLE;self.unlock()
            if self.db.rollback_error:raise OSError('lost rollback')
            return Cursor()
        if "set_config('app.user_id'" in sql:self.subject=params[0];return Cursor()
        if "pg_advisory_xact_lock" in sql:
            await self.db.lock.acquire();self.held.append(self.db.lock);return Cursor()
        if 'FROM cyomics_app.projects' in sql:
            return Cursor((UUID(PROJECT),) if self.subject==OWNER and str(params[1])==PROJECT else None)
        if 'FROM cyomics_app.dataset_versions' in sql:
            row=list(DATASET_ROW)
            if self.db.bad_source:row[10]='{}'
            return Cursor(row if str(params[0])==PROJECT and str(params[1])==DATASET else None)
        if 'FROM cyomics_app.raw_uploads' in sql:
            raw=list(RAW_META)
            if self.db.bad_raw:raw[4]='0'*64
            return Cursor(raw)
        if 'FROM cyomics_app.raw_qc_read_attempt' in sql:
            if not self.worker:raise AssertionError('worker function privilege stub')
            if 'NULL' in sql:job=None;attempt=params[0]
            else:job,attempt=params
            rows=[r for r in self.visible().values() if (job is None or r[0]==job) and r[17]==attempt]
            return Cursor(rows[0] if rows else None)
        if 'FROM cyomics_app.raw_qc_claim' in sql:
            if not self.worker:raise AssertionError('worker function privilege stub')
            await self.db.worker_lock.acquire();self.held.append(self.db.worker_lock)
            rows=list(self.visible().values())
            if any(r[12]=='running' for r in rows):return Cursor()
            queued=sorted((r for r in rows if r[12]=='queued'),key=lambda r:(r[14],str(r[0])))
            if not queued:return Cursor()
            row=copy.deepcopy(queued[0]);row[12]='running';row[13]+=1;row[15]=STAMP;row[17]=params[0]
            self.pending[str(row[0])]=row;return Cursor(row)
        if 'FROM cyomics_app.raw_qc_transition' in sql:
            if not self.worker:raise AssertionError('worker function privilege stub')
            key,attempt,revision,target,result,error=params
            row=copy.deepcopy(self.visible().get(str(key)))
            if row is None or row[17]!=attempt or row[13]!=revision or row[12]!='running':return Cursor()
            row[12]='running' if target=='reconciliation_pending' else target
            row[13]+=1;row[18]='reconciliation_pending' if target=='reconciliation_pending' else 'confirmed'
            row[16]=None if target=='reconciliation_pending' else STAMP
            row[19]=error;row[20]=result;row[21]=None if result is None else digest(result)
            self.pending[str(key)]=row;return Cursor(row)
        if 'SELECT count(*) FROM cyomics_app.raw_qc_jobs' in sql:
            return Cursor((sum(r[1]==params[0] and r[12] in ('queued','running') for r in self.visible().values()),))
        if 'FROM cyomics_app.raw_qc_jobs' in sql:
            rows=[r for r in self.visible().values() if r[1]==params[0]]
            if 'AND dataset_version_id=' in sql:
                rows=[r for r in rows if r[2]==params[1] and r[5]==params[2]]
            elif 'AND request_id=' in sql:rows=[r for r in rows if r[5]==params[1]]
            elif 'AND job_id=' in sql:rows=[r for r in rows if r[0]==params[1]]
            return Cursor(rows[0] if rows else None)
        if sql.startswith('INSERT INTO cyomics_app.raw_qc_jobs'):
            if self.db.fail_insert:raise OSError('insert unavailable')
            row=list(params)+['queued',1,STAMP,None,None,None,'confirmed',None,None,None]
            if any(r[1]==row[1] and r[5]==row[5] for r in self.visible().values()):
                raise AssertionError('duplicate stub request')
            self.pending[str(row[0])]=row;return Cursor(row)
        if sql.startswith("SELECT pg_catalog.set_config"):return Cursor()
        raise AssertionError('unhandled SQL '+sql)

class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=Database();self.pool=self.db.pool();self.repo=RawQCRepository(self.pool,REGISTRATION)
        self.worker=RawQCWorkerRepository(self.db.pool(worker=True))
    async def enqueue(self,payload=None,owner=OWNER,repo=None):
        return await (repo or self.repo).enqueue(owner,PROJECT,DATASET,payload or body(),Budget())
    async def fault(self,code,operation):
        with self.assertRaises(Fault) as cm:await operation
        self.assertEqual(cm.exception.code,code)

    async def test_owner_before_payload_or_replay(self):
        b=body();saved=await self.enqueue(b)
        before=len(self.db.sql)
        await self.fault('dataset_not_found',self.enqueue(b'not json',OTHER))
        sql=[s for s,p in self.db.sql[before:]]
        self.assertFalse(any('raw_qc_jobs' in s for s in sql))
        await self.fault('dataset_not_found',self.repo.read_job(OTHER,PROJECT,saved['job_id'],Budget()))
        await self.fault('dataset_not_found',self.repo.read_request(OTHER,PROJECT,DATASET,json.loads(b)['request_id'],Budget()))

    async def test_replay_different_family_conflict_and_status(self):
        b=body();v=await self.enqueue(b);rid=json.loads(b)['request_id']
        self.assertEqual(v,await self.enqueue(b))
        await self.fault('raw_qc_request_conflict',self.enqueue(body(rid,'lfq')))
        self.assertEqual(v,await self.repo.read_request(OWNER,PROJECT,DATASET,rid,Budget()))
        self.assertEqual(v,await self.repo.read_job(OWNER,PROJECT,v['job_id'],Budget()))
        self.assertEqual(len(self.db.rows),1)

    async def test_same_uuid_concurrent_stub_serialization(self):
        b=body()
        a,c=await asyncio.gather(self.enqueue(b),self.enqueue(b))
        self.assertEqual(a,c);self.assertEqual(len(self.db.rows),1)
        self.assertTrue(any('pg_advisory_xact_lock' in sql for sql,p in self.db.sql))

    async def test_per_project_cap_atomic_stub_and_replay_not_charged(self):
        b=body();v=await self.enqueue(b)
        outcomes=await asyncio.gather(self.enqueue(),self.enqueue(),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,Fault) and x.code=='raw_qc_capacity' for x in outcomes),1)
        self.assertEqual(len(self.db.rows),2)
        self.assertEqual(v,await self.enqueue(b))

    async def test_missing_registration_and_replay_across_pin_change(self):
        off=RawQCRepository(self.pool)
        await self.fault('raw_qc_unavailable',self.enqueue(repo=off))
        b=body();v=await self.enqueue(b)
        self.assertEqual(v,await self.enqueue(b,repo=off))
        e=copy.deepcopy(SYNTHETIC_EXECUTION);e['runner']['sha256']='f'*64
        changed=RawQCRepository(self.pool,ExecutionRegistration(canonical(e)))
        self.assertEqual(v,await self.enqueue(b,repo=changed))

    async def test_source_integrity_before_replay(self):
        b=body();await self.enqueue(b);self.db.bad_raw=True
        await self.fault('raw_qc_persisted_invalid',self.enqueue(b))
        self.db.bad_raw=False;self.db.bad_source=True
        await self.fault('raw_qc_persisted_invalid',self.enqueue(b))

    async def test_commit_unknown_then_uuid_recovery(self):
        b=body();self.db.commit_error=True
        await self.fault('raw_qc_submission_unknown',self.enqueue(b))
        self.assertEqual(len(self.db.rows),1)
        self.assertTrue(self.db.connections[-1].closed)
        v=await self.repo.read_request(OWNER,PROJECT,DATASET,json.loads(b)['request_id'],Budget())
        self.assertEqual(v,await self.enqueue(b))
        self.assertEqual(len(self.db.rows),1)

    async def test_precommit_failure_is_not_unknown_and_rollback(self):
        self.db.fail_insert=True
        await self.fault('raw_qc_unavailable',self.enqueue())
        self.assertEqual(self.db.rows,{})
        self.assertTrue(any(sql=='ROLLBACK' for sql,p in self.db.sql))
        self.assertTrue(self.db.connections[-1].closed)

    async def test_rollback_ack_after_commit_dispatch_does_not_clear_unknown(self):
        self.db.commit_error=True;self.db.commit_late_open=True
        await self.fault('raw_qc_submission_unknown',self.enqueue())
        self.assertTrue(any(sql=='ROLLBACK' for sql,p in self.db.sql))
        self.assertEqual(len(self.db.rows),1)
        self.assertTrue(self.db.connections[-1].closed)

    async def test_expired_budget_never_acquires_connection(self):
        budget=Budget(0)
        await self.fault('raw_qc_timeout',self.repo.enqueue(OWNER,PROJECT,DATASET,body(),budget))
        self.assertEqual(self.db.connections,[])

    async def test_worker_claim_fence_reconcile_and_terminal(self):
        v=await self.enqueue();attempt=str(uuid4())
        claimed=await self.worker.claim(attempt,Budget())
        self.assertEqual(claimed['job']['status'],'running')
        self.assertIsNone(await self.worker.claim(str(uuid4()),Budget()))
        await self.fault('raw_qc_attempt_conflict',self.worker.read_attempt(v['job_id'],str(uuid4()),Budget()))
        pending=await self.worker.transition(v['job_id'],attempt,2,'reconciliation_pending',Budget())
        self.assertEqual(pending['observation'],'reconciliation_pending')
        await self.fault('raw_qc_attempt_conflict',self.worker.transition(v['job_id'],attempt,2,'failed',Budget(),error_code='runner_failed'))
        done=await self.worker.transition(v['job_id'],attempt,3,'succeeded',Budget(),result_bytes=canonical(result_for(pending)).encode())
        self.assertEqual(done['status'],'succeeded')
        await self.fault('raw_qc_attempt_conflict',self.worker.transition(v['job_id'],attempt,4,'failed',Budget(),error_code='runner_failed'))

    async def test_worker_claim_commit_unknown_recover_without_respawn(self):
        await self.enqueue();attempt=str(uuid4());self.db.commit_error=True
        await self.fault('raw_qc_transition_unknown',self.worker.claim(attempt,Budget()))
        v=await self.worker.recover_claim(attempt,Budget())
        self.assertEqual(v['status'],'running')
        self.assertIsNone(await self.worker.claim(str(uuid4()),Budget()))
        self.assertEqual(len([r for r in self.db.rows.values() if r[17]==UUID(attempt)]),1)

    async def test_worker_final_commit_unknown_recover_historical_success(self):
        await self.enqueue();attempt=str(uuid4());v=(await self.worker.claim(attempt,Budget()))['job']
        self.db.commit_error=True
        await self.fault('raw_qc_transition_unknown',self.worker.transition(v['job_id'],attempt,2,'succeeded',Budget(),result_bytes=canonical(result_for(v)).encode()))
        got=await self.worker.read_attempt(v['job_id'],attempt,Budget())
        self.assertEqual(got['status'],'succeeded')
        self.assertEqual(got['revision'],3)

    async def test_worker_strict_result_and_unsupported_cancel(self):
        v=await self.enqueue();attempt=str(uuid4());running=(await self.worker.claim(attempt,Budget()))['job']
        await self.fault('invalid_raw_qc_request',self.worker.transition(v['job_id'],attempt,2,'cancelled',Budget()))
        await self.fault('invalid_raw_qc_request',self.worker.transition(v['job_id'],attempt,2,'succeeded',Budget(),result_bytes=b'{}'))
        done=await self.worker.transition(v['job_id'],attempt,2,'failed',Budget(),error_code='worker_interrupted')
        self.assertIsNone(done['result']);self.assertEqual(done['status'],'failed')
        sql=' '.join(s for s,p in self.db.sql)
        self.assertNotIn('UPDATE cyomics_app.raw_qc_jobs',sql)

    async def test_invalid_subject_and_request_lookup(self):
        await self.fault('unauthenticated',self.enqueue(owner='unverified'))
        await self.fault('raw_qc_job_not_found',self.repo.read_request(OWNER,PROJECT,DATASET,str(uuid4()),Budget()))

if __name__=='__main__':
    unittest.main(verbosity=2)

