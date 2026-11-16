"""Immutable bytea adapter; real SQL is pending separately authorized migration/RLS QA."""
import asyncio
import hashlib
from uuid import UUID
from psycopg.pq import TransactionStatus as T
from .repository import abort, status
from .settings import Fault
from .upload_validation import COLUMNS, public_file, check_content

class UploadOutcome:
    def __init__(self):
        self.commit_started = self.committed = self.rollback_confirmed = False
    def failure(self):
        unknown = (self.commit_started or self.committed) and not self.rollback_confirmed
        return Fault(504 if unknown else 503, 'upload_result_unknown' if unknown else 'uploads_unavailable')

class UploadRepository:
    def __init__(self, pool):
        self.pool = pool

    async def transaction(self, subject, budget, operation, writing=False):
        connection = None
        outcome = budget.create
        async def execute(sql, params=(), commit=False):
            budget.check()
            async def run():
                if commit and writing:
                    outcome.commit_started = True
                return await connection.execute(sql, params)
            return await budget.run(run, 2, lambda: abort(connection))
        async def query(sql, params=(), many=False):
            await execute("SELECT pg_catalog.set_config('statement_timeout', %s, true)", (str(max(1, min(2000, int(budget.remaining()*1000)))),))
            cursor = await execute(sql, params)
            return await budget.run(lambda: cursor.fetchall() if many else cursor.fetchone(), 2, lambda: abort(connection))
        def late(task):
            if not task.cancelled() and task.exception() is None:
                value = task.result(); abort(value); self.pool.release(value)
        try:
            connection = await budget.run(self.pool.getconn, 1, discard=late)
            if status(connection) != T.IDLE:
                raise Fault(503, 'uploads_unavailable')
            await execute('BEGIN ISOLATION LEVEL READ COMMITTED' + ('' if writing else ' READ ONLY'))
            await execute("SELECT pg_catalog.set_config('app.user_id', %s, true), pg_catalog.set_config('statement_timeout', %s, true), pg_catalog.set_config('lock_timeout', %s, true)", (subject, '2000', '1000'))
            result = await operation(query)
            await execute('COMMIT', commit=True)
            if writing:
                outcome.committed = True
            budget.check()
            if status(connection) != T.IDLE:
                raise Fault(503, 'uploads_unavailable')
            return result
        except BaseException as exc:
            if connection is not None and not connection.closed and status(connection) in (T.INTRANS,T.INERROR) and budget.remaining():
                try:
                    await execute('ROLLBACK')
                    outcome.rollback_confirmed = status(connection) == T.IDLE
                except BaseException:
                    abort(connection)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, Fault) and not outcome.commit_started:
                raise
            raise outcome.failure() from None
        finally:
            if connection is not None:
                self.pool.release(connection)

    async def project(self, query, subject, project_id):
        if await query('SELECT project_id FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s', (UUID(subject), UUID(project_id))) is None:
            raise Fault(404, 'project_not_found')

    async def ensure_project(self, subject, project_id, budget):
        return await self.transaction(subject, budget, lambda q: self.project(q, subject, project_id))

    async def save(self, subject, project_id, values, raw, budget):
        check_content(raw, values)
        async def operation(q):
            await self.project(q, subject, project_id)
            lock = int.from_bytes(hashlib.sha256(('cyomics:raw-upload:v1:'+subject).encode('ascii')).digest()[:8], 'big', signed=True)
            if not (await q('SELECT pg_catalog.pg_try_advisory_xact_lock(%s)', (lock,)))[0]:
                raise Fault(429, 'upload_busy')
            # READ COMMITTED + owner lock: idempotency, quota and INSERT share this TX.
            existing = await q('SELECT '+COLUMNS+' FROM cyomics_app.raw_uploads WHERE project_id=%s AND request_id=%s', (UUID(project_id),UUID(values['request_id'])))
            if existing is not None:
                file = public_file(existing)
                if any(file[k] != values[k] for k in ('request_id','filename','format','bytes','sha256')):
                    raise Fault(409, 'upload_request_conflict')
                return 200, {'file':file}
            counts = await q('SELECT count(*), COALESCE(sum(bytes),0), count(*) FILTER (WHERE project_id=%s), COALESCE(sum(bytes) FILTER (WHERE project_id=%s),0) FROM cyomics_app.raw_uploads WHERE EXISTS (SELECT 1 FROM cyomics_app.projects p WHERE p.project_id=raw_uploads.project_id AND p.owner_id=%s)', (UUID(project_id),UUID(project_id),UUID(subject)))
            if counts[0] >= 8 or counts[1]+len(raw) > 67108864 or counts[2] >= 4 or counts[3]+len(raw) > 33554432:
                raise Fault(413, 'upload_quota_exceeded')
            row = await q('INSERT INTO cyomics_app.raw_uploads (project_id,request_id,filename,format,bytes,sha256,content) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING '+COLUMNS, (UUID(project_id),UUID(values['request_id']),values['filename'],values['format'],len(raw),hashlib.sha256(raw).hexdigest(),raw))
            return 201, {'file':public_file(row)}
        return await self.transaction(subject, budget, operation, writing=True)

    async def read(self, subject, route, budget):
        async def operation(q):
            pid = route['project_id']
            await self.project(q, subject, pid)
            if route['kind'] == 'list':
                rows = await q('SELECT '+COLUMNS+' FROM cyomics_app.raw_uploads WHERE project_id=%s ORDER BY created_at DESC,file_id DESC LIMIT 5', (UUID(pid),), many=True)
                if len(rows) > 4:
                    raise Fault(503, 'uploads_unavailable')
                return {'items':[public_file(row) for row in rows], 'nextCursor':None}
            field = 'request_id' if route['kind'] == 'receipt' else 'file_id'
            row = await q('SELECT '+COLUMNS+(',content' if route['kind']=='content' else '')+' FROM cyomics_app.raw_uploads WHERE project_id=%s AND '+field+'=%s', (UUID(pid), UUID(route['id'])))
            if row is None:
                raise Fault(404, 'file_not_found')
            file = public_file(row[:9])
            if route['kind'] != 'content':
                return {'file':file}
            if len(row[9]) != file['bytes']:
                raise Fault(503, 'uploads_unavailable')
            raw = bytes(row[9])
            if len(raw) != file['bytes'] or hashlib.sha256(raw).hexdigest() != file['sha256']:
                raise Fault(503, 'uploads_unavailable')
            return file, raw
        return await self.transaction(subject, budget, operation)
