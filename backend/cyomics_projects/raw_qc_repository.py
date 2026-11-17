"""Owner repositories + narrow worker function calls. No HTTP, sockets, or launch on import.
The API caller must supply Authority.verify's subject, never a client Owner field.
Worker pool MUST authenticate as the separately reviewed function-only worker role.
"""
import asyncio
from uuid import UUID, uuid4
from psycopg.pq import TransactionStatus as T
from .repository import abort, status
from .import_repository import COLUMNS as DATASET_COLUMNS
from .settings import Fault
from .validation import canonical_uuid
from .raw_qc_contract import (COLUMNS, MAX_RESPONSE, canonical, digest, request_input,
    request_sha, binding_sha, bound_source, decode_record, strict_document, result_value, ERROR_CODES)


class Transactions:
    def __init__(self, pool):
        self.pool = pool

    async def transaction(self, subject, budget, operation, writing=False):
        connection = None
        mutation = commit_started = False
        async def execute(sql, params=(), commit=False, write=False):
            nonlocal mutation, commit_started
            budget.check()
            async def run():
                nonlocal mutation, commit_started
                if write:
                    mutation = True  # failure after dispatch may leave a pending transaction
                if commit and writing and mutation:
                    commit_started = True
                return await connection.execute(sql, params)
            return await budget.run(run,2,lambda:abort(connection))
        async def query(sql, params=(), write=False):
            await execute("SELECT pg_catalog.set_config('statement_timeout', %s, true)",
                          (str(max(1,min(2000,int(budget.remaining()*1000)))),))
            cursor = await execute(sql,params,write=write)
            return await budget.run(cursor.fetchone,2,lambda:abort(connection))
        def discard(task):
            if not task.cancelled() and task.exception() is None:
                late = task.result(); abort(late); self.pool.release(late)
        try:
            connection = await budget.run(self.pool.getconn,1,discard=discard)
            if status(connection) != T.IDLE:
                raise Fault(503,'raw_qc_unavailable')
            await execute('BEGIN ISOLATION LEVEL READ COMMITTED'+('' if writing else ' READ ONLY'))
            await execute("SELECT pg_catalog.set_config('app.user_id', %s, true), "
                          "pg_catalog.set_config('statement_timeout', %s, true), "
                          "pg_catalog.set_config('lock_timeout', %s, true)",
                          (subject or '', '2000', '1000'))
            result = await operation(query)
            budget.check()
            await execute('COMMIT',commit=True)
            budget.check()
            if status(connection) != T.IDLE:
                raise Fault(503,'raw_qc_unavailable')
            return result
        except BaseException as error:
            if connection is not None and not connection.closed and status(connection) in (T.INTRANS,T.INERROR) and budget.remaining():
                try:
                    await execute('ROLLBACK')
                except BaseException:
                    abort(connection)
            if commit_started:
                if connection is not None:
                    abort(connection)
                # A rollback receipt never disproves a previously dispatched COMMIT.
                raise Fault(504,'raw_qc_submission_unknown' if subject else 'raw_qc_transition_unknown') from None
            if isinstance(error,asyncio.CancelledError):
                raise
            if isinstance(error,Fault):
                raise
            raise Fault(504 if isinstance(error,TimeoutError) else 503,
                        'raw_qc_timeout' if isinstance(error,TimeoutError) else 'raw_qc_unavailable') from None
        finally:
            if connection is not None:
                self.pool.release(connection)


