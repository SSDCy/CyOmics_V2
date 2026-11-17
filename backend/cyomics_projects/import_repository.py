"""Owner-scoped immutable aggregate repository; no connections or SQL on import."""
import asyncio
from uuid import UUID
from psycopg.pq import TransactionStatus as T
from .repository import abort, status
from .import_persistence import ImportFault, StoredRaw, check_ids
from .import_persistence import decode_version
from .import_report import CONTRACT_VERSION
from .upload_validation import MAX_BYTES

COLUMNS = ('dataset_version_id,project_id,file_id,parser_contract,config_sha256,config_snapshot,'
           'source_bytes,source_sha256,representation,parsed_table_materialized,report_json,report_sha256,created_at')


class ImportRepository:
    def __init__(self, pool):
        self.pool = pool

    async def _transaction(self, subject, budget, operation, *, writing=False, key=None, before_commit=lambda: None):
        connection = None
        commit_started = committed = rollback_confirmed = False
        async def execute(sql, params=(), commit=False):
            budget.check()
            async def run():
                nonlocal commit_started
                if commit and writing:
                    before_commit()
                    budget.check()
                    commit_started = True
                return await connection.execute(sql, params)
            return await budget.run(run, 2, lambda: abort(connection))
        async def query(sql, params=()):
            await execute("SELECT pg_catalog.set_config('statement_timeout', %s, true)", (str(max(1,min(2000,int(budget.remaining()*1000)))),))
            cursor = await execute(sql, params)
            return await budget.run(cursor.fetchone, 2, lambda: abort(connection))
        def discard(task):
            if not task.cancelled() and task.exception() is None:
                late = task.result(); abort(late); self.pool.release(late)
        try:
            connection = await budget.run(self.pool.getconn, 1, discard=discard)
            if status(connection) != T.IDLE:
                raise ImportFault(503, 'import_unavailable')
            await execute('BEGIN ISOLATION LEVEL READ COMMITTED' + ('' if writing else ' READ ONLY'))
            await execute("SELECT pg_catalog.set_config('app.user_id', %s, true), pg_catalog.set_config('statement_timeout', %s, true), pg_catalog.set_config('lock_timeout', %s, true)", (subject,'2000','1000'))
            result = await operation(query)
            budget.check()
            await execute('COMMIT', commit=True)
            committed = writing
            budget.check()
            if status(connection) != T.IDLE:
                raise ImportFault(503, 'import_unavailable')
            return result
        except BaseException as error:
            if connection is not None and not connection.closed and status(connection) in (T.INTRANS,T.INERROR) and budget.remaining():
                try:
                    await execute('ROLLBACK')
                    rollback_confirmed = status(connection) == T.IDLE
                except BaseException:
                    abort(connection)
            if writing and (commit_started or committed) and not rollback_confirmed:
                raise ImportFault(504, 'import_result_unknown', version_key=key) from None
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, ImportFault):
                raise
            if isinstance(error, TimeoutError):
                raise ImportFault(504, 'import_timeout') from None
            raise ImportFault(503, 'import_unavailable') from None
        finally:
            if connection is not None:
                self.pool.release(connection)

    async def _authorize(self, query, subject, project_id, file_id, content=False):
        if await query('SELECT project_id FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s', (UUID(subject),UUID(project_id))) is None:
            raise ImportFault(404, 'project_not_found')
        row = await query('SELECT file_id,project_id,format,bytes,sha256,status' + (',content' if content else '') + ' FROM cyomics_app.raw_uploads WHERE project_id=%s AND file_id=%s', (UUID(project_id),UUID(file_id)))
        if row is None:
            raise ImportFault(404, 'file_not_found')
        return row

    async def load_raw(self, subject, project_id, file_id, budget):
        check_ids(subject, project_id, file_id)
        async def operation(query):
            row = await self._authorize(query,subject,project_id,file_id,content=True)
            if not isinstance(row[6],(bytes,bytearray,memoryview)) or not 1 <= len(row[6]) <= MAX_BYTES:
                raise ImportFault(503,'import_source_integrity_failed')
            return StoredRaw(str(row[0]),str(row[1]),row[2],row[3],row[4],row[5],bytes(row[6]))
        return await self._transaction(subject,budget,operation)

    async def lookup(self, subject, project_id, file_id, contract, config_hash, budget):
        check_ids(subject,project_id,file_id)
        async def operation(query):
            await self._authorize(query,subject,project_id,file_id)
            return await query('SELECT '+COLUMNS+' FROM cyomics_app.dataset_versions WHERE project_id=%s AND file_id=%s AND parser_contract=%s AND config_sha256=%s', (UUID(project_id),UUID(file_id),contract,config_hash))
        return await self._transaction(subject,budget,operation)

    async def publish(self, subject, candidate, budget, checkpoint):
        source = candidate.source
        check_ids(subject,source.project_id,source.raw_file_id)
        async def operation(query):
            row = await self._authorize(query,subject,source.project_id,source.raw_file_id)
            if row[3] != source.expected_bytes or row[4] != source.expected_raw_sha256 or row[2] != 'proteinGroups_txt' or row[5] != 'stored':
                raise ImportFault(503,'import_source_integrity_failed')
            checkpoint()
            result = await query('INSERT INTO cyomics_app.dataset_versions (dataset_version_id,project_id,file_id,parser_contract,config_sha256,config_snapshot,source_bytes,source_sha256,representation,parsed_table_materialized,report_json,report_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (file_id,parser_contract,config_sha256) DO NOTHING RETURNING '+COLUMNS,
                                 (UUID(candidate.version_id),UUID(source.project_id),UUID(source.raw_file_id),
                                  CONTRACT_VERSION,candidate.config_sha256,candidate.config_json,source.expected_bytes,
                                  source.expected_raw_sha256,'raw_backed',False,candidate.report_json,candidate.report_sha256))
            if result is None:
                # READ COMMITTED: a new statement observes a concurrent committed winner.
                result = await query('SELECT '+COLUMNS+' FROM cyomics_app.dataset_versions WHERE project_id=%s AND file_id=%s AND parser_contract=%s AND config_sha256=%s', (UUID(source.project_id),UUID(source.raw_file_id),CONTRACT_VERSION,candidate.config_sha256))
            if result is None:
                raise ImportFault(503,'import_unavailable')
            checkpoint()
            # Validate the full returned aggregate before COMMIT, including a concurrent winner.
            return decode_version(result,source,candidate.config_json,candidate.version_id)
        return await self._transaction(subject,budget,operation,writing=True,key=candidate.version_id,before_commit=checkpoint)
