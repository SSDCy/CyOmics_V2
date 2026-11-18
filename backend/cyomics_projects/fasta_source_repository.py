"""Default-off internal Owner/project/source read. No auth, writes or parsing.

Only a trusted Authority-verified caller may supply actor. The supplied pool's
actual runtime role and RLS are a separate live configuration/SQL gate.
"""
import asyncio
from datetime import datetime, timezone
import json
import math
from uuid import UUID
from psycopg.pq import TransactionStatus as T
from .settings import Fault
from . import fasta_intake_contract as model

SQL = ('SELECT s.source_id,s.project_id,s.request_id,s.filename,s.format,'
       's.bytes,s.sha256,s.status,s.created_at,s.content '
       'FROM cyomics_app.fasta_sources s '
       'JOIN cyomics_app.projects p ON p.project_id=s.project_id '
       'WHERE p.owner_id=%s AND p.project_id=%s AND s.project_id=%s AND s.source_id=%s LIMIT 2')


def _uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _fault(status=503, code='fasta_intake_unavailable'):
    return Fault(status, code)


def _decode(row, project, identifier, check):
    check()
    value = None
    try:
        if type(row) not in (tuple,list) or len(row) != 10:
            raise ValueError()
        sid,pid,rid,name,fmt,size,sha,state,created,content = row
        if (any(type(v) is not UUID for v in (sid,pid,rid))
                or str(sid) != identifier or str(pid) != project
                or type(size) is not int or not 1 <= size <= model.MAX_SOURCE
                or any(type(v) is not str for v in (name,fmt,sha,state))
                or type(created) is not datetime or created.tzinfo is None
                or created.utcoffset() is None):
            raise ValueError()
        if type(content) is memoryview:
            if (content.ndim != 1 or content.itemsize != 1 or not content.contiguous
                    or not 1 <= content.nbytes <= model.MAX_SOURCE):
                raise ValueError()
            content = content.tobytes()
        if type(content) is not bytes or len(content) != size:
            raise ValueError()
        stamp = created.astimezone(timezone.utc).isoformat(timespec='microseconds')
        if not stamp.endswith('+00:00'):
            raise ValueError()
        stamp = stamp[:-6]+'Z'
        document = dict(source_id=str(sid),project_id=str(pid),request_id=str(rid),
            filename=name,format=fmt,bytes=size,sha256=sha,status=state,createdAt=stamp)
        raw = json.dumps(document,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')
        value = model.source_from_json(raw,content)
    except BaseException:
        pass
    if value is None:
        raise _fault(503,'fasta_source_integrity_failed')
    check()
    return value


class FastaSourceRepository:
    def __init__(self, pool=None):
        self.pool = pool
        self._quarantined = False

    @property
    def quarantined(self):
        return self._quarantined

    async def load_owned_source(self, actor=None, project_id=None, source_id=None, budget=None):
        if self.pool is None or self._quarantined:
            raise _fault()
        if not _uuid(actor):
            raise _fault(401,'unauthenticated')
        if not _uuid(project_id) or not _uuid(source_id):
            raise _fault(400,'invalid_fasta_request')
        if budget is None or any(not callable(getattr(budget,k,None)) for k in ('check','remaining','run')):
            raise _fault()
        pool = self.pool  # late cleanup belongs to this exact trusted pool
        connection = None
        abandoned = commit_started = cancelled = begun = False
        failure = value = None
        cleanup = {}

        def check():
            if self._quarantined:
                raise _fault()
            budget.check()

        def milliseconds(maximum):
            remaining = budget.remaining()
            if type(remaining) not in (int,float) or not math.isfinite(remaining) or remaining <= 0:
                raise TimeoutError()
            return str(max(1,min(maximum,int(remaining*1000))))

        def close_once(item):
            if item is None:
                return True
            state = cleanup.setdefault(id(item),{'connection':item,'close_attempted':False,
                'release_attempted':False,'released':False,'uncertain':False})
            if not state['close_attempted']:
                state['close_attempted'] = True
                try:
                    if item.closed is not True:
                        item.pgconn.finish()
                except BaseException:
                    state['uncertain'] = True
            if state['uncertain']:
                self._quarantined = True
                return False
            try:
                closed = item.closed is True
            except BaseException:
                state['uncertain'] = True
                closed = False
            if not closed:
                self._quarantined = True
            return closed

        def release_closed(item):
            if item is None:
                return True
            if not close_once(item):
                return False
            state = cleanup[id(item)]
            if state['release_attempted']:
                return state['released']
            state['release_attempted'] = True
            try:
                pool.release(item)
                state['released'] = True
            except BaseException:
                self._quarantined = True
            return state['released']

        def abandon_acquisition():
            # A still-running getconn cannot safely be treated as a free read
            # slot. A late result is closed below; no automatic unquarantine.
            self._quarantined = True

        def discard(task):
            try:
                if not task.cancelled():
                    error = task.exception()
                    if error is None:
                        release_closed(task.result())
            except BaseException:
                self._quarantined = True

        def abandon_connection():
            nonlocal abandoned
            abandoned = True
            # Never let a driver finish error prevent Budget cancellation and
            # eventual task.exception() consumption.
            close_once(connection)

        async def execute(sql, params=(), *, committing=False):
            check()
            if abandoned or connection.closed:
                raise _fault()
            async def operation():
                nonlocal commit_started
                check()
                if committing:
                    commit_started = True
                return await connection.execute(sql,params)
            return await budget.run(operation,2,abandon_connection)

        async def fetch(cursor):
            check()
            return await budget.run(cursor.fetchone,2,abandon_connection)

        try:
            check()
            async def acquire():
                check()
                return await pool.getconn()
            connection = await budget.run(acquire,1,abandon_acquisition,discard)
            check()
            if connection.closed or connection.pgconn.transaction_status != T.IDLE:
                raise _fault()
            await execute('BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY')
            begun = True
            await execute("SELECT pg_catalog.set_config('app.user_id', %s, true), "
                "pg_catalog.set_config('statement_timeout', %s, true), "
                "pg_catalog.set_config('lock_timeout', %s, true)",
                (actor,milliseconds(2000),milliseconds(1000)))
            await execute("SELECT pg_catalog.set_config('statement_timeout', %s, true)",(milliseconds(2000),))
            cursor = await execute(SQL,(UUID(actor),UUID(project_id),UUID(project_id),UUID(source_id)))
            first = await fetch(cursor)
            if await fetch(cursor) is not None:
                raise _fault(503,'fasta_source_integrity_failed')
            if first is None:
                raise _fault(404,'fasta_source_not_found')
            value = _decode(first,project_id,source_id,check)
            check()
            await execute('COMMIT',committing=True)
            check()
            if connection.pgconn.transaction_status != T.IDLE:
                raise _fault()
        except BaseException as error:
            cancelled = isinstance(error,asyncio.CancelledError)
            # Never issue another SQL statement on a cancelled/abandoned or
            # COMMIT-uncertain socket. Even read-only results remain unconfirmed.
            rollback = False
            if connection is not None and begun and not abandoned and not commit_started:
                try:
                    rollback = (not connection.closed and connection.pgconn.transaction_status in (T.INTRANS,T.INERROR)
                        and bool(budget.remaining()))
                except BaseException:
                    pass
            if rollback:
                try:
                    await execute('ROLLBACK')
                except BaseException:
                    close_once(connection)
            if commit_started or isinstance(error,TimeoutError):
                failure = _fault(504,'fasta_intake_timeout')
            elif isinstance(error,Fault) and (error.status,error.code) in {
                    (404,'fasta_source_not_found'),(503,'fasta_source_integrity_failed'),
                    (503,'fasta_intake_unavailable')}:
                failure = _fault(error.status,error.code)
            else:
                failure = _fault()
        finally:
            cleanup_ok = release_closed(connection)
        if cancelled:
            raise asyncio.CancelledError() from None
        if failure is not None:
            # Preserve timeout/COMMIT uncertainty even if cleanup also fails.
            if not cleanup_ok and failure.status != 504:
                raise _fault() from None
            raise failure from None
        if not cleanup_ok or self._quarantined:
            raise _fault() from None
        try:
            check()
        except BaseException:
            raise _fault(504,'fasta_intake_timeout') from None
        return value