class RawQCRepository(Transactions):
    def __init__(self, pool, registration=None):
        super().__init__(pool)
        self.registration = registration  # None by default: no new enqueue without reviewed config

    @staticmethod
    def ids(subject, project, dataset=None, request=None, job=None):
        if not canonical_uuid(subject):
            raise Fault(401,'unauthenticated')
        if not canonical_uuid(project) or any(v is not None and not canonical_uuid(v) for v in (dataset,request,job)):
            raise Fault(400,'invalid_raw_qc_request')

    async def owner(self, query, subject, project):
        if await query('SELECT project_id FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s',
                       (UUID(subject),UUID(project))) is None:
            raise Fault(404,'dataset_not_found')

    async def source(self, query, subject, project, dataset, family):
        await self.owner(query,subject,project)
        row = await query('SELECT '+DATASET_COLUMNS+' FROM cyomics_app.dataset_versions WHERE project_id=%s AND dataset_version_id=%s',
                          (UUID(project),UUID(dataset)))
        if row is None:
            raise Fault(404,'dataset_not_found')
        raw = await query('SELECT file_id,project_id,format,bytes,sha256,status FROM cyomics_app.raw_uploads WHERE project_id=%s AND file_id=%s',
                          (UUID(project),row[2]))
        if raw is None:
            raise Fault(503,'raw_qc_persisted_invalid')
        return bound_source(row,raw,project,dataset,family)

    async def enqueue(self, subject, project, dataset, raw_body, budget):
        self.ids(subject,project,dataset)
        async def operation(query):
            # Explicit owner comes before body/source validation and replay.
            await self.owner(query,subject,project)
            body = request_input(raw_body)
            source = await self.source(query,subject,project,dataset,body['quant_family'])
            request_hash = request_sha(project,dataset,body['quant_family'])
            await query('SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 47004))',(project,))
            prior = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_jobs WHERE project_id=%s AND request_id=%s',
                                (UUID(project),UUID(body['request_id'])))
            if prior is not None:
                existing = decode_record(prior)
                if existing['request_sha256'] != request_hash:
                    raise Fault(409,'raw_qc_request_conflict')
                if str(prior[4]) != subject:
                    raise Fault(404,'raw_qc_job_not_found')
                # Validate current source independently; old execution pins remain immutable across deployments.
                return decode_record(prior,source)
            count = await query("SELECT count(*) FROM cyomics_app.raw_qc_jobs WHERE project_id=%s AND status IN ('queued','running')",(UUID(project),))
            if count is None or type(count[0]) is not int:
                raise Fault(503,'raw_qc_persisted_invalid')
            if count[0] >= 2:
                raise Fault(429,'raw_qc_capacity')
            if self.registration is None:
                raise Fault(503,'raw_qc_unavailable')
            try:
                execution = self.registration.snapshot()
            except Exception:
                raise Fault(503,'raw_qc_unavailable') from None
            binding = binding_sha(request_hash,source,execution,body['quant_family'])
            row = await query('INSERT INTO cyomics_app.raw_qc_jobs '
                '(job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,binding_sha256,source_json,quant_family,params_json,execution_json) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING '+COLUMNS,
                (uuid4(),UUID(project),UUID(dataset),UUID(source['file_id']),UUID(subject),UUID(body['request_id']),
                 request_hash,binding,canonical(source),body['quant_family'],'{}',canonical(execution)),write=True)
            value = decode_record(row,source)
            if value['request_id'] != body['request_id'] or value['binding_sha256'] != binding:
                raise Fault(503,'raw_qc_persisted_invalid')
            return value
        return await self.transaction(subject,budget,operation,writing=True)

    async def read_request(self, subject, project, dataset, request, budget):
        self.ids(subject,project,dataset,request=request)
        async def operation(query):
            await self.owner(query,subject,project)
            row = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_jobs WHERE project_id=%s AND dataset_version_id=%s AND request_id=%s',
                              (UUID(project),UUID(dataset),UUID(request)))
            return await self.checked(query,subject,project,row)
        return await self.transaction(subject,budget,operation)

    async def read_job(self, subject, project, job, budget):
        self.ids(subject,project,job=job)
        async def operation(query):
            await self.owner(query,subject,project)
            row = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_jobs WHERE project_id=%s AND job_id=%s',
                              (UUID(project),UUID(job)))
            return await self.checked(query,subject,project,row)
        return await self.transaction(subject,budget,operation)

    async def checked(self, query, subject, project, row):
        if row is None or str(row[4]) != subject:
            raise Fault(404,'raw_qc_job_not_found')
        value = decode_record(row)
        source = await self.source(query,subject,project,value['source']['dataset_version_id'],value['quant_family'])
        return decode_record(row,source)


