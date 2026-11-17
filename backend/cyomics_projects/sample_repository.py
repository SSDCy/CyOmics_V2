"""Append-only repository. Caller transports never supply trusted source descriptors."""
import asyncio
from uuid import UUID, uuid4
from psycopg.pq import TransactionStatus as T
from .repository import abort, status
from .import_repository import COLUMNS as DATASET_COLUMNS
from .settings import Fault
from .validation import canonical_uuid
from .sample_persistence import (canonical, sha, failure, trusted_source, request_input,
                                 decode_record, envelope, MODEL_CONTRACT, VALIDATOR_SHA256)

COLUMNS = ('sample_design_version_id,parent_version_id,revision,request_id,request_sha256,'
           'design_json,design_sha256,created_at,model_contract,validator_sha256')


class SampleDesignRepository:
    def __init__(self, pool): self.pool = pool

    async def _transaction(self, subject, budget, operation, writing=False):
        connection = None
        async def execute(sql, params=(), commit=False):
            budget.check()
            async def run():
                if commit and writing: budget.create.commit_started = True
                return await connection.execute(sql, params)
            return await budget.run(run, 2, lambda: abort(connection))
        async def query(sql, params=()):
            await execute("SELECT pg_catalog.set_config('statement_timeout', %s, true)",
                          (str(max(1,min(2000,int(budget.remaining()*1000)))),))
            cursor = await execute(sql, params)
            return await budget.run(cursor.fetchone, 2, lambda: abort(connection))
        def discard(task):
            if not task.cancelled() and task.exception() is None:
                late = task.result(); abort(late); self.pool.release(late)
        try:
            connection = await budget.run(self.pool.getconn, 1, discard=discard)
            if status(connection) != T.IDLE: raise failure(budget)
            await execute('BEGIN ISOLATION LEVEL READ COMMITTED' + ('' if writing else ' READ ONLY'))
            await execute("SELECT pg_catalog.set_config('app.user_id', %s, true), pg_catalog.set_config('statement_timeout', %s, true), pg_catalog.set_config('lock_timeout', %s, true)", (subject,'2000','1000'))
            result = await operation(query)
            budget.check()
            await execute('COMMIT', commit=True)
            if writing: budget.create.committed = True
            budget.check()
            if status(connection) != T.IDLE: raise failure(budget)
            return result
        except BaseException as error:
            if connection is not None and not connection.closed and status(connection) in (T.INTRANS,T.INERROR) and budget.remaining():
                try:
                    await execute('ROLLBACK')
                    budget.create.rollback_confirmed = status(connection) == T.IDLE
                except BaseException: abort(connection)
            # Once COMMIT is dispatched, conservatively retain uncertainty even
            # if cleanup runs. A rollback acknowledgement is not a commit receipt.
            if writing and (budget.create.commit_started or budget.create.committed):
                if connection is not None: abort(connection)
                raise failure(budget) from None
            if isinstance(error, asyncio.CancelledError): raise
            if isinstance(error, Fault): raise
            raise failure(budget, error) from None
        finally:
            if connection is not None: self.pool.release(connection)

    async def _source(self, query, subject, project, dataset):
        if await query('SELECT project_id FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s', (UUID(subject), UUID(project))) is None:
            raise Fault(404, 'dataset_not_found')
        row = await query('SELECT '+DATASET_COLUMNS+' FROM cyomics_app.dataset_versions WHERE project_id=%s AND dataset_version_id=%s', (UUID(project), UUID(dataset)))
        if row is None: raise Fault(404, 'dataset_not_found')
        return trusted_source(row, project, dataset)

    def _ids(self, subject, project, dataset, request=None):
        if not canonical_uuid(subject): raise Fault(401,'unauthenticated')
        if not canonical_uuid(project) or not canonical_uuid(dataset) or request is not None and not canonical_uuid(request):
            raise Fault(400,'invalid_sample_design_request')

    async def read(self, subject, project, dataset, family, budget, request_id=None):
        self._ids(subject,project,dataset,request_id)
        async def operation(query):
            source = await self._source(query,subject,project,dataset)
            if family not in ('Intensity','LFQ intensity'): raise Fault(422,'invalid_sample_design')
            row = await query('SELECT '+COLUMNS+' FROM cyomics_app.sample_design_versions WHERE project_id=%s AND dataset_version_id=%s AND quant_family=%s' + (' AND request_id=%s' if request_id else ' ORDER BY revision DESC LIMIT 1'),
                              (UUID(project), UUID(dataset), family) + ((UUID(request_id),) if request_id else ()))
            if row is None and request_id: raise Fault(404,'sample_design_not_found')
            return envelope(project,dataset,family,None if row is None else decode_record(row,source,family))
        return await self._transaction(subject,budget,operation)

    async def save(self, subject, project, dataset, family, payload, budget):
        self._ids(subject,project,dataset)
        async def operation(query):
            # Auth precedes payload/source-dependent validation and replay.
            source = await self._source(query,subject,project,dataset)
            if family not in ('Intensity','LFQ intensity'): raise Fault(422,'invalid_sample_design')
            await query('SELECT pg_catalog.pg_advisory_xact_lock(%s)',
                        (int.from_bytes(bytes.fromhex(sha(dataset+':'+family))[:8], 'big', signed=True),))
            body = request_input(payload,source,family)
            request_hash = sha(canonical({'project_id':project,'dataset_version_id':dataset,'quant_family':family,'request':body}))
            scope = (UUID(project), UUID(dataset), family)
            prior = await query('SELECT '+COLUMNS+' FROM cyomics_app.sample_design_versions WHERE project_id=%s AND dataset_version_id=%s AND quant_family=%s AND request_id=%s', scope+(UUID(body['request_id']),))
            if prior is not None:
                if prior[4] != request_hash: raise Fault(409,'sample_design_request_conflict')
                return envelope(project,dataset,family,decode_record(prior,source,family))
            head = await query('SELECT '+COLUMNS+' FROM cyomics_app.sample_design_versions WHERE project_id=%s AND dataset_version_id=%s AND quant_family=%s ORDER BY revision DESC LIMIT 1',scope)
            decoded = None if head is None else decode_record(head,source,family)
            if body['expected_head_version_id'] != (None if decoded is None else decoded['sample_design_version_id']):
                raise Fault(409,'sample_design_head_conflict')
            revision = 1 if decoded is None else decoded['revision']+1
            if revision > 100: raise Fault(422,'sample_design_version_limit')
            text = canonical(body['design'])
            budget.create.write_started = True
            row = await query('INSERT INTO cyomics_app.sample_design_versions (sample_design_version_id,project_id,dataset_version_id,quant_family,parent_version_id,revision,request_id,request_sha256,design_json,design_sha256,model_contract,validator_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING '+COLUMNS,
                              (uuid4(),)+scope+(None if decoded is None else UUID(decoded['sample_design_version_id']), revision,UUID(body['request_id']),request_hash,text,sha(text),MODEL_CONTRACT,VALIDATOR_SHA256))
            return envelope(project,dataset,family,decode_record(row,source,family))
        return await self._transaction(subject,budget,operation,writing=True)