class RawQCWorkerRepository(Transactions):
    """Only predeclared SQL functions; no caller Owner and no generic UPDATE API.
    Pool selection/credentials are not proven by a Python class; actual grants are a QA gate.
    No worker loop/launch/export/download exists in this slice.
    """
    @staticmethod
    def keys(job=None, attempt=None, revision=None):
        if any(v is not None and not canonical_uuid(v) for v in (job,attempt)):
            raise Fault(400,'invalid_raw_qc_request')
        if revision is not None and (type(revision) is not int or revision < 1):
            raise Fault(400,'invalid_raw_qc_request')

    async def claim(self, attempt, budget):
        self.keys(attempt=attempt)
        if attempt is None:
            raise Fault(400,'invalid_raw_qc_request')
        async def operation(query):
            row = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_claim(%s)',(UUID(attempt),),write=True)
            if row is None:
                return None
            result = decode_record(row)
            if str(row[17]) != attempt or result['status'] != 'running':
                raise Fault(503,'raw_qc_persisted_invalid')
            return {'job':result,'attempt_id':attempt}
        return await self.transaction(None,budget,operation,writing=True)

    async def read_attempt(self, job, attempt, budget):
        self.keys(job,attempt)
        if job is None or attempt is None:
            raise Fault(400,'invalid_raw_qc_request')
        async def operation(query):
            row = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_read_attempt(%s,%s)',
                              (UUID(job),UUID(attempt)))
            if row is None:
                raise Fault(409,'raw_qc_attempt_conflict')
            return decode_record(row)
        return await self.transaction(None,budget,operation)

    async def recover_claim(self, attempt, budget):
        """Recover lost claim COMMIT without knowing its assigned job UUID. Never claims again."""
        self.keys(attempt=attempt)
        if attempt is None:
            raise Fault(400,'invalid_raw_qc_request')
        async def operation(query):
            row = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_read_attempt(NULL,%s)',(UUID(attempt),))
            return None if row is None else decode_record(row)
        return await self.transaction(None,budget,operation)

    async def transition(self, job, attempt, revision, target, budget, *, result_bytes=None, error_code=None):
        self.keys(job,attempt,revision)
        if job is None or attempt is None or revision is None or target not in ('succeeded','failed','reconciliation_pending'):
            raise Fault(400,'invalid_raw_qc_request')
        async def operation(query):
            current = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_read_attempt(%s,%s)',
                                  (UUID(job),UUID(attempt)))
            if current is None:
                raise Fault(409,'raw_qc_attempt_conflict')
            value = decode_record(current)
            if value['status'] != 'running' or value['revision'] != revision:
                raise Fault(409,'raw_qc_attempt_conflict')
            result_text = None
            try:
                if target == 'succeeded':
                    if error_code is not None:
                        raise ValueError()
                    result = strict_document(result_bytes,MAX_RESPONSE)
                    result_value(result,value)
                    result_text = canonical(result)
                elif result_bytes is not None or (target == 'failed' and error_code not in ERROR_CODES) or (target != 'failed' and error_code is not None):
                    raise ValueError()
            except (ValueError,TypeError,KeyError,UnicodeError):
                raise Fault(400,'invalid_raw_qc_request') from None
            row = await query('SELECT '+COLUMNS+' FROM cyomics_app.raw_qc_transition(%s,%s,%s,%s,%s,%s)',
                              (UUID(job),UUID(attempt),revision,target,result_text,error_code),write=True)
            if row is None:
                raise Fault(409,'raw_qc_attempt_conflict')
            decoded = decode_record(row)
            expected = 'running' if target == 'reconciliation_pending' else target
            if decoded['status'] != expected or decoded['revision'] != revision+1:
                raise Fault(503,'raw_qc_persisted_invalid')
            return decoded
        return await self.transaction(None,budget,operation,writing=True)

